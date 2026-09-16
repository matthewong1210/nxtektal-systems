"""Joint inputs preserve demand, conservation and one shared staff capacity."""
import numpy as np
import pytest

from nxt_range_ops.config.models import OperatingHoursConfig
from nxt_range_ops.core.directives import AssignCollection, AssignStaffWork, RequestHumanAssistance
from nxt_range_ops.core.entities import HumanAssistReason, RobotActivity
from nxt_range_ops.core.events import EventKind
from nxt_range_ops.core.joint_inputs import validate_joint_inputs
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.core.skills import SkillOutcome, SkillOutcomeModel, SkillType
from nxt_range_ops.env.actions import ActionCatalog
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.baselines import make_baseline
from nxt_range_ops.scenarios.generators import make_scenario


def scenario(*, staff=1, minutes=30):
    base = make_scenario("normal_weekday")
    human = base.human_ops.model_copy(update={
        "staff_count": staff,
        "response_delay_s": base.human_ops.response_delay_s.model_copy(update={"value": 60.0}),
        "fix_duration_s": base.human_ops.fix_duration_s.model_copy(update={"value": 120.0}),
    })
    return base.model_copy(update={
        "hours": OperatingHoursConfig(open_minute=480, close_minute=480 + minutes),
        "human_ops": human,
    })


def job(job_id="inspection-1", *, available=480, duration=10, deadline=490):
    return {"job_id": job_id, "available_minute": available, "duration_minutes": duration,
            "deadline_minute": deadline, "hole_number": 1, "checkpoint_id": "h1-bunker"}


def inputs(s, *, demand=0, jobs=(), blocks=None):
    return {"schema": "nxt-joint-scenario-inputs/v0", "environment": "SIMULATION",
            "demand_by_minute": [demand] * (s.hours.close_minute - s.hours.open_minute),
            "collection_blocks": {z: list(blocks or []) for z in s.zone_ids},
            "staff_jobs": list(jobs)}


def validate(body, s):
    return validate_joint_inputs(body, zone_ids=s.zone_ids, open_minute=s.hours.open_minute,
                                 close_minute=s.hours.close_minute)


class FixedSkills(SkillOutcomeModel):
    def __init__(self, travel_s=1.0, collection_s=20.0):
        self.travel_s = travel_s
        self.collection_s = collection_s

    def sample(self, request, rng):
        duration = self.travel_s if request.skill is SkillType.TRAVEL else self.collection_s
        return SkillOutcome(success=True, duration_s=duration, energy_wh=0.0)


@pytest.mark.parametrize("mutation", [
    lambda b: b.update(extra=True),
    lambda b: b.update(environment="REAL"),
    lambda b: b.update(schema="unknown"),
    lambda b: b.update(demand_by_minute=[0]),
    lambda b: b["demand_by_minute"].__setitem__(0, True),
    lambda b: b["demand_by_minute"].__setitem__(0, -1),
    lambda b: b["demand_by_minute"].__setitem__(0, 1.0),
    lambda b: b["collection_blocks"].pop(next(iter(b["collection_blocks"]))),
    lambda b: b["collection_blocks"].update(unknown=[]),
    lambda b: b["collection_blocks"][next(iter(b["collection_blocks"]))].append(
        {"start_minute": 480.0, "end_minute": 485}),
    lambda b: b["collection_blocks"][next(iter(b["collection_blocks"]))].append(
        {"start_minute": 485, "end_minute": 485}),
    lambda b: b["staff_jobs"].append(dict(b["staff_jobs"][0])),
    lambda b: b["staff_jobs"][0].update(extra="hidden truth"),
    lambda b: b["staff_jobs"][0].update(available_minute=479),
    lambda b: b["staff_jobs"][0].update(available_minute=510),
    lambda b: b["staff_jobs"][0].update(deadline_minute=511),
    lambda b: b["staff_jobs"][0].update(deadline_minute=479),
    lambda b: b["staff_jobs"][0].update(hole_number=19),
    lambda b: b["staff_jobs"][0].update(hole_number=True),
    lambda b: b["staff_jobs"][0].update(duration_minutes=True),
    lambda b: b["staff_jobs"][0].update(duration_minutes=float("nan")),
    lambda b: b["staff_jobs"][0].update(duration_minutes=float("inf")),
    lambda b: b["staff_jobs"][0].update(duration_minutes=0),
    lambda b: b["staff_jobs"][0].update(duration_minutes=10**1000),
    lambda b: b["staff_jobs"][0].update(checkpoint_id=""),
])
def test_strict_joint_contract(mutation):
    s = scenario()
    body = inputs(s, jobs=[job()])
    mutation(body)
    with pytest.raises(ValueError):
        validate(body, s)


