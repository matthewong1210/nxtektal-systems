"""Replayable V3 SIMULATION session with durable collection-execution ticks.

This composition root is the sole owner of policy calls and ``RangeOpsEnv``
steps for a V3 session.  The execution store owns durable intent/result
evidence; RangeSimulation and BallLedger remain the runtime truth owners.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import time

import numpy as np

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
_STATE_IDENTITY_KEYS = (
    "schema", "environment", "series_id", "session_id", "round_id", "round_index",
    "engine_digest", "config_digest", "session_epoch_utc", "control_interval_s",
    "session_end_sim_t_s",
)
_STATE_FIELDS = set(_STATE_IDENTITY_KEYS) | {
    "status", "step", "now_sim_t_s", "simulation_time_utc", "replay_digest",
    "compiled_digest", "unconfirmed_outbox",
}
_STATE_STATUSES = {
    "INITIALIZED", "CHUNK_COMPLETE", "TIME_BUDGET", "DISK_LIMIT",
    "PAUSED", "OUTBOX_PENDING", "SESSION_COMPLETE",
}


class ReplayMismatch(ValueError):
    """A persisted tick cannot be reconstructed byte-for-byte."""


class OutboxPending(ValueError):
    """A committed device outbox must be handled by the device owner."""


@dataclass(frozen=True)
class ExecutionAdmission:
    """Writable execution evidence exposed only while the V3 root lock is held."""

    store: CollectionExecutionStore
    identity: dict
    now_sim_t_s: float


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


def _canonical_value(value):
    """Detach JSON evidence, including numpy and mapping facade values."""
    if isinstance(value, Mapping):
        result = {}
        for raw_key, item in value.items():
            if isinstance(raw_key, str):
                key = raw_key
            elif isinstance(raw_key, (int, np.integer)) and not isinstance(raw_key, (bool, np.bool_)):
                key = str(int(raw_key))
            else:
                raise ValueError("causal evidence mapping keys must be strings or integers")
            if key in result:
                raise ValueError("causal evidence mapping key collision")
            result[key] = _canonical_value(item)
        return result
    if isinstance(value, np.ndarray):
        return _canonical_value(value.tolist())
    if isinstance(value, np.generic):
        return _canonical_value(value.item())
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("causal evidence must contain finite numbers")
        return value
    raise ValueError(f"unsupported causal evidence value: {type(value).__name__}")


class V3Session:
    """One V3 runtime. Public advance/recover acquire the root session lock."""

    def __init__(self, root, config, compiled):
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
        self.store = CollectionExecutionStore(
            self.root / "collection-execution.jsonl", self.identity, policy_id=POLICY_ID)
        self.execution_api = execution_api
        self.policy = JointDispatchPolicy(
            self.scenario, self.env.catalog, candidate_catalog()[1], seed=self.config["seed"])
        self.policy.reset()
        self.step_count = 0
        self._recovered = False
        self._known_execution_ids = set()

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
        self._known_execution_ids.update(row["execution_id"] for row in executions)
        return self._all_assignment_snapshots()

    def _all_assignment_snapshots(self):
        result = {}
        for execution_id in sorted(self._known_execution_ids):
            snapshot = self.env.collection_assignment_snapshot(execution_id)
            if snapshot is not None:
                result[execution_id] = snapshot
        return result

    def _policy_state(self):
        rng = getattr(self.policy, "rng", None)
        catalog = getattr(self.policy, "catalog", None)
        return _canonical_value({
            "candidate": self.policy.candidate,
            "version": self.policy.version,
            "name": self.policy.name,
            "previous_inventory": self.policy._previous_inventory,
            "visible_catalog_staff_names": sorted(catalog._staff_names),
            "rng_state": None if rng is None else rng.bit_generator.state,
        })

    def _causal_post_state(self):
        observation, info = self.visible_policy_inputs()
        return _canonical_value({
            "state_summary": self.env.sim.state_summary(),
            "simulator_rng_state": self.env.sim.rng_state_snapshot(),
            "legacy_events": self.env.sim.events.to_dicts(),
            "assignment_snapshots": self._all_assignment_snapshots(),
            "policy_inputs": {"observation": observation, "info": info},
            "metrics": self.env.sim.metrics.to_dict(),
            "joint_metrics": self.env.sim.joint_metrics,
            "staff_work_snapshots": self.env.sim.staff_work_snapshots(),
            "step_count": self.step_count,
            "policy_state": self._policy_state(),
        })

    def _remember_replay_executions(self):
        for item in self.store.replay_plan():
            self._known_execution_ids.update(item["committed"]["executions"])

    def runtime_digest(self):
        self._remember_replay_executions()
        return digest(self._causal_post_state())

    def _post_state_digest(self, executions):
        for row in executions:
            self._known_execution_ids.add(row["execution_id"])
        return digest(self._causal_post_state())

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
        self.obs, _, terminated, truncated, self.info = self.env.step(selected["index"])
        self.step_count += 1
        shield = {key: self.info["shield"][key] for key in ("allowed", "reason")}
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

    def _cleanup_rejected_arm(self, prepared, result):
        decision = prepared["decision"]
        if decision["selection"] == "WAIT_SLOT" and not result["safety_shield"]["allowed"]:
            self.env.disarm_collection_assignment(decision["execution_id"])

    def _record_pending_failure(self, prepared, observed):
        observed_digest = execution_api.digest(_canonical_value(observed))
        now = max(self.env.sim.now, prepared["decision"]["sim_t_s"])
        self.store.record_replay_failure(
            execution_api.digest(prepared), now_sim_t_s=now, observed_digest=observed_digest)

    def _raise_committed_mismatch(
        self, item, final_sim_t_s, message, observed, *, seal_mismatch,
        cause=None,
    ):
        prepared, committed = item["prepared"], item["committed"]
        observed_digest = execution_api.digest(_canonical_value({
            "message": message,
            "observed": observed,
        }))
        if seal_mismatch:
            self.store.record_committed_replay_failure(
                prepared["tick_sequence"],
                prepared_digest=execution_api.digest(prepared),
                committed_digest=execution_api.digest(committed),
                observed_digest=observed_digest,
                now_sim_t_s=final_sim_t_s,
            )
        if cause is None:
            raise ReplayMismatch(message)
        raise ReplayMismatch(message) from cause

    def _replay_committed(self, *, seal_mismatch):
        plan = self.store.replay_plan()
        final_sim_t_s = (self.env.sim.now if not plan
                         else plan[-1]["committed"]["result"]["now_sim_t_s"])
        previous = 0
        for item in plan:
            prepared, committed = item["prepared"], item["committed"]
            if prepared["tick_sequence"] != previous + 1 or committed["tick_sequence"] != prepared["tick_sequence"]:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "replay plan tick sequence is not contiguous",
                    {"previous_tick_sequence": previous,
                     "prepared_tick_sequence": prepared["tick_sequence"],
                     "committed_tick_sequence": committed["tick_sequence"]},
                    seal_mismatch=seal_mismatch,
                )
            try:
                original = self._policy_action()
            except Exception as exc:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "committed policy prefix could not be reconstructed",
                    {"exception": type(exc).__name__, "detail": str(exc)}, cause=exc,
                    seal_mismatch=seal_mismatch)
            if original != prepared["decision"]["original_action"]:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "committed policy prefix differs during replay",
                    {"original_action": original,
                     "stored_original_action": prepared["decision"]["original_action"]},
                    seal_mismatch=seal_mismatch,
                )
            executions = list(committed["executions"].values())
            try:
                observed = self._apply_prepared(prepared, executions)
            except Exception as exc:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "committed simulator action differs during replay",
                    {"exception": type(exc).__name__, "detail": str(exc)}, cause=exc,
                    seal_mismatch=seal_mismatch)
            if observed != committed["result"]:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "committed simulator result differs during replay",
                    {"result": observed, "stored_result": committed["result"]},
                    seal_mismatch=seal_mismatch,
                )
            try:
                self._cleanup_rejected_arm(prepared, observed)
            except Exception as exc:
                self._raise_committed_mismatch(
                    item, final_sim_t_s, "committed rejected-arm cleanup differs during replay",
                    {"exception": type(exc).__name__, "detail": str(exc)}, cause=exc,
                    seal_mismatch=seal_mismatch)
            previous = prepared["tick_sequence"]
        return plan

    def _reconstruct_prefix_unlocked(self, *, initialize_session, seal_mismatch):
        if initialize_session:
            self.store.initialize_session(now_sim_t_s=self.env.sim.now)
        elif not self.store.journal.read():
            raise ReplayMismatch("V3 execution journal is not initialized")
        if self.step_count or self.env.sim.now != self.scenario.hours.open_seconds:
            raise ValueError("recovery requires a fresh V3 runtime")
        before = self.store.recovery_state()
        if before["status"] == "REPLAY_MISMATCH":
            raise ReplayMismatch("session was sealed by an earlier replay mismatch")
        if before["status"] == "EDGE_WITHOUT_COMMIT":
            raise ReplayMismatch("Edge evidence has no matching committed simulator tick")
        plan = self._replay_committed(seal_mismatch=seal_mismatch)
        return plan, self.store.recovery_state()

    def _verify_prefix_unlocked(self):
        """Reconstruct an existing prefix without initialization or sealing."""
        return self._reconstruct_prefix_unlocked(
            initialize_session=False, seal_mismatch=False
        )

    def _recover_prefix_unlocked(self):
        """Initialize when needed and durably seal committed replay drift."""
        return self._reconstruct_prefix_unlocked(
            initialize_session=True, seal_mismatch=True
        )

    def _recover_unlocked(self):
        plan, state = self._recover_prefix_unlocked()
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
            try:
                self._cleanup_rejected_arm(prepared, result)
            except Exception as exc:
                self._raise_committed_mismatch(
                    {"prepared": prepared, "committed": committed}, result["now_sim_t_s"],
                    "committed rejected-arm cleanup failed during pending recovery",
                    {"exception": type(exc).__name__, "detail": str(exc)},
                    seal_mismatch=True, cause=exc)
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
        try:
            self._cleanup_rejected_arm(prepared, result)
        except Exception as exc:
            self._raise_committed_mismatch(
                {"prepared": prepared, "committed": committed}, result["now_sim_t_s"],
                "committed rejected-arm cleanup failed during live execution",
                {"exception": type(exc).__name__, "detail": str(exc)},
                seal_mismatch=True, cause=exc)
        self.store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
        self._crash(crash_hook, "after_cursor", committed)
        return committed

    def advance(self, *, crash_hook=None):
        with _lock(self.root / ".session.lock"):
            return self._advance_unlocked(crash_hook=crash_hook)


def _state(session, status):
    recovery = session.store.recovery_state()
    result = {key: deepcopy(session.identity[key]) for key in _STATE_IDENTITY_KEYS}
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
    if (type(state) is not dict or set(state) != _STATE_FIELDS
            or any(state[key] != identity[key] for key in _STATE_IDENTITY_KEYS)
            or state["compiled_digest"] != digest(compiled)):
        raise ValueError("compiled scenario or persisted V3 state identity changed")
    now = state["now_sim_t_s"]
    if (state["status"] not in _STATE_STATUSES
            or type(state["step"]) is not int or state["step"] < 0
            or type(now) not in (int, float) or not math.isfinite(now)
            or not 0 <= now <= identity["session_end_sim_t_s"]
            or state["simulation_time_utc"] != execution_api.simulation_utc(identity, now)
            or type(state["replay_digest"]) is not str
            or re.fullmatch(r"[0-9a-f]{64}", state["replay_digest"]) is None
            or type(state["unconfirmed_outbox"]) is not int
            or state["unconfirmed_outbox"] < 0):
        raise ValueError("invalid persisted V3 state")
    return stored, compiled, state


def _read_control(root):
    control = read_json(root / "control.json")
    if type(control) is not dict or set(control) != {"paused"} or type(control["paused"]) is not bool:
        raise ValueError("invalid V3 pause control")
    return control


def _validate_saved_runtime(saved, session, recovery):
    if saved.get("status") not in _STATE_STATUSES:
        raise ValueError("invalid persisted V3 session status")
    expected = {
        "step": recovery["tick_sequence"],
        "now_sim_t_s": session.env.sim.now,
        "simulation_time_utc": execution_api.simulation_utc(
            session.identity, session.env.sim.now),
        "replay_digest": recovery["replay_digest"],
    }
    current_unconfirmed = len(recovery["unconfirmed_outbox"])
    saved_unconfirmed = saved.get("unconfirmed_outbox")
    if (any(saved.get(key) != value for key, value in expected.items())
            or type(saved_unconfirmed) is not int
            or not current_unconfirmed <= saved_unconfirmed):
        raise ValueError("persisted V3 state differs from the validated execution prefix")


def _validated_read_runtime(root):
    config, compiled, saved = _load_root(root, None)
    control = _read_control(root)
    session = V3Session(root, config, compiled)
    _, recovery = session._verify_prefix_unlocked()
    if recovery["status"] in {"PREPARED_NO_COMMIT", "COMMITTED_CURSOR_STALE"}:
        raise ReplayMismatch("V3 session recovery is required before external access")
    if recovery["status"] not in {"NO_PREPARED", "COMMITTED_OUTBOX_UNCONFIRMED"}:
        raise ReplayMismatch("V3 session is not available for external access")
    _validate_saved_runtime(saved, session, recovery)
    return session, saved, control


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
        control = _read_control(root)
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


@contextmanager
def execution_admission(root):
    """Hold the V3 session lock across Task 5 evidence admission writes."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("V3 session root must not be a symlink")
    with _lock(root / ".session.lock"):
        session, _, _ = _validated_read_runtime(root)
        yield ExecutionAdmission(
            store=session.store,
            identity=deepcopy(session.identity),
            now_sim_t_s=session.env.sim.now,
        )


