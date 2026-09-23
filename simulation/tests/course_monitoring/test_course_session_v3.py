"""V3 is the sole policy/step owner and replays committed simulator evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path

import pytest

from nxt_edge_task.contracts import TaskRequest
from nxt_edge_task.journal import JsonlJournal, RecordSpec
from nxt_range_ops.scenarios.generators import make_scenario
from scripts.course_collection_execution import simulation_utc
from scripts.joint_learning import atomic_json, read_record


ROOT = Path(__file__).resolve().parents[2]


def iso(epoch: str, seconds: int) -> str:
    value = datetime.fromisoformat(epoch.replace("Z", "+00:00")) + timedelta(seconds=seconds)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def compiled_fixture(*, end_minute: int = 12) -> dict:
    base = make_scenario("normal_weekday")
    scenario = base.model_copy(update={
        "name": "v3_session_test",
        "hours": base.hours.model_copy(update={"open_minute": 1, "close_minute": end_minute}),
        "episode": base.episode.model_copy(update={"control_interval_s": 60, "max_steps": end_minute + 2}),
        "initial_dispenser_frac": 1.0,
    })
    length = end_minute - 1
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
            "collection_blocks": {zone_id: [] for zone_id in scenario.zone_ids},
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


def session(runner, tmp_path, *, compiled=None, policy=None):
    compiled = compiled or compiled_fixture()
    cfg = config(runner, compiled)
    return runner.V3Session(tmp_path, cfg, compiled, policy=policy)


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
    pause = probe.env.catalog.index_of(f"pause_robot({probe.env.scenario.robot_ids[0]})")
    policy = FixedPolicy(pause)
    probe.policy = policy
    calls = 0
    step = probe.env.step

    def counted(action):
        nonlocal calls
        calls += 1
        return step(action)

    monkeypatch.setattr(probe.env, "step", counted)
    committed = probe.advance()
    decision = probe.store.replay_plan()[-1]["prepared"]["decision"]
    assert policy.calls == calls == 1
    assert decision["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert decision["original_action"] == decision["selected_action"]
    assert decision["selected_action"]["index"] == pause
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
    snapshot = runner.open_store(tmp_path).snapshot(
        server_time_utc="2030-01-01T00:00:00Z", session_state="PAUSED")
    assert snapshot["session_state"] == "PAUSED"
    assert snapshot["server_time_utc"] == "2030-01-01T00:00:00Z"
    assert snapshot["simulation_time_utc"] == simulation_utc(active, before)


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


@pytest.mark.parametrize("boundary, expected", [
    ("before_prepare", "NO_PREPARED"),
    ("after_prepare", "PREPARED_NO_COMMIT"),
    ("after_step", "PREPARED_NO_COMMIT"),
    ("after_commit", "COMMITTED_CURSOR_STALE"),
])
def test_crash_boundaries_recover_without_duplicate_commit(runner, tmp_path, boundary, expected):
    v3 = session(runner, tmp_path)
    with pytest.raises(Crash, match=boundary):
        v3.advance(crash_hook=crash_at(boundary))
    assert v3.store.recovery_state()["status"] == expected
    recovered = session(runner, tmp_path)
    recovered.recover()
    assert len(recovered.store.replay_plan()) == (0 if boundary == "before_prepare" else 1)
    assert recovered.store.recovery_state()["status"] == "NO_PREPARED"
    assert recovered.env.sim.now == (60 if boundary == "before_prepare" else 120)


def test_committed_outbox_unconfirmed_is_replayed_not_confirmed_or_restarted(runner, tmp_path):
    v3 = session(runner, tmp_path, policy=FixedPolicy(0))
    accepted_request(v3, tmp_path)
    with pytest.raises(Crash, match="after_cursor"):
        v3.advance(crash_hook=crash_at("after_cursor"))
    state = v3.store.recovery_state()
    assert state["status"] == "COMMITTED_OUTBOX_UNCONFIRMED" and state["unconfirmed_outbox"]
    before = deepcopy(state["unconfirmed_outbox"])
    recovered = session(runner, tmp_path, policy=FixedPolicy(0))
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


def test_committed_prefix_result_drift_fails_closed(runner, tmp_path, monkeypatch):
    first = session(runner, tmp_path)
    first.advance()
    second = session(runner, tmp_path)
    monkeypatch.setattr(second, "_post_state_digest", lambda executions: "d" * 64)
    with pytest.raises(runner.ReplayMismatch, match="committed simulator result"):
        second.recover()
    assert len(second.store.replay_plan()) == 1


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


def test_v2_roots_and_contracts_remain_distinct():
    from scripts import course_session, course_session_series
    assert course_session.SCHEMA == "nxt-whole-course-session/v2"
    assert course_session_series.SCHEMA == "nxt-course-session-series/v1"
    assert not (ROOT / "scripts/course_session.py").samefile(ROOT / "scripts/course_session_v3.py")