def test_union_access_windows_and_detach_inputs():
    s = scenario()
    body = inputs(s, jobs=[job()], blocks=[
        {"start_minute": 482, "end_minute": 488},
        {"start_minute": 480, "end_minute": 485},
        {"start_minute": 488, "end_minute": 489},
    ])
    normalized = validate(body, s)
    assert normalized["collection_blocks"][s.zone_ids[0]] == [{"start_minute": 480, "end_minute": 489}]
    body["staff_jobs"][0]["job_id"] = "edited"
    body["demand_by_minute"][0] = 900
    assert normalized["staff_jobs"][0]["job_id"] == "inspection-1"
    assert normalized["demand_by_minute"][0] == 0


def test_collection_denial_preserves_play_demand_and_conserved_landings():
    s = scenario()
    sim = RangeSimulation(s, 7, joint_inputs=inputs(s, demand=100, blocks=[
        {"start_minute": 480, "end_minute": 485}]))
    before = sim.dispenser_count()
    decision = sim.apply_directive(AssignCollection(s.robot_ids[0], s.zone_ids[0]))
    assert not decision.allowed and "access" in decision.reason
    sim.advance(300)
    assert all(z.is_open for z in sim.zone_snapshots())
    assert sim.metrics.demand_balls_total == 500
    assert sim.dispenser_count() == before - 500
    assert sim.demand_history() == [{"minute": m, "requested": 100} for m in range(480, 485)]
    assert sim.joint_metrics["collection_access_denied_s"] == 300 * len(s.zone_ids)
    assert sim.apply_directive(AssignCollection(s.robot_ids[0], s.zone_ids[0])).allowed
    sim.ledger.assert_conserved()


@pytest.mark.parametrize("travel_s,collection_s,expected_collected", [(120, 20, False), (1, 20, True), (1, 59, False)])
def test_weather_interrupts_travel_or_collection_without_extra_balls(travel_s, collection_s, expected_collected):
    s = scenario()
    sim = RangeSimulation(s, 2, FixedSkills(travel_s, collection_s), joint_inputs=inputs(s, blocks=[
        {"start_minute": 481, "end_minute": 485}]))
    rid, zid = s.robot_ids[0], s.zone_ids[0]
    assert sim.apply_directive(AssignCollection(rid, zid)).allowed
    sim.advance(61)
    robot = sim.robot_or_none(rid)
    assert robot.activity is RobotActivity.IDLE
    assert robot.assigned_zone is None
    assert (robot.payload_balls > 0) is expected_collected
    conserved = sim.ledger.counts()
    sim.advance(60)
    assert sim.ledger.counts() == conserved
    assert not sim.apply_directive(AssignCollection(rid, zid)).allowed
    sim.ledger.assert_conserved()


def test_staff_raw_calls_reserve_immediately_and_share_recovery_capacity():
    s = scenario()
    sim = RangeSimulation(s, 3, joint_inputs=inputs(s, demand=2000, jobs=[job(), job("inspection-2")]))
    assert sim.apply_directive(AssignStaffWork("inspection-1")).allowed
    assert sim.staff_summary() == (1, 1, 0)
    assert not sim.apply_directive(AssignStaffWork("inspection-2")).allowed
    assert not sim.apply_directive(AssignStaffWork("inspection-1")).allowed
    assert sim.staff_work_snapshots()[0]["status"] == "ASSIGNED"
    assert sim.apply_directive(RequestHumanAssistance(s.robot_ids[0], HumanAssistReason.OTHER)).allowed
    sim.advance(1)
    assert sim.staff_summary() == (1, 1, 1)
    assert sim.staff_work_snapshots()[0]["status"] == "IN_PROGRESS"
    sim.advance(598)
    assert sim.staff_summary() == (1, 1, 1)
    assert sim.joint_metrics["course_staff_busy_s"] == 599
    sim.advance(2)
    assert sim.staff_summary() == (1, 1, 0)
    assert sim.staff_work_snapshots()[0]["status"] == "COMPLETED"
    assert not sim.apply_directive(AssignStaffWork("inspection-2")).allowed
    assert sim.metrics.human_interventions_completed == 0
    complete = [e for e in sim.events.records if e.kind is EventKind.STAFF_WORK_COMPLETED]
    assert len(complete) == 1 and complete[0].payload["repair_verified"] is False
    sim.advance(180)
    assert sim.staff_summary() == (1, 0, 0)
    assert sim.metrics.human_interventions_completed == 1
    assert sim.apply_directive(AssignStaffWork("inspection-2")).allowed
    sim.ledger.assert_conserved()


