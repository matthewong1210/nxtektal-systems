from __future__ import annotations

import json
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone

import pytest

import nxt_pilot_ops.ledger as ledger_module

from nxt_pilot_ops.contracts import (
    BallAvailabilityPolicyConfig,
    DecisionTrace,
    Recommendation,
    RecommendationAction,
    RobotCandidateTrace,
    RobotStatus,
)
from nxt_pilot_ops.ledger import (
    JsonlEventLedger,
    LedgerIntegrityError,
    LedgerTransitionError,
    execution_acknowledged_event,
    execution_requested_event,
    recommendation_issued_event,
    recommendation_outcome_event,
    recommendation_response_event,
)
from nxt_pilot_ops.serialization import canonical_json, stable_digest, to_primitive
from nxt_pilot_ops.workflow import (
    CaseStatus,
    ExecutionAcknowledgement,
    ExecutionRequest,
    RecommendationOutcome,
    RecommendationResponse,
    ResponseKind,
)


NOW = datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)


def _recommendation() -> Recommendation:
    return Recommendation.create(
        site_id="site-1",
        deployment_id="deployment-1",
        issued_at=NOW,
        execute_before=NOW + timedelta(minutes=20),
        action=RecommendationAction.DISPATCH_COLLECTOR,
        target_robot_id="R1",
        policy_id="ball-availability-guardian",
        policy_version="0.1.0",
        decision_trace_id=_trace().trace_id,
        expected_stockout_without_action_minutes=30.0,
        expected_stockout_with_action_minutes=70.0,
        protected_through_horizon=False,
        projected_stockout_delay_minutes=40.0,
        summary=(
            "Recommend operator dispatch of R1 by "
            "2026-08-08T14:20:00.000000Z "
            "to extend clean-ball availability."
        ),
    )


def _trace() -> DecisionTrace:
    candidate = RobotCandidateTrace(
        robot_id="R1",
        status=RobotStatus.AVAILABLE,
        raw_activity="idle",
        raw_health="ok",
        battery_fraction=0.9,
        capabilities=("collect",),
        payload_balls=0,
        current_task_id=None,
        fault_code=None,
        requires_washing=False,
        eligible=True,
        exclusion_reasons=(),
        replenishment_eta_minutes=2.0,
        expected_clean_ball_yield=4_000,
        yield_provenance="fixture:yield",
        eta_provenance="fixture:eta",
        arrival_before_first_stockout=True,
        projected_stockout_with_action_minutes=70.0,
        protected_through_horizon=False,
        projected_extension_minutes=40.0,
    )
    return DecisionTrace.create(
        policy_id="ball-availability-guardian",
        policy_version="0.1.0",
        policy_config=BallAvailabilityPolicyConfig(
            safety_buffer_minutes=8.0,
            alert_lead_minutes=20.0,
        ),
        site_id="site-1",
        deployment_id="deployment-1",
        observed_at=NOW,
        source_sequence=7,
        source_schema_version="facility-state-stream/v1",
        source_ref="fixture:7",
        snapshot_digest="a" * 64,
        range_open=True,
        range_open_provenance="fixture:range-open",
        collection_allowed=True,
        collection_permission_provenance="fixture:collection-permission",
        collection_block_reason=None,
        washer_available=True,
        washer_availability_provenance="fixture:washer",
        clean_available=4_000,
        clean_available_provenance="fixture:clean-available",
        clean_sensed=3_000.0,
        clean_sensed_provenance="fixture:clean-sensed",
        inventory_basis="clean_sensed",
        inventory_balls=3_000.0,
        inventory_provenance="fixture:clean-sensed",
        current_demand_balls_per_minute=100.0,
        current_demand_provenance="fixture:current-demand",
        forecast_demand_balls_per_minute=None,
        forecast_bucket_minutes=None,
        forecast_demand_provenance="fixture:no-forecast",
        minutes_to_close=180.0,
        minutes_to_close_provenance="fixture:closing-horizon",
        selected_demand_basis="current_demand",
        demand_rates_used_balls_per_minute=(100.0,),
        projection_horizon_minutes=180.0,
        committed_inbound_batches=(),
        projected_stockout_without_action_minutes=30.0,
        candidates=(candidate,),
        selected_robot_id="R1",
        latest_safe_dispatch_delay_minutes=20.0,
        data_completeness_score=1.0,
        missing_data_reasons=(),
        rationale=("Fixture decision trace.",),
    )


