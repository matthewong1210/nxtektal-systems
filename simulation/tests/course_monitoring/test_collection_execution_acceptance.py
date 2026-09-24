"""Executable backend gate for the twenty frozen V3 acceptance cases.

The normal-loop artifact is produced once from a fresh demo root and reused by
the cases that inspect the same causal execution.  The other cases construct
fresh durable roots and exercise the owning runtime/store/device APIs; none of
the assertions treats another test's existence as evidence.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from nxt_edge_task.contracts import EdgeTaskError, TaskRequest, parse_utc, stable_digest
from nxt_edge_task.journal import JsonlJournal, RecordSpec
from nxt_range_ops.core.entities import RobotActivity
from scripts import course_collection_execution as execution_api
from scripts import course_session_task_device as device_api
from scripts import course_session_v3
from tests.course_monitoring import test_course_collection_execution as store_fx
from tests.course_monitoring import test_course_session_v3 as session_fx
from tests.edge_task import test_course_session_task_device as device_fx
from tests.range_ops import test_collection_execution_assignment as assignment_fx


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = SIMULATION_ROOT / "docs/collection_execution_v1_architecture.md"
RUNTIME_WITNESS = (
    SIMULATION_ROOT
    / "tests/course_monitoring/fixtures/collection-execution-normal-loop-v3.json"
)
SCHEMA = json.loads(
    (SIMULATION_ROOT / "docs/contracts/collection-execution-v1/schema.json").read_text()
)
_oracle_spec = importlib.util.spec_from_file_location(
    "acceptance_wire_oracle",
    SIMULATION_ROOT / "tests/pilot_ops/test_collection_execution_wire_contract.py",
)
wire_oracle = importlib.util.module_from_spec(_oracle_spec)
assert _oracle_spec.loader is not None
_oracle_spec.loader.exec_module(wire_oracle)


@dataclass(frozen=True)
class AcceptanceCase:
    case_id: str
    name: str


ACCEPTANCE_MANIFEST = (
    AcceptanceCase("01", "Normal, policy preserved"),
    AcceptanceCase("02", "No opportunity"),
    AcceptanceCase("03", "Deterministic pending order"),
    AcceptanceCase("04", "Safety rejection"),
    AcceptanceCase("05", "Full payload then unload"),
    AcceptanceCase("06", "Temporary empty zone"),
    AcceptanceCase("07", "Access closes at boundary"),
    AcceptanceCase("08", "Policy redirection/preemption"),
    AcceptanceCase("09", "Low battery, fault, e-stop, assistance"),
    AcceptanceCase("10", "Execution timeout"),
    AcceptanceCase("11", "Duplicate/unknown request"),
    AcceptanceCase("12", "Chunk recovery"),
    AcceptanceCase("13", "Device restart/incarnation"),
    AcceptanceCase("14", "Terminal conflict"),
    AcceptanceCase("15", "Quantity integrity"),
    AcceptanceCase("16", "Stage separation"),
    AcceptanceCase("17", "Clock separation"),
    AcceptanceCase("18", "Schedule form reuse"),
    AcceptanceCase("19", "Bound handoff"),
    AcceptanceCase("20", "Crash boundaries"),
)


class WallClock:
    def __init__(self, value=datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds: int):
        self.value += timedelta(seconds=seconds)


@pytest.fixture(scope="session")
def normal_loop(tmp_path_factory):
    from scripts.course_collection_execution_demo import CourseCollectionExecutionDemo

    root = tmp_path_factory.mktemp("acceptance-normal")
    runtime = CourseCollectionExecutionDemo(root, initialize=True, wall_clock=WallClock())
    try:
        result = runtime.run(advance=2)
        evidence = runtime.evidence()
    finally:
        runtime.close()
    snapshot = result["collection_executions"]
    witness_text = RUNTIME_WITNESS.read_text(encoding="utf-8").strip()
    witness = json.loads(witness_text)
    assert snapshot == witness
    assert json.dumps(snapshot, sort_keys=True, separators=(",", ":")) == witness_text
    return {"root": root, "result": result, "evidence": evidence}


def _execution(normal_loop):
    return normal_loop["result"]["collection_executions"]["executions"][0]


def _assert_wire_contract(snapshot):
    wire_oracle.validator(SCHEMA, "#/$defs/ExecutionSnapshot").validate(snapshot)
    wire_oracle.relations(snapshot)


def _iso(identity, seconds):
    return execution_api.simulation_utc(identity, seconds)


def _public_action(env, index):
    directive = env.catalog.decode(index)
    name = type(directive).__name__
    target = (
        getattr(directive, "zone_id", None)
        if name in {"AssignCollection", "ReassignRobot"}
        else getattr(directive, "job_id", None)
        if name == "AssignStaffWork"
        else None
    )
    return {
        "name": name,
        "index": int(index),
        "robot_id": getattr(directive, "robot_id", None),
        "target_id": target,
    }


def _action(env, expression):
    return _public_action(env, env.catalog.index_of(expression))


def _catalog(env):
    return [_public_action(env, spec.index) for spec in env.catalog.specs]


@dataclass
class AlignedRuntime:
    env: object
    store: object
    identity: dict
    binding: dict
    request: dict
    task: TaskRequest
    collect: dict
    unload: dict
    device: object | None

    def view(self):
        robot = self.env.sim._robots[self.binding["runtime_robot_id"]]
        return {
            "session_id": self.identity["session_id"],
            "round_id": self.identity["round_id"],
            "robots": {
                robot.robot_id: {
                    "activity": str(robot.activity.value).upper(),
                    "payload_balls": robot.payload_balls,
                }
            },
            "catalog_actions": _catalog(self.env),
            "continuations": {},
        }


def _aligned_runtime(
    tmp_path,
    monkeypatch,
    *,
    balls=100,
    access=False,
    capacity=20,
    cycle_minutes=None,
    with_device=False,
):
    env = assignment_fx.environment(balls=balls, access=access)
    robot_id, zone_id, station_id = (
        env.scenario.robot_ids[0], env.scenario.zone_ids[0], env.scenario.station_ids[0]
    )
    robot = env.sim._robots[robot_id]
    robot.cfg = robot.cfg.model_copy(update={"payload_capacity_balls": capacity})
    env.disarm_collection_assignment("execution-1")
    now = int(env.sim.now)

    identity, _planning, _records = store_fx.inputs(tmp_path / "identity-source")
    identity.update(
        control_interval_s=15,
        session_end_sim_t_s=now + 1800,
        station_ids=[station_id],
        runtime_bindings=[{
            "robot_id": "picker-01", "zone_id": "Z1",
            "runtime_robot_id": robot_id, "runtime_zone_id": zone_id,
            "handoff_station_id": station_id,
        }],
    )
    config = device_fx.load_config() if with_device else None
    if config is not None:
        identity.update(site_id=config.site_id, deployment_id=config.deployment_id)
    due, expiry = _iso(identity, now), _iso(identity, now + 300)
    nonce = "acceptance-device"
    provisioned_at = parse_utc(due).isoformat(timespec="microseconds").replace("+00:00", "Z")
    incarnation = (
        "boot-picker-01-"
        + stable_digest({
            "robot_id": "picker-01", "provisioned_at_utc": provisioned_at, "nonce": nonce,
        })[:12]
        if with_device
        else "fixture-incarnation-001"
    )
    task = TaskRequest.build(
        site_id=identity["site_id"], deployment_id=identity["deployment_id"],
        simulation_env_id=config.simulation_env_id if config else "fixture",
        target_robot_id="picker-01", target_incarnation=incarnation,
        task_type="COLLECT_BALLS_ZONE", zone_id="Z1",
        issued_at_utc=due, expires_at_utc=expiry, progress_window_s=60,
        issued_by="SIMULATION_TEST_ENTRY:acceptance",
    )
    cycle = cycle_minutes or {"travel": 2, "collect": 5, "return": 2, "unload": 2}
    source = {
        "request_id": "acceptance-input", "revision": 1, "input_digest": "a" * 64,
        "zones": [{
            "robot_id": "picker-01", "zone_id": "Z1",
            "cycle_minutes": {
                "value": {**cycle, "wash": 3, "supply": 1},
                "source_kind": "MEASURED", "source_ref": "acceptance-runtime",
                "observed_at_utc": _iso(identity, now - 1),
                "valid_until_utc": _iso(identity, now + 1200),
            },
        }],
    }
    schedule = {
        "robot_id": "picker-01", "zone_id": "Z1", "due_at_utc": due,
        "expires_at_utc": expiry, "operator": "acceptance",
        "admission_reference": "acceptance-confirmation",
    }
    planning = {
        "schema": "nxt-planning/v1", "environment": "SIMULATION", "latest_input": source,
        "plans": [{
            "plan_id": "acceptance-plan", "version": 1, "input_revision": 1,
            "input_digest": source["input_digest"],
            "selection": {"robot_id": "picker-01", "zone_id": "Z1", "start_at_utc": due},
        }],
        "confirmations": [{
            "confirmation_id": "acceptance-confirmation", "plan_id": "acceptance-plan",
            "plan_version": 1, "input_revision": 1, "schedule_id": "acceptance-schedule",
            "schedule": schedule, "task_id": task.task_id, "task_created_at_utc": due,
        }],
    }
    edge = JsonlJournal(tmp_path / "task-created.jsonl")
    edge.append(RecordSpec("task_created", "EDGE", due, {
        "task_id": task.task_id, "schedule_id": "acceptance-schedule", "request": task.to_dict(),
    }))
    store = execution_api.CollectionExecutionStore(tmp_path / "execution.jsonl", identity)
    binding = store.bind_confirmed_tasks(planning, edge.read(), identity, now)[0]
    request = execution_api.make_request(binding, "acceptance-request", due, expiry)
    device = None
    if with_device:
        @contextmanager
        def admission(_root):
            yield SimpleNamespace(
                store=store,
                identity=deepcopy(identity),
                now_sim_t_s=store.replay()["now_sim_t_s"],
            )

        monkeypatch.setattr(device_api, "execution_admission", admission)
        monkeypatch.setattr(device_api, "restart_reconciliation", admission)
        device = device_api.SimulatorBackedTaskDevice(
            tmp_path / "session", config, "picker-01",
            journal_path=tmp_path / "device.jsonl", initialize=True,
            provisioning_nonce=lambda: nonce,
        )
        device.start()
        device.admit(task, request)
    else:
        store.submit(request)
        store.record_acceptance(request["execution_id"], "acceptance-edge-event")
    return AlignedRuntime(
        env=env, store=store, identity=identity, binding=binding, request=request,
        task=task, collect=_action(env, f"assign_collection({robot_id},{zone_id})"),
        unload=_action(env, f"send_to_handoff({robot_id})"), device=device,
    )


def _live_tick(runtime, original, *, snapshot_transform=None):
    store, env = runtime.store, runtime.env
    now = env.sim.now
    decision = store.arbitrate(original, now, runtime.view())
    state = store.replay()
    prepared = store.prepare_tick(
        decision,
        previous_cursor=state["tick_sequence"],
        previous_digest=state["replay_digest"],
    )
    if decision["selection"] == "WAIT_SLOT":
        env.arm_collection_assignment(
            runtime.request["execution_id"], runtime.binding["runtime_robot_id"],
            runtime.binding["runtime_zone_id"], runtime.binding["handoff_station_id"],
            now + runtime.binding["max_execution_s"],
        )
    _obs, _reward, _terminated, _truncated, info = env.step(
        decision["selected_action"]["index"]
    )
    snapshot = env.collection_assignment_snapshot(runtime.request["execution_id"])
    if snapshot is not None and snapshot_transform is not None:
        snapshot = snapshot_transform(deepcopy(snapshot))
    snapshots = {} if snapshot is None else {runtime.request["execution_id"]: snapshot}
    committed = store.commit_tick(
        prepared, now_sim_t_s=env.sim.now, runtime_snapshots=snapshots,
        safety_shield=info["shield"], post_state_digest="a" * 64,
    )
    if decision["selection"] == "WAIT_SLOT" and not info["shield"]["allowed"]:
        env.disarm_collection_assignment(runtime.request["execution_id"])
    store.publish_cursor(committed["tick_sequence"], committed["replay_digest"])
    if runtime.device is not None:
        runtime.device.consume_committed()
    else:
        for row in store.committed_outbox(unconfirmed_only=True):
            store.confirm_outbox(row["outbox_id"], "acceptance-" + row["outbox_id"])
    return committed


def _snapshot(runtime, *, session_state="ACTIVE", server="2035-01-02T03:04:05Z"):
    value = runtime.store.snapshot(server_time_utc=server, session_state=session_state)
    _assert_wire_contract(value)
    return value


def _case_01(context):
    snapshot = context.normal["result"]["collection_executions"]
    row = snapshot["executions"][0]
    assert row["state"] == "SUCCEEDED"
    assert row["raw_quantity"]["balls"] == row["unload_quantity"]["balls"] > 0
    assert [(a["original_action"]["name"], a["selected_action"]["name"], a["selection"])
            for a in row["actions"]] == [
        ("Wait", "AssignCollection", "WAIT_SLOT"),
        ("SendToHandoff", "SendToHandoff", "ORIGINAL_POLICY_CONVERGED"),
    ]
    _assert_wire_contract(snapshot)


def _case_02(context):
    v3 = session_fx.session(course_session_v3, context.root("no-opportunity"))
    request = session_fx.accepted_request(v3, context.root("no-opportunity-input"))
    assert type(v3.policy).__name__ == "JointDispatchPolicy"
    before = v3.env.sim.ledger.counts()
    robot_id = v3.env.scenario.robot_ids[0]
    charge = v3.env.catalog.index_of(f"send_to_charge({robot_id})")
    interval = v3.identity["control_interval_s"]
    while v3.env.sim.now + interval < request["latest_start_sim_t_s"]:
        v3.obs["robot_battery"][0] = 0.0
        v3.obs["robot_payload_frac"][0] = 0.0
        v3.info["robots"][0]["activity"] = RobotActivity.IDLE.value
        v3.info["action_mask"] = {charge: True}
        v3.advance()
        session_fx.confirm_outbox(v3)
    # Probe the arbiter at the exact exclusive boundary: even a Wait opportunity
    # is only a terminalization, never a late start. The live policy remains
    # non-Wait for every executed tick in the eligible window.
    wait = v3.env.catalog.index_of("wait")
    boundary = v3.store.arbitrate(
        _public_action(v3.env, wait), request["latest_start_sim_t_s"],
        v3.runtime_view(v3.store.replay()["executions"]),
    )
    assert boundary["original_action"]["name"] == "Wait"
    assert boundary["execution_id"] is None
    assert boundary["terminalizations"] == {
        request["execution_id"]: "POLICY_SLOT_MISSED",
    }
    v3.obs["robot_battery"][0] = 0.0
    v3.obs["robot_payload_frac"][0] = 0.0
    v3.info["robots"][0]["activity"] = RobotActivity.IDLE.value
    v3.info["action_mask"] = {charge: True}
    v3.advance()
    session_fx.confirm_outbox(v3)
    decisions = [row["prepared"]["decision"] for row in v3.store.replay_plan()]
    assert all(d["original_action"]["name"] != "Wait" for d in decisions)
    assert all(d["selected_action"] == d["original_action"] for d in decisions)
    assert all(d["selection"] == "ORIGINAL_POLICY_UNCHANGED" for d in decisions)
    row = v3.store.replay()["executions"][request["execution_id"]]
    assert (row["state"], row["reason"], row["terminal_sim_t_s"]) == (
        "MISSED", "POLICY_SLOT_MISSED", request["latest_start_sim_t_s"],
    )
    assert row["assignment_id"] is None and row["actions"] == []
    assert v3.env.collection_assignment_snapshot(request["execution_id"]) is None
    assert v3.env.sim.ledger.counts() == before


def _second_confirmed_input(identity, template, suffix, *, expiry_s):
    planning = deepcopy(template)
    due = _iso(identity, 60)
    expiry = _iso(identity, expiry_s)
    task = TaskRequest.build(
        site_id=identity["site_id"], deployment_id=identity["deployment_id"],
        simulation_env_id="fixture", target_robot_id="picker-01",
        target_incarnation="fixture-incarnation-001", task_type="COLLECT_BALLS_ZONE",
        zone_id="Z1", issued_at_utc=due, expires_at_utc=expiry,
        progress_window_s=60, issued_by=f"SIMULATION_TEST_ENTRY:{suffix}",
    )
    planning["plans"][0].update(plan_id=f"plan-{suffix}")
    planning["plans"][0]["selection"]["start_at_utc"] = due
    confirmation = planning["confirmations"][0]
    confirmation.update(
        confirmation_id=f"confirmation-{suffix}", plan_id=f"plan-{suffix}",
        schedule_id=f"schedule-{suffix}", task_id=task.task_id,
        task_created_at_utc=due,
    )
    confirmation["schedule"].update(
        due_at_utc=due, expires_at_utc=expiry, operator=suffix,
        admission_reference=f"confirmation-{suffix}",
    )
    edge = RecordSpec("task_created", "EDGE", due, {
        "task_id": task.task_id, "schedule_id": f"schedule-{suffix}", "request": task.to_dict(),
    })
    return planning, edge


def _ordered_store(root, reverse):
    identity, planning, first_records = store_fx.inputs(root / "base")
    second_planning, second_spec = _second_confirmed_input(identity, planning, "second", expiry_s=240)
    store = execution_api.CollectionExecutionStore(root / "execution.jsonl", identity)
    first = store.bind_confirmed_tasks(planning, first_records, identity, 60)[0]
    edge = JsonlJournal(root / "second-edge.jsonl")
    edge.append(second_spec)
    second = store.bind_confirmed_tasks(second_planning, edge.read(), identity, 60)[0]
    requests = [
        execution_api.make_request(first, "first-request", _iso(identity, 60), _iso(identity, 300)),
        execution_api.make_request(second, "second-request", _iso(identity, 60), _iso(identity, 240)),
    ]
    for request in reversed(requests) if reverse else requests:
        store.submit(request)
        store.record_acceptance(request["execution_id"], "accepted-" + request["request_id"])
    return store, requests


def _case_03(context):
    left, left_requests = _ordered_store(context.root("pending-left"), False)
    right, _right_requests = _ordered_store(context.root("pending-right"), True)
    first = left.arbitrate(store_fx.WAIT, 60, store_fx.view())
    reversed_first = right.arbitrate(store_fx.WAIT, 60, store_fx.view())
    expected = sorted(
        [{k: q[k] for k in ("execution_id", "eligible_sim_t_s", "latest_start_sim_t_s")}
         for q in left_requests],
        key=lambda row: (row["latest_start_sim_t_s"], row["eligible_sim_t_s"], row["execution_id"]),
    )
    assert first["eligible_pending"] == reversed_first["eligible_pending"] == expected
    selected = first["execution_id"]
    request = next(q for q in left_requests if q["execution_id"] == selected)
    prepared = left.prepare_tick(first, previous_cursor=0, previous_digest=left.replay()["replay_digest"])
    committed = left.commit_tick(
        prepared, now_sim_t_s=120,
        runtime_snapshots={selected: store_fx.assignment(request)},
        safety_shield={"allowed": True, "reason": None}, post_state_digest="a" * 64,
    )
    left.publish_cursor(1, committed["replay_digest"])
    runtime = store_fx.view()
    runtime["continuations"][selected] = store_fx.COLLECT
    other = next(q["execution_id"] for q in left_requests if q["execution_id"] != selected)
    runtime["continuations"][other] = store_fx.COLLECT
    continuation = left.arbitrate(store_fx.WAIT, 120, runtime)
    assert continuation["execution_id"] == selected
    assert continuation["selection"] == "RUNNING_CONTINUATION"
    assert continuation["eligible_pending"]
    assert sum(row["state"] == "RUNNING" for row in left.replay()["executions"].values()) == 1
    assert all(row["assignment_id"] is None for eid, row in left.replay()["executions"].items() if eid != selected)
    # A continuation-shaped action for a pending request never becomes a
    # second live lease. An invalid action for the sole running lease fails
    # without adding a prepared record.
    before = left.journal.path.read_bytes()
    runtime["continuations"][selected] = store_fx.UNLOAD
    with pytest.raises(execution_api.CollectionExecutionError, match="continuation"):
        left.arbitrate(store_fx.WAIT, 120, runtime)
    assert left.journal.path.read_bytes() == before
    assert sum(row["state"] == "RUNNING" for row in left.replay()["executions"].values()) == 1
    invalid_replay = deepcopy(left.replay())
    invalid_replay["executions"][other]["state"] = "RUNNING"
    context.monkeypatch.setattr(left, "replay", lambda: deepcopy(invalid_replay))
    with pytest.raises(execution_api.CollectionExecutionError, match="multiple running leases"):
        left.arbitrate(store_fx.WAIT, 120, runtime)
    assert left.journal.path.read_bytes() == before


def _case_04(context):
    compiled = session_fx.compiled_fixture(collection_blocked=True)
    root = context.root("safety")
    v3 = session_fx.session(course_session_v3, root, compiled=compiled)
    request = session_fx.accepted_request(v3, root / "input")
    before = v3.env.sim.ledger.counts()
    committed = v3.advance()
    row = v3.store.replay()["executions"][request["execution_id"]]
    assert committed["result"]["safety_shield"]["allowed"] is False
    assert (row["state"], row["reason"], row["edge_evidence"]["effective_state"]) == (
        "REJECTED", "SAFETY_REJECTED", "FAILED",
    )
    assert row["assignment_id"] is None
    assert v3.env.collection_assignment_snapshot(request["execution_id"]) is None
    assert v3.env.sim.ledger.counts() == before


def _case_05(context):
    row = _execution(context.normal)
    plan = context.normal["evidence"]["replay_plan"]
    states = [
        tick["committed"]["executions"].get(row["execution_id"])
        for tick in plan
        if row["execution_id"] in tick["committed"]["executions"]
    ]
    running = next(value for value in states if value["state"] == "RUNNING")
    assert running["runtime_evidence"]["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert running["raw_quantity"]["balls"] > 0
    assert running["unload_quantity"]["balls"] == 0
    assert row["state"] == "SUCCEEDED"
    assert row["raw_quantity"]["balls"] == row["unload_quantity"]["balls"] > 0


def _case_06(context):
    zero = _aligned_runtime(context.root("empty-zero"), context.monkeypatch, balls=0)
    for action in (store_fx.WAIT, store_fx.WAIT, store_fx.WAIT):
        _live_tick(zero, action)
    zero_row = _snapshot(zero)["executions"][0]
    assert (zero_row["state"], zero_row["reason"]) == ("FAILED", "ZONE_EMPTY")
    assert zero_row["raw_quantity"]["balls"] == zero_row["unload_quantity"]["balls"] == 0

    partial = _aligned_runtime(context.root("empty-partial"), context.monkeypatch, balls=9)
    for action in (store_fx.WAIT, store_fx.WAIT, store_fx.WAIT, partial.unload):
        _live_tick(partial, action)
    partial_row = _snapshot(partial)["executions"][0]
    assert (partial_row["state"], partial_row["reason"]) == ("PARTIAL", "ZONE_EMPTY")
    assert partial_row["raw_quantity"]["balls"] == partial_row["unload_quantity"]["balls"] == 9


def _case_07(context):
    runtime = _aligned_runtime(
        context.root("access-boundary"), context.monkeypatch,
        balls=100, access=True, capacity=200,
    )
    ledger = runtime.env.sim.ledger
    move = type(ledger).move
    station_transfers = []

    def observed_move(owner, source, destination, balls):
        before = owner.count(destination)
        result = move(owner, source, destination, balls)
        if owner is ledger and destination == "station:H1":
            station_transfers.append((owner.count(destination) - before, balls))
        return result

    context.monkeypatch.setattr(type(ledger), "move", observed_move)
    before_counts = runtime.env.sim.ledger.counts()
    for action in (store_fx.WAIT,) * 5 + (runtime.unload,) + (store_fx.WAIT,) * 3:
        _live_tick(runtime, action)
    native = runtime.env.collection_assignment_snapshot(runtime.request["execution_id"])
    row = _snapshot(runtime)["executions"][0]
    boundary = runtime.env.scenario.hours.open_seconds + 60
    raw_moves = [event for event in native["events"] if event["kind"] == "RAW_COLLECTED_TO_ROBOT"]
    assert [event["t_s"] for event in raw_moves] == [boundary - 49, boundary - 39,
                                                      boundary - 29, boundary - 19,
                                                      boundary - 9]
    exit_event = next(event for event in native["events"] if event["kind"] == "COLLECTION_EXIT")
    unload_event = next(event for event in native["events"] if event["kind"] == "UNLOADED_TO_STATION")
    assert exit_event["t_s"] == boundary
    assert native["terminal_sim_t_s"] == unload_event["t_s"] == boundary + 18
    assert native["collection_exit_reason"] == native["terminal_reason"] == "COLLECTION_ACCESS_BLOCKED"
    assert (row["state"], row["reason"]) == ("PARTIAL", "COLLECTION_ACCESS_BLOCKED")
    assert row["raw_quantity"]["balls"] == row["unload_quantity"]["balls"] > 0
    after_counts = runtime.env.sim.ledger.counts()
    quantity = row["raw_quantity"]["balls"]
    assert before_counts["zone:Z1"] - after_counts["zone:Z1"] == quantity
    assert after_counts["robot:R1"] == 0
    assert unload_event["balls"] == quantity and unload_event["station_id"] == "H1"
    assert station_transfers == [(quantity, quantity)]


def _case_08(context):
    runtime = _aligned_runtime(context.root("reassign"), context.monkeypatch)
    _live_tick(runtime, store_fx.WAIT)
    zone = runtime.env.scenario.zone_ids[1]
    reassign = _action(runtime.env, f"reassign_robot({runtime.binding['runtime_robot_id']},{zone})")
    _live_tick(runtime, reassign)
    native = runtime.env.collection_assignment_snapshot(runtime.request["execution_id"])
    row = _snapshot(runtime)["executions"][0]
    action = row["actions"][-1]
    assert action["original_action"] == action["selected_action"] == reassign
    assert action["selection"] == "POLICY_PREEMPTED"
    assert native["terminal_reason"] == "POLICY_PREEMPTED"
    assert row["state"] == "PARTIAL" and not row["success_display_allowed"]
    assert sum(event["kind"] == "ASSIGNMENT_STARTED" for event in native["events"]) == 1
    assert native["unloaded_balls"] == 0


def _runtime_status(runtime):
    robot = runtime.env.sim._robots[runtime.binding["runtime_robot_id"]]
    return {
        "session_id": runtime.identity["session_id"], "round_id": runtime.identity["round_id"],
        "robot_id": runtime.task.target_robot_id,
        "simulation_time_utc": _iso(runtime.identity, runtime.env.sim.now),
        "session_state": "ACTIVE", "activity": robot.activity.value.upper(),
        "payload_balls": robot.payload_balls, "paused": robot.activity is RobotActivity.PAUSED,
        "faulted": robot.activity is RobotActivity.FAILED,
        "estop_latched": robot.activity is RobotActivity.EMERGENCY_STOPPED,
        "awaiting_human": robot.activity is RobotActivity.AWAITING_HUMAN,
    }


def _case_09(context):
    observed = {}
    for cause in ("LOW_BATTERY", "ROBOT_FAULT", "ESTOP_LATCHED", "HUMAN_ASSISTANCE_REQUIRED"):
        runtime = _aligned_runtime(
            context.root("protection-" + cause.lower()), context.monkeypatch,
            with_device=True,
        )
        _live_tick(runtime, store_fx.WAIT)
        robot = runtime.env.sim._robots[runtime.binding["runtime_robot_id"]]
        before_estop = None
        if cause == "LOW_BATTERY":
            robot.battery_wh = 0
            runtime.env.sim._check_battery_floor(robot)
            original = store_fx.WAIT
        elif cause == "ROBOT_FAULT":
            runtime.env.sim._fail_robot(robot, "acceptance fault")
            original = store_fx.WAIT
        elif cause == "ESTOP_LATCHED":
            runtime.env.sim._latch_estop(robot, "acceptance stop")
            before_estop = robot.pos
            original = store_fx.WAIT
        else:
            spec = next(
                item for item in runtime.env.catalog.specs
                if item.name.startswith("request_human_assistance(")
            )
            original = _public_action(runtime.env, spec.index)
        _live_tick(runtime, original)
        native = runtime.env.collection_assignment_snapshot(runtime.request["execution_id"])
        row = _snapshot(runtime)["executions"][0]
        assert native["terminal_reason"] == row["reason"] == cause
        assert row["state"] != "SUCCEEDED" and not row["success_display_allowed"]
        assert row["edge_evidence"]["effective_state"] == "FAILED"
        assert runtime.device.view.tasks[runtime.task.task_id].terminal_kind == "FAILED"
        status = runtime.device.status_message(_runtime_status(runtime))
        observed[cause] = (row, status.to_dict())
        if cause == "ESTOP_LATCHED":
            before_extra = {
                "position": robot.pos,
                "payload": robot.payload_balls,
                "ledger": deepcopy(runtime.env.sim.ledger.counts()),
                "assignment": deepcopy(native),
            }
            for _ in range(2):
                runtime.env.step(runtime.collect["index"])
            assert robot.pos == before_estop == before_extra["position"]
            assert robot.payload_balls == before_extra["payload"]
            assert runtime.env.sim.ledger.counts() == before_extra["ledger"]
            assert runtime.env.collection_assignment_snapshot(
                runtime.request["execution_id"]
            ) == before_extra["assignment"]
    low, low_status = observed["LOW_BATTERY"]
    assert low["device_protection"] == {
        "protected": False, "reasons": [], "authorization_blocked": False,
    }
    assert low_status["availability"] == "faulted"
    assert "LOW_BATTERY" not in low["device_protection"]["reasons"]
    assistance = observed["HUMAN_ASSISTANCE_REQUIRED"][0]
    assert assistance["actions"][-1]["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert assistance["actions"][-1]["original_action"] == assistance["actions"][-1]["selected_action"]
    for cause, availability in (
        ("ROBOT_FAULT", "faulted"),
        ("ESTOP_LATCHED", "estopped"),
        ("HUMAN_ASSISTANCE_REQUIRED", "awaiting_human"),
    ):
        row, status = observed[cause]
        assert row["device_protection"]["protected"]
        assert row["device_protection"]["authorization_blocked"]
        assert cause in row["device_protection"]["reasons"]
        assert status["availability"] == availability


def _case_10(context):
    runtime = _aligned_runtime(
        context.root("timeout"), context.monkeypatch,
        cycle_minutes={"travel": 0, "collect": 0, "return": 0, "unload": 0.25},
    )
    start = runtime.env.sim.now

    def incomplete(native):
        native["events"] = native["events"][:-1]
        return native

    _live_tick(runtime, store_fx.WAIT, snapshot_transform=incomplete)
    native = runtime.env.collection_assignment_snapshot(runtime.request["execution_id"])
    row = _snapshot(runtime)["executions"][0]
    assert native["terminal_reason"] == "EXECUTION_TIMEOUT"
    assert native["terminal_sim_t_s"] == start + runtime.binding["max_execution_s"] == start + 15
    assert native["terminal_sim_t_s"] < runtime.request["latest_start_sim_t_s"]
    assert (row["state"], row["reason"]) == ("INCONCLUSIVE", "EXECUTION_TIMEOUT")
    assert row["raw_quantity"]["balls"] is None and row["unload_quantity"]["balls"] is None
    assert row["device_protection"]["authorization_blocked"]


def _case_11(context):
    store, _binding, request = store_fx.setup(execution_api, context.root("idempotency"))
    receipt = store.request_result(request["request_id"])
    before = store.journal.path.read_bytes()
    assert store.submit(request) == receipt
    assert store.journal.path.read_bytes() == before
    with pytest.raises(execution_api.CollectionExecutionError, match="content conflict"):
        store.submit({**request, "expires_at_utc": "2026-09-16T00:04:00Z"})
    alias = {**request, "request_id": "request-alias"}
    assert store.submit(alias) == receipt
    assert len(store.replay()["executions"]) == 1
    with pytest.raises(execution_api.CollectionExecutionError) as unknown:
        store.request_result("unknown-original-id")
    assert unknown.value.code == "collection_execution_request_not_found"


def _case_12(context):
    from nxt_edge_task.executor import RobotCore
    from scripts.joint_learning import read_json, read_record

    root = context.normal["root"]
    session_root = root / "session-v3"
    evidence_paths = (
        root / "edge/edge_task_journal.jsonl",
        root / "device/picker-01/robot_task_journal.jsonl",
        session_root / "collection-execution.jsonl",
        session_root / "state.json",
    )
    before_bytes = {path: path.read_bytes() for path in evidence_paths}
    config = read_json(session_root / "config.json")
    compiled = read_record(session_root / "compiled.json")
    expected_assignment = context.normal["evidence"]["assignment"]

    def unexpected_device_start(_core):
        raise AssertionError("ordinary V3 replay must not restart RobotCore")

    context.monkeypatch.setattr(RobotCore, "on_start", unexpected_device_start)
    first = course_session_v3.V3Session(session_root, config, compiled)
    first_state = first.recover()
    first_assignment = first.env.collection_assignment_snapshot(
        expected_assignment["execution_id"]
    )
    first_counts = first.env.sim.ledger.counts()
    first_replay = first.store.replay()
    second = course_session_v3.V3Session(session_root, config, compiled)
    second_state = second.recover()
    assert second.env.collection_assignment_snapshot(
        expected_assignment["execution_id"]
    ) == first_assignment == expected_assignment
    assert second.env.sim.ledger.counts() == first_counts
    assert second.store.replay()["replay_digest"] == first_replay["replay_digest"]
    assert first_state == second_state
    assert {path: path.read_bytes() for path in evidence_paths} == before_bytes


def _case_13(context):
    # Accepted but not started -> FAILED on a real device restart.
    api, device, store, _identity, task, request = device_fx._device_fixture(
        context.root("restart-not-started"), context.monkeypatch
    )
    device.admit(task, request)
    restarted = api.SimulatorBackedTaskDevice(
        device.session_root, device.config, "picker-01",
        journal_path=device.journal.path, initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert (row["state"], row["reason"]) == ("FAILED", "NOT_STARTED_AFTER_RESTART")

    # Started without terminal -> INCONCLUSIVE; residual old work is blocked.
    api, device, store, _identity, task, request = device_fx._device_fixture(
        context.root("restart-running"), context.monkeypatch
    )
    device.admit(task, request)
    device_fx._commit_collection(store, request)
    device.consume_committed(limit=1)
    restarted = api.SimulatorBackedTaskDevice(
        device.session_root, device.config, "picker-01",
        journal_path=device.journal.path, initialize=False,
    )
    restarted.start()
    row = store.replay()["executions"][request["execution_id"]]
    assert (row["state"], row["reason"]) == (
        "INCONCLUSIVE", "INTERRUPTED_EXECUTION_UNKNOWN_OUTCOME",
    )
    assert row["device_protection"]["authorization_blocked"]
    assert store.committed_outbox(unconfirmed_only=True) == []

    # A newly provisioned device has a different incarnation and rejects the old task.
    new_device = api.SimulatorBackedTaskDevice(
        device.session_root, device.config, "picker-01",
        journal_path=context.root("new-incarnation") / "device.jsonl", initialize=True,
        provisioning_nonce=lambda: "new-incarnation",
    )
    new_device.start()
    with pytest.raises(EdgeTaskError, match="authorization_blocked"):
        new_device.admit(task, request)
    assert new_device.core.view.executions_for(task.task_id) == 0


def _case_14(context):
    store, _binding, request = store_fx.setup(execution_api, context.root("terminal-conflict"))
    store_fx.tick(store, 60, snapshots={request["execution_id"]: store_fx.assignment(request)})
    store_fx.tick(
        store, 120, action=store_fx.UNLOAD,
        snapshots={request["execution_id"]: store_fx.assignment(request, terminal=True)},
    )
    terminal = next(row for row in store.committed_outbox() if row["event_kind"] == "SUCCEEDED")
    store.record_edge_evidence(
        request["execution_id"], terminal_states=["INCONCLUSIVE"],
        event_ids=["conflicting-terminal"], now_sim_t_s=180,
        outbox_id=terminal["outbox_id"],
    )
    row = store.replay()["executions"][request["execution_id"]]
    assert row["state"] == "INCONCLUSIVE" and row["reason"] == "TERMINAL_CONFLICT"
    assert row["edge_evidence"]["effective_state"] == "CONFLICT"
    assert row["edge_evidence"]["result_verification"] == "CONFLICT"
    assert not row["success_display_allowed"]
    assert row["device_protection"]["authorization_blocked"]


def _case_15(context):
    row = _execution(context.normal)
    trace = context.normal["evidence"]["assignment"]
    raw = [event for event in trace["events"] if event["kind"] == "RAW_COLLECTED_TO_ROBOT"]
    unload = [event for event in trace["events"] if event["kind"] == "UNLOADED_TO_STATION"]
    assert sum(event["balls"] for event in raw) == row["raw_quantity"]["balls"] > 0
    assert sum(event["balls"] for event in unload) == row["unload_quantity"]["balls"]
    assert row["raw_quantity"]["assignment_id"] == row["unload_quantity"]["assignment_id"] == trace["assignment_id"]
    assert row["runtime_evidence"]["conservation_passed"] is True
    assert row["runtime_evidence"]["payload_parity_passed"] is True
    assert row["raw_quantity"]["balls"] is not None
    store, _binding, _request = store_fx.setup(execution_api, context.root("quantity-unknown"))
    pending = store.snapshot(server_time_utc="2035-01-02T03:04:05Z")["executions"][0]
    assert pending["raw_quantity"]["balls"] is None
    assert pending["unload_quantity"]["balls"] is None


def _case_16(context):
    result, trace = context.normal["result"], context.normal["evidence"]["assignment"]
    planning = result["planning"]
    assert planning["outcomes"] == []
    assert planning["latest_input"]["inventory_clean_balls"]["value"] == 600
    assert [event["kind"] for event in trace["events"] if event["kind"] in {
        "RAW_COLLECTED_TO_ROBOT", "UNLOADED_TO_STATION",
    }]
    assert all(
        "washed" not in event["kind"].lower() and "supplied" not in event["kind"].lower()
        for event in trace["events"]
    )
    assert context.normal["evidence"]["planning_record_kinds"].count("planning_outcome_recorded") == 0


def _case_17(context):
    from scripts.course_collection_execution_demo import CourseCollectionExecutionDemo

    root = context.root("clocks")
    wall = WallClock()
    runtime = CourseCollectionExecutionDemo(root, initialize=True, wall_clock=wall)
    try:
        runtime.start()
        runtime.ensure_execution()
        before = runtime.task_operations_snapshot()
        before_snapshot = runtime.collection_executions()
        wall.advance(16)
        wall_only = runtime.task_operations_snapshot()
        wall_snapshot = runtime.collection_executions()
        assert parse_utc(wall_only["server_time_utc"]) - parse_utc(before["server_time_utc"]) == timedelta(seconds=16)
        assert wall_only["runtime"] == before["runtime"]
        assert wall_snapshot["now_sim_t_s"] == before_snapshot["now_sim_t_s"]
        assert wall_snapshot["simulation_time_utc"] == before_snapshot["simulation_time_utc"]
        assert wall_snapshot["executions"] == before_snapshot["executions"]
        course_session_v3.set_paused(runtime.session_root, True)
        paused = runtime.task_operations_snapshot()
        paused_snapshot = runtime.collection_executions()
        assert paused["runtime"]["simulation_time_utc"] == before["runtime"]["simulation_time_utc"]
        assert paused["runtime"]["session_state"] == "PAUSED"
        assert paused["scheduler"] == {"state": "RUNNING", "detail": None}
        assert paused_snapshot["session_state"] == "PAUSED"
        assert paused_snapshot["executions"][0]["state"] == before_snapshot["executions"][0]["state"] == "PENDING"
    finally:
        runtime.close()


def _case_18(context):
    from scripts.course_collection_execution_demo import CourseCollectionExecutionDemo

    wall = WallClock()
    runtime = CourseCollectionExecutionDemo(
        context.root("schedule-form"), initialize=True, wall_clock=wall
    )
    try:
        runtime.start()
        status = runtime.runtime_status()
        assert status["simulation_time_utc"] == "2026-09-16T08:10:00Z"
        simulation_now = parse_utc(status["simulation_time_utc"])
        schedule = {
            "robot_id": "picker-01",
            "zone_id": "Z1",
            "due_at_utc": (simulation_now + timedelta(minutes=1)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "expires_at_utc": (simulation_now + timedelta(minutes=2)).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
            "operator": "acceptance",
        }
        created = runtime.create_schedule(schedule)
        read = runtime.task_operations_snapshot()
        assert created["status"] == "created"
        assert parse_utc(read["server_time_utc"]) == wall.value
        assert read["scheduler"] == {"state": "RUNNING", "detail": None}
        assert parse_utc(created["schedule"]["due_at_utc"]).year == 2026
        assert parse_utc(created["schedule"]["due_at_utc"]) == parse_utc(schedule["due_at_utc"])
        assert parse_utc(created["schedule"]["expires_at_utc"]) == parse_utc(schedule["expires_at_utc"])
        assert wall.value.year == 2035
    finally:
        runtime.close()


def _case_19(context):
    for label, stations in (("zero", []), ("multiple", ["H1", "H2"])):
        identity, planning, records = store_fx.inputs(context.root("handoff-" + label) / "input")
        identity["station_ids"] = stations
        path = context.root("handoff-" + label) / "execution.jsonl"
        with pytest.raises(execution_api.CollectionExecutionError, match="exactly one"):
            execution_api.CollectionExecutionStore(path, identity)
        assert not path.exists()
    identity, planning, records = store_fx.inputs(context.root("handoff-wrong") / "input")
    identity["runtime_bindings"][0]["handoff_station_id"] = "H2"
    path = context.root("handoff-wrong") / "execution.jsonl"
    store = execution_api.CollectionExecutionStore(path, identity)
    with pytest.raises(execution_api.CollectionExecutionError, match="sole station"):
        store.bind_confirmed_tasks(planning, records, identity, 60)
    assert not path.exists()
    row = _execution(context.normal)
    unload_events = [
        event for event in context.normal["evidence"]["assignment"]["events"]
        if event["kind"] == "UNLOADED_TO_STATION"
    ]
    assert row["state"] == "SUCCEEDED"
    assert {event["station_id"] for event in unload_events} == {row["handoff_station_id"]} == {"H1"}


def _case_20(context):
    compiled = session_fx.compiled_fixture()
    zone_id = compiled["scenario"]["zones"][0]["zone_id"]
    compiled["session_inputs"]["demand_by_minute"][0] = 100
    compiled["session_inputs"]["landing_zones_by_minute"][0] = [zone_id] * 100
    context.monkeypatch.setattr(
        course_session_v3.JointDispatchPolicy,
        "act",
        lambda self, _obs, _info: self.catalog.index_of("wait"),
    )

    control_root = context.root("crash-control")
    control = session_fx.session(course_session_v3, control_root, compiled=compiled)
    control_request = session_fx.accepted_request(control, control_root / "input")
    control.advance()
    control_row = control.store.replay()["executions"][control_request["execution_id"]]
    control_assignment = control.env.collection_assignment_snapshot(
        control_request["execution_id"]
    )
    control_counts = control.env.sim.ledger.counts()
    control_digest = control.store.replay()["replay_digest"]
    assert control_assignment["raw_collected_balls"] == 40
    assert control_counts["robot:R1"] == 40

    for boundary in ("before_prepare", "after_prepare", "after_step", "after_commit"):
        root = context.root("crash-" + boundary)
        v3 = session_fx.session(course_session_v3, root, compiled=compiled)
        request = session_fx.accepted_request(v3, root / "input")
        initial_counts = v3.env.sim.ledger.counts()
        with pytest.raises(session_fx.Crash, match=boundary):
            v3.advance(crash_hook=session_fx.crash_at(boundary))
        recovered = session_fx.session(course_session_v3, root, compiled=compiled)
        recovered.recover()
        row = recovered.store.replay()["executions"][request["execution_id"]]
        assignment = recovered.env.collection_assignment_snapshot(request["execution_id"])
        if boundary == "before_prepare":
            assert len(recovered.store.replay_plan()) == 0
            assert row["state"] == "PENDING" and assignment is None
            assert recovered.env.sim.ledger.counts() == initial_counts
            assert recovered.store.recovery_state()["status"] == "NO_PREPARED"
        else:
            assert len(recovered.store.replay_plan()) == 1
            assert row == control_row
            assert assignment == control_assignment
            assert recovered.env.sim.ledger.counts() == control_counts
            assert recovered.store.replay()["replay_digest"] == control_digest
            assert recovered.store.recovery_state()["status"] == "COMMITTED_OUTBOX_UNCONFIRMED"
            assert sum(
                event["kind"] == "RAW_COLLECTED_TO_ROBOT"
                for event in assignment["events"]
            ) == 1

    for boundary in ("after_device_append", "after_v3_evidence", "after_v3_confirm"):
        _api, device, store, _identity, task, request = device_fx._device_fixture(
            context.root("cross-journal-" + boundary), context.monkeypatch
        )
        device.admit(task, request)
        device_fx._commit_collection(store, request)

        class Crash(RuntimeError):
            pass

        def crash(name, _payload):
            if name == boundary:
                raise Crash(name)

        with pytest.raises(Crash, match=boundary):
            device.consume_committed(crash_hook=crash, limit=1)
        before = [
            record for record in device.journal.read()
            if record.record_kind == "task_event_persisted"
            and record.payload.get("committed_outbox_id") is not None
        ]
        device.consume_committed(limit=1)
        records = [
            record for record in device.journal.read()
            if record.record_kind == "task_event_persisted"
            and record.payload.get("committed_outbox_id") is not None
        ]
        assert len(before) == 1
        linked_id = before[0].payload["committed_outbox_id"]
        linked = [
            record for record in records
            if record.payload["committed_outbox_id"] == linked_id
        ]
        assert [(r.sequence, r.record_id, r.to_dict()) for r in linked] == [
            (before[0].sequence, before[0].record_id, before[0].to_dict())
        ]
        assert len({record.payload["committed_outbox_id"] for record in records}) == len(records)
        assert device.core.view.executions_for(task.task_id) == 1
        assert all(
            row["outbox_id"] != linked_id
            for row in store.committed_outbox(unconfirmed_only=True)
        )

    root = context.root("unverifiable-prefix")
    v3 = session_fx.session(course_session_v3, root)
    request = session_fx.accepted_request(v3, root / "input")
    wait = v3.env.catalog.index_of("wait")
    v3.policy = session_fx.FixedPolicy(wait)
    with pytest.raises(session_fx.Crash, match="after_prepare"):
        v3.advance(crash_hook=session_fx.crash_at("after_prepare"))
    bad = session_fx.session(course_session_v3, root)
    pause = bad.env.catalog.index_of(f"pause_robot({bad.env.scenario.robot_ids[0]})")
    bad.policy = session_fx.FixedPolicy(pause)
    with pytest.raises(course_session_v3.ReplayMismatch):
        bad.recover()
    row = bad.store.replay()["executions"][request["execution_id"]]
    assert row["state"] == "INCONCLUSIVE"
    assert row["conflicts"]["replay_mismatch"]
    assert row["device_protection"]["authorization_blocked"]


CASE_ASSERTIONS = {
    "01": _case_01, "02": _case_02, "03": _case_03, "04": _case_04,
    "05": _case_05, "06": _case_06, "07": _case_07, "08": _case_08,
    "09": _case_09, "10": _case_10, "11": _case_11, "12": _case_12,
    "13": _case_13, "14": _case_14, "15": _case_15, "16": _case_16,
    "17": _case_17, "18": _case_18, "19": _case_19, "20": _case_20,
}


@dataclass
class AcceptanceContext:
    normal: dict
    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch

    def root(self, name):
        value = self.tmp_path / name
        value.mkdir(parents=True, exist_ok=True)
        return value


def test_acceptance_manifest_matches_the_frozen_architecture():
    ids = [case.case_id for case in ACCEPTANCE_MANIFEST]
    assert ids == [f"{value:02d}" for value in range(1, 21)]
    assert len(set(ids)) == len(set(case.name for case in ACCEPTANCE_MANIFEST)) == 20
    section = ARCHITECTURE.read_text(encoding="utf-8").split(
        "## Phase 3B acceptance cases frozen by this design", 1
    )[1].split("## Explicitly unimplemented", 1)[0]
    frozen = re.findall(r"^\d+\. \*\*(.+?):\*\*", section, flags=re.MULTILINE)
    assert frozen == [case.name for case in ACCEPTANCE_MANIFEST]
    assert set(CASE_ASSERTIONS) == set(ids)


@pytest.mark.parametrize(
    "case", ACCEPTANCE_MANIFEST,
    ids=lambda case: f"{case.case_id}-{case.name}",
)
def test_frozen_acceptance_case(case, normal_loop, tmp_path, monkeypatch):
    context = AcceptanceContext(normal_loop, tmp_path, monkeypatch)
    CASE_ASSERTIONS[case.case_id](context)
