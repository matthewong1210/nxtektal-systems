"""V3 is the sole policy/step owner and replays committed simulator evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import inspect
import json
from pathlib import Path

import pytest

from nxt_edge_task.contracts import TaskRequest
from nxt_edge_task.journal import JsonlJournal, RecordSpec
from nxt_range_ops.core import ledger as ledger_locations
from nxt_range_ops.core.skills import SkillOutcome, SkillOutcomeModel, SkillType
from nxt_range_ops.scenarios.generators import make_scenario
from scripts.course_collection_execution import simulation_utc
from scripts.joint_learning import atomic_json, read_record, write_record


ROOT = Path(__file__).resolve().parents[2]


def iso(epoch: str, seconds: int) -> str:
    value = datetime.fromisoformat(epoch.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def compiled_fixture(*, end_minute: int = 12, collection_blocked: bool = False) -> dict:
    base = make_scenario("normal_weekday")
    scenario = base.model_copy(update={
        "name": "v3_session_test",
        "hours": base.hours.model_copy(update={"open_minute": 1, "close_minute": end_minute}),
        "episode": base.episode.model_copy(update={"control_interval_s": 60, "max_steps": end_minute + 2}),
        "initial_dispenser_frac": 1.0,
    })
    length = end_minute - 1
    blocks = {zone_id: [] for zone_id in scenario.zone_ids}
    if collection_blocked:
        blocks = {zone_id: [{"start_minute": 1, "end_minute": end_minute}]
                  for zone_id in scenario.zone_ids}
    return {
        "schema": "nxt-course-session-scenario/v1",
        "environment": "SIMULATION",
        "scenario": scenario.model_dump(mode="json"),
        "session_inputs": {
            "schema": "nxt-course-range-session/v1",
            "environment": "SIMULATION",
            "days": 1,
            "demand_by_minute": [0] * length,
            "landing_zones_by_minute": [[] for _ in range(length)],
            "collection_blocks": blocks,
            "staff_job_slots": [],
        },
    }


@pytest.fixture
def runner():
    path = ROOT / "scripts/course_session_v3.py"
    assert path.exists(), "V3 session driver is not implemented"
    from scripts import course_session_v3
    return course_session_v3


@pytest.fixture
def series_runner():
    path = ROOT / "scripts/course_session_series_v3.py"
    assert path.exists(), "V3 series driver is not implemented"
    from scripts import course_session_series_v3
    return course_session_series_v3


def config(runner, compiled: dict, **updates) -> dict:
    scenario = compiled["scenario"]
    robot, zone, station = (scenario["robots"][0]["robot_id"], scenario["zones"][0]["zone_id"],
                            scenario["stations"][0]["station_id"])
    value = deepcopy(runner.DEFAULT_CONFIG)
    value.update({
        "series_id": "series-v3-test",
        "session_id": "session-v3-test",
        "round_id": "round-v3-test",
        "round_index": 0,
        "session_epoch_utc": "2026-09-16T00:00:00Z",
        "seed": 53,
        "days": 1,
        "advance_steps": 1,
        "site_id": "synthetic-site",
        "deployment_id": "synthetic-sim",
        "commissioned_site_digest": "a" * 64,
        "runtime_bindings": [{
            "robot_id": "picker-01", "zone_id": "Z1", "runtime_robot_id": robot,
            "runtime_zone_id": zone, "handoff_station_id": station,
        }],
    })
    value.update(updates)
    return value


def session(runner, tmp_path, *, compiled=None):
    compiled = compiled or compiled_fixture()
    cfg = config(runner, compiled)
    return runner.V3Session(tmp_path, cfg, compiled)


class FixedPolicy:
    def __init__(self, action: int):
        self.action = action
        self.calls = 0

    def reset(self):
        self.calls = 0

    def act(self, obs, info):
        self.calls += 1
        return self.action


class Crash(RuntimeError):
    pass


def crash_at(name):
    def hook(boundary, payload):
        if boundary == name:
            raise Crash(name)
    return hook


def confirm_outbox(v3):
    for row in v3.store.committed_outbox(unconfirmed_only=True):
        v3.store.confirm_outbox(row["outbox_id"], "edge-" + row["outbox_id"])


def accepted_request(v3, tmp_path, *, cycle_minutes=None):
    """Create verified Planning/Edge inputs; execution remains simulator-owned."""
    identity = v3.identity
    epoch = identity["session_epoch_utc"]
    now = int(v3.env.sim.now)
    mapping = identity["runtime_bindings"][0]
    due, expiry = iso(epoch, now), iso(epoch, now + 300)
    task = TaskRequest.build(
        site_id=identity["site_id"], deployment_id=identity["deployment_id"],
        simulation_env_id="v3-session-test", target_robot_id=mapping["robot_id"],
        target_incarnation="fixture-incarnation-001", task_type="COLLECT_BALLS_ZONE",
        zone_id=mapping["zone_id"], issued_at_utc=due, expires_at_utc=expiry,
        progress_window_s=60, issued_by="SIMULATION_TEST_ENTRY:manager",
    )
    cycle = cycle_minutes or {"travel": 1, "collect": 1, "return": 1, "unload": 1}
    evidence = {
        "value": {**cycle, "wash": 3, "supply": 1},
        "source_kind": "MEASURED", "source_ref": "synthetic cycle fixture",
        "observed_at_utc": iso(epoch, now - 1), "valid_until_utc": iso(epoch, now + 600),
    }
    source = {
        "request_id": "planning-input-001", "revision": 1, "input_digest": "b" * 64,
        "zones": [{"robot_id": mapping["robot_id"], "zone_id": mapping["zone_id"],
                   "cycle_minutes": evidence}],
    }
    schedule = {
        "robot_id": mapping["robot_id"], "zone_id": mapping["zone_id"],
        "due_at_utc": due, "expires_at_utc": expiry, "operator": "manager",
        "admission_reference": "confirmation-001",
    }
    planning = {
        "schema": "nxt-planning/v1", "environment": "SIMULATION", "latest_input": source,
        "plans": [{"plan_id": "plan-001", "version": 1, "input_revision": 1,
                   "input_digest": source["input_digest"],
                   "selection": {"robot_id": mapping["robot_id"], "zone_id": mapping["zone_id"],
                                 "start_at_utc": due}}],
        "confirmations": [{"confirmation_id": "confirmation-001", "plan_id": "plan-001",
                           "plan_version": 1, "input_revision": 1, "schedule_id": "schedule-001",
                           "schedule": schedule, "task_id": task.task_id, "task_created_at_utc": due}],
    }
    edge = JsonlJournal(tmp_path / "edge.jsonl")
    edge.append(RecordSpec("task_created", "EDGE", due, {
        "task_id": task.task_id, "schedule_id": "schedule-001", "request": task.to_dict(),
    }))
    binding = v3.store.bind_confirmed_tasks(planning, edge.read(), identity, now)[0]
    request = v3.execution_api.make_request(binding, "execution-request-001", due, expiry)
    v3.store.submit(request)
    v3.store.record_acceptance(request["execution_id"], "edge-accepted-001")
    return request


def candidate_execution(v3, request):
    robot_id = v3.identity["runtime_bindings"][0]["runtime_robot_id"]
    candidate = v3.env.sim._assignment_candidates.get(robot_id)
    return None if candidate is None else candidate.execution_id


def test_v3_identity_clock_and_exact_finite_endpoint(runner, tmp_path):
    compiled = compiled_fixture(end_minute=4)
    v3 = session(runner, tmp_path, compiled=compiled)
    assert v3.identity["schema"] == "nxt-whole-course-session/v3"
    assert {k: v3.identity[k] for k in ("series_id", "session_id", "round_id", "round_index")} == {
        "series_id": "series-v3-test", "session_id": "session-v3-test",
        "round_id": "round-v3-test", "round_index": 0,
    }
    assert v3.env.sim.now == 60
    assert simulation_utc(v3.identity, v3.env.sim.now) == "2026-09-16T00:01:00Z"
    while not v3.env.sim.facility_closed:
        v3.advance()
    assert v3.env.sim.now == v3.identity["session_end_sim_t_s"] == 240
    with pytest.raises(RuntimeError, match="completed"):
        v3.advance()


def test_advance_calls_policy_and_public_step_once_and_preserves_nonwait(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    probe = runner.V3Session(tmp_path, config(runner, compiled), compiled)
    probe.obs["robot_battery"][0] = 0.0
    policy_calls = 0
    step_calls = 0
    act = probe.policy.act
    step = probe.env.step

    def counted_policy(obs, info):
        nonlocal policy_calls
        policy_calls += 1
        return act(obs, info)

    def counted(action):
        nonlocal step_calls
        step_calls += 1
        return step(action)

    monkeypatch.setattr(probe.policy, "act", counted_policy)
    monkeypatch.setattr(probe.env, "step", counted)
    committed = probe.advance()
    decision = probe.store.replay_plan()[-1]["prepared"]["decision"]
    assert policy_calls == step_calls == 1
    assert decision["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert decision["original_action"] == decision["selected_action"]
    assert decision["selected_action"]["name"] != "Wait"
    assert committed["result"]["now_sim_t_s"] == 120


def test_no_requests_matches_direct_original_policy_trajectory(runner, tmp_path):
    compiled = compiled_fixture()
    first = session(runner, tmp_path / "v3", compiled=compiled)
    second = session(runner, tmp_path / "baseline", compiled=compiled)
    for _ in range(3):
        first.advance()
        obs, info = second.visible_policy_inputs()
        action = int(second.policy.act(obs, info))
        second.obs, _, _, _, second.info = second.env.step(action)
        second.step_count += 1
    assert first.runtime_digest() == second.runtime_digest()


def test_pause_is_business_state_not_wall_read_health(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    active = runner.run(tmp_path, cfg)
    before = active["now_sim_t_s"]
    atomic_json(tmp_path / "control.json", {"paused": True})
    paused = runner.run(tmp_path)
    assert paused["status"] == "PAUSED" and paused["now_sim_t_s"] == before
    snapshot = runner.read_execution_snapshot(
        tmp_path, server_time_utc="2030-01-01T00:00:00Z")
    assert snapshot["session_state"] == "PAUSED"
    assert snapshot["server_time_utc"] == "2030-01-01T00:00:00Z"
    assert snapshot["simulation_time_utc"] == simulation_utc(active, before)


def test_runtime_status_is_locked_detached_and_read_neutral(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    state = runner.run(tmp_path, cfg)

    def durable_bytes():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in sorted(tmp_path.rglob("*"))
            if path.is_file()
        }

    before = durable_bytes()
    first = runner.read_runtime_status(tmp_path, "picker-01")
    second = runner.read_runtime_status(tmp_path, "picker-01")

    assert first == second == {
        "session_id": cfg["session_id"],
        "round_id": cfg["round_id"],
        "robot_id": "picker-01",
        "simulation_time_utc": simulation_utc(state, state["now_sim_t_s"]),
        "session_state": "ACTIVE",
        "activity": "IDLE",
        "payload_balls": 0,
        "paused": False,
        "faulted": False,
        "estop_latched": False,
        "awaiting_human": False,
    }
    assert durable_bytes() == before
    assert read_record(tmp_path / "state.json")["replay_digest"] == state["replay_digest"]
    with pytest.raises(ValueError, match="binding"):
        runner.read_runtime_status(tmp_path, "unknown-robot")
    assert durable_bytes() == before

    runner.set_paused(tmp_path, True)
    paused_before = durable_bytes()
    paused = runner.read_runtime_status(tmp_path, "picker-01")
    assert paused["session_state"] == "PAUSED"
    assert paused["paused"] is True
    assert paused["simulation_time_utc"] == first["simulation_time_utc"]
    assert durable_bytes() == paused_before


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("policy", "committed policy prefix"),
        ("simulator", "committed simulator result"),
    ],
)
def test_runtime_status_committed_mismatch_is_read_only_but_run_seals(
    runner, tmp_path, monkeypatch, drift, message
):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)

    state_path = tmp_path / "state.json"
    journal_path = tmp_path / "collection-execution.jsonl"

    def durable_bytes():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in sorted(tmp_path.rglob("*"))
            if path.is_file()
        }

    state_before = state_path.read_bytes()
    journal_before = journal_path.read_bytes()
    root_before = durable_bytes()
    if drift == "policy":
        def mismatched_policy(session):
            robot_id = session.env.scenario.robot_ids[0]
            return session._action(
                session.env.catalog.index_of(f"pause_robot({robot_id})")
            )

        monkeypatch.setattr(runner.V3Session, "_policy_action", mismatched_policy)
    else:
        monkeypatch.setattr(
            runner.V3Session,
            "_post_state_digest",
            lambda _session, _executions: "d" * 64,
        )

    with pytest.raises(runner.ReplayMismatch, match=message):
        runner.read_runtime_status(tmp_path, "picker-01")

    assert state_path.read_bytes() == state_before
    assert journal_path.read_bytes() == journal_before
    assert durable_bytes() == root_before

    with pytest.raises(runner.ReplayMismatch, match=message):
        runner.run(tmp_path)

    assert state_path.read_bytes() == state_before
    assert journal_path.read_bytes() != journal_before
    assert b'"record_kind":"committed_replay_failure"' in journal_path.read_bytes()
    assert session(runner, tmp_path, compiled=compiled).store.recovery_state()[
        "status"
    ] == "REPLAY_MISMATCH"


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("policy", "committed policy prefix"),
        ("simulator", "committed simulator result"),
    ],
)
def test_execution_admission_seals_existing_committed_mismatch(
    runner, tmp_path, monkeypatch, drift, message
):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)
    store = session(runner, tmp_path, compiled=compiled).store
    record_kinds_before = [row.record_kind for row in store.journal.read()]

    with monkeypatch.context() as mismatch:
        if drift == "policy":
            def mismatched_policy(runtime):
                robot_id = runtime.env.scenario.robot_ids[0]
                return runtime._action(
                    runtime.env.catalog.index_of(f"pause_robot({robot_id})")
                )

            mismatch.setattr(runner.V3Session, "_policy_action", mismatched_policy)
        else:
            mismatch.setattr(
                runner.V3Session,
                "_post_state_digest",
                lambda _runtime, _executions: "d" * 64,
            )

        with pytest.raises(runner.ReplayMismatch, match=message):
            with runner.execution_admission(tmp_path):
                pytest.fail("mismatched prefix exposed authorization authority")

    restored = session(runner, tmp_path, compiled=compiled).store
    assert restored.recovery_state()["status"] == "REPLAY_MISMATCH"
    assert [row.record_kind for row in restored.journal.read()] == [
        *record_kinds_before,
        "committed_replay_failure",
    ]
    with pytest.raises(runner.ReplayMismatch, match="sealed"):
        with runner.execution_admission(tmp_path):
            pytest.fail("sealed prefix exposed authorization authority")


def test_execution_admission_does_not_initialize_a_missing_root(runner, tmp_path):
    missing = tmp_path / "missing-session"

    with pytest.raises(FileNotFoundError):
        with runner.execution_admission(missing):
            pytest.fail("missing session exposed authorization authority")

    assert not missing.exists()


def test_structural_recovery_status_reads_cursor_stale_without_replay(
    runner, tmp_path, monkeypatch
):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)
    with pytest.raises(Crash, match="after_commit"):
        runner.run(tmp_path, crash_hook=crash_at("after_commit"))
    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def forbid_step(_env, _action):
        pytest.fail("structural recovery status must not replay the simulator")

    monkeypatch.setattr(runner.RangeOpsEnv, "step", forbid_step)

    assert runner.structural_recovery_status(tmp_path) == "COMMITTED_CURSOR_STALE"
    assert {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == before


def test_runtime_continuation_omits_collect_and_only_handoffs_after_boundary(runner, tmp_path):
    v3 = session(runner, tmp_path)
    robot, zone = v3.env.scenario.robot_ids[0], v3.env.scenario.zone_ids[0]
    execution = {
        "execution_id": "c" * 64, "state": "RUNNING", "runtime_robot_id": robot,
        "runtime_zone_id": zone, "runtime_evidence": {"collection_exit_reason": None},
    }
    before = v3.runtime_view([execution])
    assert before["continuations"] == {}
    execution["runtime_evidence"]["collection_exit_reason"] = "ROBOT_PAYLOAD_FULL"
    after = v3.runtime_view([execution])
    continuation = after["continuations"][execution["execution_id"]]
    assert continuation["name"] == "SendToHandoff"
    assert continuation["robot_id"] == robot and continuation["target_id"] is None


def test_short_ticks_do_not_restart_an_inflight_handoff(runner, tmp_path):
    """A Wait tick may continue a stopped assignment, never an active task.

    Regression target: exposing SendToHandoff while the bound robot is already
    TRAVELING used to interrupt and resample the same loaded travel every tick.
    """

    compiled = compiled_fixture(end_minute=10)
    scenario = make_scenario("normal_weekday")
    scenario = scenario.model_copy(
        update={
            "name": "v3_short_tick_handoff",
            "hours": scenario.hours.model_copy(
                update={"open_minute": 1, "close_minute": 10}
            ),
            "episode": scenario.episode.model_copy(
                update={"control_interval_s": 15, "max_steps": 40}
            ),
            "robots": [
                scenario.robots[0].model_copy(
                    update={"payload_capacity_balls": 20}
                )
            ],
            "stations": [scenario.stations[0]],
        }
    )
    compiled["scenario"] = scenario.model_dump(mode="json")
    compiled["session_inputs"]["demand_by_minute"] = [0] * 9
    compiled["session_inputs"]["landing_zones_by_minute"] = [[] for _ in range(9)]
    compiled["session_inputs"]["collection_blocks"] = {
        zone_id: [] for zone_id in scenario.zone_ids
    }
    v3 = runner.V3Session(
        tmp_path,
        config(runner, compiled, control_interval_s=15),
        compiled,
    )
    assert type(v3.policy).__name__ == "JointDispatchPolicy"
    zone_id = v3.env.scenario.zone_ids[0]
    robot_id = v3.env.scenario.robot_ids[0]
    v3.env.sim.ledger.move(
        ledger_locations.DISPENSER, ledger_locations.zone_loc(zone_id), 20
    )

    sampled: list[SkillType] = []

    class CountingSkills(SkillOutcomeModel):
        def sample(self, request, _rng):
            sampled.append(request.skill)
            duration = {
                SkillType.TRAVEL: 40,
                SkillType.COLLECT_CYCLE: 5,
                SkillType.DOCK: 5,
                SkillType.UNLOAD: 5,
                SkillType.CHARGE_CONNECT: 5,
            }[request.skill]
            return SkillOutcome(True, duration, 1)

    v3.env.sim.skill_model = CountingSkills()
    request = accepted_request(
        v3,
        tmp_path / "planning",
        cycle_minutes={"travel": 2, "collect": 2, "return": 2, "unload": 2},
    )
    wait_index = v3.env.catalog.index_of("wait")

    first_tick = True
    while v3.store.replay()["executions"][request["execution_id"]]["state"] not in {
        "SUCCEEDED",
        "PARTIAL",
        "REJECTED",
        "MISSED",
        "FAILED",
        "INCONCLUSIVE",
    }:
        assert v3.env.sim.now < 600
        # Only the initial tick exposes the Wait slot that starts the request.
        # Every later tick uses the real JointDispatchPolicy and simulator mask.
        if first_tick:
            v3.info["action_mask"] = {wait_index: True}
            first_tick = False
        v3.advance()
        confirm_outbox(v3)

    row = v3.store.replay()["executions"][request["execution_id"]]
    selected_handoffs = [
        action
        for action in row["actions"]
        if action["selected_action"]["name"] == "SendToHandoff"
    ]
    handoff_starts = [
        event
        for event in v3.env.sim.events.to_dicts()
        if event["kind"] == "travel_started"
        and event["payload"].get("robot_id") == robot_id
        and event["payload"].get("dest")
        == ledger_locations.station_loc(v3.env.scenario.station_ids[0])
    ]

    assert row["actions"][0]["selection"] == "WAIT_SLOT"
    policy_handoffs = [
        action for action in selected_handoffs
        if action["original_action"]["name"] == "SendToHandoff"
    ]
    assert policy_handoffs
    assert all(
        action["selected_action"] == action["original_action"]
        and action["safety_shield"] == "ACCEPTED"
        for action in policy_handoffs
    )
    assert len(handoff_starts) == 1
    assert sampled.count(SkillType.TRAVEL) == 2
    assert sampled.count(SkillType.COLLECT_CYCLE) == 1
    assert sampled.count(SkillType.DOCK) == 1
    assert sampled.count(SkillType.UNLOAD) == 1
    assert v3.env.sim.metrics.loaded_travel_s == 40
    assert row["state"] == "SUCCEEDED"
    assert row["raw_quantity"]["balls"] == row["unload_quantity"]["balls"] == 20


def test_policy_provenance_has_no_public_injection_path(runner, tmp_path):
    compiled = compiled_fixture()
    assert "policy" not in inspect.signature(runner.V3Session).parameters
    with pytest.raises(TypeError, match="policy"):
        runner.V3Session(
            tmp_path / "forged", config(runner, compiled), compiled,
            policy=FixedPolicy(0),
        )
    v3 = session(runner, tmp_path / "fixed", compiled=compiled)
    assert type(v3.policy).__name__ == "JointDispatchPolicy"
    assert v3.store.policy_id == runner.POLICY_ID


def test_causal_digest_includes_visible_inputs_and_policy_state(runner, tmp_path):
    v3 = session(runner, tmp_path)
    evidence = v3._causal_post_state()
    initial_inventory = float(v3.obs["dispenser_inventory_frac"][0])
    assert set(evidence) == {
        "state_summary", "simulator_rng_state", "legacy_events", "assignment_snapshots", "policy_inputs",
        "metrics", "joint_metrics", "staff_work_snapshots", "step_count", "policy_state",
    }
    assert set(evidence["policy_inputs"]) == {"observation", "info"}
    assert isinstance(evidence["policy_inputs"]["info"]["action_mask"], dict)
    assert evidence["policy_state"]["candidate"]["candidate_id"] == "balanced"
    assert evidence["policy_state"]["version"] == "joint-dispatch/v0"
    assert evidence["policy_state"]["name"] == "balanced"
    assert evidence["policy_state"]["rng_state"] is not None
    json.dumps(evidence, sort_keys=True, allow_nan=False)

    original = v3.runtime_digest()
    v3.obs["dispenser_inventory_frac"][0] -= 0.125
    assert v3.runtime_digest() != original
    sensed_digest = v3.runtime_digest()
    v3.policy._previous_inventory = (float(v3.env.sim.now), 0.25)
    assert v3.runtime_digest() != sensed_digest
    assert evidence["policy_inputs"]["observation"]["dispenser_inventory_frac"][0] == initial_inventory


@pytest.mark.parametrize(
    "rng_name",
    ("_rng_demand", "_rng_skills", "_rng_failures", "_rng_sensors", "_rng_forecast"),
)
def test_runtime_digest_commits_every_named_simulator_rng(runner, tmp_path, rng_name):
    v3 = session(runner, tmp_path)
    before = v3.runtime_digest()

    getattr(v3.env.sim, rng_name).random()

    assert v3.runtime_digest() != before


def test_runtime_digest_is_rng_neutral(runner, tmp_path):
    v3 = session(runner, tmp_path)
    before = v3.env.sim.rng_state_snapshot()
    assert set(before) == {"demand", "skills", "failures", "sensors", "forecast"}

    first = v3.runtime_digest()
    second = v3.runtime_digest()

    assert first == second
    assert v3.env.sim.rng_state_snapshot() == before


def test_rejected_wait_slot_disarms_only_after_durable_commit(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(collection_blocked=True)
    v3 = session(runner, tmp_path, compiled=compiled)
    request = accepted_request(v3, tmp_path)
    disarmed = []
    commit = v3.store.commit_tick
    disarm = v3.env.disarm_collection_assignment

    def observe_disarm(execution_id):
        disarmed.append(execution_id)
        return disarm(execution_id)

    def observe_commit(prepared, **result):
        assert candidate_execution(v3, request) == request["execution_id"]
        assert disarmed == []
        return commit(prepared, **result)

    monkeypatch.setattr(v3.env, "disarm_collection_assignment", observe_disarm)
    monkeypatch.setattr(v3.store, "commit_tick", observe_commit)
    committed = v3.advance()

    assert committed["result"]["safety_shield"]["allowed"] is False
    assert disarmed == [request["execution_id"]]
    assert candidate_execution(v3, request) is None


def test_commit_failure_leaves_rejected_candidate_armed(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(collection_blocked=True)
    v3 = session(runner, tmp_path, compiled=compiled)
    request = accepted_request(v3, tmp_path)
    disarmed = []
    disarm = v3.env.disarm_collection_assignment

    def observe_disarm(execution_id):
        disarmed.append(execution_id)
        return disarm(execution_id)

    def fail_commit(prepared, **result):
        assert candidate_execution(v3, request) == request["execution_id"]
        raise OSError("commit failed")

    monkeypatch.setattr(v3.env, "disarm_collection_assignment", observe_disarm)
    monkeypatch.setattr(v3.store, "commit_tick", fail_commit)
    with pytest.raises(OSError, match="commit failed"):
        v3.advance()

    assert disarmed == []
    assert candidate_execution(v3, request) == request["execution_id"]
    assert v3.store.recovery_state()["status"] == "PREPARED_NO_COMMIT"


def test_after_commit_crash_replay_performs_rejected_candidate_cleanup(runner, tmp_path):
    compiled = compiled_fixture(collection_blocked=True)
    v3 = session(runner, tmp_path, compiled=compiled)
    request = accepted_request(v3, tmp_path)
    with pytest.raises(Crash, match="after_commit"):
        v3.advance(crash_hook=crash_at("after_commit"))

    assert v3.store.recovery_state()["status"] == "COMMITTED_CURSOR_STALE"
    assert candidate_execution(v3, request) == request["execution_id"]
    recovered = session(runner, tmp_path, compiled=compiled)
    recovered.recover()
    assert candidate_execution(recovered, request) is None
    assert len(recovered.store.replay_plan()) == 1


def test_after_step_recovery_commits_then_cleans_rejected_candidate(runner, tmp_path):
    compiled = compiled_fixture(collection_blocked=True)
    v3 = session(runner, tmp_path, compiled=compiled)
    request = accepted_request(v3, tmp_path)
    with pytest.raises(Crash, match="after_step"):
        v3.advance(crash_hook=crash_at("after_step"))

    assert v3.env.sim.now == 120
    assert candidate_execution(v3, request) == request["execution_id"]
    assert v3.store.recovery_state()["status"] == "PREPARED_NO_COMMIT"
    assert v3.store.replay_plan() == []
    recovered = session(runner, tmp_path, compiled=compiled)
    recovered.recover()
    assert candidate_execution(recovered, request) is None
    assert len(recovered.store.replay_plan()) == 1
    assert recovered.store.recovery_state()["status"] == "COMMITTED_OUTBOX_UNCONFIRMED"


@pytest.mark.parametrize("boundary, expected", [
    ("before_prepare", "NO_PREPARED"),
    ("after_prepare", "PREPARED_NO_COMMIT"),
    ("after_step", "PREPARED_NO_COMMIT"),
    ("after_commit", "COMMITTED_CURSOR_STALE"),
])
def test_crash_boundaries_recover_without_duplicate_commit(runner, tmp_path, boundary, expected):
    v3 = session(runner, tmp_path)
    before = (v3.env.sim.now, v3.step_count, len(v3.env.sim.events.to_dicts()))
    with pytest.raises(Crash, match=boundary):
        v3.advance(crash_hook=crash_at(boundary))
    after = (v3.env.sim.now, v3.step_count, len(v3.env.sim.events.to_dicts()))
    if boundary == "after_step":
        assert after[0] == before[0] + v3.config["control_interval_s"]
        assert after[1] == before[1] + 1
        assert after[2] > before[2]
    elif boundary == "after_prepare":
        assert after == before
    assert v3.store.recovery_state()["status"] == expected
    recovered = session(runner, tmp_path)
    recovered.recover()
    assert len(recovered.store.replay_plan()) == (0 if boundary == "before_prepare" else 1)
    assert recovered.store.recovery_state()["status"] == "NO_PREPARED"
    assert recovered.env.sim.now == (60 if boundary == "before_prepare" else 120)


def test_committed_outbox_unconfirmed_is_replayed_not_confirmed_or_restarted(runner, tmp_path):
    compiled = compiled_fixture(collection_blocked=True)
    v3 = session(runner, tmp_path, compiled=compiled)
    accepted_request(v3, tmp_path)
    with pytest.raises(Crash, match="after_cursor"):
        v3.advance(crash_hook=crash_at("after_cursor"))
    state = v3.store.recovery_state()
    assert state["status"] == "COMMITTED_OUTBOX_UNCONFIRMED" and state["unconfirmed_outbox"]
    before = deepcopy(state["unconfirmed_outbox"])
    recovered = session(runner, tmp_path, compiled=compiled)
    recovered.recover()
    after = recovered.store.recovery_state()
    assert after["status"] == "COMMITTED_OUTBOX_UNCONFIRMED"
    assert after["unconfirmed_outbox"] == before
    assert len(recovered.store.replay_plan()) == 1


def test_prepared_recovery_policy_mismatch_is_durably_sealed(runner, tmp_path):
    v3 = session(runner, tmp_path)
    with pytest.raises(Crash):
        v3.advance(crash_hook=crash_at("after_prepare"))
    bad = session(runner, tmp_path)
    bad.policy = FixedPolicy(bad.env.catalog.index_of(f"pause_robot({bad.env.scenario.robot_ids[0]})"))
    with pytest.raises(runner.ReplayMismatch):
        bad.recover()
    state = bad.store.recovery_state()
    assert state["status"] == "REPLAY_MISMATCH"
    assert state["replay_failure"]["observed_digest"] is not None


def test_replay_digest_matches_and_never_writes_second_logical_tick(runner, tmp_path):
    first = session(runner, tmp_path)
    for _ in range(3):
        first.advance()
    plan = first.store.replay_plan()
    digest = first.runtime_digest()
    second = session(runner, tmp_path)
    second.recover()
    assert second.runtime_digest() == digest
    assert second.store.replay_plan() == plan
    assert second.env.sim.now == first.env.sim.now
    assert second.store.recovery_state()["replay_failure"] is None


def test_committed_prefix_result_drift_fails_closed(runner, tmp_path, monkeypatch):
    first = session(runner, tmp_path)
    first.advance()
    plan = first.store.replay_plan()
    second = session(runner, tmp_path)
    monkeypatch.setattr(second, "_post_state_digest", lambda executions: "d" * 64)
    with pytest.raises(runner.ReplayMismatch, match="committed simulator result"):
        second.recover()
    failure = second.store.recovery_state()["replay_failure"]
    assert second.store.recovery_state()["status"] == "REPLAY_MISMATCH"
    assert failure["tick_sequence"] == 1
    assert failure["prepared_digest"] == runner.execution_api.digest(plan[0]["prepared"])
    assert failure["committed_digest"] == runner.execution_api.digest(plan[0]["committed"])
    assert failure["now_sim_t_s"] == plan[-1]["committed"]["result"]["now_sim_t_s"]
    with pytest.raises(runner.CollectionExecutionError, match="sealed"):
        second.store.snapshot(server_time_utc="2030-01-01T00:00:00Z")
    with pytest.raises(runner.CollectionExecutionError, match="sealed"):
        second.store.arbitrate(
            {"name": "Wait", "index": 0, "robot_id": None, "target_id": None},
            failure["now_sim_t_s"], second.runtime_view([]),
        )
    with pytest.raises(runner.ReplayMismatch, match="sealed"):
        session(runner, tmp_path).recover()


def test_committed_policy_mismatch_is_durably_sealed(runner, tmp_path):
    first = session(runner, tmp_path)
    first.advance()
    bad = session(runner, tmp_path)
    pause = bad.env.catalog.index_of(f"pause_robot({bad.env.scenario.robot_ids[0]})")
    bad.policy = FixedPolicy(pause)
    with pytest.raises(runner.ReplayMismatch, match="policy prefix"):
        bad.recover()
    state = bad.store.recovery_state()
    assert state["status"] == "REPLAY_MISMATCH"
    assert state["replay_failure"]["observed_digest"] is not None


def test_committed_selected_action_mismatch_is_durably_sealed(runner, tmp_path, monkeypatch):
    first = session(runner, tmp_path)
    first.advance()
    bad = session(runner, tmp_path)
    action = bad._action
    calls = 0

    def drift_after_original(index):
        nonlocal calls
        calls += 1
        value = action(index)
        if calls > 1:
            value["target_id"] = "drifted-target"
        return value

    monkeypatch.setattr(bad, "_action", drift_after_original)
    with pytest.raises(runner.ReplayMismatch, match="simulator action"):
        bad.recover()
    assert bad.store.recovery_state()["status"] == "REPLAY_MISMATCH"


def test_torn_and_drifted_prefixes_fail_loud(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path / "drift", cfg)
    changed = deepcopy(compiled)
    changed["scenario"]["name"] = "different"
    from scripts.joint_learning import write_record
    write_record(tmp_path / "drift" / "compiled.json", changed)
    with pytest.raises(ValueError, match="compiled"):
        runner.run(tmp_path / "drift")

    torn = session(runner, tmp_path / "torn")
    torn.advance()
    with (tmp_path / "torn" / "collection-execution.jsonl").open("ab") as stream:
        stream.write(b'{"torn":')
    with pytest.raises(ValueError):
        session(runner, tmp_path / "torn").recover()


def test_run_lock_immutable_identity_and_cursor_last(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    first = runner.run(tmp_path, cfg)
    assert read_record(tmp_path / "state.json") == first
    with pytest.raises(ValueError, match="immutable"):
        runner.run(tmp_path, {**cfg, "session_id": "changed-session"})
    with (tmp_path / ".session.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another worker"):
            runner.run(tmp_path)


def test_execution_admission_holds_session_lock_and_returns_no_step_authority(
        runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    state = runner.run(tmp_path, cfg)

    with runner.execution_admission(tmp_path) as admission:
        assert set(vars(admission)) == {"store", "identity", "now_sim_t_s"}
        assert admission.now_sim_t_s == state["now_sim_t_s"]
        assert admission.identity == read_record(tmp_path / "identity.json")
        admission.identity["session_id"] = "detached-copy"
        assert read_record(tmp_path / "identity.json")["session_id"] == cfg["session_id"]
        assert len(admission.store.replay_plan()) == state["step"]
        with pytest.raises(ValueError, match="another worker"):
            with runner.execution_admission(tmp_path):
                pass
    assert not hasattr(runner, "open_store")


def test_execution_admission_rejects_tampered_identity(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)
    identity = read_record(tmp_path / "identity.json")
    identity["session_id"] = "tampered-session"
    write_record(tmp_path / "identity.json", identity)
    with pytest.raises(ValueError, match="identity changed"):
        with runner.execution_admission(tmp_path):
            pass


def test_execution_admission_rejects_tampered_state_identity(runner, tmp_path, monkeypatch):
    compiled = compiled_fixture()
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)
    state = read_record(tmp_path / "state.json")
    state["session_id"] = "tampered-session"
    write_record(tmp_path / "state.json", state)
    with pytest.raises(ValueError, match="state identity"):
        with runner.execution_admission(tmp_path):
            pass


def test_read_snapshot_accepts_device_confirmation_after_saved_outbox_cursor(
        runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(collection_blocked=True)
    cfg = config(runner, compiled)
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    runner.run(tmp_path, cfg)
    v3 = session(runner, tmp_path, compiled=compiled)
    v3.recover()
    accepted_request(v3, tmp_path)
    v3.advance()
    write_record(tmp_path / "state.json", runner._state(v3, "OUTBOX_PENDING"))
    confirm_outbox(v3)

    snapshot = runner.read_execution_snapshot(
        tmp_path, server_time_utc="2030-01-01T00:00:00Z")
    assert snapshot["session_state"] == "ACTIVE"
    assert snapshot["executions"][0]["state"] == "REJECTED"


def test_series_v2_is_bounded_and_creates_only_v3_children(runner, series_runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(end_minute=3)
    child = config(runner, compiled, advance_steps=10)
    parent = deepcopy(series_runner.DEFAULT_CONFIG)
    parent.update(series_id="bounded-series", max_rounds=2, session_config=child)
    parent["session_config"]["series_id"] = "bounded-series"
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    one = series_runner.run(tmp_path, parent)
    two = series_runner.run(tmp_path)
    done = series_runner.run(tmp_path)
    assert done["schema"] == "nxt-course-session-series/v2"
    assert done["status"] == "SERIES_COMPLETE" and done["completed_rounds"] == 2
    assert [row["round_index"] for row in done["history"]] == [0, 1]
    for row in done["history"]:
        assert read_record(tmp_path / row["directory"] / "state.json")["schema"] == "nxt-whole-course-session/v3"
    assert one["completed_rounds"] <= two["completed_rounds"] <= done["completed_rounds"]


def test_series_rejects_current_round_skip_and_unknown_status(
        runner, series_runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(end_minute=3)
    child = config(runner, compiled, advance_steps=10)
    parent = deepcopy(series_runner.DEFAULT_CONFIG)
    parent.update(series_id="validated-series", max_rounds=3, session_config=child)
    parent["session_config"]["series_id"] = "validated-series"
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    one = series_runner.run(tmp_path, parent)
    assert one["completed_rounds"] == 1

    skipped = deepcopy(one)
    next_config = series_runner._round_config(parent, 1)
    skipped["current"] = {
        "round_index": 1, "session_id": next_config["session_id"],
        "round_id": next_config["round_id"], "directory": "round-0000000001",
        "status": "SESSION_COMPLETE",
    }
    write_record(tmp_path / "state.json", skipped)
    with pytest.raises(ValueError, match="current.*relationship"):
        series_runner.status(tmp_path)

    unknown = deepcopy(one)
    unknown["status"] = "ALIEN"
    write_record(tmp_path / "state.json", unknown)
    with pytest.raises(ValueError, match="status"):
        series_runner.status(tmp_path)


@pytest.mark.parametrize("mutation", ["alter_state", "delete_identity"])
def test_series_rejects_altered_or_deleted_completed_child_evidence(
        runner, series_runner, tmp_path, monkeypatch, mutation):
    compiled = compiled_fixture(end_minute=3)
    child = config(runner, compiled, advance_steps=10)
    parent = deepcopy(series_runner.DEFAULT_CONFIG)
    parent.update(series_id="child-evidence-series", max_rounds=1, session_config=child)
    parent["session_config"]["series_id"] = "child-evidence-series"
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))
    done = series_runner.run(tmp_path, parent)
    child_root = tmp_path / done["history"][0]["directory"]
    if mutation == "alter_state":
        state = read_record(child_root / "state.json")
        state["status"] = "CHUNK_COMPLETE"
        write_record(child_root / "state.json", state)
    else:
        (child_root / "identity.json").unlink()
    with pytest.raises(ValueError, match="completed V3 child"):
        series_runner.status(tmp_path)


def test_series_reconciles_child_complete_parent_stale_idempotently(
        runner, series_runner, tmp_path, monkeypatch):
    compiled = compiled_fixture(end_minute=3)
    child = config(runner, compiled, advance_steps=1)
    parent = deepcopy(series_runner.DEFAULT_CONFIG)
    parent.update(series_id="reconcile-series", max_rounds=1, session_config=child)
    parent["session_config"]["series_id"] = "reconcile-series"
    monkeypatch.setattr(runner, "compile_session", lambda _: deepcopy(compiled))

    partial = series_runner.run(tmp_path, parent)
    assert partial["completed_rounds"] == 0
    child_root = tmp_path / partial["current"]["directory"]
    assert runner.run(child_root)["status"] == "SESSION_COMPLETE"
    reconciled = series_runner.run(tmp_path)
    repeated = series_runner.run(tmp_path)
    assert reconciled["completed_rounds"] == 1
    assert reconciled["history"] == repeated["history"]
    assert reconciled["status"] == repeated["status"] == "SERIES_COMPLETE"


def test_v2_roots_and_contracts_remain_distinct():
    from scripts import course_session, course_session_series
    assert course_session.SCHEMA == "nxt-whole-course-session/v2"
    assert course_session_series.SCHEMA == "nxt-course-session-series/v1"
    assert not (ROOT / "scripts/course_session.py").samefile(ROOT / "scripts/course_session_v3.py")
