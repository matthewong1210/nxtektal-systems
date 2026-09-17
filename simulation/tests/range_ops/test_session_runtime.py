"""Continuous finite sessions preserve one ledger and observation-led work."""
import numpy as np
import pytest

from nxt_range_ops.config.models import EpisodeConfig, OperatingHoursConfig, TimeWindow
from nxt_range_ops.core.directives import AssignCollection, AssignStaffWork, SendToCharge, RequestHumanAssistance
from nxt_range_ops.core.entities import HumanAssistReason, RobotActivity
from nxt_range_ops.core.events import EventKind
from nxt_range_ops.core.session_inputs import SCHEMA, validate_session_inputs
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario


def scenario(*, opening=480, closing=490, stock_fraction=0.8, staff=1):
    base = make_scenario("normal_weekday")
    return base.model_copy(update={
        "hours": OperatingHoursConfig(open_minute=opening, close_minute=closing),
        "episode": EpisodeConfig(control_interval_s=60, max_steps=20_000),
        "total_balls": 1000, "initial_dispenser_frac": stock_fraction,
        "robots": [robot.model_copy(update={
            "idle_power_w": robot.idle_power_w.model_copy(update={"value": 0.0}),
            "mean_time_between_failures_h": robot.mean_time_between_failures_h.model_copy(update={"value": 1e12}),
        }) for robot in base.robots],
        "human_ops": base.human_ops.model_copy(update={"staff_count": staff}),
    })


def slot(name="job-1", *, checkpoint="h01-bunker", task="INSPECT", duration=5):
    return {"job_id": name, "hole_number": 1, "checkpoint_id": checkpoint,
            "duration_minutes": duration, "task_kind": task}


def inputs(s, *, days=2, slots=()):
    length = (days - 1) * 1440 + s.hours.close_minute - s.hours.open_minute
    return {"schema": SCHEMA, "environment": "SIMULATION", "days": days,
            "demand_by_minute": [0] * length,
            "landing_zones_by_minute": [[] for _ in range(length)],
            "collection_blocks": {zone: [] for zone in s.zone_ids},
            "staff_job_slots": list(slots)}


def shots(body, s, minute, targets):
    index = minute - s.hours.open_minute
    body["demand_by_minute"][index] = len(targets)
    body["landing_zones_by_minute"][index] = list(targets)


def validate(body, s):
    return validate_session_inputs(body, zone_ids=s.zone_ids,
                                   open_minute=s.hours.open_minute, close_minute=s.hours.close_minute)


@pytest.mark.parametrize("change", [
    lambda b: b.update(extra=True),
    lambda b: b.update(schema="unknown"),
    lambda b: b.update(environment="REAL"),
    lambda b: b.update(days=True),
    lambda b: b.update(days=0),
    lambda b: b.update(days=8),
    lambda b: b.update(demand_by_minute=[0]),
    lambda b: b.update(landing_zones_by_minute=[[]]),
    lambda b: b["demand_by_minute"].__setitem__(0, True),
    lambda b: b["demand_by_minute"].__setitem__(0, -1),
    lambda b: b["demand_by_minute"].__setitem__(0, 10_001),
    lambda b: b["landing_zones_by_minute"][0].append("unknown"),
    lambda b: b["collection_blocks"].pop(next(iter(b["collection_blocks"]))),
    lambda b: b["collection_blocks"][next(iter(b["collection_blocks"]))].append({"start_minute": 480, "end_minute": 480}),
    lambda b: b["staff_job_slots"].append(dict(b["staff_job_slots"][0])),
    lambda b: b["staff_job_slots"][0].update(available_minute=480),
    lambda b: b["staff_job_slots"][0].update(task_kind="ROBOT_MOW"),
    lambda b: b["staff_job_slots"][0].update(duration_minutes=float("nan")),
    lambda b: b["staff_job_slots"][0].update(duration_minutes=10**1000),
    lambda b: b["staff_job_slots"][0].update(duration_minutes=True),
    lambda b: b["staff_job_slots"][0].update(hole_number=19),
])
def test_strict_bounded_session_contract(change):
    s = scenario()
    body = inputs(s, slots=[slot()])
    change(body)
    with pytest.raises(ValueError):
        validate(body, s)