def read_execution_snapshot(root, *, server_time_utc):
    """Return one validated read-only projection without exposing its store."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("V3 session root must not be a symlink")
    with _lock(root / ".session.lock"):
        session, saved, control = _validated_read_runtime(root)
        session_state = ("PAUSED" if control["paused"] else
                         "ENDED" if saved["status"] == "SESSION_COMPLETE" else "ACTIVE")
        return session.store.snapshot(
            server_time_utc=server_time_utc, session_state=session_state)


def read_runtime_status(root, robot_id):
    """Return the detached simulator facts required by the Edge status owner.

    This reader owns no live runtime reference.  It verifies and reconstructs
    the durable prefix under the session lock, then returns only the declared
    Edge/runtime binding and protection facts.  It never advances beyond that
    prefix or writes a heartbeat, policy decision, or simulator result.
    """
    root = Path(root)
    if root.is_symlink():
        raise ValueError("V3 session root must not be a symlink")
    if type(robot_id) is not str:
        raise ValueError("robot binding requires a string robot_id")
    with _lock(root / ".session.lock"):
        session, _saved, control = _validated_read_runtime(root)
        bindings = [
            row for row in session.identity["runtime_bindings"]
            if row["robot_id"] == robot_id
        ]
        if len(bindings) != 1:
            raise ValueError("robot binding is missing or ambiguous")
        runtime_robot_id = bindings[0]["runtime_robot_id"]
        robots = [
            row.to_dict() for row in session.env.sim.robot_snapshots()
            if row.robot_id == runtime_robot_id
        ]
        if len(robots) != 1:
            raise ReplayMismatch("bound runtime robot is missing or ambiguous")
        robot = robots[0]
        paused = control["paused"]
        session_state = (
            "PAUSED" if paused else
            "ENDED" if session.env.sim.facility_closed else
            "ACTIVE"
        )
        activity = robot["activity"].upper()
        return {
            "session_id": session.identity["session_id"],
            "round_id": session.identity["round_id"],
            "robot_id": robot_id,
            "simulation_time_utc": execution_api.simulation_utc(
                session.identity, session.env.sim.now
            ),
            "session_state": session_state,
            "activity": activity,
            "payload_balls": robot["payload_balls"],
            "paused": paused,
            "faulted": robot["health"] == "failed" or activity == "FAILED",
            "estop_latched": robot["estop_latched"],
            "awaiting_human": robot["awaiting_human"],
        }


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
