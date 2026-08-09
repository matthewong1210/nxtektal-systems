from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from enum import Enum

import pytest

from nxt_facility.build import build_facility_state
from nxt_range_ops.core.entities import RobotActivity, RobotHealth
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario

from nxt_pilot_ops.adapters import (
    AdapterError,
    FacilityStateAdapterContext,
    adapt_facility_state,
)
from nxt_pilot_ops.contracts import RobotStatus


MIDNIGHT = datetime(2026, 8, 8, tzinfo=timezone.utc)


def _state():
    scenario = make_scenario("normal_weekday")
    simulation = RangeSimulation(scenario, seed=7)
    return simulation, build_facility_state(simulation)


def _context(*, sequence: int = 0, sensed_valid: bool = False):
    return FacilityStateAdapterContext(
        site_id="site-a",
        deployment_id="deployment-a",
        simulation_midnight=MIDNIGHT,
        source_sequence=sequence,
        source_ref=f"live:state:{sequence}",
        clean_sensed_valid=sensed_valid,
    )


def test_adapter_maps_real_facility_state_with_required_context() -> None:
    _, state = _state()
    snapshot = adapt_facility_state(state, context=_context())

    assert snapshot.site_id == "site-a"
    assert snapshot.deployment_id == "deployment-a"
    assert snapshot.observed_at == datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    assert snapshot.source_sequence == 0
    assert snapshot.source_schema_version is None
    assert snapshot.clean_available == state.ball_flow.clean_available
    assert snapshot.clean_sensed is None
    assert "declared_invalid_by_adapter_context" in snapshot.clean_sensed_provenance
    assert snapshot.forecast_demand_balls_per_minute == (
        state.demand.forecast_balls_per_minute
    )
    assert snapshot.forecast_bucket_minutes == state.demand.forecast_bucket_minutes
    assert snapshot.minutes_to_close == state.demand.minutes_to_close
    assert snapshot.range_open is state.meta.facility_open


def test_adapter_preserves_valid_float_sensed_value_separately() -> None:
    simulation, _ = _state()
    simulation.advance(60.0)
    state = build_facility_state(simulation)
    snapshot = adapt_facility_state(
        state, context=_context(sequence=1, sensed_valid=True)
    )

    assert isinstance(state.ball_flow.clean_sensed, float)
    assert snapshot.clean_sensed == state.ball_flow.clean_sensed
    assert snapshot.clean_available == state.ball_flow.clean_available
    assert snapshot.decision_inventory_basis == "clean_sensed"
    assert snapshot.observed_at == datetime(2026, 8, 8, 6, 1, tzinfo=timezone.utc)


def test_adapter_does_not_fabricate_unavailable_operational_inputs() -> None:
    _, state = _state()
    snapshot = adapt_facility_state(state, context=_context())

    assert snapshot.current_demand_balls_per_minute is None
    assert snapshot.inbound_batches == ()
    assert snapshot.collection_allowed is None
    assert snapshot.collection_block_reason is None
    assert snapshot.washer_available is None
    assert snapshot.robots
    for robot in snapshot.robots:
        assert robot.capabilities is None
        assert robot.replenishment_eta_minutes is None
        assert robot.expected_clean_ball_yield is None
        assert robot.requires_washing is None
        assert robot.current_task_id is None
        assert robot.fault_code is None


@pytest.mark.parametrize(
    ("activity", "expected"),
    [
        (RobotActivity.IDLE, RobotStatus.AVAILABLE),
        (RobotActivity.TRAVELING, RobotStatus.BUSY),
        (RobotActivity.COLLECTING, RobotStatus.BUSY),
        (RobotActivity.QUEUED_HANDOFF, RobotStatus.BUSY),
        (RobotActivity.UNLOADING, RobotStatus.BUSY),
        (RobotActivity.QUEUED_CHARGER, RobotStatus.CHARGING),
        (RobotActivity.CHARGING, RobotStatus.CHARGING),
        (RobotActivity.PAUSED, RobotStatus.PAUSED),
        (RobotActivity.FAILED, RobotStatus.FAULTED),
        (RobotActivity.EMERGENCY_STOPPED, RobotStatus.ESTOPPED),
        (RobotActivity.AWAITING_HUMAN, RobotStatus.AWAITING_HUMAN),
    ],
)
def test_adapter_maps_every_robot_activity_explicitly(
    activity: RobotActivity, expected: RobotStatus
) -> None:
    _, state = _state()
    robot = replace(
        state.robots[0],
        activity=activity,
        health=RobotHealth.OK,
        estop_latched=False,
        awaiting_human=False,
    )
    mapped = adapt_facility_state(
        replace(state, robots=(robot,)), context=_context()
    ).robots[0]
    assert mapped.status is expected
    assert mapped.raw_activity == activity.value
    assert mapped.raw_health == RobotHealth.OK.value


