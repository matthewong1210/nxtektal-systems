"""Edge-side task and device lifecycle: pure derivation over the Edge journal.

The Edge learns about a robot only through messages it received on the
transport.  Every decision here is a pure function of the derived view
plus one input (a received status, a received event, a tick of the
injected clock, or a task-creation request) and returns the journal
records that document the decision.  The view is updated **only** by
applying journal records, so replaying the journal after a restart
reconstructs exactly the state the live process had.

Task state machine (terminal states absorb):

    CREATED -> ACCEPTED -> RUNNING -> {SUCCEEDED | FAILED | INCONCLUSIVE}
    CREATED -> REJECTED
    any non-terminal -> BLOCKED_AWAITING_HUMAN (ASSISTANCE_REQUIRED)
    BLOCKED_AWAITING_HUMAN -> RUNNING (PROGRESS)

Events are ordered per task by ``(boot_sequence, event_sequence)``; an
event below the applied maximum is late evidence and never regresses the
state.  A second, different terminal from the robot is a *terminal
conflict*: both terminals are preserved as evidence, the effective result
becomes ``CONFLICT``, and the device's authorization gate closes -- no new
task and no republication until a human handles it (PR B) -- without
rewriting any release or dispatch fact recorded before the conflict.

Edge timeouts change flags and visibility only; terminal states come from
robot events alone.  Nothing here publishes, sleeps, or reads a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping

from .contracts import (
    SUPPORTED_TASK_TYPES,
    TERMINAL_KINDS,
    AdmissionFacts,
    EdgeTaskConfig,
    EdgeTaskError,
    ErrorCode,
    EventKind,
    RobotStatusMessage,
    TaskEvent,
    TaskRequest,
    bounded_detail,
    parse_utc,
    reason_class,
    topic_parts,
    utc_text,
)
from .journal import JournalRecord, PreconditionFailed, RecordSpec

# --------------------------------------------------------------------------
# Record kinds (Edge journal, PR A)
# --------------------------------------------------------------------------

EDGE_STARTED = "edge_started"
TRANSPORT_SESSION = "transport_session"
TASK_CREATED = "task_created"
TASK_CREATE_REJECTED = "task_create_rejected"
TASK_PUBLISH_ATTEMPTED = "task_publish_attempted"
TASK_PUBLISH_CONFIRMED = "task_publish_confirmed"
DEVICE_STATUS_CHANGED = "device_status_changed"
DEVICE_HEARTBEAT = "device_heartbeat"
DEVICE_STATUS_REJECTED = "device_status_rejected"
DEVICE_LIVENESS_CHANGED = "device_liveness_changed"
SESSION_REGRESSION = "session_regression"
TASK_EVENT_RECEIVED = "task_event_received"
TASK_EVENT_REJECTED = "task_event_rejected"
ROBOT_REQUEST_REJECTED = "robot_request_rejected"
TASK_RECONCILIATION_FLAGGED = "task_reconciliation_flagged"
CONFLICTING_TERMINAL = "conflicting_terminal"

EDGE_RECORD_KINDS = frozenset(
    {
        EDGE_STARTED,
        TRANSPORT_SESSION,
        TASK_CREATED,
        TASK_CREATE_REJECTED,
        TASK_PUBLISH_ATTEMPTED,
        TASK_PUBLISH_CONFIRMED,
        DEVICE_STATUS_CHANGED,
        DEVICE_HEARTBEAT,
        DEVICE_STATUS_REJECTED,
        DEVICE_LIVENESS_CHANGED,
        SESSION_REGRESSION,
        TASK_EVENT_RECEIVED,
        TASK_EVENT_REJECTED,
        ROBOT_REQUEST_REJECTED,
        TASK_RECONCILIATION_FLAGGED,
        CONFLICTING_TERMINAL,
    }
)


class TaskState(StrEnum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    BLOCKED_AWAITING_HUMAN = "BLOCKED_AWAITING_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    REJECTED = "REJECTED"


TERMINAL_STATES = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.INCONCLUSIVE, TaskState.REJECTED}
)


class Disposition(StrEnum):
    APPLIED = "applied"
    LATE_EVIDENCE = "late_evidence"
    DUPLICATE = "duplicate"
    CONFLICTING_REPLAY = "conflicting_replay"
    EVIDENCE = "evidence"
    UNEXPECTED_ACCEPTANCE = "unexpected_acceptance"
    UNEXPECTED_REJECTION = "unexpected_rejection"


class Connectivity(StrEnum):
    UNKNOWN = "UNKNOWN"
    ONLINE = "ONLINE"
    STALE = "STALE"
    OFFLINE = "OFFLINE"


# Reconciliation reasons that clear once robot evidence for the task arrives.
CLEARABLE_REASONS = frozenset(
    {
        "new_boot_session",
        "heartbeat_mismatch",
        "progress_window_elapsed",
        "acceptance_window_elapsed",
        "expired_unconfirmed",
        "edge_restart",
        "transport_session_lost",
    }
)
# Sticky reasons are never cleared by later robot evidence.  Only a terminal
# conflict (effective_result CONFLICT) and a session regression close the
# authorization gate; the others stay visible for PR B's human handling.
STICKY_REASONS = frozenset(
    {
        "conflicting_terminal",
        "conflicting_replay",
        "post_terminal_activity",
        "unexpected_acceptance",
        "unexpected_rejection",
        "session_regression",
    }
)

EFFECTIVE_CONFLICT = "CONFLICT"


# --------------------------------------------------------------------------
# Derived views
# --------------------------------------------------------------------------


@dataclass
class TaskView:
    task_id: str
    request: TaskRequest
    created_at: datetime
    created_record_id: str
    state: TaskState = TaskState.CREATED
    applied_max: tuple[int, int] | None = None
    applied: list[dict[str, Any]] = field(default_factory=list)
    seen_keys: dict[tuple[int, int], str] = field(default_factory=dict)
    events_by_boot: dict[int, set[int]] = field(default_factory=dict)
    acceptance_observed: bool = False
    terminals: list[dict[str, Any]] = field(default_factory=list)
    first_terminal: dict[str, Any] | None = None
    effective_result: str | None = None
    result_verification: str | None = None
    reconciliation_reasons: list[str] = field(default_factory=list)
    publish_attempts: int = 0
    last_publish_attempted_at: datetime | None = None
    post_expiry_attempts: int = 0
    publish_confirmations: int = 0
    last_publish_confirmed_at: datetime | None = None
    post_expiry_publishes: int = 0
    last_event_received_at: datetime | None = None
    flagged_since_last_event: set[str] = field(default_factory=set)
    expired_unconfirmed: bool = False
    exceptions: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def blocked(self) -> bool:
        return self.state is TaskState.BLOCKED_AWAITING_HUMAN

    @property
    def reconciliation_required(self) -> bool:
        return bool(self.reconciliation_reasons)

    @property
    def authorization_conflict(self) -> bool:
        return self.effective_result == EFFECTIVE_CONFLICT

    @property
    def missing_sequences(self) -> list[int]:
        if self.applied_max is None:
            return []
        boot, top = self.applied_max
        seen = self.events_by_boot.get(boot, set())
        return [seq for seq in range(1, top + 1) if seq not in seen]

    @property
    def evidence_incomplete(self) -> bool:
        return bool(self.missing_sequences) or (self.is_terminal and not self.acceptance_observed)

    @property
    def expires_at(self) -> datetime:
        return self.request.expires_at

    def summary(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "target_robot_id": self.request.target_robot_id,
            "task_type": self.request.task_type,
            "zone_id": self.request.zone_id,
            "state": self.state.value,
            "effective_result": self.effective_result,
            "result_verification": self.result_verification,
            "first_terminal": self.first_terminal,
            "terminals": list(self.terminals),
            "applied_max": None if self.applied_max is None else list(self.applied_max),
            "missing_sequences": self.missing_sequences,
            "acceptance_observed": self.acceptance_observed,
            "evidence_incomplete": self.evidence_incomplete,
            "reconciliation_required": self.reconciliation_required,
            "reconciliation_reasons": list(self.reconciliation_reasons),
            "publish_attempts": self.publish_attempts,
            "publish_confirmations": self.publish_confirmations,
            "last_publish_confirmed_at_utc": None
            if self.last_publish_confirmed_at is None
            else utc_text(self.last_publish_confirmed_at),
            "expired_unconfirmed": self.expired_unconfirmed,
            "expires_at_utc": self.request.expires_at_utc,
            "issued_by": self.request.issued_by,
            "exceptions": list(self.exceptions),
        }


@dataclass
class DeviceView:
    robot_id: str
    connectivity: Connectivity = Connectivity.UNKNOWN
    connectivity_since: datetime | None = None
    boot_sequence: int | None = None
    boot_id: str | None = None
    last_status_sequence: int | None = None
    last_valid_status: dict[str, Any] | None = None
    last_valid_received_at: datetime | None = None
    session_regression: bool = False
    regression_detail: dict[str, Any] | None = None
    last_regression_rejection_at: datetime | None = None

    @property
    def last_reported_availability(self) -> str | None:
        return None if self.last_valid_status is None else self.last_valid_status["availability"]

    @property
    def current_task(self) -> dict[str, Any] | None:
        return None if self.last_valid_status is None else self.last_valid_status["current_task"]

    def summary(self) -> dict[str, Any]:
        return {
            "robot_id": self.robot_id,
            "connectivity": self.connectivity.value,
            "connectivity_since_utc": None if self.connectivity_since is None else utc_text(self.connectivity_since),
            "boot_sequence": self.boot_sequence,
            "boot_id": self.boot_id,
            "last_status_sequence": self.last_status_sequence,
            "last_valid_status_received_at_utc": None
            if self.last_valid_received_at is None
            else utc_text(self.last_valid_received_at),
            "last_reported_availability": self.last_reported_availability,
            "current_task": self.current_task,
            "session_regression": self.session_regression,
        }


@dataclass
class EdgeView:
    config: EdgeTaskConfig
    tasks: dict[str, TaskView] = field(default_factory=dict)
    devices: dict[str, DeviceView] = field(default_factory=dict)
    edge_started_at: datetime | None = None
    applied_count: int = 0
    last_record_id: str | None = None

    def __post_init__(self) -> None:
        for robot_id in self.config.robot_ids:
            self.devices.setdefault(robot_id, DeviceView(robot_id=robot_id))

    # ---- queries --------------------------------------------------------

    def active_task_for(self, robot_id: str) -> TaskView | None:
        for task in self.tasks.values():
            if task.request.target_robot_id == robot_id and not task.is_terminal:
                return task
        return None

    def authorization_block_reason(self, robot_id: str) -> str | None:
        device = self.devices.get(robot_id)
        if device is not None and device.session_regression:
            return "session_regression"
        for task in self.tasks.values():
            if task.request.target_robot_id == robot_id and task.authorization_conflict:
                return f"terminal_conflict:{task.task_id}"
        return None

    # ---- applying journal records --------------------------------------

    def apply_all(self, records: tuple[JournalRecord, ...]) -> None:
        for record in records[self.applied_count :]:
            self.apply(record)

    def apply(self, record: JournalRecord) -> None:
        kind = record.record_kind
        payload = record.payload
        when = parse_utc(record.recorded_at_utc, "recorded_at_utc")
        if kind == EDGE_STARTED:
            self.edge_started_at = when
            for device in self.devices.values():
                device.connectivity = Connectivity.UNKNOWN
                device.connectivity_since = when
                device.last_status_sequence = None
            for task in self.tasks.values():
                if not task.is_terminal and "edge_restart" not in task.reconciliation_reasons:
                    task.reconciliation_reasons.append("edge_restart")
        elif kind == TRANSPORT_SESSION:
            if payload["event"] == "session_lost":
                for device in self.devices.values():
                    device.connectivity = Connectivity.UNKNOWN
                    device.connectivity_since = when
                    device.last_status_sequence = None
                for task in self.tasks.values():
                    if not task.is_terminal and "transport_session_lost" not in task.reconciliation_reasons:
                        task.reconciliation_reasons.append("transport_session_lost")
        elif kind == TASK_CREATED:
            request = TaskRequest.from_dict(_thaw(payload["request"]))
            self.tasks[request.task_id] = TaskView(
                task_id=request.task_id,
                request=request,
                created_at=when,
                created_record_id=record.record_id,
            )
        elif kind == TASK_PUBLISH_ATTEMPTED:
            task = self.tasks[payload["task_id"]]
            task.publish_attempts += 1
            task.last_publish_attempted_at = when
            if payload.get("post_expiry"):
                task.post_expiry_attempts += 1
        elif kind == TASK_PUBLISH_CONFIRMED:
            task = self.tasks[payload["task_id"]]
            task.publish_confirmations += 1
            task.last_publish_confirmed_at = when
            if payload.get("post_expiry"):
                task.post_expiry_publishes += 1
        elif kind == DEVICE_STATUS_CHANGED:
            device = self.devices.setdefault(payload["robot_id"], DeviceView(robot_id=payload["robot_id"]))
            status = dict(_thaw(payload["status"]))
            device.boot_sequence = status["boot_sequence"]
            device.boot_id = status["boot_id"]
            device.last_status_sequence = status["status_sequence"]
            device.last_valid_status = status
            device.last_valid_received_at = when
            if device.connectivity is not Connectivity.ONLINE:
                device.connectivity = Connectivity.ONLINE
                device.connectivity_since = when
        elif kind == DEVICE_HEARTBEAT:
            device = self.devices.setdefault(payload["robot_id"], DeviceView(robot_id=payload["robot_id"]))
            device.last_status_sequence = payload["status_sequence"]
            device.last_valid_received_at = when
        elif kind == DEVICE_LIVENESS_CHANGED:
            device = self.devices.setdefault(payload["robot_id"], DeviceView(robot_id=payload["robot_id"]))
            device.connectivity = Connectivity(payload["to"])
            device.connectivity_since = when
            if payload["to"] == Connectivity.ONLINE.value and payload.get("status") is not None:
                status = dict(_thaw(payload["status"]))
                device.boot_sequence = status["boot_sequence"]
                device.boot_id = status["boot_id"]
                device.last_status_sequence = status["status_sequence"]
                device.last_valid_status = status
                device.last_valid_received_at = when
        elif kind == SESSION_REGRESSION:
            device = self.devices.setdefault(payload["robot_id"], DeviceView(robot_id=payload["robot_id"]))
            device.session_regression = True
            device.regression_detail = dict(_thaw(payload))
            for task in self.tasks.values():
                if task.request.target_robot_id == payload["robot_id"] and not task.is_terminal:
                    if "session_regression" not in task.reconciliation_reasons:
                        task.reconciliation_reasons.append("session_regression")
        elif kind == TASK_EVENT_RECEIVED:
            self._apply_event_record(record, when)
        elif kind == ROBOT_REQUEST_REJECTED:
            task = self.tasks.get(payload["task_id"])
            if task is not None:
                task.exceptions.append(
                    {"kind": ROBOT_REQUEST_REJECTED, "reason_code": payload["reason_code"], "record_id": record.record_id}
                )
        elif kind == TASK_RECONCILIATION_FLAGGED:
            task = self.tasks[payload["task_id"]]
            reason = payload["reason"]
            if reason not in task.reconciliation_reasons:
                task.reconciliation_reasons.append(reason)
            task.flagged_since_last_event.add(reason)
            if reason == "expired_unconfirmed":
                task.expired_unconfirmed = True
        elif kind == CONFLICTING_TERMINAL:
            task = self.tasks[payload["task_id"]]
            task.effective_result = EFFECTIVE_CONFLICT
            task.result_verification = "conflicting"
        elif kind in {TASK_CREATE_REJECTED, DEVICE_STATUS_REJECTED, TASK_EVENT_REJECTED}:
            if kind == DEVICE_STATUS_REJECTED and payload.get("code") == "session_regression":
                device = self.devices.setdefault(payload["robot_id"], DeviceView(robot_id=payload["robot_id"]))
                device.last_regression_rejection_at = when
            task_id = payload.get("task_id")
            task = self.tasks.get(task_id) if task_id else None
            if task is not None:
                task.exceptions.append({"kind": kind, "code": payload.get("code"), "record_id": record.record_id})
        if record.sequence > self.applied_count:
            self.applied_count = record.sequence
            self.last_record_id = record.record_id

    def _apply_event_record(self, record: JournalRecord, when: datetime) -> None:
        payload = record.payload
        task = self.tasks[payload["task_id"]]
        event = TaskEvent.from_dict(dict(_thaw(payload["event"])))
        disposition = Disposition(payload["disposition"])
        key = event.order_key
        task.last_event_received_at = when
        task.flagged_since_last_event.clear()
        # Any robot evidence for the task clears the clearable reasons.
        task.reconciliation_reasons = [r for r in task.reconciliation_reasons if r in STICKY_REASONS]
        if disposition is Disposition.CONFLICTING_REPLAY:
            if "conflicting_replay" not in task.reconciliation_reasons:
                task.reconciliation_reasons.append("conflicting_replay")
            return
        if disposition is Disposition.DUPLICATE:
            return
        task.seen_keys[key] = event.content_digest()
        task.events_by_boot.setdefault(event.boot_sequence, set()).add(event.event_sequence)
        # Only an applied or genuinely late ACCEPTED counts as the acceptance of
        # this execution; an anomalous re-acceptance stays evidence.
        if event.kind is EventKind.ACCEPTED and disposition in {Disposition.APPLIED, Disposition.LATE_EVIDENCE}:
            task.acceptance_observed = True
        conflict = payload.get("conflict") is True
        if disposition in {Disposition.LATE_EVIDENCE, Disposition.EVIDENCE, Disposition.UNEXPECTED_ACCEPTANCE, Disposition.UNEXPECTED_REJECTION}:
            if event.is_terminal:
                task.terminals.append(_terminal_ref(event, record.record_id))
            if disposition in {Disposition.EVIDENCE, Disposition.UNEXPECTED_ACCEPTANCE, Disposition.UNEXPECTED_REJECTION}:
                reason = payload.get("flag_reason")
                if reason and reason not in task.reconciliation_reasons:
                    task.reconciliation_reasons.append(reason)
            if conflict:
                # The gate is derivable from the evidence record itself, so a torn
                # batch (this line persisted, its companions not) still closes it.
                task.effective_result = EFFECTIVE_CONFLICT
                task.result_verification = "conflicting"
                if "conflicting_terminal" not in task.reconciliation_reasons:
                    task.reconciliation_reasons.append("conflicting_terminal")
            return
        # applied
        task.applied_max = key
        task.applied.append({"kind": event.kind.value, "boot_sequence": event.boot_sequence, "event_sequence": event.event_sequence, "record_id": record.record_id})
        task.state = TaskState(payload["state_after"])
        if event.is_terminal:
            ref = _terminal_ref(event, record.record_id)
            task.terminals.append(ref)
            if task.first_terminal is None:
                task.first_terminal = ref
                task.effective_result = event.kind.value
                task.result_verification = "robot_reported"
            if conflict:
                task.effective_result = EFFECTIVE_CONFLICT
                task.result_verification = "conflicting"
                if "conflicting_terminal" not in task.reconciliation_reasons:
                    task.reconciliation_reasons.append("conflicting_terminal")


def _terminal_ref(event: TaskEvent, record_id: str) -> dict[str, Any]:
    return {
        "kind": event.kind.value,
        "boot_sequence": event.boot_sequence,
        "event_sequence": event.event_sequence,
        "reason_code": event.reason_code,
        "record_id": record_id,
    }


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def derive_edge_view(config: EdgeTaskConfig, records: tuple[JournalRecord, ...]) -> EdgeView:
    view = EdgeView(config=config)
    view.apply_all(records)
    return view


# --------------------------------------------------------------------------
# Decision functions (pure): each returns RecordSpecs for the journal
# --------------------------------------------------------------------------


def _spec(kind: str, origin: str, now: datetime, payload: Mapping[str, Any]) -> RecordSpec:
    return RecordSpec(record_kind=kind, origin=origin, recorded_at_utc=utc_text(now), payload=dict(payload))


def edge_started_spec(config: EdgeTaskConfig, facts: AdmissionFacts, now: datetime) -> RecordSpec:
    return _spec(
        EDGE_STARTED,
        "EDGE",
        now,
        {
            "edge_started_at_utc": utc_text(now),
            "config_digest": config.config_digest,
            "manifest_digest": facts.manifest_digest,
            "site_id": config.site_id,
            "deployment_id": config.deployment_id,
            "simulation_env_id": config.simulation_env_id,
        },
    )


def transport_session_spec(event: str, now: datetime, *, session_present: bool | None, detail: str = "") -> RecordSpec:
    return _spec(
        TRANSPORT_SESSION,
        "EDGE",
        now,
        {"event": event, "session_present": session_present, "detail": bounded_detail(detail)},
    )


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    status: str  # created | idempotent | rejected
    task_id: str | None
    code: str | None
    detail: str | None


def decide_task_create(
    view: EdgeView,
    facts: AdmissionFacts,
    request: TaskRequest,
    now: datetime,
) -> tuple[CreateOutcome, list[RecordSpec]]:
    """Admit a SIMULATION test-entry task or record why it was refused."""

    config = view.config

    def reject(code: ErrorCode, detail: str) -> tuple[CreateOutcome, list[RecordSpec]]:
        spec = _spec(
            TASK_CREATE_REJECTED,
            "SIM_ENTRY",
            now,
            {"task_id": request.task_id, "code": code.value, "detail": bounded_detail(detail), "request": request.to_dict()},
        )
        return CreateOutcome("rejected", request.task_id, code.value, bounded_detail(detail)), [spec]

    if request.site_id != config.site_id or request.site_id != facts.site_id:
        return reject(ErrorCode.SITE_MISMATCH, "request site_id does not match the Edge deployment")
    if request.deployment_id != config.deployment_id or request.deployment_id != facts.deployment_id:
        return reject(ErrorCode.DEPLOYMENT_MISMATCH, "request deployment_id does not match the Edge deployment")
    if request.simulation_env_id != config.simulation_env_id:
        return reject(ErrorCode.ENVIRONMENT_MISMATCH, "request simulation_env_id does not match the Edge configuration")
    if request.target_robot_id not in facts.robot_ids or request.target_robot_id not in config.robot_ids:
        return reject(ErrorCode.UNKNOWN_ROBOT, "target_robot_id is not a commissioned, configured robot")
    if request.zone_id not in facts.zone_ids:
        return reject(ErrorCode.UNKNOWN_ZONE, "parameters.zone_id is not a commissioned zone")
    if request.task_type not in SUPPORTED_TASK_TYPES:
        return reject(ErrorCode.INVALID_FIELD, "task_type is outside the V0 vocabulary")
    existing = view.tasks.get(request.task_id)
    if existing is not None:
        # Byte-identical resend of an existing task: idempotent, nothing appended,
        # even after the task expired.
        return CreateOutcome("idempotent", request.task_id, None, None), []
    if now >= request.expires_at:
        return reject(ErrorCode.INVALID_FIELD, "request is already expired at creation")
    block = view.authorization_block_reason(request.target_robot_id)
    if block is not None:
        return reject(ErrorCode.AUTHORIZATION_BLOCKED, f"device authorization gate closed: {block}")
    active = view.active_task_for(request.target_robot_id)
    if active is not None:
        return reject(ErrorCode.ROBOT_HAS_ACTIVE_TASK, f"robot already has non-terminal task {active.task_id}")
    spec = _spec(
        TASK_CREATED,
        "SIM_ENTRY",
        now,
        {
            "task_id": request.task_id,
            "request": request.to_dict(),
            "provenance": {
                "issued_by": request.issued_by,
                "manifest_digest": facts.manifest_digest,
                "config_digest": config.config_digest,
                "environment": "SIMULATION",
            },
        },
    )
    return CreateOutcome("created", request.task_id, None, None), [spec]


def publish_attempted_spec(view: EdgeView, task_id: str, now: datetime, *, trigger: str, attempt: int) -> RecordSpec:
    """Persisted before the transport call: bounds attempts even when hop-1 never confirms."""

    task = view.tasks[task_id]
    return _spec(
        TASK_PUBLISH_ATTEMPTED,
        "EDGE",
        now,
        {"task_id": task_id, "attempt": attempt, "trigger": trigger, "post_expiry": now >= task.expires_at},
    )


def delivery_rejected_spec(topic: str, code: str, detail: str, now: datetime, *, transport: str, qos: int | None = None) -> RecordSpec:
    """A transport-level delivery the gateway refuses before decoding (retained, wrong QoS)."""

    return _spec(
        TASK_EVENT_REJECTED,
        "EDGE",
        now,
        {"task_id": None, "robot_id": None, "code": code, "detail": bounded_detail(detail), "topic": bounded_detail(topic), "qos": qos, "transport": transport},
    )


def publish_confirmed_spec(view: EdgeView, task_id: str, now: datetime, *, trigger: str, attempt: int) -> RecordSpec:
    task = view.tasks[task_id]
    return _spec(
        TASK_PUBLISH_CONFIRMED,
        "EDGE",
        now,
        {
            "task_id": task_id,
            "attempt": attempt,
            "trigger": trigger,
            "post_expiry": now >= task.expires_at,
        },
    )


def decide_status(
    view: EdgeView,
    topic: str,
    payload: bytes,
    now: datetime,
    *,
    transport: str,
) -> list[RecordSpec]:
    """Classify one received robot-status delivery."""

    config = view.config

    def rejected(code: ErrorCode, detail: str, robot_id: str | None) -> list[RecordSpec]:
        return [
            _spec(
                DEVICE_STATUS_REJECTED,
                "EDGE",
                now,
                {"robot_id": robot_id, "code": code.value, "detail": bounded_detail(detail), "topic": bounded_detail(topic), "transport": transport},
            )
        ]

    try:
        site_id, topic_robot, family = topic_parts(topic)
        if family != "status":
            raise EdgeTaskError(ErrorCode.INVALID_TOPIC, "not a status topic")
        message = RobotStatusMessage.from_json(payload)
    except EdgeTaskError as exc:
        return rejected(exc.code, exc.detail, None)
    if site_id != config.site_id or message.site_id != config.site_id:
        return rejected(ErrorCode.SITE_MISMATCH, "status site_id disagrees with the Edge site", message.robot_id)
    if message.deployment_id != config.deployment_id:
        return rejected(ErrorCode.DEPLOYMENT_MISMATCH, "status deployment_id disagrees with the Edge deployment", message.robot_id)
    if message.simulation_env_id != config.simulation_env_id:
        return rejected(ErrorCode.ENVIRONMENT_MISMATCH, "status simulation_env_id disagrees with the Edge configuration", message.robot_id)
    if topic_robot != message.robot_id:
        return rejected(ErrorCode.TARGET_MISMATCH, "topic robot_id disagrees with payload robot_id", message.robot_id)
    if message.robot_id not in config.robot_ids:
        return rejected(ErrorCode.UNKNOWN_ROBOT, "status from a robot that is not configured", message.robot_id)

    device = view.devices[message.robot_id]
    specs: list[RecordSpec] = []
    if device.boot_sequence is not None and message.boot_sequence < device.boot_sequence:
        # A live status from a lower boot than the one already seen: the robot's
        # persisted boot counter went backwards (journal wiped).  Sticky.
        if not device.session_regression:
            specs.append(
                _spec(
                    SESSION_REGRESSION,
                    "EDGE",
                    now,
                    {
                        "robot_id": message.robot_id,
                        "seen_boot_sequence": device.boot_sequence,
                        "received_boot_sequence": message.boot_sequence,
                        "status": message.to_dict(),
                        "transport": transport,
                    },
                )
            )
        last_rejection = device.last_regression_rejection_at
        if last_rejection is None or now - last_rejection >= timedelta(seconds=config.stale_after_s / 2):
            specs.append(
                _spec(
                    DEVICE_STATUS_REJECTED,
                    "EDGE",
                    now,
                    {
                        "robot_id": message.robot_id,
                        "code": "session_regression",
                        "detail": "status boot_sequence regressed below the current session",
                        "topic": topic,
                        "transport": transport,
                    },
                )
            )
        return specs
    if (
        device.boot_sequence == message.boot_sequence
        and device.last_status_sequence is not None
        and message.status_sequence <= device.last_status_sequence
    ):
        return [
            _spec(
                DEVICE_STATUS_REJECTED,
                "EDGE",
                now,
                {"robot_id": message.robot_id, "code": "stale_status", "detail": "status_sequence did not advance", "topic": topic, "transport": transport},
            )
        ]
    new_session = device.boot_sequence is None or message.boot_sequence > device.boot_sequence
    status = message.to_dict()
    changed = device.last_valid_status is None or _content_key(device.last_valid_status) != message.content_key() or new_session
    if device.connectivity is not Connectivity.ONLINE:
        specs.append(
            _spec(
                DEVICE_LIVENESS_CHANGED,
                "EDGE",
                now,
                {
                    "robot_id": message.robot_id,
                    "from": device.connectivity.value,
                    "to": Connectivity.ONLINE.value,
                    "reason": "status_received",
                    "status": status,
                    "transport": transport,
                },
            )
        )
    elif changed:
        specs.append(
            _spec(
                DEVICE_STATUS_CHANGED,
                "ROBOT",
                now,
                {"robot_id": message.robot_id, "status": status, "new_session": new_session, "transport": transport},
            )
        )
    else:
        # Unchanged heartbeat: journal a compact receipt at most once per half
        # stale window so liveness derives from records alone without one
        # record per heartbeat.  Between receipts the message is simply
        # acknowledged; ``last_valid_status_received_at`` may therefore lag the
        # true last heartbeat by up to ``stale_after_s / 2``.
        last = device.last_valid_received_at
        if last is None or now - last >= timedelta(seconds=config.stale_after_s / 2):
            specs.append(
                _spec(
                    DEVICE_HEARTBEAT,
                    "ROBOT",
                    now,
                    {"robot_id": message.robot_id, "boot_sequence": message.boot_sequence, "status_sequence": message.status_sequence, "transport": transport},
                )
            )
    # Reconciliation triggers derived from the heartbeat.
    for task in view.tasks.values():
        if task.request.target_robot_id != message.robot_id or task.is_terminal:
            continue
        if new_session and "new_boot_session" not in task.flagged_since_last_event:
            specs.append(_flag(task, "new_boot_session", now, {"boot_sequence": message.boot_sequence}))
        elif (
            task.state in {TaskState.ACCEPTED, TaskState.RUNNING}
            and message.current_task is None
            and message.availability.value == "available"
            and "heartbeat_mismatch" not in task.flagged_since_last_event
            and task.last_event_received_at is not None
            and now - task.last_event_received_at >= timedelta(seconds=config.stale_after_s)
        ):
            specs.append(_flag(task, "heartbeat_mismatch", now, {"status_sequence": message.status_sequence}))
    return specs


def _content_key(status: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(_thaw(status))
    payload.pop("status_sequence", None)
    payload.pop("reported_at_utc", None)
    return payload


def _flag(task: TaskView, reason: str, now: datetime, extra: Mapping[str, Any]) -> RecordSpec:
    return _spec(
        TASK_RECONCILIATION_FLAGGED,
        "EDGE",
        now,
        {"task_id": task.task_id, "reason": reason, "state": task.state.value, **dict(extra)},
    )


def decide_event(
    view: EdgeView,
    topic: str,
    payload: bytes,
    now: datetime,
    *,
    transport: str,
) -> list[RecordSpec]:
    """Classify one received task event and produce its journal records."""

    config = view.config

    def rejected(code: ErrorCode, detail: str, task_id: str | None, robot_id: str | None) -> list[RecordSpec]:
        return [
            _spec(
                TASK_EVENT_REJECTED,
                "EDGE",
                now,
                {"task_id": task_id, "robot_id": robot_id, "code": code.value, "detail": bounded_detail(detail), "topic": bounded_detail(topic), "transport": transport},
            )
        ]

    try:
        site_id, topic_robot, family = topic_parts(topic)
        if family != "event":
            raise EdgeTaskError(ErrorCode.INVALID_TOPIC, "not an event topic")
        event = TaskEvent.from_json(payload)
    except EdgeTaskError as exc:
        return rejected(exc.code, exc.detail, None, None)
    if site_id != config.site_id or event.site_id != config.site_id:
        return rejected(ErrorCode.SITE_MISMATCH, "event site_id disagrees with the Edge site", event.task_id, event.robot_id)
    if event.deployment_id != config.deployment_id:
        return rejected(ErrorCode.DEPLOYMENT_MISMATCH, "event deployment_id disagrees with the Edge deployment", event.task_id, event.robot_id)
    if event.simulation_env_id != config.simulation_env_id:
        return rejected(ErrorCode.ENVIRONMENT_MISMATCH, "event simulation_env_id disagrees with the Edge configuration", event.task_id, event.robot_id)
    if topic_robot != event.robot_id:
        return rejected(ErrorCode.TARGET_MISMATCH, "topic robot_id disagrees with payload robot_id", event.task_id, event.robot_id)
    if event.robot_id not in config.robot_ids:
        return rejected(ErrorCode.UNKNOWN_ROBOT, "event from a robot that is not configured", event.task_id, event.robot_id)
    task = view.tasks.get(event.task_id)
    if task is None:
        return rejected(ErrorCode.UNKNOWN_TASK, "event references a task the Edge never created", event.task_id, event.robot_id)
    if event.robot_id != task.request.target_robot_id:
        return rejected(ErrorCode.TARGET_MISMATCH, "event robot_id is not the task's target robot", event.task_id, event.robot_id)
    if event.is_request_level_rejection:
        return [
            _spec(
                ROBOT_REQUEST_REJECTED,
                "EDGE",
                now,
                {"task_id": event.task_id, "robot_id": event.robot_id, "reason_code": event.reason_code, "reason_class": _class_text(event.reason_code), "event": event.to_dict(), "transport": transport},
            )
        ]

    base = {
        "task_id": event.task_id,
        "robot_id": event.robot_id,
        "event": event.to_dict(),
        "transport": transport,
        "received_at_utc": utc_text(now),
        "state_before": task.state.value,
        "reason_class": _class_text(event.reason_code),
    }
    key = event.order_key
    digest = event.content_digest()

    def received(disposition: Disposition, state_after: TaskState, **extra: Any) -> RecordSpec:
        missing_after, acceptance_after = _prospective_gap(task, event, disposition)
        return _spec(
            TASK_EVENT_RECEIVED,
            "ROBOT",
            now,
            {
                **base,
                "disposition": disposition.value,
                "state_after": state_after.value,
                "missing_sequences_after": missing_after,
                "acceptance_observed_after": acceptance_after,
                **extra,
            },
        )

    if key in task.seen_keys:
        if task.seen_keys[key] == digest:
            return [received(Disposition.DUPLICATE, task.state)]
        return [
            received(Disposition.CONFLICTING_REPLAY, task.state),
            _flag(task, "conflicting_replay", now, {"boot_sequence": key[0], "event_sequence": key[1]}),
        ]

    above = task.applied_max is None or key > task.applied_max

    device = view.devices.get(event.robot_id)
    if device is not None and device.session_regression:
        # A robot whose persisted boot counter went backwards may re-execute a
        # queued request; its events are evidence only and never move the task.
        return [received(Disposition.EVIDENCE, task.state, flag_reason="session_regression")]

    def conflict_specs(first: Mapping[str, Any], conflicting: Mapping[str, Any]) -> list[RecordSpec]:
        specs: list[RecordSpec] = []
        if not task.authorization_conflict:
            specs.append(
                _spec(
                    CONFLICTING_TERMINAL,
                    "EDGE",
                    now,
                    {
                        "task_id": task.task_id,
                        "robot_id": event.robot_id,
                        "first_terminal": dict(first),
                        "conflicting_terminal": dict(conflicting),
                        "prior_release_preserved": True,
                    },
                )
            )
        specs.append(_flag(task, "conflicting_terminal", now, {"boot_sequence": key[0], "event_sequence": key[1]}))
        return specs

    this_terminal = {"kind": event.kind.value, "boot_sequence": key[0], "event_sequence": key[1], "reason_code": event.reason_code}

    if task.is_terminal:
        assert task.first_terminal is not None
        if event.is_terminal and event.kind.value != task.first_terminal["kind"]:
            disposition = Disposition.EVIDENCE if above else Disposition.LATE_EVIDENCE
            return [received(disposition, task.state, conflict=True), *conflict_specs(task.first_terminal, this_terminal)]
        if event.is_terminal:
            return [received(Disposition.EVIDENCE if above else Disposition.LATE_EVIDENCE, task.state)]
        if not above:
            return [received(Disposition.LATE_EVIDENCE, task.state)]
        return [
            received(Disposition.EVIDENCE, task.state, flag_reason="post_terminal_activity"),
            _flag(task, "post_terminal_activity", now, {"boot_sequence": key[0], "event_sequence": key[1]}),
        ]

    if not above:
        return [received(Disposition.LATE_EVIDENCE, task.state)]

    def apply_terminal(state_after: TaskState) -> list[RecordSpec]:
        # A differing terminal may already sit in the evidence (late or unexpected
        # arrival before this one): the conflict is detected in either order.
        prior = [t for t in task.terminals if t["kind"] != event.kind.value]
        if not prior:
            return [received(Disposition.APPLIED, state_after)]
        return [received(Disposition.APPLIED, state_after, conflict=True), *conflict_specs(this_terminal, prior[0])]

    kind = event.kind
    state = task.state
    if kind is EventKind.ACCEPTED:
        if state is TaskState.CREATED:
            return [received(Disposition.APPLIED, TaskState.ACCEPTED)]
        return [
            received(Disposition.UNEXPECTED_ACCEPTANCE, state, flag_reason="unexpected_acceptance"),
            _flag(task, "unexpected_acceptance", now, {"note": "acceptance after progress", "boot_sequence": key[0], "event_sequence": key[1]}),
        ]
    if kind is EventKind.PROGRESS:
        return [received(Disposition.APPLIED, TaskState.RUNNING)]
    if kind is EventKind.ASSISTANCE_REQUIRED:
        return [received(Disposition.APPLIED, TaskState.BLOCKED_AWAITING_HUMAN)]
    if kind is EventKind.REJECTED:
        if state is TaskState.CREATED:
            return apply_terminal(TaskState.REJECTED)
        return [
            received(Disposition.UNEXPECTED_REJECTION, state, flag_reason="unexpected_rejection"),
            _flag(task, "unexpected_rejection", now, {"note": "rejection after acceptance", "boot_sequence": key[0], "event_sequence": key[1]}),
        ]
    # SUCCEEDED / FAILED / INCONCLUSIVE
    return apply_terminal(TaskState(kind.value))


def _prospective_gap(task: TaskView, event: TaskEvent, disposition: Disposition) -> tuple[list[int], bool]:
    """What ``missing_sequences``/``acceptance_observed`` will read once this record applies."""

    if disposition in {Disposition.DUPLICATE, Disposition.CONFLICTING_REPLAY}:
        return task.missing_sequences, task.acceptance_observed
    seen = {boot: set(seqs) for boot, seqs in task.events_by_boot.items()}
    seen.setdefault(event.boot_sequence, set()).add(event.event_sequence)
    applied_max = event.order_key if disposition is Disposition.APPLIED else task.applied_max
    if applied_max is None:
        missing: list[int] = []
    else:
        boot, top = applied_max
        missing = [seq for seq in range(1, top + 1) if seq not in seen.get(boot, set())]
    acceptance = task.acceptance_observed or (
        event.kind is EventKind.ACCEPTED and disposition in {Disposition.APPLIED, Disposition.LATE_EVIDENCE}
    )
    return missing, acceptance


def _class_text(reason_code: str | None) -> str | None:
    klass = reason_class(reason_code)
    return None if klass is None else klass.value


def decide_tick(view: EdgeView, now: datetime) -> list[RecordSpec]:
    """Liveness transitions and timer-driven reconciliation flags."""

    config = view.config
    specs: list[RecordSpec] = []
    for device in view.devices.values():
        since = device.connectivity_since
        if device.connectivity is Connectivity.UNKNOWN:
            if since is not None and now - since >= timedelta(seconds=config.restart_grace_s):
                specs.append(_liveness(device, Connectivity.OFFLINE, "no_status_since_edge_restart", now))
        elif device.connectivity is Connectivity.ONLINE:
            last = device.last_valid_received_at
            if last is not None and now - last >= timedelta(seconds=config.stale_after_s):
                specs.append(_liveness(device, Connectivity.STALE, "stale_after_s_elapsed", now))
        elif device.connectivity is Connectivity.STALE:
            last = device.last_valid_received_at
            if last is not None and now - last >= timedelta(seconds=config.offline_after_s):
                specs.append(_liveness(device, Connectivity.OFFLINE, "offline_after_s_elapsed", now))
    for task in view.tasks.values():
        if task.is_terminal:
            continue
        device = view.devices[task.request.target_robot_id]
        if task.state is TaskState.CREATED and now >= task.expires_at and not task.expired_unconfirmed:
            specs.append(_flag(task, "expired_unconfirmed", now, {"expires_at_utc": task.request.expires_at_utc}))
        if (
            task.state in {TaskState.ACCEPTED, TaskState.RUNNING}
            and device.connectivity in {Connectivity.ONLINE, Connectivity.STALE}
            and "progress_window_elapsed" not in task.flagged_since_last_event
        ):
            anchor = task.last_event_received_at or task.created_at
            if now - anchor >= timedelta(seconds=task.request.progress_window_s):
                specs.append(_flag(task, "progress_window_elapsed", now, {"progress_window_s": task.request.progress_window_s}))
        if (
            task.state is TaskState.CREATED
            and task.publish_confirmations > 0
            and now < task.expires_at
            and device.connectivity in {Connectivity.ONLINE, Connectivity.STALE}
            and "acceptance_window_elapsed" not in task.flagged_since_last_event
            and task.last_publish_confirmed_at is not None
            and now - task.last_publish_confirmed_at >= timedelta(seconds=task.request.progress_window_s)
        ):
            specs.append(_flag(task, "acceptance_window_elapsed", now, {"progress_window_s": task.request.progress_window_s}))
    return specs


def _liveness(device: DeviceView, to: Connectivity, reason: str, now: datetime) -> RecordSpec:
    return _spec(
        DEVICE_LIVENESS_CHANGED,
        "EDGE",
        now,
        {
            "robot_id": device.robot_id,
            "from": device.connectivity.value,
            "to": to.value,
            "reason": reason,
            "status": None,
            "last_valid_status": device.last_valid_status,
            "last_valid_status_received_at_utc": None
            if device.last_valid_received_at is None
            else utc_text(device.last_valid_received_at),
            "transport": None,
        },
    )


@dataclass(frozen=True, slots=True)
class RepublishCandidate:
    task_id: str
    trigger: str
    attempt: int


def republish_candidates(view: EdgeView, now: datetime) -> list[RepublishCandidate]:
    """Tasks whose byte-identical request may be (re)published now.

    Never for a terminal, conflicted, blocked, session-regressed, offline, or
    exhausted task; at most one publication after expiry; never a new task_id.
    """

    config = view.config
    candidates: list[RepublishCandidate] = []
    for task in view.tasks.values():
        if task.is_terminal or task.blocked or task.authorization_conflict:
            continue
        if view.authorization_block_reason(task.request.target_robot_id) is not None:
            continue
        device = view.devices[task.request.target_robot_id]
        if device.connectivity not in {Connectivity.ONLINE, Connectivity.STALE}:
            continue
        if task.publish_attempts >= config.max_republish_attempts:
            continue
        expired = now >= task.expires_at
        if expired and task.post_expiry_attempts >= 1:
            continue
        last = task.last_publish_attempted_at
        if last is not None and now - last < timedelta(seconds=config.min_republish_interval_s):
            continue
        attempt = task.publish_attempts + 1
        if task.publish_confirmations == 0:
            candidates.append(RepublishCandidate(task.task_id, "initial", attempt))
            continue
        pending = [r for r in task.reconciliation_reasons if r in CLEARABLE_REASONS]
        if not pending:
            continue
        candidates.append(RepublishCandidate(task.task_id, "reconcile:" + ",".join(sorted(pending)), attempt))
    return candidates


class EdgeCore:
    """Owns the derived view and runs every decision inside the journal lock."""

    def __init__(self, config: EdgeTaskConfig, facts: AdmissionFacts, *, transport: str) -> None:
        self.config = config
        self.facts = facts
        self.transport = transport
        self.view = EdgeView(config=config)

    def _sync(self, records: tuple[JournalRecord, ...]) -> None:
        applied = self.view.applied_count
        if len(records) < applied:
            raise PreconditionFailed("journal_shrank", "the journal has fewer records than already applied")
        if applied and records[applied - 1].record_id != self.view.last_record_id:
            raise PreconditionFailed("journal_diverged", "the journal prefix no longer matches the records already applied")
        self.view.apply_all(records)

    def builder(self, decide):
        """Wrap a ``decide(view) -> list[RecordSpec]`` for ``JsonlJournal.append_via``."""

        def build(records: tuple[JournalRecord, ...]) -> list[RecordSpec]:
            self._sync(records)
            return list(decide(self.view))

        return build

    def absorb(self, appended: tuple[JournalRecord, ...]) -> None:
        for record in appended:
            if record.sequence > self.view.applied_count:
                self.view.apply(record)

    def refresh(self, records: tuple[JournalRecord, ...]) -> None:
        self._sync(records)


__all__ = [
    "CLEARABLE_REASONS",
    "CONFLICTING_TERMINAL",
    "DEVICE_HEARTBEAT",
    "DEVICE_LIVENESS_CHANGED",
    "DEVICE_STATUS_CHANGED",
    "DEVICE_STATUS_REJECTED",
    "EDGE_RECORD_KINDS",
    "EDGE_STARTED",
    "EFFECTIVE_CONFLICT",
    "ROBOT_REQUEST_REJECTED",
    "SESSION_REGRESSION",
    "STICKY_REASONS",
    "TASK_CREATED",
    "TASK_CREATE_REJECTED",
    "TASK_EVENT_RECEIVED",
    "TASK_EVENT_REJECTED",
    "TASK_PUBLISH_ATTEMPTED",
    "TASK_PUBLISH_CONFIRMED",
    "TASK_RECONCILIATION_FLAGGED",
    "TERMINAL_STATES",
    "TRANSPORT_SESSION",
    "Connectivity",
    "CreateOutcome",
    "DeviceView",
    "Disposition",
    "EdgeCore",
    "EdgeView",
    "RepublishCandidate",
    "TaskState",
    "TaskView",
    "decide_event",
    "decide_status",
    "decide_task_create",
    "decide_tick",
    "delivery_rejected_spec",
    "derive_edge_view",
    "edge_started_spec",
    "publish_attempted_spec",
    "publish_confirmed_spec",
    "republish_candidates",
    "transport_session_spec",
]
