"""Ledger-backed assignment evidence; control still enters through env.step."""
from __future__ import annotations

import hashlib
import json

import pytest

from nxt_range_ops.core import ledger as locations
from nxt_range_ops.core.directives import SendToHandoff
from nxt_range_ops.core.entities import RobotActivity
from nxt_range_ops.core.skills import SkillOutcome, SkillOutcomeModel, SkillType
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.scenarios.generators import make_scenario


class FixedSkills(SkillOutcomeModel):
    def sample(self, request, rng):
        return SkillOutcome(True, 10 if request.skill is SkillType.COLLECT_CYCLE else 1, 0)


def environment(*, balls=100, deadline=100, close_minutes=10, access=False, enabled=True, wrong_station=False):
    base = make_scenario("normal_weekday")
    scenario = base.model_copy(update={
        "robots": [base.robots[0].model_copy(update={"payload_capacity_balls": 20})],
        "stations": ([base.stations[0], base.stations[0].model_copy(update={"station_id": "zz-other"})]
                     if wrong_station else [base.stations[0]]),
        "hours": base.hours.model_copy(update={"close_minute": base.hours.open_minute + close_minutes}),
        "episode": base.episode.model_copy(update={"control_interval_s": 15}),
        "skills": base.skills.model_copy(update={"collect_cycle_balls": 7}),
    })
    zone = scenario.zone_ids[0]
    start = scenario.hours.open_minute
    joint = {"schema": "nxt-joint-scenario-inputs/v0", "environment": "SIMULATION",
             "demand_by_minute": [0] * close_minutes, "staff_jobs": [],
             "collection_blocks": {z: ([{"start_minute": start + 1, "end_minute": start + 2}]
                                      if access and z == zone else []) for z in scenario.zone_ids}}
    evidence_option = (
        {} if enabled is None else {"collection_assignment_evidence": enabled}
    )
    env = RangeOpsEnv(
        scenario,
        lambda _: FixedSkills(),
        joint_inputs=joint,
        **evidence_option,
    )
    env.reset(seed=53)
    for z in scenario.zone_ids:
        env.sim.ledger.move(locations.zone_loc(z), locations.DISPENSER, scenario.total_balls)
    env.sim.ledger.move(locations.DISPENSER, locations.zone_loc(zone), balls)
    if enabled:
        env.arm_collection_assignment("execution-1", scenario.robot_ids[0], zone,
                                      scenario.station_ids[-1], env.sim.now + deadline)
    return env


class LongHandoffSkills(SkillOutcomeModel):
    def __init__(self):
        self.sampled = []

    def sample(self, request, rng):
        self.sampled.append(request.skill)
        duration = 40 if request.skill is SkillType.TRAVEL else 100
        return SkillOutcome(True, duration, 1)


def step(env, verb="wait", zone=None):
    rid = env.scenario.robot_ids[0]
    name = ("wait" if verb == "wait" else
            f"{verb}({rid},{zone or env.scenario.zone_ids[0]})" if verb in ("assign_collection", "reassign_robot")
            else f"{verb}({rid})")
    return env.step(env.catalog.index_of(name))


def snapshot(env):
    return env.collection_assignment_snapshot("execution-1")


def finish_collection(env):
    step(env, "assign_collection")
    step(env)
    step(env)


def test_only_final_shield_accepted_exact_match_starts_assignment():
    env = environment()
    assert snapshot(env) is None
    env.sim._zones[env.scenario.zone_ids[0]].is_open = False
    result = step(env, "assign_collection")
    assert not result[-1]["shield"]["allowed"]
    assert snapshot(env) is None
    assert env.sim.metrics.balls_collected == 0


def test_other_zone_does_not_consume_candidate():
    env = environment()
    step(env, "assign_collection", env.scenario.zone_ids[1])
    assert snapshot(env) is None
    step(env, "assign_collection")
    assert snapshot(env)["events"][0]["kind"] == "ASSIGNMENT_STARTED"


