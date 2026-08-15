"""Policy evaluation through the runtime: verdicts, IDs, missingness."""

from __future__ import annotations

from nxt_agent_runtime import CycleKind, EvaluationJournal
from nxt_pilot_ops.contracts import (
    EvaluationVerdict,
    RecommendationAction,
)
from nxt_pilot_ops.ledger import JsonlEventLedger

from .conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)


def _run_pilot(tmp_path, base_inputs):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    outcomes = runtime.run(3)
    return runtime, outcomes


def test_no_action_uses_the_existing_verdict_with_full_evidence(
    tmp_path, base_inputs
):
    runtime, outcomes = _run_pilot(tmp_path, base_inputs)
    record = outcomes[0].record
    assert record.verdict is EvaluationVerdict.NO_ACTION
    assert record.recommendation_id is None
    assert record.recommendation_action is None
    assert record.ledger_event_id is None
    # A reviewer can answer "why did the Agent not act" from the record.
    trace = record.decision_trace
    assert trace is not None
    assert trace["policy_id"] == "ball-availability-guardian"
    assert trace["trace_id"] == record.trace_id
    assert trace["projected_stockout_without_action_minutes"] is None
    assert any(
        "No clean-ball stockout is projected" in line
        for line in trace["rationale"]
    )
    assert trace["missing_data_reasons"]
    # NO_ACTION leaves the Shadow Ops ledger untouched.
    ledger = JsonlEventLedger(evidence_paths(tmp_path)["ledger"])
    assert [item.event_type for item in ledger.records()] == [
        "recommendation_issued"
    ]


def test_escalation_uses_the_existing_recommend_path(tmp_path, base_inputs):
    runtime, outcomes = _run_pilot(tmp_path, base_inputs)
    record = outcomes[1].record
    assert record.verdict is EvaluationVerdict.RECOMMEND
    assert record.recommendation_action is RecommendationAction.OPERATOR_INTERVENTION
    assert record.decision_trace is None
    ledger = JsonlEventLedger(evidence_paths(tmp_path)["ledger"])
    issued = [
        item
        for item in ledger.records()
        if item.event_type == "recommendation_issued"
    ]
    assert [item.event_id for item in issued] == [record.ledger_event_id]
    payload = issued[0].payload
    assert (
        payload["recommendation"]["recommendation_id"]
        == record.recommendation_id
    )
    assert payload["decision_trace"]["trace_id"] == record.trace_id
    # Escalation, not dispatch: no robot is targeted.
    assert payload["recommendation"]["target_robot_id"] is None


def test_missing_deployment_facts_remain_missing(tmp_path, base_inputs):
    """The native path never invents capability, ETA, yield, permission,
    demand, or washer facts; every candidate stays safety-excluded."""
    runtime, outcomes = _run_pilot(tmp_path, base_inputs)
    trace = outcomes[0].record.decision_trace
    assert trace["collection_allowed"] is None
    assert trace["washer_available"] is None
    assert trace["current_demand_balls_per_minute"] is None
    assert "unavailable" in trace["collection_permission_provenance"]
    assert "unavailable" in trace["washer_availability_provenance"]
    for candidate in trace["candidates"]:
        assert candidate["capabilities"] is None
        assert candidate["replenishment_eta_minutes"] is None
        assert candidate["expected_clean_ball_yield"] is None
        assert candidate["requires_washing"] is None
        assert candidate["eligible"] is False
        assert "missing_capabilities" in candidate["exclusion_reasons"]
        assert "missing_replenishment_eta" in candidate["exclusion_reasons"]
        assert (
            "missing_expected_clean_ball_yield"
            in candidate["exclusion_reasons"]
        )


def test_evaluation_ids_are_stable_across_identical_runs(
    tmp_path, base_inputs
):
    _, first = _run_pilot(tmp_path / "a", base_inputs)
    _, second = _run_pilot(tmp_path / "b", base_inputs)
    for left, right in zip(first[:2], second[:2]):
        assert left.record.evaluation_id == right.record.evaluation_id
        assert left.record.trace_id == right.record.trace_id
        assert left.record.recommendation_id == right.record.recommendation_id
        assert left.record.snapshot_digest == right.record.snapshot_digest
        assert left.envelope_id == right.envelope_id


def test_record_preserves_envelope_identity_and_provenance(
    tmp_path, base_inputs
):
    runtime, outcomes = _run_pilot(tmp_path, base_inputs)
    for outcome in outcomes[:2]:
        record = outcome.record
        assert record.site_id == SITE_ID
        assert record.deployment_id == DEPLOYMENT_ID
        assert record.envelope_id == outcome.envelope_id
        assert record.envelope_id.startswith("fse:")
        journal = EvaluationJournal(evidence_paths(tmp_path)["journal"])
        stored = journal.record_for_sequence(record.sequence_number)
        assert stored == record


def test_exactly_one_outcome_per_admitted_envelope(tmp_path, base_inputs):
    runtime, outcomes = _run_pilot(tmp_path, base_inputs)
    journal = EvaluationJournal(evidence_paths(tmp_path)["journal"])
    records = journal.read()
    assert [record.sequence_number for record in records] == [0, 1]
    assert len({record.evaluation_id for record in records}) == 2
