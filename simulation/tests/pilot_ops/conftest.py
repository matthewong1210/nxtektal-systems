"""Deterministic factories for Shadow Ops policy-core tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nxt_pilot_ops.contracts import (
    OperationalSnapshot,
    RobotOperationalState,
    RobotStatus,
)
from nxt_pilot_ops.guardian import BallAvailabilityGuardian


@pytest.fixture
def observed_at() -> datetime:
    return datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def robot_factory():
    """Build an otherwise eligible collector with explicit provenance."""

    def build(**overrides) -> RobotOperationalState:
        values = {
            "robot_id": "R-1",
            "status": RobotStatus.AVAILABLE,
            "battery_fraction": 0.90,
            "expected_clean_ball_yield": 1_000,
            "replenishment_eta_minutes": 2.0,
            "yield_provenance": "fixture:derived-yield",
            "eta_provenance": "fixture:derived-eta",
            "capabilities": frozenset({"collect"}),
            "capability_provenance": "fixture:capabilities",
            "requires_washing": False,
            "washing_requirement_provenance": "fixture:washing-requirement",
            "payload_balls": 0,
            "current_task_id": None,
            "fault_code": None,
            "raw_activity": "idle",
            "raw_health": "ok",
        }
        values.update(overrides)
        return RobotOperationalState(**values)

    return build


@pytest.fixture
def snapshot_factory(observed_at, robot_factory):
    """Build a snapshot with a first stockout at exactly ten minutes."""

    def build(**overrides) -> OperationalSnapshot:
        values = {
            "site_id": "site-1",
            "deployment_id": "deployment-1",
            "observed_at": observed_at,
            "source_sequence": 7,
            "source_schema_version": "facility-state-stream/v1",
            "source_ref": "fixture:sequence-7",
            "clean_available": 1_000,
            "clean_available_provenance": "fixture:accounting-ledger",
            "clean_sensed": 1_000.0,
            "clean_sensed_provenance": "fixture:dispenser-sensor",
            "current_demand_balls_per_minute": 100.0,
            "current_demand_provenance": "fixture:current-demand",
            "forecast_demand_balls_per_minute": None,
            "forecast_bucket_minutes": None,
            "forecast_demand_provenance": "fixture:no-forecast",
            "minutes_to_close": 60.0,
            "minutes_to_close_provenance": "fixture:closing-horizon",
            "range_open": True,
            "range_open_provenance": "fixture:schedule",
            "collection_allowed": True,
            "collection_permission_provenance": "fixture:route-permission",
            "collection_block_reason": None,
            "washer_available": True,
            "washer_availability_provenance": "fixture:washer-health",
            "inbound_batches": (),
            "robots": (robot_factory(),),
            "missing_data_reasons": (),
        }
        values.update(overrides)
        return OperationalSnapshot(**values)

    return build


@pytest.fixture
def guardian() -> BallAvailabilityGuardian:
    return BallAvailabilityGuardian()