def _recreate_trace(
    trace: DecisionTrace | None = None, **overrides
) -> DecisionTrace:
    original = _trace() if trace is None else trace
    values = {
        item.name: getattr(original, item.name)
        for item in fields(original)
        if item.name != "trace_id"
    }
    values.update(overrides)
    return DecisionTrace.create(**values)


def _recreate_recommendation(
    recommendation: Recommendation | None = None, **overrides
) -> Recommendation:
    original = _recommendation() if recommendation is None else recommendation
    values = {
        item.name: getattr(original, item.name)
        for item in fields(original)
        if item.name != "recommendation_id"
    }
    values.update(overrides)
    return Recommendation.create(**values)


def _response(recommendation: Recommendation) -> RecommendationResponse:
    return RecommendationResponse.create(
        recommendation_id=recommendation.recommendation_id,
        responded_at=NOW + timedelta(minutes=1),
        operator_id="operator-1",
        kind=ResponseKind.ACCEPT,
        reason_code="APPROVED",
    )


def _request(recommendation: Recommendation) -> ExecutionRequest:
    return ExecutionRequest.create(
        recommendation_id=recommendation.recommendation_id,
        effective_recommendation_id=recommendation.recommendation_id,
        requested_at=NOW + timedelta(minutes=2),
        requested_by="operator-1",
    )


def _acknowledgement(
    recommendation: Recommendation,
    request: ExecutionRequest,
) -> ExecutionAcknowledgement:
    return ExecutionAcknowledgement.create(
        recommendation_id=recommendation.recommendation_id,
        effective_recommendation_id=recommendation.recommendation_id,
        request_id=request.request_id,
        acknowledged_at=NOW + timedelta(minutes=3),
        acknowledged_by="operator-2",
    )


def _outcome(recommendation: Recommendation) -> RecommendationOutcome:
    return RecommendationOutcome.create(
        recommendation_id=recommendation.recommendation_id,
        recorded_at=NOW + timedelta(minutes=10),
        executed=True,
        actual_start_at=NOW + timedelta(minutes=4),
        actual_completed_at=NOW + timedelta(minutes=9),
        balls_returned=3_800,
        stockout_occurred=False,
        stockout_minutes=0,
    )


def _append_full_workflow(ledger: JsonlEventLedger) -> Recommendation:
    recommendation = _recommendation()
    request = _request(recommendation)
    acknowledgement = _acknowledgement(recommendation, request)
    events = (
        recommendation_issued_event(recommendation, _trace()),
        recommendation_response_event(
            response=_response(recommendation),
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        ),
        execution_requested_event(
            request=request,
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        ),
        execution_acknowledged_event(
            acknowledgement=acknowledgement,
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        ),
        recommendation_outcome_event(
            outcome=_outcome(recommendation),
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        ),
    )
    for event in events:
        ledger.append(event)
    return recommendation


