"""Contract-shape tests for nxt_facility.state (no simulator involvement)."""

from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from nxt_range_ops.core.entities import (
    RobotActivity,
    RobotHealth,
    RobotStateSnapshot,
)

from nxt_facility.state import (
    BallFlow,
    ChargingState,
    DemandState,
    EnvironmentState,
    FacilityMeta,
    FacilityState,
    FleetSummary,
    StaffState,
    WasherState,
)

FACILITY_PKG = Path(__file__).resolve().parents[2] / "nxt_facility"

# Modules that must stay importable without any simulation stack.
CONTRACT_MODULES = ("state.py", "analysis.py")
FORBIDDEN_IMPORTS = {"simpy", "gymnasium", "numpy", "pandas", "nxt_sim"}


def make_ball_flow(**overrides) -> BallFlow:
    base = dict(
        total_balls=100,
        clean_available=60,
        clean_sensed=58.5,
        in_wash=10,
        dirty_buffered=(("H1", 15),),
        on_field=(("Z1", 9), ("Z2", 4)),
        in_transit=(("R1", 2),),
    )
    base.update(overrides)
    return BallFlow(**base)


def make_state(ball_flow: BallFlow | None = None) -> FacilityState:
    return FacilityState(
        meta=FacilityMeta(
            t_s=21600.0,
            minute_of_day=360.0,
            facility_open=True,
            scenario_name="unit",
            seed=7,
        ),
        ball_flow=ball_flow or make_ball_flow(),
        washer=WasherState(
            throughput_balls_per_minute=40.0, batch_size_balls=200, wip=10
        ),
        demand=DemandState(
            forecast_balls_per_minute=(5.0, 8.0, 8.0, 3.0),
            forecast_bucket_minutes=30,
            minutes_to_close=960.0,
            demand_balls_total=120,
            demand_balls_served=118,
            stockout_minutes=1.5,
            service_availability=0.99,
        ),
        fleet=FleetSummary(total=3, operable=2, inoperative=1, charging=1, awaiting_human=0),
        charging=ChargingState(slots=2, in_use=1, queue_length=0),
        staff=StaffState(capacity=1, busy=0, queued_requests=0),
        environment=EnvironmentState(
            wet_ground_speed_multiplier=1.0,
            zones_open=2,
            zones_total=2,
            stations_open=1,
            stations_total=1,
        ),
        robots=(),
        zones=(),
        stations=(),
    )


def test_facility_state_is_frozen():
    state = make_state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.meta = None
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.ball_flow.clean_available = 0


def test_ball_flow_totals_and_conservation():
    flow = make_ball_flow()
    assert flow.dirty_buffered_total == 15
    assert flow.on_field_total == 13
    assert flow.in_transit_total == 2
    # 60 + 10 + 15 + 13 + 2 == 100
    assert flow.conserved is True
    assert make_ball_flow(clean_available=59).conserved is False


def test_to_dict_is_json_serializable_and_deterministic():
    a = json.dumps(make_state().to_dict(), sort_keys=True)
    b = json.dumps(make_state().to_dict(), sort_keys=True)
    assert a == b
    payload = json.loads(a)
    assert payload["ball_flow"]["clean_available"] == 60
    assert payload["staff"]["capacity"] == 1


def test_robot_or_none():
    snapshot = RobotStateSnapshot(
        robot_id="R1",
        activity=RobotActivity.IDLE,
        health=RobotHealth.OK,
        battery_frac=1.0,
        payload_balls=0,
        payload_capacity_balls=200,
        location="charger",
        destination=None,
        assigned_zone=None,
        estop_latched=False,
        awaiting_human=False,
    )
    state = dataclasses.replace(make_state(), robots=(snapshot,))
    assert state.robot_or_none("R1") is snapshot
    assert state.robot_or_none("R9") is None


def test_contract_importable_without_simulation_stack():
    """The real enforcement: state/analysis import fine with simpy, gymnasium,
    numpy, and pandas blocked (the AST scan below cannot see transitive
    leaks through package __init__ side effects — found by adversarial
    review when nxt_range_ops.core.__init__ dragged in the simulator)."""
    probe = textwrap.dedent(
        """
        import importlib.abc
        import sys

        class Blocker(importlib.abc.MetaPathFinder):
            BLOCKED = {"simpy", "gymnasium", "numpy", "pandas"}

            def find_spec(self, fullname, path=None, target=None):
                if fullname.split(".")[0] in self.BLOCKED:
                    raise ImportError("blocked by contract test: " + fullname)
                return None

        sys.meta_path.insert(0, Blocker())
        import nxt_facility.state
        import nxt_facility.analysis
        import nxt_facility
        from nxt_facility import FacilityState, classify_state
        print("contract-ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=FACILITY_PKG.parent,
    )
    assert result.returncode == 0, result.stderr
    assert "contract-ok" in result.stdout


def test_contract_modules_import_no_simulation_libraries():
    for module_name in CONTRACT_MODULES:
        path = FACILITY_PKG / module_name
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            roots = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            banned = roots & FORBIDDEN_IMPORTS
            assert not banned, f"{module_name} imports forbidden module(s): {banned}"