def test_adapter_maps_degraded_health_conservatively_offline() -> None:
    _, state = _state()
    robot = replace(
        state.robots[0],
        activity=RobotActivity.IDLE,
        health=RobotHealth.DEGRADED,
    )
    mapped = adapt_facility_state(
        replace(state, robots=(robot,)), context=_context()
    ).robots[0]
    assert mapped.status is RobotStatus.OFFLINE
    assert mapped.raw_health == "degraded"


def test_adapter_status_safety_flags_override_idle() -> None:
    _, state = _state()
    estopped = replace(state.robots[0], estop_latched=True)
    awaiting = replace(state.robots[0], awaiting_human=True)
    failed = replace(state.robots[0], health=RobotHealth.FAILED)

    assert adapt_facility_state(
        replace(state, robots=(estopped,)), context=_context()
    ).robots[0].status is RobotStatus.ESTOPPED
    assert adapt_facility_state(
        replace(state, robots=(awaiting,)), context=_context()
    ).robots[0].status is RobotStatus.AWAITING_HUMAN
    assert adapt_facility_state(
        replace(state, robots=(failed,)), context=_context()
    ).robots[0].status is RobotStatus.FAULTED


def test_adapter_rejects_unmapped_or_malformed_object_status() -> None:
    _, state = _state()
    malformed = replace(state.robots[0], activity="mystery")
    with pytest.raises(AdapterError, match="upstream RobotActivity"):
        adapt_facility_state(
            replace(state, robots=(malformed,)), context=_context()
        )


def test_adapter_rejects_foreign_enum_with_a_known_status_value() -> None:
    class ForeignActivity(str, Enum):
        IDLE = "idle"

    _, state = _state()
    malformed = replace(state.robots[0], activity=ForeignActivity.IDLE)
    with pytest.raises(AdapterError, match="upstream RobotActivity"):
        adapt_facility_state(
            replace(state, robots=(malformed,)), context=_context()
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"simulation_midnight": datetime(2026, 8, 8)},
        {
            "simulation_midnight": datetime(
                2026, 8, 8, 1, 0, tzinfo=timezone.utc
            )
        },
        {"source_sequence": True},
        {"clean_sensed_valid": 1},
    ],
)
def test_adapter_context_rejects_ambiguous_clock_and_boolean_numerics(
    overrides: dict,
) -> None:
    values = {
        "site_id": "site-a",
        "deployment_id": "deployment-a",
        "simulation_midnight": MIDNIGHT,
        "source_sequence": 0,
        "source_ref": "live:state:0",
        "clean_sensed_valid": False,
    }
    values.update(overrides)
    with pytest.raises(AdapterError):
        FacilityStateAdapterContext(**values)


def test_adapter_rejects_clock_inconsistent_with_seconds_since_midnight() -> None:
    _, state = _state()
    malformed = replace(
        state,
        meta=replace(state.meta, t_s=60.0, minute_of_day=361.0),
    )
    with pytest.raises(AdapterError, match="seconds since midnight"):
        adapt_facility_state(malformed, context=_context())


def test_object_adapter_rejects_nonconserving_ball_flow() -> None:
    _, state = _state()
    malformed = replace(
        state,
        ball_flow=replace(
            state.ball_flow,
            clean_available=state.ball_flow.clean_available + 1,
        ),
    )
    with pytest.raises(AdapterError, match="conservation ledger"):
        adapt_facility_state(malformed, context=_context())


def test_object_adapter_rejects_boolean_ball_flow_counts_even_if_conserved() -> None:
    _, state = _state()
    flow = state.ball_flow
    malformed = replace(
        state,
        ball_flow=replace(
            flow,
            total_balls=flow.total_balls - flow.in_wash + 1,
            in_wash=True,
        ),
    )
    assert malformed.ball_flow.conserved is True
    with pytest.raises(AdapterError, match="booleans are not integers"):
        adapt_facility_state(malformed, context=_context())
