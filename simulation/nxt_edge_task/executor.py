"""Protocol-double executor rules: the simulated robot's side of the exchange.

``RobotCore`` is pure: it derives its view from its own journal, decides
what to persist for each received request or clock tick, and exposes the
events that still need publishing.  Composition roots own the transport,
the clock, and the process.

Invariants the mock robot honours (and a real device would have to):

* persist-before-publish -- every decision and every event is a journal
  record before it can be published; an unpersisted event is never sent;
* execution evidence comes in three separate records -- ``task_decision``
  (intent), ``execution_started`` (the simulated action began), and
  ``execution_completed`` (it ended).  Execution count is the number of
  ``execution_started`` records for a task;
* restart branches -- accepted-but-never-started -> FAILED
  ``not_started_after_restart``; started-but-not-completed -> INCONCLUSIVE
  ``interrupted_execution_unknown_outcome`` and the robot stays
  ``awaiting_human``; completed-but-unpublished -> republish the original
  event; completed-but-no-terminal-persisted -> report the persisted
  outcome; a request persisted without its decision is decided at restart
  exactly as if it had just arrived; a rejected decision without its event
  publishes that rejection.  Nothing is ever re-executed after a restart;
* the robot's protective condition (``awaiting_human``, availability,
  energy and fault facts) is derived from the persisted task events and the
  operator reset record, never from a separate trailing record, so a torn
  batch can only leave the robot *more* protected, never less;
* identity continuity -- an empty journal is not a first boot.  The first
  boot is an explicit operator act (``robot_provisioned``); afterwards a
  missing journal is state loss and the start is refused before any request
  can be read.  Re-provisioning is a new incarnation whose ``boot_id``
  carries a fresh provisioning token, so the Edge can tell it apart even
  when the boot counter restarts at the number it already saw;
* a duplicate identical request replays the task's full persisted history
  without executing; a request whose ``task_id`` does not match its
  content is rejected at ``event_sequence`` 0 and touches no task;
* local protection is script-driven: no Edge message can pause, resume,
  reset, or clear anything; only the explicit SIMULATION ``simulate_reset``
  returns the double to ``available``.

There is no physics here: no navigation, no ball counts, no positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .contracts import (
    Availability,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    EventKind,
    RobotConfig,
    RobotStatusMessage,
    TaskEvent,
    TaskRequest,
    bounded_detail,
    parse_utc,
    stable_digest,
    topic_parts,
    utc_text,
)
from .journal import JournalRecord, PreconditionFailed, RecordSpec

ROBOT_PROVISIONED = "robot_provisioned"
ROBOT_STARTED = "robot_started"
REQUEST_RECEIVED = "request_received"
REQUEST_REJECTED = "request_rejected"
TASK_DECISION = "task_decision"
TASK_EVENT_PERSISTED = "task_event_persisted"
EVENT_PUBLISH_CONFIRMED = "event_publish_confirmed"
EXECUTION_STARTED = "execution_started"
EXECUTION_PROGRESS = "execution_progress"
EXECUTION_COMPLETED = "execution_completed"
SIMULATE_RESET = "simulate_reset"

ROBOT_RECORD_KINDS = frozenset(
    {
        ROBOT_PROVISIONED,
        ROBOT_STARTED,
        REQUEST_RECEIVED,
        REQUEST_REJECTED,
        TASK_DECISION,
        TASK_EVENT_PERSISTED,
        EVENT_PUBLISH_CONFIRMED,
        EXECUTION_STARTED,
        EXECUTION_PROGRESS,
        EXECUTION_COMPLETED,
        SIMULATE_RESET,
    }
)

_TERMINAL_EVENT_KINDS = frozenset(kind.value for kind in (EventKind.SUCCEEDED, EventKind.FAILED, EventKind.INCONCLUSIVE, EventKind.REJECTED))
_RECHARGE_REASONS = frozenset({"energy_insufficient", "needs_manual_recharge"})

BEHAVIORS = (
    "accept_and_succeed",
    "fail_cannot_continue",
    "help_needs_manual_recharge",
    "help_unknown_fault",
    "silent_after_accept",
    "crash_after_accept",
    "crash_after_execution_started",
    "crash_after_result_persisted",
    "standby",
)


def parse_behavior(text: str) -> tuple[str, str | None]:
    name, _, argument = text.partition(":")
    if name not in BEHAVIORS:
        raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"unknown behavior {name!r}")
    if name == "help_unknown_fault":
        if not argument:
            raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "help_unknown_fault requires :<code>")
        return name, argument
    if argument:
        raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"behavior {name!r} takes no argument")
    return name, None


@dataclass
class RobotTaskView:
    task_id: str
    request: TaskRequest
    content_digest: str
    decision: str | None = None
    reason_code: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    confirmed: set[tuple[int, int]] = field(default_factory=set)
    execution_started: bool = False
    execution_completed: bool = False
    completed_outcome: str | None = None
    terminal_kind: str | None = None
    step: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.terminal_kind is not None

    @property
    def blocked(self) -> bool:
        return any(event["kind"] == EventKind.ASSISTANCE_REQUIRED.value for event in self.events) and not self.is_terminal

    @property
    def active(self) -> bool:
        return self.decision == "accepted" and not self.is_terminal


@dataclass
class RobotView:
    robot_id: str
    provisioned: bool = False
    provisioning_token: str | None = None
    boot_sequence: int = 0
    boot_id: str | None = None
    tasks: dict[str, RobotTaskView] = field(default_factory=dict)
    availability: Availability = Availability.AVAILABLE
    awaiting_human: bool = False
    can_continue: bool | None = True
    needs_manual_recharge: bool | None = False
    safe_return_confirmed: bool | None = None
    fault_code: str | None = None
    current_task_id: str | None = None
    status_sequence: int = 0
    applied_count: int = 0
    executions_started: int = 0
    executions_by_task: dict[str, int] = field(default_factory=dict)
    # Request-level (sequence 0) rejections never belong to a task's history;
    # they are keyed by their own journal record sequence.
    rejections: list[dict[str, Any]] = field(default_factory=list)
    confirmed_rejections: set[int] = field(default_factory=set)
    last_record_id: str | None = None

    def apply_all(self, records: tuple[JournalRecord, ...]) -> None:
        for record in records[self.applied_count :]:
            self.apply(record)

    def apply(self, record: JournalRecord) -> None:
        kind = record.record_kind
        payload = record.payload
        if kind == ROBOT_PROVISIONED:
            self.provisioned = True
            self.provisioning_token = payload["provisioning_token"]
        elif kind == ROBOT_STARTED:
            self.boot_sequence = payload["boot_sequence"]
            self.boot_id = payload["boot_id"]
            self.status_sequence = 0
        elif kind == REQUEST_RECEIVED and payload["disposition"] == "new":
            request = TaskRequest.from_dict(_thaw(payload["request"]))
            self.tasks[request.task_id] = RobotTaskView(
                task_id=request.task_id, request=request, content_digest=payload["content_digest"]
            )
        elif kind == TASK_DECISION:
            task = self.tasks[payload["task_id"]]
            task.decision = payload["decision"]
            task.reason_code = payload["reason_code"]
            if payload["decision"] == "accepted":
                self.current_task_id = task.task_id
                self.availability = Availability.BUSY
        elif kind == TASK_EVENT_PERSISTED:
            event = _thaw(payload["event"])
            if event["event_sequence"] == 0:
                self.rejections.append({"record_sequence": record.sequence, "event": event})
            else:
                task = self.tasks.get(event["task_id"])
                if task is None:
                    raise ValueError(f"robot journal event for unknown task {event['task_id']!r} at sequence {record.sequence}")
                task.events.append(event)
                self._derive_condition(task, event)
        elif kind == EVENT_PUBLISH_CONFIRMED:
            if payload.get("record_sequence") is not None:
                self.confirmed_rejections.add(payload["record_sequence"])
            else:
                task = self.tasks[payload["task_id"]]
                task.confirmed.add((payload["boot_sequence"], payload["event_sequence"]))
        elif kind == EXECUTION_STARTED:
            task = self.tasks[payload["task_id"]]
            task.execution_started = True
            task.step = max(task.step, 1)
            self.executions_started += 1
            self.executions_by_task[task.task_id] = self.executions_by_task.get(task.task_id, 0) + 1
        elif kind == EXECUTION_PROGRESS:
            task = self.tasks[payload["task_id"]]
            task.step = max(task.step, payload["step"])
        elif kind == EXECUTION_COMPLETED:
            task = self.tasks[payload["task_id"]]
            task.execution_completed = True
            task.completed_outcome = payload["outcome"]
        elif kind == SIMULATE_RESET:
            # The explicit operator act is the only record that clears protection.
            self._clear_condition()
            self.current_task_id = None
        if record.sequence > self.applied_count:
            self.applied_count = record.sequence
            self.last_record_id = record.record_id

    # -- condition derivation (pure function of the persisted events) -------

    def _derive_condition(self, task: RobotTaskView, event: Mapping[str, Any]) -> None:
        kind = event["kind"]
        reason = event.get("reason_code")
        if kind == EventKind.ASSISTANCE_REQUIRED.value:
            self._protect(reason)
            return
        if kind not in _TERMINAL_EVENT_KINDS:
            return
        task.terminal_kind = kind
        if self.current_task_id == task.task_id:
            self.current_task_id = None
        if kind == EventKind.SUCCEEDED.value:
            self._clear_condition()
        elif kind in {EventKind.FAILED.value, EventKind.INCONCLUSIVE.value}:
            self._protect(reason)
        # REJECTED never changes the robot's condition.

    def _protect(self, reason: str | None) -> None:
        self.awaiting_human = True
        self.safe_return_confirmed = None
        if reason is not None and reason.startswith("unknown:"):
            self.availability = Availability.FAULTED
            self.fault_code = reason[len("unknown:"):]
            self.can_continue = None
            self.needs_manual_recharge = None
        elif reason in _RECHARGE_REASONS:
            self.availability = Availability.AWAITING_HUMAN
            self.can_continue = False
            self.needs_manual_recharge = True
        elif reason == "cannot_continue":
            if self.availability is not Availability.FAULTED:
                self.availability = Availability.AWAITING_HUMAN
            self.can_continue = False
            # An earlier energy fact (needs_manual_recharge) is kept, never erased.
        else:
            # not_started_after_restart, interrupted_execution_unknown_outcome, ...:
            # the energy and fault facts persisted before the interruption are
            # kept and a faulted robot stays faulted.
            if self.availability is not Availability.FAULTED:
                self.availability = Availability.AWAITING_HUMAN

    def _clear_condition(self) -> None:
        self.availability = Availability.AVAILABLE
        self.awaiting_human = False
        self.can_continue = True
        self.needs_manual_recharge = False
        self.safe_return_confirmed = None
        self.fault_code = None

    def pending_publications(self) -> list[tuple[dict[str, Any], int | None]]:
        """Persisted, not yet hop-1-confirmed publications as ``(event, rejection_record_sequence)``.

        The second element is ``None`` for task-history events and the journal
        record sequence for request-level (sequence 0) rejections, which are
        confirmed by that sequence rather than by ``(boot, event_sequence)``.
        """

        pending: list[tuple[tuple[int, int, int], dict[str, Any], int | None]] = []
        for task in self.tasks.values():
            for event in task.events:
                key = (event["boot_sequence"], event["event_sequence"])
                if key not in task.confirmed:
                    pending.append(((event["boot_sequence"], event["event_sequence"], 0), event, None))
        for item in self.rejections:
            if item["record_sequence"] not in self.confirmed_rejections:
                event = item["event"]
                pending.append(((event["boot_sequence"], 0, item["record_sequence"]), event, item["record_sequence"]))
        pending.sort(key=lambda entry: entry[0])
        return [(event, ref) for _key, event, ref in pending]

    def history_for(self, task_id: str) -> list[dict[str, Any]]:
        """The task's own persisted history; request-level rejections are never part of it."""

        task = self.tasks[task_id]
        return sorted(task.events, key=lambda item: (item["boot_sequence"], item["event_sequence"]))

    def executions_for(self, task_id: str) -> int:
        return self.executions_by_task.get(task_id, 0)

    def summary(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "provisioned": self.provisioned,
            "boot_sequence": self.boot_sequence,
            "availability": self.availability.value,
            "awaiting_human": self.awaiting_human,
            "current_task_id": self.current_task_id,
            "tasks": {
                task_id: {
                    "decision": task.decision,
                    "reason_code": task.reason_code,
                    "execution_started": task.execution_started,
                    "execution_completed": task.execution_completed,
                    "terminal_kind": task.terminal_kind,
                    "events": [(e["boot_sequence"], e["event_sequence"], e["kind"]) for e in task.events],
                    "unconfirmed": [
                        (e["boot_sequence"], e["event_sequence"])
                        for e in task.events
                        if (e["boot_sequence"], e["event_sequence"]) not in task.confirmed
                    ],
                }
                for task_id, task in self.tasks.items()
            },
        }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def derive_robot_view(robot_id: str, records: tuple[JournalRecord, ...]) -> RobotView:
    view = RobotView(robot_id=robot_id)
    view.apply_all(records)
    return view


