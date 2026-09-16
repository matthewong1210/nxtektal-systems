"""Human-reviewed grounds work in a closed, synthetic simulation.

The sole authority here is an append-only sequence of immutable workflow
events. Observations remain evidence; a usable finding opens a review candidate,
not a confirmed defect or a robot command. Work completion requires an explicit
independent verification before the case is marked verified. The caller supplies
time, observation plain data, resources, serialization, locking and persistence.
No device, transport, clock, facility inventory or automatic decision is used.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Mapping


SCHEMA = "nxt-grounds-workflow/v0"
EVENT_SCHEMA = "nxt-grounds-workflow-event/v0"
OBSERVATION_SCHEMA = "nxt-course-condition-observation/v0"
CONDITION_KINDS = frozenset({"DIVOT", "BUNKER_SURFACE", "STANDING_WATER"})
TASK_KINDS = frozenset({"INSPECT", "RAKE_BUNKER", "REPAIR_DIVOT"})
_ALLOWED_TASKS = {
    "DIVOT": frozenset({"INSPECT", "REPAIR_DIVOT"}),
    "BUNKER_SURFACE": frozenset({"INSPECT", "RAKE_BUNKER"}),
    "STANDING_WATER": frozenset({"INSPECT"}),
}
_OBSERVATION_KEYS = frozenset({
    "schema", "environment", "source_kind", "score_calibration", "observation_id",
    "site_id", "deployment_id", "map_revision", "frame_id", "checkpoint_id",
    "hole_number", "feature_id", "cart_id", "camera_id", "captured_at_utc",
    "condition", "inspected_condition", "quality", "cart_position",
    "target_position", "detection_score", "evidence_ref",
})
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII)
_GENESIS = "0" * 64


class GroundsError(ValueError):
    """A rejected workflow operation; no event was appended."""

    def __init__(self, code: str, detail: str) -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}")


def _fail(code: str, detail: str) -> None:
    raise GroundsError(code, detail)


def _text(value: Any, name: str) -> str:
    if (type(value) is not str or not value or value != value.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail("grounds_invalid_request", f"{name} must be nonempty trimmed text")
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        _fail("grounds_invalid_request", f"finite JSON required: {exc}")


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _at(value: Any) -> str:
    try:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("timezone required")
            when = value.astimezone(timezone.utc)
        else:
            if type(value) is not str or _UTC.fullmatch(value) is None:
                raise ValueError("UTC text ending in Z required")
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return when.isoformat().replace("+00:00", "Z").replace(".000000Z", "Z").rstrip(" ")
    except (ValueError, TypeError, OverflowError) as exc:
        _fail("grounds_invalid_time", f"explicit valid UTC time required: {exc}")


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _number(value: Any, name: str) -> float:
    if type(value) not in (float, int):
        _fail("grounds_invalid_observation", f"{name} must be finite numeric evidence")
    try:
        result = float(value)
    except OverflowError:
        _fail("grounds_invalid_observation", f"{name} must be finite")
    if not math.isfinite(result):
        _fail("grounds_invalid_observation", f"{name} must be finite")
    return 0.0 if result == 0 else result


def _observation(value: Any, identity: Mapping[str, str], at: str) -> dict[str, Any]:
    """Check the serialized observation seam without importing its owner."""
    if not isinstance(value, Mapping) or set(value) != _OBSERVATION_KEYS:
        _fail("grounds_invalid_observation", "observation fields do not match the v0 contract")
    row = _copy(dict(value))
    markers = {"schema": OBSERVATION_SCHEMA, "environment": "SIMULATION",
               "source_kind": "SYNTHETIC_FIXTURE", "score_calibration": "NOT_CALIBRATED"}
    if any(row[key] != expected for key, expected in markers.items()):
        _fail("grounds_invalid_observation", "only synthetic SIMULATION observation evidence is accepted")
    if any(row[key] != expected for key, expected in identity.items()):
        _fail("grounds_identity_mismatch", "site, deployment and map revision must match")
    for name in ("observation_id", "frame_id", "checkpoint_id", "feature_id", "cart_id", "camera_id", "evidence_ref", "condition", "inspected_condition", "quality"):
        _text(row[name], name)
    if not row["evidence_ref"].startswith("synthetic:") or not row["evidence_ref"][10:] or any(c.isspace() for c in row["evidence_ref"]):
        _fail("grounds_invalid_observation", "evidence_ref must identify synthetic evidence")
    if type(row["hole_number"]) is not int or not 1 <= row["hole_number"] <= 18:
        _fail("grounds_invalid_observation", "hole_number must be an integer from 1 through 18")
    if row["inspected_condition"] not in CONDITION_KINDS or row["condition"] not in CONDITION_KINDS | {"CLEAR"}:
        _fail("grounds_invalid_observation", "unsupported condition kind")
    if row["condition"] != "CLEAR" and row["condition"] != row["inspected_condition"]:
        _fail("grounds_invalid_observation", "finding must match inspected_condition")
    if row["quality"] not in {"USABLE", "BLURRED", "OCCLUDED", "POSE_UNCERTAIN"}:
        _fail("grounds_invalid_observation", "unknown observation quality")
    captured = _at(row["captured_at_utc"])
    if _time(captured) > _time(at):
        _fail("grounds_future_observation", "observation cannot precede its capture in workflow time")
    # Match the producer's canonical decimal/calendar representation for IDs.
    row["captured_at_utc"] = captured[:19] + (("." + captured[20:-1].rstrip("0")) if "." in captured and captured[20:-1].rstrip("0") else "") + "Z"
    for name in ("cart_position", "target_position"):
        position = row[name]
        if position is None:
            if row["quality"] == "USABLE":
                _fail("grounds_invalid_observation", "USABLE evidence requires both position estimates")
            continue
        if not isinstance(position, dict) or set(position) != {"x_m", "y_m", "accuracy_m"}:
            _fail("grounds_invalid_observation", "position requires x_m, y_m and accuracy_m")
        row[name] = {key: _number(position[key], key) for key in position}
        if row[name]["accuracy_m"] <= 0:
            _fail("grounds_invalid_observation", "position accuracy must be positive")
    row["detection_score"] = _number(row["detection_score"], "detection_score")
    if not 0 <= row["detection_score"] <= 1:
        _fail("grounds_invalid_observation", "detection_score must be between zero and one")
    expected = "observation_" + _digest({k: v for k, v in row.items() if k != "observation_id"})[:32]
    if row["observation_id"] != expected:
        _fail("grounds_observation_integrity", "observation content does not match its ID")
    return row


def _frame_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return row["frame_id"], row["checkpoint_id"], row["inspected_condition"]


def _resource(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"id", "kind", "capabilities"}:
        _fail("grounds_invalid_resource", "resource requires id, kind and explicit capabilities")
    resource_id = _text(value["id"], "resource.id")
    if _text(value["kind"], "resource.kind") not in {"HUMAN", "SIMULATED_ROBOT"}:
        _fail("grounds_invalid_resource", "only HUMAN or SIMULATED_ROBOT resources are accepted")
    capabilities = value["capabilities"]
    if type(capabilities) not in (list, tuple) or not capabilities:
        _fail("grounds_invalid_resource", "resource capabilities must be explicitly declared")
    values = [_text(item, "capability") for item in capabilities]
    if len(set(values)) != len(values):
        _fail("grounds_invalid_resource", "duplicate capabilities")
    return {"id": resource_id, "kind": value["kind"], "capabilities": sorted(values)}


class GroundsWorkflow:
    """Event-derived, human-reviewed simulation workflow; no execution port."""

    def __init__(self, site_id: str, deployment_id: str, map_revision: str) -> None:
        self._identity = {"site_id": _text(site_id, "site_id"),
                          "deployment_id": _text(deployment_id, "deployment_id"),
                          "map_revision": _text(map_revision, "map_revision")}
        self._encoded_events: tuple[str, ...] = ()

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """Detached records. Mutating a returned dictionary cannot alter history."""
        return tuple(json.loads(encoded) for encoded in self._encoded_events)

    def _state(self) -> dict[str, dict[str, Any]]:
        observations: dict[str, Any] = {}
        cases: dict[str, Any] = {}
        tasks: dict[str, Any] = {}
        for event in self.events:
            p, kind, at = event["payload"], event["kind"], event["at_utc"]
            if kind == "observation_recorded":
                obs, case_id = p["observation"], p["case_id"]
                observations[obs["observation_id"]] = obs
                if case_id is not None:
                    if case_id not in cases:
                        cases[case_id] = {"case_id": case_id, "checkpoint_id": obs["checkpoint_id"],
                            "feature_id": obs["feature_id"], "hole_number": obs["hole_number"],
                            "condition_kind": obs["inspected_condition"], "status": "CANDIDATE",
                            "opened_at_utc": at, "updated_at_utc": at, "priority": "NORMAL",
                            "deadline_at_utc": None, "observation_ids": [], "task_ids": [],
                            "latest_task_id": None, "verification_observation_id": None}
                    case = cases[case_id]
                    case["observation_ids"].append(obs["observation_id"])
                    case["updated_at_utc"] = at
            elif kind == "case_reviewed":
                case = cases[p["case_id"]]
                case.update(status="CONFIRMED" if p["decision"] == "confirm" else "DISMISSED",
                            reviewed_by=p["operator"], review_reason=p["reason"], updated_at_utc=at)
                if p["decision"] == "dismiss":
                    case["closed_at_utc"] = at
            elif kind == "task_assigned":
                case = cases[p["case_id"]]
                tasks[p["task_id"]] = {**p, "status": "ASSIGNED", "assigned_at_utc": at,
                                       "started_at_utc": None, "completed_at_utc": None}
                case["task_ids"].append(p["task_id"])
                case.update(status="ASSIGNED", latest_task_id=p["task_id"], updated_at_utc=at)
            elif kind in {"task_started", "task_completed"}:
                task = tasks[p["task_id"]]
                status = "IN_PROGRESS" if kind == "task_started" else "AWAITING_VERIFICATION"
                task.update(status=status)
                task["started_at_utc" if kind == "task_started" else "completed_at_utc"] = at
                if kind == "task_completed":
                    task["completed_by"] = p["operator"]
                cases[task["case_id"]].update(status=status, updated_at_utc=at)
            elif kind == "case_verified":
                case = cases[p["case_id"]]
                obs = p["observation"]
                observations[obs["observation_id"]] = obs
                if obs["observation_id"] not in case["observation_ids"]:
                    case["observation_ids"].append(obs["observation_id"])
                case.update(status="VERIFIED" if p["passed"] else "REOPENED", updated_at_utc=at,
                            verification_observation_id=obs["observation_id"], verified_by=p["operator"],
                            verification_reason=p["reason"])
                if p["passed"]:
                    case["closed_at_utc"] = at
                tasks[case["latest_task_id"]]["status"] = "VERIFIED" if p["passed"] else "VERIFICATION_FAILED"
            elif kind == "case_adjusted":
                cases[p["case_id"]].update(priority=p["priority"], deadline_at_utc=p["deadline_at_utc"],
                    adjusted_by=p["operator"], adjustment_reason=p["reason"], updated_at_utc=at)
        return {"observations": observations, "cases": cases, "tasks": tasks}

    def _emit(self, kind: str, payload: dict[str, Any], at: str) -> dict[str, Any]:
        events = self.events
        if events and _time(at) < _time(events[-1]["at_utc"]):
            _fail("grounds_time_regression", "workflow events must be recorded in chronological order")
        body = {"schema": EVENT_SCHEMA, "environment": "SIMULATION", **self._identity,
                "sequence": len(events) + 1, "previous_hash": events[-1]["event_hash"] if events else _GENESIS,
                "kind": kind, "at_utc": at, "payload": _copy(payload)}
        digest = _digest(body)
        event = {**body, "event_hash": digest, "event_id": "grounds_event_" + digest[:32]}
        self._encoded_events += (_json(event),)
        return self._receipt(event, "created")

    def _receipt(self, event: Mapping[str, Any], disposition: str) -> dict[str, Any]:
        state = self._state()
        payload = event["payload"]
        task_id = payload.get("task_id")
        case_id = payload.get("case_id")
        if task_id and case_id is None:
            case_id = state["tasks"][task_id]["case_id"]
        return {"disposition": disposition, "event_id": event["event_id"],
                "case_id": case_id, "task_id": task_id,
                "case": _copy(state["cases"].get(case_id)), "task": _copy(state["tasks"].get(task_id))}

    def _duplicate(self, kind: str, payload: Mapping[str, Any], at: str) -> dict[str, Any] | None:
        for event in self.events:
            if event["kind"] == kind and event["at_utc"] == at and _json(event["payload"]) == _json(payload):
                return self._receipt(event, "duplicate")
        return None

    @staticmethod
    def _case(state: dict[str, Any], case_id: str) -> dict[str, Any]:
        _text(case_id, "case_id")
        if case_id not in state["cases"]:
            _fail("grounds_not_found", "case does not exist")
        return state["cases"][case_id]

    @staticmethod
    def _task(state: dict[str, Any], task_id: str) -> dict[str, Any]:
        _text(task_id, "task_id")
        if task_id not in state["tasks"]:
            _fail("grounds_not_found", "task does not exist")
        return state["tasks"][task_id]

    @staticmethod
    def _check_observation_conflict(state: dict[str, Any], observation: dict[str, Any]) -> None:
        for row in state["observations"].values():
            if row["observation_id"] == observation["observation_id"] or _frame_key(row) == _frame_key(observation):
                if _json(row) != _json(observation):
                    _fail("grounds_observation_conflict", "observation or frame identity is already bound to different evidence")
            if row["checkpoint_id"] == observation["checkpoint_id"] and (row["feature_id"], row["hole_number"]) != (observation["feature_id"], observation["hole_number"]):
                _fail("grounds_observation_conflict", "checkpoint cannot change feature or hole within one map revision")

    def observe(self, observation: Mapping[str, Any], at: str | datetime | None = None) -> dict[str, Any]:
        if not isinstance(observation, Mapping):
            _fail("grounds_invalid_observation", "observation must be a plain mapping")
        when = _at(at if at is not None else observation.get("captured_at_utc"))
        obs = _observation(observation, self._identity, when)
        state = self._state()
        self._check_observation_conflict(state, obs)
        for event in self.events:
            if event["kind"] in {"observation_recorded", "case_verified"} and event["payload"]["observation"]["observation_id"] == obs["observation_id"]:
                return self._receipt(event, "duplicate")
        case_id = None
        if obs["quality"] == "USABLE" and obs["condition"] != "CLEAR":
            related = [case for case in state["cases"].values()
                       if case["checkpoint_id"] == obs["checkpoint_id"] and case["condition_kind"] == obs["inspected_condition"]]
            active = [case for case in related if case["status"] not in {"VERIFIED", "DISMISSED"}]
            # A delayed historical frame is evidence, not a newly recurring
            # issue after a manager already dismissed or verified the episode.
            closed_cutoffs = [
                _time(state["observations"][case["verification_observation_id"]]["captured_at_utc"])
                if case["status"] == "VERIFIED" else _time(case["closed_at_utc"])
                for case in related if case["status"] in {"VERIFIED", "DISMISSED"}
            ]
            if active:
                case_id = active[0]["case_id"]
            elif not closed_cutoffs or _time(obs["captured_at_utc"]) > max(closed_cutoffs):
                case_id = "grounds_case_" + _digest({**self._identity, "observation_id": obs["observation_id"]})[:32]
        return self._emit("observation_recorded", {"observation": obs, "case_id": case_id}, when)

    def review(self, case_id: str, decision: str, operator: str, reason: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        payload = {"case_id": _text(case_id, "case_id"), "decision": decision,
                   "operator": _text(operator, "operator"), "reason": _text(reason, "reason")}
        if _text(decision, "decision") not in {"confirm", "dismiss"}:
            _fail("grounds_invalid_request", "review decision must be confirm or dismiss")
        duplicate = self._duplicate("case_reviewed", payload, when)
        if duplicate:
            return duplicate
        case = self._case(self._state(), case_id)
        allowed = {"CANDIDATE", "REOPENED"} if decision == "confirm" else {"CANDIDATE", "CONFIRMED", "REOPENED"}
        if case["status"] not in allowed:
            _fail("grounds_transition_rejected", "case cannot receive this review in its current state")
        return self._emit("case_reviewed", payload, when)

    def assign(self, case_id: str, resource: Mapping[str, Any], task_kind: str, operator: str, reason: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        resource = _resource(resource)
        _text(task_kind, "task_kind")
        args = {"case_id": _text(case_id, "case_id"), "resource": resource, "task_kind": task_kind,
                "operator": _text(operator, "operator"), "reason": _text(reason, "reason")}
        task_id = "grounds_task_" + _digest({**self._identity, **args, "assigned_at_utc": when})[:32]
        payload = {**args, "task_id": task_id}
        duplicate = self._duplicate("task_assigned", payload, when)
        if duplicate:
            return duplicate
        state = self._state()
        case = self._case(state, case_id)
        if case["status"] not in {"CONFIRMED", "REOPENED"}:
            _fail("grounds_transition_rejected", "only human-confirmed or reopened cases can be assigned")
        if task_kind not in _ALLOWED_TASKS[case["condition_kind"]] or task_kind not in resource["capabilities"]:
            _fail("grounds_incompatible_resource", "task must match both case kind and declared resource capability")
        if resource["kind"] == "SIMULATED_ROBOT" and task_kind not in {"INSPECT", "RAKE_BUNKER"}:
            _fail("grounds_incompatible_resource", "this rehearsal has no simulated robot divot repair capability")
        if any(task["resource"]["id"] == resource["id"] and task["status"] in {"ASSIGNED", "IN_PROGRESS"} for task in state["tasks"].values()):
            _fail("grounds_resource_busy", "resource already has an assigned or in-progress task")
        return self._emit("task_assigned", payload, when)

    def start(self, task_id: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        payload = {"task_id": _text(task_id, "task_id")}
        duplicate = self._duplicate("task_started", payload, when)
        if duplicate:
            return duplicate
        task = self._task(self._state(), task_id)
        if task["status"] != "ASSIGNED":
            _fail("grounds_transition_rejected", "only an assigned synthetic task may start")
        return self._emit("task_started", payload, when)

    def complete(self, task_id: str, operator: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        payload = {"task_id": _text(task_id, "task_id"), "operator": _text(operator, "operator")}
        duplicate = self._duplicate("task_completed", payload, when)
        if duplicate:
            return duplicate
        task = self._task(self._state(), task_id)
        if task["status"] != "IN_PROGRESS":
            _fail("grounds_transition_rejected", "completion requires an in-progress task")
        return self._emit("task_completed", payload, when)

    def verify(self, case_id: str, observation: Mapping[str, Any], passed: bool, operator: str, reason: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        obs = _observation(observation, self._identity, when)
        if type(passed) is not bool:
            _fail("grounds_invalid_request", "passed must be an explicit boolean")
        payload = {"case_id": _text(case_id, "case_id"), "observation": obs, "passed": passed,
                   "operator": _text(operator, "operator"), "reason": _text(reason, "reason")}
        duplicate = self._duplicate("case_verified", payload, when)
        if duplicate:
            return duplicate
        state = self._state()
        case = self._case(state, case_id)
        if case["status"] != "AWAITING_VERIFICATION":
            _fail("grounds_transition_rejected", "verification requires completed work awaiting independent evidence")
        self._check_observation_conflict(state, obs)
        if obs["quality"] != "USABLE":
            _fail("grounds_unusable_verification", "blurred, occluded or uncertain evidence cannot verify a repair")
        if any(obs[field] != case[field] for field in ("checkpoint_id", "feature_id", "hole_number")) or obs["inspected_condition"] != case["condition_kind"]:
            _fail("grounds_verification_mismatch", "verification must inspect the same checkpoint, feature, hole and condition")
        task = state["tasks"][case["latest_task_id"]]
        if _time(obs["captured_at_utc"]) <= _time(task["completed_at_utc"]):
            _fail("grounds_old_verification", "verification capture must be newer than task completion")
        if any(row["checkpoint_id"] == obs["checkpoint_id"]
               and row["inspected_condition"] == obs["inspected_condition"]
               and _time(row["captured_at_utc"]) > _time(obs["captured_at_utc"])
               for row in state["observations"].values()):
            _fail("grounds_old_verification", "newer observations require review; older evidence cannot verify the current condition")
        if any(row["checkpoint_id"] == obs["checkpoint_id"]
               and row["inspected_condition"] == obs["inspected_condition"]
               and _time(row["captured_at_utc"]) == _time(obs["captured_at_utc"])
               and row["quality"] == "USABLE" and row["condition"] != obs["condition"]
               for row in state["observations"].values()):
            _fail("grounds_conflicting_verification", "usable observations disagree at this capture time; a newer independent observation is required")
        original_frames = {state["observations"][oid]["frame_id"] for oid in case["observation_ids"]
                           if _time(state["observations"][oid]["captured_at_utc"]) <= _time(task["completed_at_utc"])}
        if obs["frame_id"] in original_frames:
            _fail("grounds_reused_verification", "verification requires an independent new frame")
        expected = "CLEAR" if passed else case["condition_kind"]
        if obs["condition"] != expected:
            _fail("grounds_verification_mismatch", "passed requires CLEAR; failed requires the original condition to remain")
        return self._emit("case_verified", payload, when)

    def adjust(self, case_id: str, priority: str, deadline_at_utc: str | None, operator: str, reason: str, at: str | datetime) -> dict[str, Any]:
        when = _at(at)
        if _text(priority, "priority") not in {"LOW", "NORMAL", "HIGH", "URGENT"}:
            _fail("grounds_invalid_request", "priority must be an explicit supported human selection")
        deadline = None if deadline_at_utc is None else _at(deadline_at_utc)
        if deadline is not None and _time(deadline) <= _time(when):
            _fail("grounds_invalid_time", "a new deadline must be later than the adjustment")
        payload = {"case_id": _text(case_id, "case_id"), "priority": priority, "deadline_at_utc": deadline,
                   "operator": _text(operator, "operator"), "reason": _text(reason, "reason")}
        duplicate = self._duplicate("case_adjusted", payload, when)
        if duplicate:
            return duplicate
        case = self._case(self._state(), case_id)
        if case["status"] in {"VERIFIED", "DISMISSED"}:
            _fail("grounds_transition_rejected", "terminal cases cannot be reprioritized")
        return self._emit("case_adjusted", payload, when)

    def snapshot(self) -> dict[str, Any]:
        state = self._state()
        return {"schema": SCHEMA, "environment": "SIMULATION", **self._identity,
                "observations": list(state["observations"].values()), "cases": list(state["cases"].values()),
                "tasks": list(state["tasks"].values()), "events": list(self.events)}

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "environment": "SIMULATION", **self._identity, "events": list(self.events)}

    @classmethod
    def from_dict(cls, value: Any) -> GroundsWorkflow:
        """Verify bytes, identity, chain and legal deterministic event replay.

        This detects corruption and illegal transitions, not an attacker who
        can replace the entire unanchored history with another valid history.
        The composition root is responsible for durable storage and its anchor.
        """
        try:
            if not isinstance(value, Mapping) or set(value) != {"schema", "environment", "site_id", "deployment_id", "map_revision", "events"}:
                _fail("grounds_invalid_history", "history envelope has unexpected fields")
            if value["schema"] != SCHEMA or value["environment"] != "SIMULATION" or type(value["events"]) is not list:
                _fail("grounds_invalid_history", "unsupported history schema or environment")
            result = cls(value["site_id"], value["deployment_id"], value["map_revision"])
            for event in value["events"]:
                if not isinstance(event, dict):
                    _fail("grounds_invalid_history", "event must be a JSON object")
                kind, payload, at = event["kind"], event["payload"], event["at_utc"]
                count = len(result.events)
                if kind == "observation_recorded":
                    result.observe(payload["observation"], at)
                elif kind == "case_reviewed":
                    result.review(**payload, at=at)
                elif kind == "task_assigned":
                    result.assign(**{k: v for k, v in payload.items() if k != "task_id"}, at=at)
                elif kind == "task_started":
                    result.start(**payload, at=at)
                elif kind == "task_completed":
                    result.complete(**payload, at=at)
                elif kind == "case_verified":
                    result.verify(**payload, at=at)
                elif kind == "case_adjusted":
                    result.adjust(**payload, at=at)
                else:
                    _fail("grounds_invalid_history", "unknown workflow event kind")
                if len(result.events) != count + 1 or _json(result.events[-1]) != _json(event):
                    _fail("grounds_invalid_history", "event identity, chain, association or deterministic replay disagrees")
            return result
        except GroundsError as exc:
            if exc.code == "grounds_invalid_history":
                raise
            raise GroundsError("grounds_invalid_history", str(exc)) from exc
        except (TypeError, KeyError, ValueError, IndexError) as exc:
            raise GroundsError("grounds_invalid_history", f"history cannot be replayed: {exc}") from exc


__all__ = ["GroundsWorkflow", "GroundsError", "SCHEMA", "TASK_KINDS"]