def test_staff_cannot_jump_pending_same_timestamp_robot_assistance():
    s = scenario(staff=2)
    sim = RangeSimulation(s, 3, joint_inputs=inputs(s, jobs=[job()]))
    assert sim.apply_directive(RequestHumanAssistance(s.robot_ids[0], HumanAssistReason.OTHER)).allowed
    assert not sim.apply_directive(AssignStaffWork("inspection-1")).allowed
    sim.advance(1)
    assert sim.staff_summary() == (2, 1, 0)
    assert sim.apply_directive(AssignStaffWork("inspection-1")).allowed
    assert sim.staff_summary() == (2, 2, 0)


def test_catalog_preserves_indices_and_only_observed_jobs_are_visible():
    s = scenario()
    body = inputs(s, jobs=[job("future", available=485), job("current")])
    env = RangeOpsEnv(s, joint_inputs=body)
    legacy = ActionCatalog(s)
    assert env.catalog.specs[:len(legacy)] == legacy.specs
    assert [spec.name for spec in env.catalog.specs[len(legacy):]] == [
        "assign_staff_work(current)", "assign_staff_work(future)"]
    obs, info = env.reset(seed=4)
    assert [j["job_id"] for j in info["joint_ops"]["staff_jobs"]] == ["current"]
    assert not info["action_mask"][env.catalog.index_of("assign_staff_work(future)")]
    assert "joint_metrics" not in info["joint_ops"]
    assert not env.sim.apply_directive(AssignStaffWork("future")).allowed
    info["joint_ops"]["staff_jobs"][0]["status"] = "COMPLETED"
    assert env.sim.staff_work_snapshots()[0]["status"] == "PENDING"
    for _ in range(5):
        obs, _, _, _, info = env.step(0)
    assert [j["job_id"] for j in info["joint_ops"]["staff_jobs"]] == ["current", "future"]
    assert info["action_mask"][env.catalog.index_of("assign_staff_work(future)")]


def test_pending_wait_and_partial_work_count_at_episode_boundary():
    s = scenario(minutes=10)
    sim = RangeSimulation(s, 9, joint_inputs=inputs(s, jobs=[
        job("busy", duration=20, deadline=485), job("pending", deadline=486)]))
    assert sim.apply_directive(AssignStaffWork("busy")).allowed
    sim.advance(600)
    metrics = sim.joint_metrics
    assert metrics["staff_jobs_completed"] == 0
    assert metrics["staff_jobs_pending"] == metrics["staff_jobs_in_progress"] == 1
    assert metrics["staff_jobs_overdue"] == 2
    assert metrics["course_staff_busy_s"] == 600
    assert metrics["course_staff_wait_s"] == 600
    assert metrics["course_staff_deadline_late_s"] == 540
    assert not sim.apply_directive(AssignStaffWork("pending")).allowed


def test_paired_requested_demand_does_not_depend_on_policy_served_balls():
    s = scenario(minutes=120).model_copy(update={"initial_dispenser_frac": 0.03})
    body = inputs(s, demand=300)
    histories, served = [], []
    for collect in (False, True):
        env = RangeOpsEnv(s, joint_inputs=body)
        policy = make_baseline("inventory_threshold", s, env.catalog, seed=11)
        obs, info = env.reset(seed=11)
        for _ in range(120):
            action = policy.act(obs, info) if collect else 0
            obs, _, ended, truncated, info = env.step(action)
            if ended or truncated:
                break
        histories.append(env.sim.demand_history())
        served.append(env.sim.metrics.demand_balls_served)
        env.sim.ledger.assert_conserved()
    assert served[0] != served[1]
    assert histories[0] == histories[1] == [{"minute": m, "requested": 300} for m in range(480, 600)]


def test_explicit_none_is_legacy_observation_info_actions_and_rng():
    s = scenario()
    default, explicit = RangeOpsEnv(s), RangeOpsEnv(s, joint_inputs=None)
    assert default.catalog.specs == explicit.catalog.specs
    for env in (default, explicit):
        env.reset(seed=23)
    for _ in range(30):
        a, b = default.step(0), explicit.step(0)
        for key in a[0]:
            np.testing.assert_array_equal(a[0][key], b[0][key])
        assert "joint_ops" not in a[4] and "joint_ops" not in b[4]
        assert a[1:4] == b[1:4]
    assert default.sim.events.to_dicts() == explicit.sim.events.to_dicts()
    assert default.sim.ledger.counts() == explicit.sim.ledger.counts()
