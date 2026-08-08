from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from nxt_facility.build import build_facility_state
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_range_twin.stream import write_jsonl

from nxt_pilot_ops.adapters import (
    NATIVE_STREAM_SCHEMA,
    read_native_facility_state_stream,
)
from nxt_pilot_ops.contracts import (
    EvaluationVerdict,
    RecommendationAction,
)
from nxt_pilot_ops.guardian import BallAvailabilityGuardian


def _native_risk_capture(tmp_path: Path) -> tuple[Path, Path]:
    scenario = make_scenario("normal_weekday").model_copy(
        update={"initial_dispenser_frac": 0.001}
    )
    simulation = RangeSimulation(scenario, seed=7)
    state = build_facility_state(simulation)
    states_path = tmp_path / "facility_states.jsonl"
    meta_path = tmp_path / "stream.meta.json"
    write_jsonl(states_path, [state.to_dict()])
    meta = {
        "schema": NATIVE_STREAM_SCHEMA,
        "site_id": "sim-baseline",
        "deployment_id": "shadow-test",
        "episode_id": "normal_weekday-seed7",
        "scenario_name": "normal_weekday",
        "seed": 7,
        "policy": "inventory_threshold",
        "policy_version": "0",
        "control_interval_s": 60.0,
        "every_steps": 1,
        "n_records": 1,
        "simulator_version": "0.5.0",
        "git_commit": "abc1234",
        "disclaimer": "synthetic test scenario; not real facility performance",
    }
    meta_path.write_text(json.dumps(meta, sort_keys=True), encoding="utf-8")
    return states_path, meta_path


def test_integration_repository_native_synthetic_state_to_guardian_trace(
    tmp_path: Path,
) -> None:
    states_path, meta_path = _native_risk_capture(tmp_path)
    midnight = datetime(2026, 8, 8, tzinfo=timezone.utc)
    snapshot = read_native_facility_state_stream(
        states_path,
        meta_path,
        simulation_midnight=midnight,
        first_record_clean_sensed_valid=False,
    )[0]

    guardian = BallAvailabilityGuardian()
    first = guardian.evaluate(snapshot)
    second = guardian.evaluate(snapshot)

    assert first.verdict is EvaluationVerdict.RECOMMEND
    assert first.recommendation is not None
    assert first.recommendation.action is RecommendationAction.OPERATOR_INTERVENTION
    assert first.recommendation.target_robot_id is None
    assert first.trace.trace_id == second.trace.trace_id
    assert first.recommendation.recommendation_id == second.recommendation.recommendation_id
    assert first.trace.inventory_basis == "clean_available"
    assert first.trace.clean_sensed is None
    assert first.trace.source_sequence == 0
    assert first.trace.projected_stockout_without_action_minutes is not None
    assert first.trace.candidates
    assert all(not candidate.eligible for candidate in first.trace.candidates)
    assert all(
        "missing_replenishment_eta" in candidate.exclusion_reasons
        and "missing_expected_clean_ball_yield" in candidate.exclusion_reasons
        and "missing_capabilities" in candidate.exclusion_reasons
        for candidate in first.trace.candidates
    )