class RobotCore:
    """Pure executor decisions for one protocol double."""

    def __init__(self, config: EdgeTaskConfig, robot: RobotConfig, behavior: str) -> None:
        self.config = config
        self.robot = robot
        self.behavior, self.behavior_argument = parse_behavior(behavior)
        self.view = RobotView(robot_id=robot.robot_id)
        self.exit_requested = False
        self.exit_reason: str | None = None

    @property
    def effective_task_types(self) -> tuple[str, ...]:
        """``standby`` advertises and accepts nothing, whatever the config lists."""

        return () if self.behavior == "standby" else tuple(self.robot.task_types)

    # ------------------------------------------------------------------
    # journal plumbing
    # ------------------------------------------------------------------

    def builder(self, decide):
        def build(records: tuple[JournalRecord, ...]) -> list[RecordSpec]:
            applied = self.view.applied_count
            if len(records) < applied:
                raise PreconditionFailed("journal_shrank", "the robot journal has fewer records than already applied")
            if applied and records[applied - 1].record_id != self.view.last_record_id:
                raise PreconditionFailed("journal_diverged", "the robot journal prefix no longer matches the applied records")
            self.view.apply_all(records)
            return list(decide(self.view))

        return build

    def absorb(self, appended: tuple[JournalRecord, ...]) -> None:
        for record in appended:
            if record.sequence > self.view.applied_count:
                self.view.apply(record)

    def _spec(self, kind: str, now: datetime, payload: Mapping[str, Any], origin: str = "DEVICE") -> RecordSpec:
        return RecordSpec(record_kind=kind, origin=origin, recorded_at_utc=utc_text(now), payload=dict(payload))

    def _next_sequence(self, task: RobotTaskView) -> int:
        boot = self.view.boot_sequence
        used = [e["event_sequence"] for e in task.events if e["boot_sequence"] == boot and e["event_sequence"] >= 1]
        return (max(used) + 1) if used else 1

    def _event(self, task: RobotTaskView, kind: EventKind, now: datetime, *, reason_code: str | None = None, detail: str = "", phase: str | None = None, sequence: int | None = None) -> dict[str, Any]:
        event = TaskEvent(
            site_id=self.config.site_id,
            deployment_id=self.config.deployment_id,
            simulation_env_id=self.config.simulation_env_id,
            task_id=task.task_id,
            robot_id=self.robot.robot_id,
            boot_id=self.view.boot_id or f"boot-{self.robot.robot_id}-unprovisioned-{max(self.view.boot_sequence, 1)}",
            boot_sequence=self.view.boot_sequence,
            event_sequence=self._next_sequence(task) if sequence is None else sequence,
            kind=kind,
            reason_code=reason_code,
            detail=bounded_detail(detail),
            reported_at_utc=utc_text(now),
            phase=phase,
        )
        return event.to_dict()

    # ------------------------------------------------------------------
    # start / restart
    # ------------------------------------------------------------------

    @staticmethod
    def assert_startable(view: RobotView, *, initialize: bool) -> None:
        """Identity continuity check, run before the journal is touched and again in-lock.

        An empty journal is provisioning only when the operator says so; an
        existing identity whose journal lacks its provisioning record (or is
        gone) is state loss and must not silently become an empty dedup store.
        """

        if view.boot_sequence == 0 and not view.provisioned:
            if not initialize:
                raise EdgeTaskError(
                    ErrorCode.ROBOT_STATE_LOST,
                    "no persisted state for this robot identity: pass --initialize only for a genuine first boot; otherwise the journal was lost and an operator must recover it",
                )
            return
        if initialize:
            raise EdgeTaskError(ErrorCode.ROBOT_ALREADY_PROVISIONED, "this robot identity is already provisioned; start it without --initialize")
        if not view.provisioned:
            raise EdgeTaskError(ErrorCode.ROBOT_STATE_LOST, "the robot journal lacks its provisioning record; state continuity cannot be established")

    def on_start(self, view: RobotView, now: datetime, *, initialize: bool = False) -> list[RecordSpec]:
        self.assert_startable(view, initialize=initialize)
        specs: list[RecordSpec] = []
        token = view.provisioning_token
        if initialize:
            token = stable_digest({"robot_id": self.robot.robot_id, "provisioned_at_utc": utc_text(now)})[:12]
            specs.append(
                self._spec(
                    ROBOT_PROVISIONED,
                    now,
                    {"robot_id": self.robot.robot_id, "provisioning_token": token, "simulation": True, "note": "explicit first boot of this robot identity (SIMULATION)"},
                    origin="OPERATOR",
                )
            )
        boot_sequence = view.boot_sequence + 1
        boot_id = f"boot-{self.robot.robot_id}-{token}-{boot_sequence}"
        specs.append(
            self._spec(
                ROBOT_STARTED,
                now,
                {
                    "robot_id": self.robot.robot_id,
                    "boot_sequence": boot_sequence,
                    "boot_id": boot_id,
                    "behavior": self.behavior if self.behavior_argument is None else f"{self.behavior}:{self.behavior_argument}",
                    "simulation": True,
                },
            )
        )
        # Restart branches are decided against the persisted evidence only.  A
        # scratch copy of the view receives each spec as it is produced so later
        # decisions in the same batch see the earlier ones (e.g. an INCONCLUSIVE
        # protects the robot before an undecided request is decided).
        scratch = RobotView(robot_id=self.robot.robot_id)
        scratch.__dict__.update({k: v for k, v in view.__dict__.items()})
        scratch.tasks = {task_id: RobotTaskView(**{**task.__dict__, "events": list(task.events), "confirmed": set(task.confirmed)}) for task_id, task in view.tasks.items()}
        scratch.executions_by_task = dict(view.executions_by_task)
        scratch.rejections = [dict(item) for item in view.rejections]
        scratch.confirmed_rejections = set(view.confirmed_rejections)
        saved_view = self.view
        self.view = scratch
        try:
            self._apply_scratch(scratch, specs)
            ordered = sorted(scratch.tasks.values(), key=lambda t: t.task_id)
            # Pass 1: accepted tasks interrupted by the restart (protection first).
            for task in ordered:
                if task.decision != "accepted" or task.is_terminal:
                    continue
                if task.execution_completed:
                    outcome = task.completed_outcome or "FAILED"
                    if outcome == "SUCCEEDED":
                        event = self._event(task, EventKind.SUCCEEDED, now, detail="collection cycle completed before restart; terminal record persisted after restart (SIMULATION)")
                    else:
                        event = self._event(task, EventKind.FAILED, now, reason_code="cannot_continue", detail="execution ended before restart with a failed outcome (SIMULATION)")
                elif not task.execution_started:
                    event = self._event(
                        task,
                        EventKind.FAILED,
                        now,
                        reason_code="not_started_after_restart",
                        detail="accepted before restart; execution never started; will not start without a new request (SIMULATION)",
                    )
                else:
                    event = self._event(
                        task,
                        EventKind.INCONCLUSIVE,
                        now,
                        reason_code="interrupted_execution_unknown_outcome",
                        detail="execution started before restart; outcome unknown; not resumed (SIMULATION)",
                    )
                batch = [self._spec(TASK_EVENT_PERSISTED, now, {"event": event})]
                specs.extend(batch)
                self._apply_scratch(scratch, batch)
                # completed-and-persisted-but-unpublished terminals are republished from pending_publications()
            # Pass 2: a rejected decision whose REJECTED event was never persisted.
            for task in ordered:
                if task.decision == "rejected" and not task.events:
                    event = self._event(task, EventKind.REJECTED, now, reason_code=task.reason_code, detail="rejection decided before restart; event persisted after restart (SIMULATION)", sequence=1)
                    batch = [self._spec(TASK_EVENT_PERSISTED, now, {"event": event})]
                    specs.extend(batch)
                    self._apply_scratch(scratch, batch)
            # Pass 3: a request persisted without any decision is decided now, as if it had just arrived.
            for task in ordered:
                if task.decision is None:
                    batch = self._decide_new_task(scratch, task, now)
                    specs.extend(batch)
                    self._apply_scratch(scratch, batch)
        finally:
            self.view = saved_view
        return specs

    def _apply_scratch(self, scratch: RobotView, specs: list[RecordSpec]) -> None:
        for spec in specs:
            sequence = scratch.applied_count + 1
            scratch.apply(
                JournalRecord(
                    schema_version="scratch",
                    sequence=sequence,
                    record_id="scratch",
                    record_kind=spec.record_kind,
                    origin=spec.origin,
                    recorded_at_utc=spec.recorded_at_utc,
                    payload=dict(spec.payload),
                )
            )

    # ------------------------------------------------------------------
    # requests
    # ------------------------------------------------------------------

    def on_request(self, view: RobotView, topic: str, payload: bytes, now: datetime) -> tuple[list[RecordSpec], list[dict[str, Any]]]:
        """Return (records to persist, history events to re-publish)."""

        def seq0_rejection(task_id: str | None, code: str, detail: str) -> list[RecordSpec]:
            specs = [
                self._spec(
                    REQUEST_REJECTED,
                    now,
                    {"task_id": task_id, "code": code, "detail": bounded_detail(detail), "topic": bounded_detail(topic)},
                    origin="EDGE",
                )
            ]
            if task_id is not None:
                event = TaskEvent(
                    site_id=self.config.site_id,
                    deployment_id=self.config.deployment_id,
                    simulation_env_id=self.config.simulation_env_id,
                    task_id=task_id,
                    robot_id=self.robot.robot_id,
                    boot_id=view.boot_id or f"boot-{self.robot.robot_id}-unprovisioned-{max(view.boot_sequence, 1)}",
                    boot_sequence=max(view.boot_sequence, 1),
                    event_sequence=0,
                    kind=EventKind.REJECTED,
                    reason_code=code,
                    detail=bounded_detail(detail),
                    reported_at_utc=utc_text(now),
                    phase=None,
                ).to_dict()
                specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": event}))
            return specs

        try:
            site_id, topic_robot, family = topic_parts(topic)
            if family != "request":
                raise EdgeTaskError(ErrorCode.INVALID_TOPIC, "not a request topic")
        except EdgeTaskError as exc:
            return seq0_rejection(None, exc.code.value, exc.detail), []
        try:
            request = TaskRequest.from_json(payload)
        except EdgeTaskError as exc:
            task_id = _peek_task_id(payload)
            code = "task_id_content_conflict" if exc.code is ErrorCode.TASK_ID_CONTENT_CONFLICT else "invalid_request"
            return seq0_rejection(task_id, code, exc.detail), []
        if request.site_id != self.config.site_id or site_id != self.config.site_id:
            return seq0_rejection(request.task_id, "site_mismatch", "request site_id is not this site"), []
        if request.deployment_id != self.config.deployment_id:
            return seq0_rejection(request.task_id, "deployment_mismatch", "request deployment_id is not this deployment"), []
        if request.simulation_env_id != self.config.simulation_env_id:
            return seq0_rejection(request.task_id, "simulation_env_mismatch", "request simulation_env_id is not this environment"), []
        if request.target_robot_id != self.robot.robot_id or topic_robot != self.robot.robot_id:
            return seq0_rejection(request.task_id, "target_mismatch", "request is not addressed to this robot"), []

        known = view.tasks.get(request.task_id)
        if known is not None:
            if known.content_digest == request.content_digest():
                specs = [
                    self._spec(
                        REQUEST_RECEIVED,
                        now,
                        {"task_id": request.task_id, "content_digest": known.content_digest, "disposition": "known_duplicate", "request": request.to_dict()},
                        origin="EDGE",
                    )
                ]
                return specs, view.history_for(request.task_id)
            # Unreachable when task_id == f(content); kept as an integrity guard.
            return seq0_rejection(request.task_id, "task_id_content_conflict", "known task_id with different content"), []

        specs = [
            self._spec(
                REQUEST_RECEIVED,
                now,
                {"task_id": request.task_id, "content_digest": request.content_digest(), "disposition": "new", "request": request.to_dict()},
                origin="EDGE",
            )
        ]
        task = RobotTaskView(task_id=request.task_id, request=request, content_digest=request.content_digest())
        specs.extend(self._decide_new_task(view, task, now))
        return specs, []

    def _decide_new_task(self, view: RobotView, task: RobotTaskView, now: datetime) -> list[RecordSpec]:
        """The task-level decision for a request that has no decision yet (arrival or restart)."""

        request = task.request

        def task_level_reject(reason_code: str, detail: str) -> list[RecordSpec]:
            event = self._event(task, EventKind.REJECTED, now, reason_code=reason_code, detail=detail, sequence=1)
            return [
                self._spec(TASK_DECISION, now, {"task_id": request.task_id, "decision": "rejected", "reason_code": reason_code}),
                self._spec(TASK_EVENT_PERSISTED, now, {"event": event}),
            ]

        if view.awaiting_human or view.availability in {Availability.FAULTED, Availability.ESTOPPED, Availability.AWAITING_HUMAN}:
            if view.availability is Availability.ESTOPPED:
                return task_level_reject("estop_latched", "robot reports a latched emergency stop (SIMULATION)")
            if view.can_continue is False and view.needs_manual_recharge:
                return task_level_reject("energy_insufficient", "robot reports it needs manual recharge (SIMULATION)")
            return task_level_reject("robot_faulted", "robot is awaiting human attention (SIMULATION)")
        if now >= request.expires_at:
            return task_level_reject("task_expired", "request expired before it was received")
        if view.current_task_id is not None:
            return task_level_reject("robot_busy", f"robot already holds task {view.current_task_id}")
        if request.task_type not in self.effective_task_types:
            return task_level_reject("unsupported_task_type", f"{self.robot.role} ({self.behavior}) does not support {request.task_type}")
        event = self._event(task, EventKind.ACCEPTED, now, detail="accepted (SIMULATION)", sequence=1)
        return [
            self._spec(TASK_DECISION, now, {"task_id": request.task_id, "decision": "accepted", "reason_code": None}),
            self._spec(TASK_EVENT_PERSISTED, now, {"event": event}),
        ]

    # ------------------------------------------------------------------
    # simulated execution steps (one step per tick)
    # ------------------------------------------------------------------

    def tick(self, view: RobotView, now: datetime) -> list[RecordSpec]:
        if self.exit_requested:
            return []
        task_id = view.current_task_id
        if task_id is None:
            return []
        task = view.tasks[task_id]
        if task.is_terminal or task.blocked:
            return []
        behavior = self.behavior
        if behavior == "standby":
            return []
        if behavior == "crash_after_accept":
            # C2: the acceptance decision and ACCEPTED event are persisted, the
            # process dies before publishing them and before execution starts.
            self.exit_requested, self.exit_reason = True, "crash_after_accept"
            return []
        unconfirmed = [e for e in task.events if (e["boot_sequence"], e["event_sequence"]) not in task.confirmed]
        if unconfirmed:
            # Persist-before-publish: do not advance while the last event is unconfirmed.
            return []
        step = task.step
        if step == 0:
            specs = [self._spec(EXECUTION_STARTED, now, {"task_id": task_id, "phase": "traveling", "simulation": True})]
            if behavior == "crash_after_execution_started":
                self.exit_requested, self.exit_reason = True, "crash_after_execution_started"
                return specs
            if behavior == "silent_after_accept":
                return specs
            specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.PROGRESS, now, phase="traveling")}))
            return specs
        if behavior == "silent_after_accept":
            return []
        if step == 1:
            if behavior == "accept_and_succeed" or behavior == "crash_after_result_persisted":
                return [
                    self._spec(EXECUTION_PROGRESS, now, {"task_id": task_id, "phase": "collecting", "step": 2}),
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.PROGRESS, now, phase="collecting")}),
                ]
            if behavior == "fail_cannot_continue":
                return [
                    self._spec(EXECUTION_COMPLETED, now, {"task_id": task_id, "outcome": "FAILED"}),
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.FAILED, now, reason_code="cannot_continue", detail="robot cannot continue the task (SIMULATION)")}),
                ]
            if behavior == "help_needs_manual_recharge":
                return [
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.ASSISTANCE_REQUIRED, now, reason_code="energy_insufficient", detail="robot needs manual recharge to continue (SIMULATION)")}),
                ]
            if behavior == "help_unknown_fault":
                code = self.behavior_argument or "E00"
                return [
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.ASSISTANCE_REQUIRED, now, reason_code=f"unknown:{code}", detail=f"robot reported fault code {code} (SIMULATION)")}),
                ]
        if step == 2 and behavior in {"accept_and_succeed", "crash_after_result_persisted"}:
            specs = [
                self._spec(EXECUTION_COMPLETED, now, {"task_id": task_id, "outcome": "SUCCEEDED"}),
                self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.SUCCEEDED, now, detail="collection cycle completed (SIMULATION; no ball count is reported)")}),
            ]
            if behavior == "crash_after_result_persisted":
                self.exit_requested, self.exit_reason = True, "crash_after_result_persisted"
            return specs
        return []

    def simulate_reset(self, view: RobotView, now: datetime) -> list[RecordSpec]:
        """Explicit SIMULATION-only local reset by an operator at the robot."""

        # The abandoned task's failure is persisted first; the operator record
        # that clears protection is the last line, so a torn batch can only
        # leave the robot protected.
        specs: list[RecordSpec] = []
        task_id = view.current_task_id
        if task_id is not None and not view.tasks[task_id].is_terminal:
            task = view.tasks[task_id]
            specs.append(self._spec(EXECUTION_COMPLETED, now, {"task_id": task_id, "outcome": "FAILED"}))
            specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.FAILED, now, reason_code="cannot_continue", detail="task abandoned by local operator reset (SIMULATION)")}))
        specs.append(self._spec(SIMULATE_RESET, now, {"simulation": True, "note": "operator reset at the robot (SIMULATION)"}, origin="OPERATOR"))
        return specs

    # ------------------------------------------------------------------
    # heartbeat
    # ------------------------------------------------------------------

    def status_message(self, view: RobotView, now: datetime) -> RobotStatusMessage:
        view.status_sequence += 1
        current = None
        if view.current_task_id is not None:
            task = view.tasks[view.current_task_id]
            last_seq = max((e["event_sequence"] for e in task.events if e["boot_sequence"] == view.boot_sequence), default=0)
            phase = "blocked" if task.blocked else ("executing" if task.execution_started else "accepted")
            current = {"task_id": task.task_id, "phase": phase, "last_event_sequence": last_seq}
        return RobotStatusMessage(
            site_id=self.config.site_id,
            deployment_id=self.config.deployment_id,
            simulation_env_id=self.config.simulation_env_id,
            robot_id=self.robot.robot_id,
            boot_id=view.boot_id or f"boot-{self.robot.robot_id}-unprovisioned-{max(view.boot_sequence, 1)}",
            boot_sequence=max(view.boot_sequence, 1),
            status_sequence=view.status_sequence,
            reported_at_utc=utc_text(now),
            availability=view.availability,
            current_task=current,
            task_types=self.effective_task_types,
            level_fraction=None,
            can_continue=view.can_continue,
            needs_manual_recharge=view.needs_manual_recharge,
            estop_latched=view.availability is Availability.ESTOPPED,
            awaiting_human=view.awaiting_human,
            safe_return_confirmed=view.safe_return_confirmed,
            fault_code=view.fault_code,
            zone_id=None if view.current_task_id is None else view.tasks[view.current_task_id].request.zone_id,
            x_m=None,
            y_m=None,
            coordinate_frame=None,
        )

    def publish_confirmed_spec(self, event: Mapping[str, Any], now: datetime, *, record_sequence: int | None = None) -> RecordSpec:
        return self._spec(
            EVENT_PUBLISH_CONFIRMED,
            now,
            {
                "task_id": event["task_id"],
                "boot_sequence": event["boot_sequence"],
                "event_sequence": event["event_sequence"],
                "record_sequence": record_sequence,
            },
        )

    def delivery_rejected_spec(self, topic: str, code: str, detail: str, now: datetime) -> RecordSpec:
        return self._spec(REQUEST_REJECTED, now, {"task_id": None, "code": code, "detail": bounded_detail(detail), "topic": bounded_detail(topic)}, origin="EDGE")


def _peek_task_id(payload: bytes) -> str | None:
    try:
        import json

        raw = json.loads(payload.decode("utf-8"))
    except Exception:  # noqa: BLE001 - any failure means no usable task_id
        return None
    task_id = raw.get("task_id") if isinstance(raw, dict) else None
    if type(task_id) is str and task_id.startswith("task_") and len(task_id) == 29:
        return task_id
    return None


__all__ = [
    "BEHAVIORS",
    "EVENT_PUBLISH_CONFIRMED",
    "EXECUTION_COMPLETED",
    "EXECUTION_PROGRESS",
    "EXECUTION_STARTED",
    "REQUEST_RECEIVED",
    "REQUEST_REJECTED",
    "ROBOT_PROVISIONED",
    "ROBOT_RECORD_KINDS",
    "ROBOT_STARTED",
    "SIMULATE_RESET",
    "TASK_DECISION",
    "TASK_EVENT_PERSISTED",
    "RobotCore",
    "RobotTaskView",
    "RobotView",
    "derive_robot_view",
    "parse_behavior",
]