def test_full_collection_then_actual_generic_handoff_has_complete_detached_evidence():
    env = environment()
    finish_collection(env)
    before = snapshot(env)
    assert before["raw_collected_balls"] == 20
    assert before["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert before["terminal_reason"] is None
    step(env, "send_to_handoff")
    evidence = snapshot(env)
    assert evidence["terminal_reason"] == "UNLOADED_ALL_COLLECTED_BALLS"
    assert evidence["raw_collected_balls"] == evidence["unloaded_balls"] == 20
    transfers = [e for e in evidence["events"] if e["kind"] == "UNLOADED_TO_STATION"]
    assert transfers[0]["station_id"] == env.scenario.station_ids[0]
    assert transfers[0]["source_location"] == locations.robot_loc(env.scenario.robot_ids[0])
    assert transfers[0]["destination_location"] == locations.station_loc(env.scenario.station_ids[0])
    assert transfers[0]["payload_after"] == 0
    assert evidence["ledger_conserved"] and evidence["robot_payload_parity"]
    assert [e["sequence"] for e in evidence["events"]] == list(range(1, len(evidence["events"]) + 1))
    assert len({e["event_id"] for e in evidence["events"]}) == len(evidence["events"])
    assert evidence["event_digest"] == hashlib.sha256(json.dumps(evidence["events"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    evidence["events"][0]["kind"] = "tampered"
    assert snapshot(env)["events"][0]["kind"] == "ASSIGNMENT_STARTED"


@pytest.mark.parametrize("balls", [0, 9])
def test_empty_zone_preserves_exit_even_after_partial_unload(balls):
    env = environment(balls=balls)
    finish_collection(env)
    assert snapshot(env)["collection_exit_reason"] == "ZONE_EMPTY"
    if balls:
        step(env, "send_to_handoff")
    assert snapshot(env)["terminal_reason"] == "ZONE_EMPTY"
    assert snapshot(env)["raw_collected_balls"] == snapshot(env)["unloaded_balls"] == balls


@pytest.mark.parametrize("offset", [0.5, 11, 15, 22.5])
def test_exact_deadline_interrupts_inflight_collection_once(offset):
    env = environment(deadline=offset)
    start = env.sim.now
    finish_collection(env)
    frozen = snapshot(env)
    assert frozen["terminal_sim_t_s"] == start + offset
    assert frozen["terminal_reason"] == "EXECUTION_TIMEOUT"
    assert all(e["t_s"] < start + offset for e in frozen["events"] if e["kind"] == "RAW_COLLECTED_TO_ROBOT")
    assert env.sim._robots[env.scenario.robot_ids[0]].task_proc is None
    step(env)
    assert snapshot(env) == frozen


def test_session_end_terminalizes_at_exact_close():
    env = environment(deadline=200, close_minutes=1)
    start = env.sim.now
    finish_collection(env)
    step(env)
    assert snapshot(env)["terminal_sim_t_s"] == start + 60
    assert snapshot(env)["terminal_reason"] == "SESSION_ENDED"


def test_access_boundary_stops_before_move_and_retains_partial_exit():
    env = environment(access=True, deadline=200)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    robot.cfg = robot.cfg.model_copy(update={"payload_capacity_balls": 200})
    # First move is t=20, followed by 30, 40, 50; t=60 is forbidden.
    class BoundarySkills(FixedSkills):
        def sample(self, request, rng):
            return SkillOutcome(True, 10, 0)
    env.sim.skill_model = BoundarySkills()
    step(env, "assign_collection")
    for _ in range(4):
        step(env)
    evidence = snapshot(env)
    assert evidence["raw_collected_balls"] == 28
    assert evidence["collection_exit_reason"] == "COLLECTION_ACCESS_BLOCKED"
    step(env, "send_to_handoff")
    step(env)
    step(env)
    assert snapshot(env)["terminal_reason"] == "COLLECTION_ACCESS_BLOCKED"


@pytest.mark.parametrize("verb", ["pause_robot", "reassign_robot"])
def test_original_policy_redirect_terminalizes_without_claiming_new_work(verb):
    env = environment()
    step(env, "assign_collection")
    step(env, verb, env.scenario.zone_ids[1])
    evidence = snapshot(env)
    assert evidence["terminal_reason"] == "POLICY_PREEMPTED"
    assert evidence["raw_collected_balls"] == 7
    assert evidence["unloaded_balls"] == 0


@pytest.mark.parametrize("cause,reason", [("battery", "LOW_BATTERY"), ("fault", "ROBOT_FAULT"),
                                         ("estop", "ESTOP_LATCHED"), ("assistance", "HUMAN_ASSISTANCE_REQUIRED")])
def test_protection_causes_terminalize_once(cause, reason):
    env = environment()
    step(env, "assign_collection")
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    if cause == "battery":
        robot.battery_wh = 0
        env.sim._check_battery_floor(robot)
    elif cause == "fault":
        env.sim._fail_robot(robot, "test fault")
    elif cause == "estop":
        env.sim._latch_estop(robot, "test stop")
    else:
        action = next(s.index for s in env.catalog.specs if s.name.startswith("request_human_assistance("))
        env.step(action)
    step(env)
    assert snapshot(env)["terminal_reason"] == reason
    assert sum(e["kind"] == "ASSIGNMENT_TERMINAL" for e in snapshot(env)["events"]) == 1
    if cause == "estop":
        before = robot.pos
        step(env, "assign_collection")
        assert robot.pos == before and robot.activity is RobotActivity.EMERGENCY_STOPPED


def test_actual_ledger_return_is_recorded_after_concurrent_inventory_change():
    env = environment(balls=9)
    def take_balls():
        yield env.sim.env.timeout(5)
        env.sim.ledger.move(locations.zone_loc(env.scenario.zone_ids[0]), locations.DISPENSER, 6)
    env.sim.env.process(take_balls())
    finish_collection(env)
    assert snapshot(env)["raw_collected_balls"] == 3
    step(env, "send_to_handoff")
    assert snapshot(env)["unloaded_balls"] == 3


def test_wrong_actual_station_never_completes_bound_handoff():
    env = environment(wrong_station=True)
    finish_collection(env)
    step(env, "send_to_handoff")
    assert snapshot(env)["terminal_reason"] != "UNLOADED_ALL_COLLECTED_BALLS"
    assert snapshot(env)["events"][-2]["station_id"] == env.scenario.station_ids[0]


def test_assignment_events_are_replay_identical_and_rng_neutral():
    left, right = environment(), environment()
    for env in (left, right):
        finish_collection(env)
        step(env, "send_to_handoff")
    assert snapshot(left) == snapshot(right)
    assert left.sim.ledger.counts() == right.sim.ledger.counts()


def test_disabled_evidence_has_no_records_and_cannot_be_armed():
    env = environment(enabled=False)
    with pytest.raises(RuntimeError, match="disabled"):
        env.arm_collection_assignment("x", env.scenario.robot_ids[0], env.scenario.zone_ids[0],
                                      env.scenario.station_ids[0], env.sim.now + 100)
    finish_collection(env)
    assert snapshot(env) is None


def test_deadline_before_unload_move_keeps_collected_balls_on_robot():
    env = environment(deadline=48)
    finish_collection(env)
    step(env, "send_to_handoff")
    evidence = snapshot(env)
    assert evidence["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert evidence["terminal_reason"] == "EXECUTION_TIMEOUT"
    assert evidence["unloaded_balls"] == 0
    assert env.sim.ledger.count(locations.robot_loc(env.scenario.robot_ids[0])) == 20
    assert evidence["terminal_sim_t_s"] == env.scenario.hours.open_seconds + 48


def test_estop_during_travel_freezes_position_and_prevents_completion():
    env = environment()
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    def stop_travel():
        yield env.sim.env.timeout(0.5)
        env.sim._latch_estop(robot, "travel stop")
    env.sim.env.process(stop_travel())
    step(env, "assign_collection")
    position = robot.pos
    step(env, "assign_collection")
    step(env)
    assert robot.pos == position
    assert snapshot(env)["terminal_reason"] == "ESTOP_LATCHED"
    assert snapshot(env)["raw_collected_balls"] == 0
    assert not any(event["kind"] == "travel_completed" for event in env.sim.events.to_dicts())


def test_runtime_station_outage_does_not_invent_policy_preemption():
    env = environment()
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    def outage():
        yield env.sim.env.timeout(1.5)
        env.sim._stations[env.scenario.station_ids[0]].is_open = False
        env.sim._interrupt_task(robot, "station_outage")
    env.sim.env.process(outage())
    step(env, "send_to_handoff")
    assert snapshot(env)["terminal_reason"] is None
    for _ in range(3):
        step(env)
    assert snapshot(env)["terminal_reason"] == "EXECUTION_TIMEOUT"


def test_arm_rejects_existing_payload_at_final_acceptance_and_duplicate_execution():
    env = environment()
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    robot.payload_balls = env.sim.ledger.move(locations.DISPENSER, locations.robot_loc(robot.robot_id), 1)
    step(env, "assign_collection")
    assert snapshot(env) is None
    assert env.sim.metrics.balls_collected == 0
    env = environment()
    finish_collection(env)
    step(env, "send_to_handoff")
    with pytest.raises(ValueError, match="already started"):
        env.arm_collection_assignment("execution-1", env.scenario.robot_ids[0], env.scenario.zone_ids[0],
                                      env.scenario.station_ids[0], env.sim.now + 100)


def test_active_evidence_does_not_change_successful_legacy_trajectory():
    left, right = environment(), environment(enabled=False)
    for env in (left, right):
        finish_collection(env)
        step(env, "send_to_handoff")
    assert left.sim.events.to_dicts() == right.sim.events.to_dicts()
    assert left.sim.metrics == right.sim.metrics
    for name in ("_rng_demand", "_rng_skills", "_rng_failures", "_rng_sensors", "_rng_forecast"):
        assert getattr(left.sim, name).bit_generator.state == getattr(right.sim, name).bit_generator.state


@pytest.mark.parametrize(
    "activity,hold_station",
    [
        (RobotActivity.TRAVELING, False),
        (RobotActivity.QUEUED_HANDOFF, True),
        (RobotActivity.UNLOADING, False),
    ],
)
def test_same_active_assignment_handoff_is_idempotent_after_shield(
    activity, hold_station
):
    """A repeated bound-station handoff is evidence, not a task restart."""

    env = environment(deadline=300)
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    station = env.sim._stations[env.scenario.station_ids[0]]
    blockers = (
        [station.resource.request() for _ in range(station.resource.capacity)]
        if hold_station
        else []
    )
    skills = LongHandoffSkills()
    env.sim.skill_model = skills

    first = step(env, "send_to_handoff")
    assert first[-1]["shield"]["allowed"]
    while robot.activity is not activity:
        assert env.sim.now < env.scenario.hours.open_seconds + 120
        step(env)
    task = robot.task_proc
    before_snapshot = snapshot(env)
    before_samples = list(skills.sampled)
    before_travel_starts = sum(
        event["kind"] == "travel_started"
        and event["payload"].get("dest")
        == locations.station_loc(env.scenario.station_ids[0])
        for event in env.sim.events.to_dicts()
    )

    repeated = step(env, "send_to_handoff")

    assert repeated[-1]["shield"]["allowed"]
    assert robot.task_proc is task
    assert skills.sampled == before_samples
    assert snapshot(env) == before_snapshot
    assert sum(
        event["kind"] == "travel_started"
        and event["payload"].get("dest")
        == locations.station_loc(env.scenario.station_ids[0])
        for event in env.sim.events.to_dicts()
    ) == before_travel_starts == 1
    assert env.sim.events.to_dicts()[-1]["kind"] == "directive_applied"
    for blocker in blockers:
        station.resource.release(blocker)


def test_disabled_assignment_evidence_keeps_legacy_repeated_handoff_bytes():
    """The V3 no-op must not change the default/V2 redirect trajectory."""

    default = environment(enabled=None, deadline=300)
    explicit_v2 = environment(enabled=False, deadline=300)
    encoded = []
    for env in (default, explicit_v2):
        finish_collection(env)
        skills = LongHandoffSkills()
        env.sim.skill_model = skills
        step(env, "send_to_handoff")
        step(env, "send_to_handoff")
        assert skills.sampled.count(SkillType.TRAVEL) == 2
        assert sum(
            event["kind"] == "travel_started"
            and event["payload"].get("dest")
            == locations.station_loc(env.scenario.station_ids[0])
            for event in env.sim.events.to_dicts()
        ) == 2
        encoded.append(
            json.dumps(
                {
                    "events": env.sim.events.to_dicts(),
                    "ledger": env.sim.ledger.counts(),
                    "robots": [row.to_dict() for row in env.sim.robot_snapshots()],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    assert encoded[0] == encoded[1]


def test_different_station_handoff_is_not_hidden_as_v3_idempotency():
    env = environment(deadline=300, wrong_station=True)
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    wrong_station = env.scenario.station_ids[0]
    assert wrong_station != snapshot(env)["handoff_station_id"]
    env.sim.skill_model = LongHandoffSkills()
    directive = SendToHandoff(robot.robot_id, wrong_station)
    assert env.sim.apply_directive(directive).allowed
    env.sim.advance(15)
    task = robot.task_proc

    assert env.sim.apply_directive(directive).allowed
    assert robot.task_proc is not task


def test_unstarted_candidate_cannot_make_repeated_handoff_idempotent():
    env = environment(deadline=300)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    moved = env.sim.ledger.move(
        locations.DISPENSER, locations.robot_loc(robot.robot_id), 20
    )
    robot.payload_balls = moved
    env.sim.skill_model = LongHandoffSkills()
    directive = SendToHandoff(robot.robot_id)
    assert env.sim.apply_directive(directive).allowed
    env.sim.advance(15)
    task = robot.task_proc

    assert snapshot(env) is None
    assert env.sim.apply_directive(directive).allowed
    assert robot.task_proc is not task


def test_active_handoff_with_no_payload_remains_safety_rejected():
    env = environment(deadline=300)
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    env.sim.skill_model = LongHandoffSkills()
    directive = SendToHandoff(robot.robot_id)
    assert env.sim.apply_directive(directive).allowed
    env.sim.advance(15)
    task = robot.task_proc
    returned = env.sim.ledger.move(
        locations.robot_loc(robot.robot_id),
        locations.DISPENSER,
        robot.payload_balls,
    )
    robot.payload_balls -= returned

    decision = env.sim.apply_directive(directive)

    assert not decision.allowed
    assert decision.reason == "no payload to hand off"
    assert robot.task_proc is task


@pytest.mark.parametrize("cause", ["fault", "estop"])
def test_hard_protection_precedes_active_handoff_idempotency(cause):
    env = environment(deadline=300)
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    env.sim.skill_model = LongHandoffSkills()
    directive = SendToHandoff(robot.robot_id)
    assert env.sim.apply_directive(directive).allowed
    env.sim.advance(15)
    if cause == "fault":
        env.sim._fail_robot(robot, "test fault")
    else:
        env.sim._latch_estop(robot, "test estop")

    decision = env.sim.apply_directive(directive)

    assert not decision.allowed
    assert robot.activity in {
        RobotActivity.FAILED,
        RobotActivity.EMERGENCY_STOPPED,
    }


def test_disarm_requires_exact_unstarted_identity_before_replacing_candidate():
    env = environment()
    args = ("execution-1", env.scenario.robot_ids[0], env.scenario.zone_ids[0],
            env.scenario.station_ids[0], env.sim.now + 100)
    env.arm_collection_assignment(*args)  # Exact candidate replay is idempotent.
    with pytest.raises(ValueError, match="different"):
        env.arm_collection_assignment("execution-2", *args[1:])
    with pytest.raises(ValueError, match="candidate"):
        env.disarm_collection_assignment("wrong-execution")
    env.disarm_collection_assignment("execution-1")
    assert snapshot(env) is None
    env.arm_collection_assignment("execution-2", *args[1:])
    step(env, "assign_collection")
    assert snapshot(env) is None
    assert env.collection_assignment_snapshot("execution-2")["raw_collected_balls"] == 7
    with pytest.raises(ValueError, match="started"):
        env.disarm_collection_assignment("execution-2")


def test_rejected_candidate_can_be_explicitly_disarmed_without_evidence():
    env = environment()
    env.sim._zones[env.scenario.zone_ids[0]].is_open = False
    step(env, "assign_collection")
    env.disarm_collection_assignment("execution-1")
    assert snapshot(env) is None
    with pytest.raises(ValueError, match="candidate"):
        env.disarm_collection_assignment("execution-1")


@pytest.mark.parametrize("unload_energy_wh", [98.0, 99.0])
def test_unload_reaching_battery_floor_preserves_transfer_and_terminalizes_low_battery(unload_energy_wh):
    env = environment()
    finish_collection(env)
    robot = env.sim._robots[env.scenario.robot_ids[0]]
    robot.cfg = robot.cfg.model_copy(update={
        "battery_capacity_wh": robot.cfg.battery_capacity_wh.model_copy(update={"value": 100.0}),
        "idle_power_w": robot.cfg.idle_power_w.model_copy(update={"value": 0.0}),
    })
    robot.battery_wh = 100.0
    assert env.scenario.safety.hard_battery_floor_frac == 0.02

    class DepletingUnloadSkills(FixedSkills):
        def sample(self, request, rng):
            if request.skill is SkillType.UNLOAD:
                return SkillOutcome(True, 1, unload_energy_wh)
            return super().sample(request, rng)

    env.sim.skill_model = DepletingUnloadSkills()
    step(env, "send_to_handoff")
    evidence = snapshot(env)
    assert evidence["terminal_reason"] == "LOW_BATTERY"
    assert evidence["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert evidence["raw_collected_balls"] == evidence["unloaded_balls"] == 20
    assert evidence["ledger_conserved"] and evidence["robot_payload_parity"]
    assert robot.payload_balls == env.sim.ledger.count(locations.robot_loc(robot.robot_id)) == 0
    assert robot.activity is RobotActivity.FAILED
    assert robot.battery_frac <= env.scenario.safety.hard_battery_floor_frac
    assert robot.task_proc is None
    assert sum(e["kind"] == "ASSIGNMENT_TERMINAL" for e in evidence["events"]) == 1
    assert sum(e["balls"] for e in evidence["events"] if e["kind"] == "UNLOADED_TO_STATION") == 20
    assert sum(e["payload"]["balls"] for e in env.sim.events.to_dicts() if e["kind"] == "unloaded") == 20
    for _ in range(4):
        step(env)
    assert snapshot(env) == evidence
