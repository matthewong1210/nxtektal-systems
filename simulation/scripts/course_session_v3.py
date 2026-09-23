"""Replayable V3 SIMULATION session with durable collection-execution ticks.

This composition root is the sole owner of policy calls and ``RangeOpsEnv``
steps for a V3 session.  The execution store owns durable intent/result
evidence; RangeSimulation and BallLedger remain the runtime truth owners.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import time

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.joint_dispatch import JointDispatchPolicy, candidate_catalog, policy_inputs
from scripts import course_collection_execution as execution_api
from scripts.course_collection_execution import CollectionExecutionStore, CollectionExecutionError
from scripts.course_session_scenario import compile_session
from scripts.joint_learning import atomic_json, canonical, digest, read_json, read_record, write_record


SCHEMA = "nxt-whole-course-session/v3"
POLICY_ID = "JointDispatchPolicy-v1"
DEFAULT_CONFIG = {
    "schema": SCHEMA,
    "environment": "SIMULATION",
    "series_id": "synthetic-series-v3",
    "session_id": "synthetic-session-v3",
    "round_id": "synthetic-round-v3",
    "round_index": 0,
    "session_epoch_utc": "2026-09-17T00:00:00Z",
    "seed": 17092026,
    "days": 1,
    "staff_count": 3,
    "initial_stock": 4000,
    "demand_scale": 1.3,
    "assumptions": {},
    "control_interval_s": 60,
    "advance_steps": 60,
    "wall_time_seconds": 240,
    "disk_limit_mb": 512,
    "site_id": "synthetic-site",
    "deployment_id": "synthetic-sim",
    "commissioned_site_digest": "0" * 64,
    "runtime_bindings": [],
}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_BINDING_KEYS = {"robot_id", "zone_id", "runtime_robot_id", "runtime_zone_id", "handoff_station_id"}
_TERMINAL_EXECUTIONS = {"SUCCEEDED", "PARTIAL", "REJECTED", "MISSED", "FAILED", "INCONCLUSIVE"}


class ReplayMismatch(ValueError):
    """A persisted tick cannot be reconstructed byte-for-byte."""


class OutboxPending(ValueError):
    """A committed device outbox must be handled by the device owner."""


def _identifier(value, name):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _utc_midnight(value):
    if type(value) is not str or not value.endswith("Z"):
        raise ValueError("session_epoch_utc must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("session_epoch_utc must be canonical UTC") from exc
    if (parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0
            or any((parsed.hour, parsed.minute, parsed.second, parsed.microsecond))
            or parsed.isoformat(timespec="seconds").replace("+00:00", "Z") != value):
        raise ValueError("session_epoch_utc must be day-zero midnight")
    return value


def validate_config(value):
    if type(value) is not dict or set(value) != set(DEFAULT_CONFIG):
        raise ValueError("V3 config must contain exactly the versioned fields")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("only the V3 SIMULATION schema is accepted")
    result = deepcopy(value)
    for key in ("series_id", "session_id", "round_id", "site_id", "deployment_id"):
        _identifier(result[key], key)
    _utc_midnight(result["session_epoch_utc"])
    if type(result["commissioned_site_digest"]) is not str or re.fullmatch(r"[0-9a-f]{64}", result["commissioned_site_digest"]) is None:
        raise ValueError("invalid commissioned_site_digest")
    for key, low, high in (
        ("round_index", 0, 2**32 - 1), ("seed", 0, 2**32 - 1),
        ("days", 1, 7), ("staff_count", 1, 12), ("initial_stock", 0, 8000),
        ("control_interval_s", 1, 3600), ("advance_steps", 1, 10080),
        ("wall_time_seconds", 1, 1800), ("disk_limit_mb", 16, 4096),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"invalid {key}")
    if (type(result["demand_scale"]) not in (int, float)
            or not math.isfinite(result["demand_scale"])
            or not 0 <= result["demand_scale"] <= 5):
        raise ValueError("invalid demand_scale")
    if type(result["assumptions"]) is not dict:
        raise ValueError("assumptions must be an object")
    bindings = result["runtime_bindings"]
    if type(bindings) is not list or len(bindings) > 10000:
        raise ValueError("runtime_bindings must be a bounded list")
    seen_edge, seen_runtime = set(), set()
    for row in bindings:
        if type(row) is not dict or set(row) != _BINDING_KEYS:
            raise ValueError("invalid runtime binding fields")
        for key in _BINDING_KEYS:
            _identifier(row[key], "runtime binding " + key)
        edge = row["robot_id"], row["zone_id"]
        runtime = row["runtime_robot_id"], row["runtime_zone_id"]
        if edge in seen_edge or runtime in seen_runtime:
            raise ValueError("runtime bindings must be one-to-one")
        seen_edge.add(edge)
        seen_runtime.add(runtime)
    result["runtime_bindings"] = sorted(bindings, key=lambda row: (
        row["robot_id"], row["zone_id"], row["runtime_robot_id"], row["runtime_zone_id"]))
    return result


def _compile_config(config):
    return {key: deepcopy(config[key]) for key in (
        "seed", "days", "staff_count", "initial_stock", "demand_scale", "assumptions")}


def serialize_compiled(compiled, config):
    """Detach compiled inputs and bind their control interval to this V3 root."""
    if type(compiled) is not dict or "scenario" not in compiled or "session_inputs" not in compiled:
        raise ValueError("compiled V3 inputs are incomplete")
    value = deepcopy(compiled)
    scenario = value["scenario"]
    if hasattr(scenario, "model_dump"):
        scenario = scenario.model_dump(mode="json")
    if type(scenario) is not dict:
        raise ValueError("compiled scenario must be an object")
    scenario = deepcopy(scenario)
    scenario["episode"]["control_interval_s"] = config["control_interval_s"]
    session_inputs = value["session_inputs"]
    if type(session_inputs) is not dict or session_inputs.get("days") != config["days"]:
        raise ValueError("compiled session days differ from immutable V3 config")
    end_s = ((config["days"] - 1) * 1440 + scenario["hours"]["close_minute"]) * 60
    start_s = scenario["hours"]["open_minute"] * 60
    scenario["episode"]["max_steps"] = math.ceil((end_s - start_s) / config["control_interval_s"]) + 1
    value["scenario"] = RangeOpsScenario.model_validate(scenario).model_dump(mode="json")
    return json.loads(canonical(value))


def engine_fingerprint():
    root = Path(__file__).resolve().parents[1]
    scripts = (
        "course_session_v3.py", "course_collection_execution.py",
        "course_session_scenario.py", "joint_learning.py",
    )
    paths = sorted([path for folder in root.glob("nxt_*") for path in folder.rglob("*.py")]
                   + [root / "scripts" / name for name in scripts])
    content = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    content["dependencies"] = {name: importlib.metadata.version(name) for name in (
        "numpy", "simpy", "gymnasium", "pydantic")}
    return digest(content)


def build_identity(config, compiled, *, engine_digest=None):
    config = validate_config(config)
    scenario = RangeOpsScenario.model_validate(compiled["scenario"])
    if len(scenario.station_ids) != 1:
        raise ValueError("V3 execution requires exactly one scenario station")
    robots, zones, stations = set(scenario.robot_ids), set(scenario.zone_ids), set(scenario.station_ids)
    for row in config["runtime_bindings"]:
        if (row["runtime_robot_id"] not in robots or row["runtime_zone_id"] not in zones
                or row["handoff_station_id"] not in stations):
            raise ValueError("runtime binding names an unknown scenario entity")
        if row["handoff_station_id"] != scenario.station_ids[0]:
            raise ValueError("runtime binding does not name the sole station")
    session_end = ((config["days"] - 1) * 1440 + scenario.hours.close_minute) * 60
    identity = {key: deepcopy(config[key]) for key in (
        "series_id", "session_id", "round_id", "round_index", "session_epoch_utc",
        "control_interval_s", "site_id", "deployment_id", "commissioned_site_digest",
        "runtime_bindings")}
    identity.update(
        schema=SCHEMA, environment="SIMULATION", engine_digest=engine_digest or engine_fingerprint(),
        config_digest=digest(config), session_end_sim_t_s=session_end,
        station_ids=list(scenario.station_ids),
    )
    return identity


@contextmanager
def _lock(path):
    if path.is_symlink():
        raise ValueError("session lock path must not be a symlink")
    stream = path.open("a+b")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("another worker owns this session") from None
        yield
    finally:
        stream.close()


def _wall_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class V3Session:
    """One V3 runtime. Public advance/recover acquire the root session lock."""

    def __init__(self, root, config, compiled, *, store=None, policy=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = validate_config(config)
        self.compiled = serialize_compiled(compiled, self.config)
        self.scenario = RangeOpsScenario.model_validate(self.compiled["scenario"])
        self.identity = build_identity(self.config, self.compiled)
        self.env = RangeOpsEnv(self.scenario, session_inputs=self.compiled["session_inputs"],
                               collection_assignment_evidence=True)
        self.obs, self.info = self.env.reset(seed=self.config["seed"])
        if self.env.sim.session_end_s != self.identity["session_end_sim_t_s"]:
            raise ValueError("compiled runtime endpoint differs from V3 identity")
        self.store = store or CollectionExecutionStore(
            self.root / "collection-execution.jsonl", self.identity, policy_id=POLICY_ID)
        self.execution_api = execution_api
        self.policy = policy or JointDispatchPolicy(
            self.scenario, self.env.catalog, candidate_catalog()[1], seed=self.config["seed"])
        self.policy.reset()
        self.step_count = 0
        self._recovered = False

    def visible_policy_inputs(self):
        return policy_inputs(self.obs, self.info)

    def _action(self, index):
        directive = self.env.catalog.decode(index)
        name = type(directive).__name__
        robot_id = getattr(directive, "robot_id", None)
        target_id = (getattr(directive, "zone_id", None)
                     if name in {"AssignCollection", "ReassignRobot"}
                     else getattr(directive, "job_id", None) if name == "AssignStaffWork" else None)
        return {"name": name, "index": int(index), "robot_id": robot_id, "target_id": target_id}

    def _catalog_actions(self):
        return [self._action(spec.index) for spec in self.env.catalog.specs]

    def _policy_action(self):
        obs, info = self.visible_policy_inputs()
        return self._action(int(self.policy.act(obs, info)))

    def runtime_view(self, executions):
        rows = list(executions.values()) if type(executions) is dict else list(executions)
        robots = {}
        for row in self.env.sim.robot_snapshots():
            value = row.to_dict()
            robots[value["robot_id"]] = {
                "activity": str(value["activity"]).upper(),
                "payload_balls": value["payload_balls"],
            }
        actions = self._catalog_actions()
        continuations = {}
        for row in rows:
            if (row.get("state") != "RUNNING"
                    or row.get("runtime_evidence", {}).get("collection_exit_reason") is None):
                continue
            matches = [action for action in actions
                       if action["name"] == "SendToHandoff"
                       and action["robot_id"] == row["runtime_robot_id"]
                       and action["target_id"] is None]
            if len(matches) != 1:
                raise ReplayMismatch("generic handoff continuation is missing or ambiguous")
            continuations[row["execution_id"]] = deepcopy(matches[0])
        return {
            "session_id": self.identity["session_id"],
            "round_id": self.identity["round_id"],
            "robots": robots,
            "catalog_actions": actions,
            "continuations": continuations,
        }

    def _projection(self, session_state="ACTIVE"):
        snapshot = self.store.snapshot(server_time_utc=_wall_utc(), session_state=session_state)
        if snapshot["now_sim_t_s"] != self.env.sim.now:
            raise ReplayMismatch("execution-store time differs from reconstructed runtime")
        return snapshot

    def _assignment_snapshots(self, executions):
        result = {}
        for row in executions:
            snapshot = self.env.collection_assignment_snapshot(row["execution_id"])
            if snapshot is not None:
                result[row["execution_id"]] = snapshot
        return result

    def runtime_digest(self):
        execution_ids = set()
        for item in self.store.replay_plan():
            execution_ids.update(item["committed"]["executions"])
        return digest({
            "runtime": self.env.sim.state_summary(),
            "events": self.env.sim.events.to_dicts(),
            "assignments": {eid: self.env.collection_assignment_snapshot(eid)
                            for eid in sorted(execution_ids)
                            if self.env.collection_assignment_snapshot(eid) is not None},
        })

    def _post_state_digest(self, executions):
        return digest({
            "runtime": self.env.sim.state_summary(),
            "events": self.env.sim.events.to_dicts(),
            "assignments": self._assignment_snapshots(executions),
        })

    @staticmethod
    def _crash(hook, boundary, payload):
        if hook is not None:
            hook(boundary, deepcopy(payload))

    def _execution_row(self, executions, execution_id):
        matches = [row for row in executions if row["execution_id"] == execution_id]
        if len(matches) != 1:
            raise ReplayMismatch("prepared execution is missing or ambiguous")
        return matches[0]

    def _apply_prepared(self, prepared, executions, *, crash_hook=None):
        decision = prepared["decision"]
        if decision["sim_t_s"] != self.env.sim.now:
            raise ReplayMismatch("prepared tick simulation time differs")
        selected = decision["selected_action"]
        if selected != self._action(selected["index"]):
            raise ReplayMismatch("prepared selected action differs from the V3 catalog")
        execution_id = decision["execution_id"]
        armed = False
        if decision["selection"] == "WAIT_SLOT":
            row = self._execution_row(executions, execution_id)
            # A committed replay row may already be terminal because this very
            # tick established the terminal. Pending recovery performs the
            # nonterminal check against its pre-step projection below.
            if not row["edge_evidence"]["accepted"] or row["incarnation"] is None:
                raise ReplayMismatch("prepared start lacks durable authorization")
            self.env.arm_collection_assignment(
                execution_id, row["runtime_robot_id"], row["runtime_zone_id"],
                row["handoff_station_id"], decision["sim_t_s"] + row["max_execution_s"])
            armed = True
        self.obs, _, terminated, truncated, self.info = self.env.step(selected["index"])
        self.step_count += 1
        shield = {key: self.info["shield"][key] for key in ("allowed", "reason")}
        if armed and not shield["allowed"]:
            self.env.disarm_collection_assignment(execution_id)
        if truncated and not terminated:
            raise ReplayMismatch("runtime truncated before the finite V3 endpoint")
        result = {
            "now_sim_t_s": self.env.sim.now,
            "runtime_snapshots": self._assignment_snapshots(executions),
            "safety_shield": shield,
            "post_state_digest": self._post_state_digest(executions),
        }
        self._crash(crash_hook, "after_step", result)
        return result

    def _record_pending_failure(self, prepared, observed):
        observed_digest = execution_api.digest(observed)
        now = max(self.env.sim.now, prepared["decision"]["sim_t_s"])
        self.store.record_replay_failure(
            execution_api.digest(prepared), now_sim_t_s=now, observed_digest=observed_digest)

    def _replay_committed(self):
        plan = self.store.replay_plan()
        previous = 0
        for item in plan:
            prepared, committed = item["prepared"], item["committed"]
            if prepared["tick_sequence"] != previous + 1 or committed["tick_sequence"] != prepared["tick_sequence"]:
                raise ReplayMismatch("replay plan tick sequence is not contiguous")
            original = self._policy_action()
            if original != prepared["decision"]["original_action"]:
                raise ReplayMismatch("committed policy prefix differs during replay")
            executions = list(committed["executions"].values())
            observed = self._apply_prepared(prepared, executions)
            if observed != committed["result"]:
                raise ReplayMismatch("committed simulator result differs during replay")
            previous = prepared["tick_sequence"]
        return plan

    def _recover_unlocked(self):
        self.store.initialize_session(now_sim_t_s=self.env.sim.now)
        if self.step_count or self.env.sim.now != self.scenario.hours.open_seconds:
            raise ValueError("recovery requires a fresh V3 runtime")
        plan = self._replay_committed()
        state = self.store.recovery_state()
        if state["status"] == "REPLAY_MISMATCH":
            raise ReplayMismatch("session was sealed by an earlier replay mismatch")
        if state["status"] == "EDGE_WITHOUT_COMMIT":
            raise ReplayMismatch("Edge evidence has no matching committed simulator tick")
        if state["status"] == "PREPARED_NO_COMMIT":
            prepared = state["pending_prepared"]
            if not state["replay_permitted"]:
                self._record_pending_failure(prepared, {"reason": "authorization_not_replayable"})
                raise ReplayMismatch("prepared authorization cannot be replayed")
            try:
                original = self._policy_action()
                if original != prepared["decision"]["original_action"]:
                    self._record_pending_failure(prepared, {
                        "reason": "policy_original_mismatch", "observed_original": original})
                    raise ReplayMismatch("prepared policy proposal differs during recovery")
                projection = self._projection()
                executions = projection["executions"]
                if prepared["decision"]["execution_id"] is not None:
                    row = self._execution_row(executions, prepared["decision"]["execution_id"])
                    if row["state"] in _TERMINAL_EXECUTIONS:
                        self._record_pending_failure(prepared, {"reason": "execution_already_terminal"})
                        raise ReplayMismatch("prepared authorization became terminal")
                result = self._apply_prepared(prepared, executions)
                committed = self.store.commit_tick(prepared, **result)
            except ReplayMismatch as exc:
                if self.store.recovery_state()["status"] != "REPLAY_MISMATCH":
                    try:
                        self._record_pending_failure(prepared, {
                            "reason": type(exc).__name__, "detail": str(exc)})
                    except CollectionExecutionError:
                        pass
                raise
            except Exception as exc:
                try:
                    self._record_pending_failure(prepared, {
                        "reason": type(exc).__name__, "detail": str(exc)})
                except CollectionExecutionError:
                    pass
                raise ReplayMismatch("prepared simulator result could not be verified") from exc
            self.store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
            state = self.store.recovery_state()
        elif state["status"] == "COMMITTED_CURSOR_STALE":
            if not plan:
                raise ReplayMismatch("cursor-stale state has no committed replay prefix")
            committed = plan[-1]["committed"]
            self.store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
            state = self.store.recovery_state()
        self._recovered = True
        return state

    def recover(self):
        with _lock(self.root / ".session.lock"):
            return self._recover_unlocked()

    def _advance_unlocked(self, *, crash_hook=None):
        self.store.initialize_session(now_sim_t_s=self.env.sim.now)
        state = self.store.recovery_state()
        if state["status"] in {"PREPARED_NO_COMMIT", "COMMITTED_CURSOR_STALE"}:
            self._recover_unlocked()
            state = self.store.recovery_state()
        if state["status"] == "REPLAY_MISMATCH":
            raise ReplayMismatch("session is sealed by replay mismatch")
        if state["status"] == "EDGE_WITHOUT_COMMIT":
            raise ReplayMismatch("orphan Edge evidence blocks V3 advancement")
        if state["status"] == "COMMITTED_OUTBOX_UNCONFIRMED":
            raise OutboxPending("committed outbox remains device-owned and unconfirmed")
        if self.env.sim.facility_closed:
            raise RuntimeError("the finite V3 session has already completed")
        projection = self._projection()
        original = self._policy_action()
        decision = self.store.arbitrate(
            original, self.env.sim.now, self.runtime_view(projection["executions"]))
        self._crash(crash_hook, "before_prepare", decision)
        prepared = self.store.prepare_tick(
            decision, previous_cursor=state["tick_sequence"], previous_digest=state["replay_digest"])
        self._crash(crash_hook, "after_prepare", prepared)
        result = self._apply_prepared(prepared, projection["executions"], crash_hook=crash_hook)
        committed = self.store.commit_tick(prepared, **result)
        self._crash(crash_hook, "after_commit", committed)
        self.store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
        self._crash(crash_hook, "after_cursor", committed)
        return committed

    def advance(self, *, crash_hook=None):
        with _lock(self.root / ".session.lock"):
            return self._advance_unlocked(crash_hook=crash_hook)


def _state(session, status):
    recovery = session.store.recovery_state()
    result = {key: deepcopy(session.identity[key]) for key in (
        "schema", "environment", "series_id", "session_id", "round_id", "round_index",
        "engine_digest", "config_digest", "session_epoch_utc", "control_interval_s",
        "session_end_sim_t_s")}
    result.update(
        status=status, step=recovery["tick_sequence"], now_sim_t_s=session.env.sim.now,
        simulation_time_utc=execution_api.simulation_utc(session.identity, session.env.sim.now),
        replay_digest=recovery["replay_digest"], compiled_digest=digest(session.compiled),
        unconfirmed_outbox=len(recovery["unconfirmed_outbox"]),
    )
    return result


def _load_root(root, config):
    stored = validate_config(read_json(root / "config.json"))
    if config is not None and validate_config(config) != stored:
        raise ValueError("existing V3 session configuration is immutable")
    compiled = read_record(root / "compiled.json")
    identity = read_record(root / "identity.json")
    expected = build_identity(stored, compiled)
    if identity != expected:
        raise ValueError("session engine/config/identity changed; preserve evidence and create a new V3 root")
    state = read_record(root / "state.json")
    if (state.get("schema") != SCHEMA or state.get("config_digest") != digest(stored)
            or state.get("compiled_digest") != digest(compiled)
            or state.get("engine_digest") != identity["engine_digest"]):
        raise ValueError("compiled scenario or V3 cursor identity changed")
    return stored, compiled, state


def run(root, config=None, *, crash_hook=None):
    """Recover and advance one finite V3 chunk under one nonblocking lock."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("V3 session root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    with _lock(root / ".session.lock"):
        state_path = root / "state.json"
        if state_path.exists():
            config, compiled, saved = _load_root(root, config)
        else:
            if any(path.name != ".session.lock" for path in root.iterdir()):
                raise ValueError("new V3 session requires an empty directory")
            config = validate_config(DEFAULT_CONFIG if config is None else config)
            compiled = serialize_compiled(compile_session(_compile_config(config)), config)
            identity = build_identity(config, compiled)
            atomic_json(root / "config.json", config)
            atomic_json(root / "control.json", {"paused": False})
            write_record(root / "compiled.json", compiled)
            write_record(root / "identity.json", identity)
            session = V3Session(root, config, compiled)
            session.store.initialize_session(now_sim_t_s=session.env.sim.now)
            saved = _state(session, "INITIALIZED")
            write_record(state_path, saved)
        control = read_json(root / "control.json")
        if type(control) is not dict or set(control) != {"paused"} or type(control["paused"]) is not bool:
            raise ValueError("invalid V3 pause control")
        if control["paused"]:
            return {**saved, "status": "PAUSED"}
        session = V3Session(root, config, compiled)
        started = time.monotonic()
        recovery = session._recover_unlocked()
        if recovery["status"] == "COMMITTED_OUTBOX_UNCONFIRMED":
            status_name = "OUTBOX_PENDING"
        else:
            status_name = "SESSION_COMPLETE" if session.env.sim.facility_closed else "CHUNK_COMPLETE"
            for _ in range(config["advance_steps"]):
                if session.env.sim.facility_closed:
                    status_name = "SESSION_COMPLETE"
                    break
                if time.monotonic() - started > config["wall_time_seconds"]:
                    status_name = "TIME_BUDGET"
                    break
                if sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) > config["disk_limit_mb"] * 1024**2:
                    status_name = "DISK_LIMIT"
                    break
                control = read_json(root / "control.json")
                if control != {"paused": False}:
                    if control == {"paused": True}:
                        status_name = "PAUSED"
                        break
                    raise ValueError("invalid V3 pause control")
                try:
                    session._advance_unlocked(crash_hook=crash_hook)
                except OutboxPending:
                    status_name = "OUTBOX_PENDING"
                    break
                if session.store.recovery_state()["status"] == "COMMITTED_OUTBOX_UNCONFIRMED":
                    status_name = "OUTBOX_PENDING"
                    break
            else:
                status_name = "SESSION_COMPLETE" if session.env.sim.facility_closed else "CHUNK_COMPLETE"
        state = _state(session, status_name)
        # Store commit/cursor fsyncs precede this disposable external cursor.
        write_record(state_path, state)
        return state


def open_store(root):
    root = Path(root)
    identity = read_record(root / "identity.json")
    return CollectionExecutionStore(root / "collection-execution.jsonl", identity, policy_id=POLICY_ID)


def status(root):
    root = Path(root)
    state = read_record(root / "state.json")
    control = read_json(root / "control.json")
    if control == {"paused": True}:
        return {**state, "status": "PAUSED"}
    if control != {"paused": False}:
        raise ValueError("invalid V3 pause control")
    return state


def set_paused(root, paused):
    if type(paused) is not bool:
        raise ValueError("paused must be boolean")
    root = Path(root)
    with _lock(root / ".session.lock"):
        read_record(root / "state.json")
        atomic_json(root / "control.json", {"paused": paused})
    return status(root)