def test_hash_chain_verifies_and_replay_is_stable(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    ledger = JsonlEventLedger(path)
    recommendation = _append_full_workflow(ledger)
    count, head = ledger.verify()
    assert count == 5
    assert len(head) == 64
    records = ledger.records()
    assert [record.sequence for record in records] == [1, 2, 3, 4, 5]
    assert all(
        current.previous_hash == previous.record_hash
        for previous, current in zip(records, records[1:])
    )
    replay = ledger.replay()
    assert replay.case_for(recommendation.recommendation_id).status is CaseStatus.OUTCOME_RECORDED
    assert JsonlEventLedger(path).replay() == replay


def test_duplicate_event_and_duplicate_logical_response_are_rejected(tmp_path) -> None:
    recommendation = _recommendation()
    ledger = JsonlEventLedger(tmp_path / "events.jsonl")
    issued = recommendation_issued_event(recommendation, _trace())
    ledger.append(issued)
    with pytest.raises(ValueError, match="duplicate event_id"):
        ledger.append(issued)

    first = _response(recommendation)
    ledger.append(
        recommendation_response_event(
            response=first,
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        )
    )
    second = RecommendationResponse.create(
        recommendation_id=recommendation.recommendation_id,
        responded_at=NOW + timedelta(minutes=2),
        operator_id="operator-2",
        kind=ResponseKind.REJECT,
        reason_code="REJECTED_LATER",
    )
    with pytest.raises(LedgerTransitionError, match="only one"):
        ledger.append(
            recommendation_response_event(
                response=second,
                site_id=recommendation.site_id,
                deployment_id=recommendation.deployment_id,
            )
        )
    assert ledger.verify()[0] == 2


def test_unknown_recommendation_and_invalid_order_leave_file_unchanged(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    ledger = JsonlEventLedger(path)
    recommendation = _recommendation()
    with pytest.raises(LedgerTransitionError, match="unknown recommendation"):
        ledger.append(
            recommendation_response_event(
                response=_response(recommendation),
                site_id=recommendation.site_id,
                deployment_id=recommendation.deployment_id,
            )
        )
    assert ledger.verify()[0] == 0

    ledger.append(recommendation_issued_event(recommendation, _trace()))
    with pytest.raises(LedgerTransitionError, match="accepted or modified"):
        ledger.append(
            execution_requested_event(
                request=_request(recommendation),
                site_id=recommendation.site_id,
                deployment_id=recommendation.deployment_id,
            )
        )
    assert ledger.verify()[0] == 1

    unknown_outcome = RecommendationOutcome.create(
        recommendation_id="rec-unknown",
        recorded_at=NOW + timedelta(minutes=10),
        executed=False,
        actual_start_at=None,
        actual_completed_at=None,
        balls_returned=0,
        stockout_occurred=False,
        stockout_minutes=0,
    )
    with pytest.raises(LedgerTransitionError, match="unknown recommendation"):
        JsonlEventLedger(tmp_path / "unknown-outcome.jsonl").append(
            recommendation_outcome_event(
                outcome=unknown_outcome,
                site_id="site-1",
                deployment_id="deployment-1",
            )
        )


def test_tampering_is_detected_before_replay(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    ledger = JsonlEventLedger(path)
    _append_full_workflow(ledger)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    records[0]["payload"]["recommendation"]["summary"] = "tampered"
    path.write_text(
        "\n".join(canonical_json(record) for record in records) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        LedgerIntegrityError, match="record hash mismatch"
    ):
        ledger.replay()
    with pytest.raises(
        LedgerIntegrityError, match="record hash mismatch"
    ):
        JsonlEventLedger(path)


def test_non_posix_backend_fails_explicitly_without_breaking_module_import(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(ledger_module, "_fcntl", None)
    with pytest.raises(RuntimeError, match="requires POSIX fcntl"):
        JsonlEventLedger(tmp_path / "events.jsonl")


def test_duplicate_json_keys_and_missing_terminal_newline_are_rejected(
    tmp_path,
) -> None:
    duplicate_path = tmp_path / "duplicate-key.jsonl"
    duplicate_ledger = JsonlEventLedger(duplicate_path)
    recommendation = _recommendation()
    duplicate_ledger.append(recommendation_issued_event(recommendation, _trace()))
    original = duplicate_path.read_text(encoding="utf-8")
    duplicate_path.write_text(
        original.replace(
            '"schema_version":"nxt-pilot-ops-ledger/v1"',
            '"schema_version":"wrong","schema_version":"nxt-pilot-ops-ledger/v1"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(LedgerIntegrityError, match="duplicate JSON key"):
        JsonlEventLedger(duplicate_path)

    truncated_path = tmp_path / "truncated.jsonl"
    truncated_ledger = JsonlEventLedger(truncated_path)
    truncated_ledger.append(recommendation_issued_event(recommendation, _trace()))
    truncated_path.write_text(
        truncated_path.read_text(encoding="utf-8").removesuffix("\n"),
        encoding="utf-8",
    )
    with pytest.raises(LedgerIntegrityError, match="not newline-terminated"):
        JsonlEventLedger(truncated_path)

    crlf_path = tmp_path / "crlf.jsonl"
    crlf_ledger = JsonlEventLedger(crlf_path)
    crlf_ledger.append(recommendation_issued_event(recommendation, _trace()))
    crlf_path.write_bytes(crlf_path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(LedgerIntegrityError, match="canonical"):
        JsonlEventLedger(crlf_path)


def test_issuance_rejects_trace_semantic_mismatches() -> None:
    recommendation = _recommendation()
    with pytest.raises(TypeError, match="clean_available"):
        replace(_trace(), clean_available=True)

    recommendation_values = {
        item.name: getattr(recommendation, item.name)
        for item in fields(recommendation)
        if item.name != "recommendation_id"
    }
    recommendation_values["target_robot_id"] = "R-forged"
    forged_target = Recommendation.create(**recommendation_values)
    with pytest.raises(ValueError, match="dispatch target"):
        recommendation_issued_event(forged_target, _trace())


def test_issuance_recomputes_candidate_counterfactuals() -> None:
    trace = _trace()
    forged_candidate = replace(
        trace.candidates[0],
        projected_stockout_with_action_minutes=75.0,
        projected_extension_minutes=45.0,
    )
    forged_trace = _recreate_trace(trace, candidates=(forged_candidate,))
    forged_recommendation = _recreate_recommendation(
        decision_trace_id=forged_trace.trace_id,
        expected_stockout_with_action_minutes=75.0,
        projected_stockout_delay_minutes=45.0,
    )

    with pytest.raises(ValueError, match="counterfactual does not match"):
        recommendation_issued_event(forged_recommendation, forged_trace)


@pytest.mark.parametrize(
    "override",
    [
        {"demand_rates_used_balls_per_minute": (50.0,)},
        {"projection_horizon_minutes": 200.0},
    ],
)
def test_trace_demand_schedule_must_match_declared_policy_inputs(override) -> None:
    with pytest.raises(ValueError, match="must match policy derivation"):
        _recreate_trace(**override)


def test_issuance_rejects_dispatch_before_the_alert_window() -> None:
    trace = _trace()
    quiet_config = replace(trace.policy_config, alert_lead_minutes=19.0)
    quiet_trace = _recreate_trace(trace, policy_config=quiet_config)
    premature = _recreate_recommendation(
        decision_trace_id=quiet_trace.trace_id,
    )

    with pytest.raises(ValueError, match="does not admit a recommendation"):
        recommendation_issued_event(premature, quiet_trace)


def test_two_ledger_instances_refresh_before_each_append(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    first = JsonlEventLedger(path)
    second = JsonlEventLedger(path)
    recommendation = _recommendation()
    first.append(recommendation_issued_event(recommendation, _trace()))
    second.append(
        recommendation_response_event(
            response=_response(recommendation),
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        )
    )
    request = _request(recommendation)
    record = first.append(
        execution_requested_event(
            request=request,
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        )
    )
    assert record.sequence == 3
    assert first.verify() == second.verify()


def test_modified_recommendation_round_trips_as_the_effective_record(tmp_path) -> None:
    recommendation = _recommendation()
    ledger = JsonlEventLedger(tmp_path / "events.jsonl")
    ledger.append(recommendation_issued_event(recommendation, _trace()))
    response = RecommendationResponse.create(
        recommendation_id=recommendation.recommendation_id,
        responded_at=NOW + timedelta(minutes=1),
        operator_id="operator-1",
        kind=ResponseKind.MODIFY,
        reason_code="ROUTE_CHANGED",
        original_recommendation=recommendation,
        replacement_action=RecommendationAction.OPERATOR_INTERVENTION,
        replacement_execute_before=NOW + timedelta(minutes=5),
    )
    ledger.append(
        recommendation_response_event(
            response=response,
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        )
    )
    replayed = JsonlEventLedger(ledger.path).replay().case_for(
        recommendation.recommendation_id
    )
    modified = response.modified_recommendation
    assert modified is not None
    assert replayed.recommendation == recommendation
    assert replayed.response == response
    assert replayed.effective_recommendation_id == modified.modified_recommendation_id
    assert replayed.status is CaseStatus.MODIFIED


def test_valid_hash_chain_with_invalid_workflow_order_is_rejected(tmp_path) -> None:
    recommendation = _recommendation()
    events = (
        execution_requested_event(
            request=_request(recommendation),
            site_id=recommendation.site_id,
            deployment_id=recommendation.deployment_id,
        ),
        recommendation_issued_event(recommendation, _trace()),
    )
    previous_hash = "0" * 64
    lines: list[str] = []
    for sequence, event in enumerate(events, start=1):
        body = {
            "schema_version": "nxt-pilot-ops-ledger/v1",
            "sequence": sequence,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "site_id": event.site_id,
            "deployment_id": event.deployment_id,
            "occurred_at": to_primitive(event.occurred_at),
            "payload": event.payload,
            "causation_id": event.causation_id,
            "previous_hash": previous_hash,
        }
        record_hash = stable_digest(body)
        lines.append(canonical_json({**body, "record_hash": record_hash}))
        previous_hash = record_hash
    path = tmp_path / "invalid-order.jsonl"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerIntegrityError, match="semantic integrity failure"):
        JsonlEventLedger(path)