def test_night_demand_unknown_landings_and_detached_contract():
    s = scenario()
    body = inputs(s)
    shots(body, s, s.hours.close_minute, [s.zone_ids[0]])
    with pytest.raises(ValueError, match="nighttime"):
        validate(body, s)
    shots(body, s, s.hours.close_minute, [])
    shots(body, s, s.hours.open_minute, ["unknown"])
    with pytest.raises(ValueError, match="landing target"):
        validate(body, s)
    shots(body, s, s.hours.open_minute, [s.zone_ids[0]])
    body["collection_blocks"][s.zone_ids[0]] = [
        {"start_minute": 1440, "end_minute": 1450}, {"start_minute": 1450, "end_minute": 1460}]
    validated = validate(body, s)
    body["landing_zones_by_minute"][0][0] = "changed"
    assert validated["landing_zones_by_minute"][0] == [s.zone_ids[0]]
    assert validated["collection_blocks"][s.zone_ids[0]] == [{"start_minute": 1440, "end_minute": 1460}]


@pytest.mark.parametrize("constructor", [RangeOpsEnv, ActionCatalog, lambda s, **kw: RangeSimulation(s, 1, **kw)])
def test_joint_and_session_contracts_cannot_be_combined(constructor):
    s = scenario()
    with pytest.raises(ValueError, match="mutually exclusive"):
        constructor(s, joint_inputs={}, session_inputs=inputs(s))


def test_only_served_prefix_lands_and_weather_does_not_erase_requests():
    s = scenario(stock_fraction=0.003)
    body = inputs(s, days=1)
    z1, z2 = s.zone_ids[:2]
    shots(body, s, 480, [z1, z2, z1, z2, z2])
    body["collection_blocks"] = {z: [{"start_minute": 480, "end_minute": 485}] for z in s.zone_ids}
    sim = RangeSimulation(s, 42, session_inputs=body)
    before = sim.ledger.counts()
    assert not sim.apply_directive(AssignCollection(s.robot_ids[0], z1)).allowed
    sim.advance(1)
    served = [event for event in sim.events.records if event.kind is EventKind.DEMAND_SERVED]
    assert len(served) == 1
    assert served[0].payload == {"requested": 5, "served": 3, "by_zone": {z1: 2, z2: 1}}
    assert sim.ledger.count("zone:" + z1) == before["zone:" + z1] + 2
    assert sim.ledger.count("zone:" + z2) == before["zone:" + z2] + 1
    assert sim.dispenser_count() == 0
    assert sim.metrics.demand_balls_total == 5
    assert sim.metrics.demand_balls_served == 3
    sim.ledger.assert_conserved()


def test_targets_in_play_closure_are_rejected_instead_of_rewritten():
    s = scenario()
    s = s.model_copy(update={"zones": [zone.model_copy(update={
        "closure_windows": [TimeWindow(start_minute=480, end_minute=485)]}) for zone in s.zones]})
    body = inputs(s)
    shots(body, s, 1920, [s.zone_ids[0]])
    with pytest.raises(ValueError, match="play closure"):
        RangeSimulation(s, 1, session_inputs=body)


def test_one_ledger_and_battery_cross_midnight_without_reset():
    s = scenario()
    s = s.model_copy(update={"robots": [robot.model_copy(update={"initial_battery_frac": 0.61}) for robot in s.robots]})
    body = inputs(s)
    shots(body, s, 480, [s.zone_ids[0]] * 2)
    shots(body, s, 1920, [s.zone_ids[1]] * 4)
    sim = RangeSimulation(s, 7, session_inputs=body)
    ledger, opening_stock = sim.ledger, sim.dispenser_count()
    sim.advance(600)
    assert not sim.facility_open and not sim.facility_closed
    assert not sim.apply_directive(AssignCollection(s.robot_ids[0], s.zone_ids[0])).allowed
    sim.advance(1920 * 60 - sim.now)
    assert sim.facility_open and sim.session_progress["day_index"] == 1
    assert sim.dispenser_count() == opening_stock - 2
    assert sim.ledger is ledger
    assert all(robot.battery_frac == pytest.approx(0.61) for robot in sim.robot_snapshots())
    sim.advance(600)
    assert sim.dispenser_count() == opening_stock - 6
    assert sim.facility_closed and sim.session_progress["fraction"] == 1
    assert sim.metrics.open_minutes_elapsed == 20
    assert sim.now == sim.session_end_s
    sim.advance(100)
    assert sim.now == sim.session_end_s
    sim.ledger.assert_conserved()


