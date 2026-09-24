"""Simulator-backed Edge device for one durable V3 course session.

This composition root never imports or calls the range simulator.  It accepts
only detached task/request data and committed outbox entries exposed while
``course_session_v3.execution_admission`` holds the session lock.  The device
journal is always acquired second.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping

from nxt_edge_task.contracts import (
    Availability,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    EventKind,
    TaskEvent,
    TaskRequest,
    parse_utc,
    request_topic,
)
from nxt_edge_task.executor import (
    ROBOT_RECORD_KINDS,
    TASK_EVENT_PERSISTED,
    RobotCore,
    derive_robot_view,
)
from nxt_edge_task.journal import JsonlJournal, JournalRecord, PreconditionFailed
from scripts.course_session_v3 import execution_admission, restart_reconciliation


_TERMINALS = frozenset(
    {
        EventKind.SUCCEEDED.value,
        EventKind.FAILED.value,
        EventKind.REJECTED.value,
        EventKind.INCONCLUSIVE.value,
    }
)
_RUNTIME_STATUS_KEYS = {
    "session_id",
    "round_id",
    "robot_id",
    "simulation_time_utc",
    "session_state",
    "activity",
    "payload_balls",
    "paused",
    "faulted",
    "estop_latched",
    "awaiting_human",
}


class SimulatorBackedTaskDevice:
    """Bridge committed simulator facts into the existing Edge v1 lifecycle."""

    def __init__(
        self,
        session_root: str | Path,
        config: EdgeTaskConfig,
        robot_id: str,
        *,
        journal_path: str | Path,
        initialize: bool = False,
        provisioning_nonce: Callable[[], str] | None = None,
    ) -> None:
        self.session_root = Path(session_root)
        self.config = config
        self.robot = config.robot(robot_id)
        self.journal = JsonlJournal(
            journal_path, allowed_kinds=ROBOT_RECORD_KINDS
        )
        self.initialize = initialize
        self.provisioning_nonce = provisioning_nonce
        self.core = RobotCore(config, self.robot, "simulator_backed")
        self._started = False
        self._identity: dict[str, Any] | None = None

    @property
    def view(self):
        return self.core.view

    @staticmethod
    def _when(identity: Mapping[str, Any], now_sim_t_s: float):
        from scripts.course_collection_execution import simulation_utc

        return parse_utc(simulation_utc(identity, now_sim_t_s))

    def _append(self, decide) -> tuple[JournalRecord, ...]:
        appended = self.journal.append_via(self.core.builder(decide))
        self.core.absorb(appended)
        return appended

    def _execution_for_task(self, store, task_id: str) -> tuple[str, dict[str, Any]]:
        matches = [
            (execution_id, row)
            for execution_id, row in store.replay()["executions"].items()
            if row["task_id"] == task_id
        ]
        if len(matches) != 1:
            raise PreconditionFailed(
                "execution_identity_conflict",
                "device task must bind exactly one V3 execution",
            )
        return matches[0]

    def _reconcile_persisted_evidence(self, admission) -> None:
        """Finish cross-journal writes from any earlier process attempt."""

        state = admission.store.replay()
        current_task_ids = {
            row["task_id"] for row in state["executions"].values()
        }
        # A request-only torn prefix has no device event to scan.  Complete an
        # exact current-session pre-acceptance rejection directly from V3;
        # historical tasks are deliberately outside this reconciliation.
        for execution_id, row in state["executions"].items():
            if (
                row["edge_evidence"]["accepted"]
                or row["edge_evidence"]["effective_state"]
                != EventKind.REJECTED.value
            ):
                continue
            task = self.core.view.tasks.get(row["task_id"])
            if task is None:
                continue
            candidates = [
                outbox
                for outbox in admission.store.committed_outbox(
                    unconfirmed_only=False
                )
                if outbox["execution_id"] == execution_id
                and outbox["event_kind"] == EventKind.REJECTED.value
            ]
            if len(candidates) != 1:
                raise PreconditionFailed(
                    "committed_event_missing",
                    "pre-acceptance terminal lacks exact committed REJECTED",
                )
            now = self._when(admission.identity, admission.now_sim_t_s)
            request = task.request
            outbox = candidates[0]
            self._append(
                lambda view: self.core.on_preaccepted_committed_terminal(
                    view, request, outbox, now
                )
            )

        records = tuple(
            record
            for record in self.journal.read()
            if record.record_kind == TASK_EVENT_PERSISTED
            and record.payload["event"]["event_sequence"] > 0
            and record.payload["event"]["task_id"] in current_task_ids
        )

        # A durable ACCEPTED is copied first.  A crash after the device append
        # must not turn the same fact into an unaccepted V3 terminal.
        for record in records:
            event = TaskEvent.from_dict(record.payload["event"])
            if event.kind is not EventKind.ACCEPTED:
                continue
            execution_id, row = self._execution_for_task(
                admission.store, event.task_id
            )
            if not row["edge_evidence"]["accepted"]:
                admission.store.record_acceptance(execution_id, record.record_id)

        # Restart-owned outcomes precede every residual committed event.  They
        # can permanently block old authorization/outbox facts in V3.
        for record in records:
            event = TaskEvent.from_dict(record.payload["event"])
            if event.reason_code not in {
                "not_started_after_restart",
                "interrupted_execution_unknown_outcome",
            }:
                continue
            execution_id, row = self._execution_for_task(
                admission.store, event.task_id
            )
            if record.record_id in row["edge_evidence"]["event_ids"]:
                continue
            admission.store.record_device_restart_outcome(
                execution_id,
                record,
                now_sim_t_s=admission.now_sim_t_s,
            )

        # Exact committed events are idempotently copied and confirmed last.
        # Events whose outbox was blocked by the restart pass never cross.
        for record in records:
            outbox_id = record.payload.get("committed_outbox_id")
            if outbox_id is None:
                continue
            event = TaskEvent.from_dict(record.payload["event"])
            execution_id, row = self._execution_for_task(
                admission.store, event.task_id
            )
            state = admission.store.replay()
            if outbox_id in state["blocked_outbox"]:
                continue
            terminals = [event.kind.value] if event.kind.value in _TERMINALS else []
            if record.record_id not in row["edge_evidence"]["event_ids"]:
                admission.store.record_edge_evidence(
                    execution_id,
                    terminal_states=terminals,
                    event_ids=[record.record_id],
                    now_sim_t_s=admission.now_sim_t_s,
                    outbox_id=outbox_id,
                )
            state = admission.store.replay()
            if outbox_id in state["outbox"] and outbox_id not in state["blocked_outbox"]:
                admission.store.confirm_outbox(outbox_id, record.record_id)

    def start(self) -> None:
        """Start or restart the real device process and reconcile restart facts."""

        if self._started:
            raise ValueError("device process is already started")
        with restart_reconciliation(self.session_root) as admission:
            if (
                admission.identity["site_id"] != self.config.site_id
                or admission.identity["deployment_id"] != self.config.deployment_id
            ):
                raise ValueError("device and V3 session identity differ")
            records = self.journal.read()
            preview = derive_robot_view(self.robot.robot_id, records)
            self.core.assert_startable(preview, initialize=self.initialize)
            nonce = None
            if self.initialize and not preview.provisioned:
                if self.provisioning_nonce is None:
                    raise ValueError("initial device start requires a provisioning nonce")
                nonce = self.provisioning_nonce()
            now = self._when(admission.identity, admission.now_sim_t_s)
            self._append(
                lambda view: self.core.on_start(
                    view,
                    now,
                    initialize=self.initialize,
                    provisioning_nonce=nonce,
                )
            )
            self._reconcile_persisted_evidence(admission)
            self._identity = deepcopy(admission.identity)
        self._started = True

    def _require_started(self) -> None:
        if not self._started:
            raise ValueError("device process is not started")

    def admit(
        self, task_request: TaskRequest, execution_request: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Durably record the V3 request before persisting Edge ACCEPTED."""

        self._require_started()
        if not isinstance(task_request, TaskRequest):
            raise TypeError("verified TaskRequest required")
        with execution_admission(self.session_root) as admission:
            receipt = admission.store.submit(execution_request)
            state = admission.store.replay()
            execution_id = receipt["execution_id"]
            row = state["executions"][execution_id]
            if (
                row["task_id"] != task_request.task_id
                or row["incarnation"] != task_request.target_incarnation
                or row["request_id"] != receipt["request_id"]
            ):
                raise PreconditionFailed(
                    "execution_identity_conflict", "task/request/V3 identity differs"
                )
            now = self._when(admission.identity, admission.now_sim_t_s)
            if row["state"] in {
                "SUCCEEDED",
                "PARTIAL",
                "REJECTED",
                "MISSED",
                "FAILED",
                "INCONCLUSIVE",
            } and not row["edge_evidence"]["accepted"]:
                candidates = [
                    outbox
                    for outbox in admission.store.committed_outbox(
                        unconfirmed_only=False
                    )
                    if outbox["execution_id"] == execution_id
                    and outbox["event_kind"] == EventKind.REJECTED.value
                ]
                if len(candidates) != 1:
                    raise PreconditionFailed(
                        "committed_event_missing",
                        "pre-acceptance terminal lacks exact committed REJECTED",
                    )
                outbox = candidates[0]
                self._append(
                    lambda view: self.core.on_preaccepted_committed_terminal(
                        view, task_request, outbox, now
                    )
                )
                event_record = self.core.committed_event_record(
                    self.journal.read(), outbox["outbox_id"]
                )
                admission.store.record_edge_evidence(
                    execution_id,
                    terminal_states=[EventKind.REJECTED.value],
                    event_ids=[event_record.record_id],
                    now_sim_t_s=admission.now_sim_t_s,
                    outbox_id=outbox["outbox_id"],
                )
                admission.store.confirm_outbox(
                    outbox["outbox_id"], event_record.record_id
                )
                return receipt

            topic = request_topic(self.config.site_id, self.robot.robot_id)
            self._append(
                lambda view: (
                    self.core.decide_persisted_request(view, task_request, now)
                    if task_request.task_id in view.tasks
                    and view.tasks[task_request.task_id].decision is None
                    else self.core.on_request(
                        view, topic, task_request.canonical_bytes(), now
                    )[0]
                )
            )
            accepted = [
                record
                for record in self.journal.read()
                if record.record_kind == TASK_EVENT_PERSISTED
                and record.payload["event"]["task_id"] == task_request.task_id
                and record.payload["event"]["kind"] == EventKind.ACCEPTED.value
            ]
            if len(accepted) != 1:
                raise EdgeTaskError(
                    ErrorCode.AUTHORIZATION_BLOCKED,
                    "device did not durably accept the V3 request",
                )
            admission.store.record_acceptance(execution_id, accepted[0].record_id)
            return receipt

    def consume_committed(
        self,
        *,
        crash_hook: Callable[[str, Mapping[str, Any]], None] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Persist device evidence, then V3 evidence, then outbox confirmation."""

        self._require_started()
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be a positive integer")
        delivered: list[dict[str, Any]] = []
        with execution_admission(self.session_root) as admission:
            rows = admission.store.committed_outbox(unconfirmed_only=True)
            if limit is not None:
                rows = rows[:limit]
            now = self._when(admission.identity, admission.now_sim_t_s)
            for outbox in rows:
                self._append(
                    lambda view, item=outbox: self.core.on_committed_execution_event(
                        view, item, now
                    )
                )
                event_record = self.core.committed_event_record(
                    self.journal.read(), outbox["outbox_id"]
                )
                payload = {**deepcopy(outbox), "event_id": event_record.record_id}
                if crash_hook is not None:
                    crash_hook("after_device_append", payload)
                terminals = (
                    [outbox["event_kind"]]
                    if outbox["event_kind"] in _TERMINALS
                    else []
                )
                admission.store.record_edge_evidence(
                    outbox["execution_id"],
                    terminal_states=terminals,
                    event_ids=[event_record.record_id],
                    now_sim_t_s=admission.now_sim_t_s,
                    outbox_id=outbox["outbox_id"],
                )
                if crash_hook is not None:
                    crash_hook("after_v3_evidence", payload)
                admission.store.confirm_outbox(
                    outbox["outbox_id"], event_record.record_id
                )
                if crash_hook is not None:
                    crash_hook("after_v3_confirm", payload)
                delivered.append(payload)
        return delivered

    def _pending_publications_locked(self, admission) -> list[dict[str, Any]]:
        records = self.journal.read()
        event_records: dict[tuple[str, int, int], JournalRecord] = {}
        for record in records:
            if record.record_kind != TASK_EVENT_PERSISTED:
                continue
            event = record.payload["event"]
            key = (event["task_id"], event["boot_sequence"], event["event_sequence"])
            prior = event_records.get(key)
            if prior is not None and prior.payload["event"] != event:
                raise PreconditionFailed(
                    "event_identity_conflict", "device event sequence has conflicting bytes"
                )
            event_records[key] = record
        state = admission.store.replay()
        publishable: list[dict[str, Any]] = []
        for event, _record_sequence in self.view.pending_publications():
            key = (event["task_id"], event["boot_sequence"], event["event_sequence"])
            record = event_records[key]
            matches = [
                row
                for row in state["executions"].values()
                if row["task_id"] == event["task_id"]
            ]
            if len(matches) != 1 or record.record_id not in matches[0]["edge_evidence"]["event_ids"]:
                continue
            outbox_id = record.payload.get("committed_outbox_id")
            if outbox_id is not None and state["confirmed"].get(outbox_id) != record.record_id:
                continue
            publishable.append(deepcopy(event))
        return publishable

    def pending_publications(self) -> list[dict[str, Any]]:
        """V3-confirmed detached events; transport publication stays separate."""

        self._require_started()
        with execution_admission(self.session_root) as admission:
            return self._pending_publications_locked(admission)

    def confirm_published(self, event: Mapping[str, Any]) -> None:
        """Persist a transport confirmation for one already-persisted event."""

        self._require_started()
        parsed = TaskEvent.from_dict(event)
        key = (parsed.boot_sequence, parsed.event_sequence)
        task = self.view.tasks.get(parsed.task_id)
        if task is None or parsed.to_dict() not in task.events:
            raise PreconditionFailed(
                "unknown_persisted_event", "publication does not name device evidence"
            )
        if key in task.confirmed:
            return
        when = parse_utc(parsed.reported_at_utc)
        with execution_admission(self.session_root) as admission:
            if parsed.to_dict() not in self._pending_publications_locked(admission):
                raise PreconditionFailed(
                    "unknown_persisted_event",
                    "publication does not name V3-confirmed device evidence",
                )
            self._append(
                lambda _view: [
                    self.core.publish_confirmed_spec(parsed.to_dict(), when)
                ]
            )

    def status_message(self, runtime_status: Mapping[str, Any]):
        """Project a complete detached runtime observation; missingness fails closed."""

        self._require_started()
        value = deepcopy(runtime_status)
        if type(value) is not dict or set(value) != _RUNTIME_STATUS_KEYS:
            raise ValueError("runtime status must contain the exact detached fields")
        if self._identity is None or any(
            value[key] != self._identity[key] for key in ("session_id", "round_id")
        ):
            raise ValueError("runtime status session identity differs")
        if value["robot_id"] != self.robot.robot_id:
            raise ValueError("runtime status robot identity differs")
        if value["session_state"] not in {"ACTIVE", "PAUSED", "ENDED"}:
            raise ValueError("runtime status session state is invalid")
        if type(value["activity"]) is not str or not value["activity"]:
            raise ValueError("runtime status activity is invalid")
        if type(value["payload_balls"]) is not int or value["payload_balls"] < 0:
            raise ValueError("runtime status payload is invalid")
        for key in ("paused", "faulted", "estop_latched", "awaiting_human"):
            if type(value[key]) is not bool:
                raise ValueError("runtime status flags must be booleans")
        now = parse_utc(value["simulation_time_utc"])
        projected = deepcopy(self.view)
        if (
            self.view.availability is Availability.ESTOPPED
            or value["estop_latched"]
        ):
            projected.availability = Availability.ESTOPPED
            projected.awaiting_human = True
        elif self.view.availability is Availability.FAULTED or value["faulted"]:
            projected.availability = Availability.FAULTED
            projected.awaiting_human = True
        elif value["awaiting_human"] or self.view.awaiting_human:
            projected.availability = Availability.AWAITING_HUMAN
            projected.awaiting_human = True
        elif value["paused"] or value["session_state"] == "PAUSED":
            projected.availability = Availability.PAUSED
        elif (
            value["session_state"] == "ACTIVE"
            and value["activity"] == "IDLE"
            and value["payload_balls"] == 0
            and projected.current_task_id is None
        ):
            projected.availability = Availability.AVAILABLE
        else:
            projected.availability = Availability.BUSY
        message = self.core.status_message(projected, now)
        self.view.status_sequence = projected.status_sequence
        return message


__all__ = ["SimulatorBackedTaskDevice"]
