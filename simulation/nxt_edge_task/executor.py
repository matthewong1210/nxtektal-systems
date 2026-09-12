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
  event.  Nothing is ever re-executed after a restart;
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
    topic_parts,
    utc_text,
)
from .journal import JournalRecord, PreconditionFailed, RecordSpec

ROBOT_STARTED = "robot_started"
REQUEST_RECEIVED = "request_received"
REQUEST_REJECTED = "request_rejected"
TASK_DECISION = "task_decision"
TASK_EVENT_PERSISTED = "task_event_persisted"
EVENT_PUBLISH_CONFIRMED = "event_publish_confirmed"
EXECUTION_STARTED = "execution_started"
EXECUTION_PROGRESS = "execution_progress"
EXECUTION_COMPLETED = "execution_completed"
CONDITION_CHANGED = "condition_changed"
SIMULATE_RESET = "simulate_reset"

ROBOT_RECORD_KINDS = frozenset(
    {
        ROBOT_STARTED,
        REQUEST_RECEIVED,
        REQUEST_REJECTED,
        TASK_DECISION,
        TASK_EVENT_PERSISTED,
        EVENT_PUBLISH_CONFIRMED,
        EXECUTION_STARTED,
        EXECUTION_PROGRESS,
        EXECUTION_COMPLETED,
        CONDITION_CHANGED,
        SIMULATE_RESET,
    }
)

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
        if kind == ROBOT_STARTED:
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
                if event["kind"] in {k.value for k in (EventKind.SUCCEEDED, EventKind.FAILED, EventKind.INCONCLUSIVE, EventKind.REJECTED)}:
                    task.terminal_kind = event["kind"]
                    if self.current_task_id == task.task_id:
                        self.current_task_id = None
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
        elif kind == CONDITION_CHANGED:
            self.availability = Availability(payload["availability"])
            self.awaiting_human = payload["awaiting_human"]
            self.can_continue = payload["can_continue"]
            self.needs_manual_recharge = payload["needs_manual_recharge"]
            self.safe_return_confirmed = payload["safe_return_confirmed"]
            self.fault_code = payload["fault_code"]
            if payload.get("clear_current_task"):
                self.current_task_id = None
        elif kind == SIMULATE_RESET:
            pass
        if record.sequence > self.applied_count:
            self.applied_count = record.sequence
            self.last_record_id = record.record_id

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
            boot_id=self.view.boot_id or f"boot-{self.robot.robot_id}-{self.view.boot_sequence}",
            boot_sequence=self.view.boot_sequence,
            event_sequence=self._next_sequence(task) if sequence is None else sequence,
            kind=kind,
            reason_code=reason_code,
            detail=bounded_detail(detail),
            reported_at_utc=utc_text(now),
            phase=phase,
        )
        return event.to_dict()

    def _condition(self, now: datetime, *, availability: Availability, awaiting_human: bool, can_continue: bool | None, needs_manual_recharge: bool | None, safe_return_confirmed: bool | None, fault_code: str | None, reason: str, clear_current_task: bool = False) -> RecordSpec:
        return self._spec(
            CONDITION_CHANGED,
            now,
            {
                "availability": availability.value,
                "awaiting_human": awaiting_human,
                "can_continue": can_continue,
                "needs_manual_recharge": needs_manual_recharge,
                "safe_return_confirmed": safe_return_confirmed,
                "fault_code": fault_code,
                "reason": reason,
                "clear_current_task": clear_current_task,
            },
        )

    # ------------------------------------------------------------------
    # start / restart
    # ------------------------------------------------------------------

    def on_start(self, view: RobotView, now: datetime) -> list[RecordSpec]:
        boot_sequence = view.boot_sequence + 1
        boot_id = f"boot-{self.robot.robot_id}-{boot_sequence}"
        specs = [
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
        ]
        # Restart branches are decided against the persisted evidence only.
        view_after = RobotView(robot_id=self.robot.robot_id)
        view_after.__dict__.update({k: v for k, v in view.__dict__.items()})
        view_after.boot_sequence = boot_sequence
        view_after.boot_id = boot_id
        saved_view = self.view
        self.view = view_after
        try:
            for task in sorted(view.tasks.values(), key=lambda t: t.task_id):
                if task.decision != "accepted" or task.is_terminal:
                    continue
                if not task.execution_started:
                    event = self._event(
                        task,
                        EventKind.FAILED,
                        now,
                        reason_code="not_started_after_restart",
                        detail="accepted before restart; execution never started; will not start without a new request (SIMULATION)",
                    )
                    specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": event}))
                    specs.append(
                        self._condition(
                            now,
                            availability=Availability.AWAITING_HUMAN,
                            awaiting_human=True,
                            can_continue=view.can_continue,
                            needs_manual_recharge=view.needs_manual_recharge,
                            safe_return_confirmed=None,
                            fault_code=view.fault_code,
                            reason="restart_before_execution_start",
                            clear_current_task=True,
                        )
                    )
                elif not task.execution_completed:
                    event = self._event(
                        task,
                        EventKind.INCONCLUSIVE,
                        now,
                        reason_code="interrupted_execution_unknown_outcome",
                        detail="execution started before restart; outcome unknown; not resumed (SIMULATION)",
                    )
                    specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": event}))
                    specs.append(
                        self._condition(
                            now,
                            availability=Availability.AWAITING_HUMAN,
                            awaiting_human=True,
                            can_continue=view.can_continue,
                            needs_manual_recharge=view.needs_manual_recharge,
                            safe_return_confirmed=None,
                            fault_code=view.fault_code,
                            reason="restart_during_execution",
                            clear_current_task=True,
                        )
                    )
                # completed-but-unpublished terminals are republished from pending_publications()
        finally:
            self.view = saved_view
        return specs

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
                    boot_id=view.boot_id or "boot-unknown",
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

        def task_level_reject(reason_code: str, detail: str) -> tuple[list[RecordSpec], list[dict[str, Any]]]:
            specs.append(self._spec(TASK_DECISION, now, {"task_id": request.task_id, "decision": "rejected", "reason_code": reason_code}))
            event = self._event(task, EventKind.REJECTED, now, reason_code=reason_code, detail=detail, sequence=1)
            specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": event}))
            return specs, []

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
        specs.append(self._spec(TASK_DECISION, now, {"task_id": request.task_id, "decision": "accepted", "reason_code": None}))
        event = self._event(task, EventKind.ACCEPTED, now, detail="accepted (SIMULATION)", sequence=1)
        specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": event}))
        return specs, []

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
                    self._condition(now, availability=Availability.AWAITING_HUMAN, awaiting_human=True, can_continue=False, needs_manual_recharge=None, safe_return_confirmed=None, fault_code=None, reason="fail_cannot_continue", clear_current_task=True),
                ]
            if behavior == "help_needs_manual_recharge":
                return [
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.ASSISTANCE_REQUIRED, now, reason_code="energy_insufficient", detail="robot needs manual recharge to continue (SIMULATION)")}),
                    self._condition(now, availability=Availability.AWAITING_HUMAN, awaiting_human=True, can_continue=False, needs_manual_recharge=True, safe_return_confirmed=None, fault_code=None, reason="help_needs_manual_recharge"),
                ]
            if behavior == "help_unknown_fault":
                code = self.behavior_argument or "E00"
                return [
                    self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.ASSISTANCE_REQUIRED, now, reason_code=f"unknown:{code}", detail=f"robot reported fault code {code} (SIMULATION)")}),
                    self._condition(now, availability=Availability.FAULTED, awaiting_human=True, can_continue=None, needs_manual_recharge=None, safe_return_confirmed=None, fault_code=code, reason="help_unknown_fault"),
                ]
        if step == 2 and behavior in {"accept_and_succeed", "crash_after_result_persisted"}:
            specs = [
                self._spec(EXECUTION_COMPLETED, now, {"task_id": task_id, "outcome": "SUCCEEDED"}),
                self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.SUCCEEDED, now, detail="collection cycle completed (SIMULATION; no ball count is reported)")}),
                self._condition(now, availability=Availability.AVAILABLE, awaiting_human=False, can_continue=True, needs_manual_recharge=False, safe_return_confirmed=None, fault_code=None, reason="task_completed", clear_current_task=True),
            ]
            if behavior == "crash_after_result_persisted":
                self.exit_requested, self.exit_reason = True, "crash_after_result_persisted"
            return specs
        return []

    def simulate_reset(self, view: RobotView, now: datetime) -> list[RecordSpec]:
        """Explicit SIMULATION-only local reset by an operator at the robot."""

        specs = [self._spec(SIMULATE_RESET, now, {"simulation": True, "note": "operator reset at the robot (SIMULATION)"}, origin="OPERATOR")]
        task_id = view.current_task_id
        if task_id is not None and not view.tasks[task_id].is_terminal:
            task = view.tasks[task_id]
            specs.append(self._spec(EXECUTION_COMPLETED, now, {"task_id": task_id, "outcome": "FAILED"}))
            specs.append(self._spec(TASK_EVENT_PERSISTED, now, {"event": self._event(task, EventKind.FAILED, now, reason_code="cannot_continue", detail="task abandoned by local operator reset (SIMULATION)")}))
        specs.append(self._condition(now, availability=Availability.AVAILABLE, awaiting_human=False, can_continue=True, needs_manual_recharge=False, safe_return_confirmed=None, fault_code=None, reason="simulate_reset", clear_current_task=True))
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
            boot_id=view.boot_id or f"boot-{self.robot.robot_id}-{view.boot_sequence}",
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
    "CONDITION_CHANGED",
    "EVENT_PUBLISH_CONFIRMED",
    "EXECUTION_COMPLETED",
    "EXECUTION_PROGRESS",
    "EXECUTION_STARTED",
    "REQUEST_RECEIVED",
    "REQUEST_REJECTED",
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