def test_charger_queue_and_nonpreemptive_staff_work_survive_midnight():
    s = scenario(opening=1430, closing=1439)
    s = s.model_copy(update={
        "robots": [robot.model_copy(update={"initial_battery_frac": 0.2}) for robot in s.robots],
        "charger": s.charger.model_copy(update={"slots": 1,
            "charge_rate_w": s.charger.charge_rate_w.model_copy(update={"value": 100.0})}),
    })
    body = inputs(s, slots=[slot(duration=30), slot("job-2", checkpoint="h02-bunker")])
    sim = RangeSimulation(s, 2, session_inputs=body)
    for rid in s.robot_ids[:2]:
        assert sim.apply_directive(SendToCharge(rid)).allowed
    for jid in ("job-1", "job-2"):
        sim.admit_observation_job(jid, "obs-" + jid, "synthetic:" + jid, 1430, 1470)
    assert sim.apply_directive(AssignStaffWork("job-1")).allowed
    sim.advance(11 * 60)
    assert sim.session_progress["day_index"] == 1 and not sim.facility_open
    assert sim.staff_summary()[1] == 1
    assert sim.charger_queue_length() == 1
    assert sim.robot_or_none(s.robot_ids[0]).battery_frac > 0.2
    assert not sim.apply_directive(AssignStaffWork("job-2")).allowed
    sim.advance(20 * 60)
    assert sim.staff_summary()[1] == 0
    assert sim.staff_work_snapshots()[0]["status"] == "COMPLETED"
    assert sim.charger_queue_length() == 1


def test_observation_admission_is_hidden_idempotent_and_capacity_guarded():
    s = scenario()
    body = inputs(s, slots=[slot(), slot("job-2"), slot("job-3", task="RAKE_BUNKER")])
    env = RangeOpsEnv(s, session_inputs=body)
    legacy = ActionCatalog(s)
    assert env.catalog.specs[:len(legacy)] == legacy.specs
    obs, info = env.reset(seed=5)
    assert info["joint_ops"]["staff_jobs"] == []
    index = env.catalog.index_of("assign_staff_work(job-1)")
    assert not info["action_mask"][index]
    args = ("job-1", "observation-1", "synthetic:camera/frame-1", 480, 489)
    first = env.admit_observation_job(*args)
    assert first["disposition"] == "admitted"
    assert env.admit_observation_job(*args)["disposition"] == "duplicate"
    with pytest.raises(ValueError, match="conflicts"):
        env.admit_observation_job("job-2", *args[1:])
    with pytest.raises(ValueError, match="active job"):
        env.admit_observation_job("job-2", "observation-2", "synthetic:other", 480, 489)
    first["job"]["status"] = "COMPLETED"
    fresh_obs, info = env.refresh_observation_info()
    for key in obs:
        np.testing.assert_array_equal(obs[key], fresh_obs[key])
    assert info["action_mask"][index]
    assert info["joint_ops"]["staff_jobs"][0]["status"] == "PENDING"
    env.admit_observation_job("job-3", "observation-3", "synthetic:third", 480, 489)
    assert env.sim.apply_directive(AssignStaffWork("job-1")).allowed
    assert not env.sim.apply_directive(AssignStaffWork("job-3")).allowed
    assert env.sim.apply_directive(RequestHumanAssistance(s.robot_ids[0], HumanAssistReason.OTHER)).allowed
    env.sim.advance(1)
    assert env.sim.staff_summary() == (1, 1, 1)


@pytest.mark.parametrize("args", [
    ("job-1", "obs-1", "synthetic:image", 481, 489),
    ("job-1", "obs-1", "file:real.jpg", 480, 489),
    ("job-1", "obs-1", "synthetic:image", True, 489),
    ("job-1", "obs-1", "synthetic:image", 480, 479),
    ("job-1", "obs-1", "synthetic:image", 480, 2000),
    ("unknown", "obs-1", "synthetic:image", 480, 489),
])
def test_invalid_or_future_observations_cannot_release_slots(args):
    s = scenario()
    env = RangeOpsEnv(s, session_inputs=inputs(s, slots=[slot()]))
    env.reset(seed=2)
    with pytest.raises(ValueError):
        env.admit_observation_job(*args)
    assert env.sim.staff_work_snapshots() == []


def test_completed_work_is_not_repair_verification_and_allows_new_observed_job():
    s = scenario()
    env = RangeOpsEnv(s, session_inputs=inputs(s, slots=[slot(task="RAKE_BUNKER", duration=1), slot("job-2", task="RAKE_BUNKER")]))
    env.reset(seed=2)
    args = ("job-1", "obs-1", "synthetic:frame-1", 480, 489)
    env.admit_observation_job(*args)
    env.step(env.catalog.index_of("assign_staff_work(job-1)"))
    env.step(0)
    assert env.admit_observation_job(*args)["disposition"] == "duplicate"
    completed = [event for event in env.sim.events.records if event.kind is EventKind.STAFF_WORK_COMPLETED]
    assert completed[0].payload["task_kind"] == "RAKE_BUNKER"
    assert completed[0].payload["repair_verified"] is False
    assert env.admit_observation_job("job-2", "obs-2", "synthetic:frame-2", 482, 490)["disposition"] == "admitted"


def test_refresh_and_prefix_replay_preserve_sensor_rng_actions_and_events():
    s = scenario(opening=1437, closing=1439)
    body = inputs(s, slots=[slot(duration=4)])
    a, b = RangeOpsEnv(s, session_inputs=body), RangeOpsEnv(s, session_inputs=body)
    for env in (a, b):
        env.reset(seed=98)
        env.admit_observation_job("job-1", "obs-1", "synthetic:frame-1", 1437, 1445)
    for step in range(1442):
        if step % 17 == 0:
            for _ in range(3):
                a.refresh_observation_info()
        action = a.catalog.index_of("assign_staff_work(job-1)") if step == 0 else 0
        left, right = a.step(action), b.step(action)
        for key in left[0]:
            np.testing.assert_array_equal(left[0][key], right[0][key])
        assert left[1:4] == right[1:4]
        assert a.observation_space.contains(left[0])
        if left[2]:
            assert left[4]["termination_reason"] == "session_complete"
            break
    assert a.sim.facility_closed
    assert a.sim.events.to_dicts() == b.sim.events.to_dicts()
    assert a.sim.ledger.counts() == b.sim.ledger.counts()
    with pytest.raises(RuntimeError, match="completed"):
        a.step(0)


def test_none_session_is_identical_to_the_legacy_path():
    s = scenario()
    a, b = RangeOpsEnv(s), RangeOpsEnv(s, session_inputs=None)
    for env in (a, b):
        env.reset(seed=11)
    for _ in range(10):
        left, right = a.step(0), b.step(0)
        for key in left[0]:
            np.testing.assert_array_equal(left[0][key], right[0][key])
        assert left[1:4] == right[1:4]
        assert "session_progress" not in left[4]
    assert a.sim.events.to_dicts() == b.sim.events.to_dicts()
    assert a.sim.ledger.counts() == b.sim.ledger.counts()


def test_observation_releases_at_receipt_and_remains_pending_until_daytime():
    s = scenario()
    sim = RangeSimulation(s, 2, session_inputs=inputs(s, slots=[slot()]))
    sim.advance(30 * 60)
    result = sim.admit_observation_job("job-1", "obs-1", "synthetic:delayed", 481, 489)
    assert result["job"]["available_minute"] == 510
    assert result["job"]["captured_minute"] == 481
    assert not sim.apply_directive(AssignStaffWork("job-1")).allowed
    assert sim.joint_metrics["staff_jobs_overdue"] == 1
    sim.advance(1920 * 60 - sim.now)
    assert sim.apply_directive(AssignStaffWork("job-1")).allowed


def test_no_new_observation_after_final_close_but_identical_retry_is_valid():
    s = scenario()
    sim = RangeSimulation(s, 2, session_inputs=inputs(s, days=1, slots=[slot(), slot("job-2")]))
    args = ("job-1", "obs-1", "synthetic:frame", 480, 489)
    sim.admit_observation_job(*args)
    sim.advance(600)
    assert sim.admit_observation_job(*args)["disposition"] == "duplicate"
    with pytest.raises(ValueError, match="complete"):
        sim.admit_observation_job("job-2", "obs-2", "synthetic:later", 489, 490)


def test_round_the_clock_schedule_has_no_artificial_midnight_closure():
    s = scenario(opening=0, closing=1440)
    sim = RangeSimulation(s, 2, session_inputs=inputs(s))
    sim.advance(1440 * 60 + 1)
    assert sim.facility_open
    assert not any(event.kind is EventKind.COLLECTION_ACCESS_BLOCKED for event in sim.events.records)
