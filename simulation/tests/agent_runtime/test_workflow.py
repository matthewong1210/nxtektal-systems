"""Manager decision queue over the existing Shadow Ops workflow."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from nxt_agent_runtime import (
    EvaluationJournal,
    ManagerDecisionQueue,
    ManagerDecisionQueueError,
)
from nxt_pilot_ops.contracts import RecommendationAction
from nxt_pilot_ops.ledger import JsonlEventLedger, LedgerTransitionError
from nxt_pilot_ops.workflow import CaseStatus, ResponseKind

from .conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    PilotObservationSource,
    calm_batch,
    evidence_paths,
    make_runtime,
    spike_batch,
)

RESPONDED_AT = datetime(2026, 8, 8, 19, 0, tzinfo=timezone.utc)
DEFER_UNTIL = datetime(2026, 8, 8, 19, 30, tzinfo=timezone.utc)


@pytest.fixture
def pilot_runtime(tmp_path, base_inputs):
    source = PilotObservationSource(
        [calm_batch(base_inputs), spike_batch(base_inputs)]
    )
    runtime = make_runtime(tmp_path, base_inputs, source)
    runtime.run(3)
    return runtime


def test_pending_queue_references_source_and_policy_identity(pilot_runtime):
    pending = pilot_runtime.queue.pending()
    assert len(pending) == 1
    entry = pending[0]
    assert entry.action is RecommendationAction.OPERATOR_INTERVENTION
    assert entry.case_status is CaseStatus.PENDING
    assert entry.recommendation_id.startswith("rec_")
    assert entry.trace_id.startswith("trace_")
    assert entry.source_envelope_id.startswith("fse:")
    assert entry.source_sequence == 1
    assert entry.evaluation_id.startswith("aev_")
    assert entry.issued_at == datetime(2026, 8, 8, 18, 30, tzinfo=timezone.utc)


def test_accept_records_workflow_evidence_only(pilot_runtime, tmp_path):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    record = queue.accept(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="staffing_confirmed",
        note="scripted pilot response",
    )
    assert record.event_type == "recommendation_response"
    assert queue.pending() == ()
    updated = queue.entry_for(entry.recommendation_id)
    assert updated.case_status is CaseStatus.ACCEPTED
    assert updated.response_kind is ResponseKind.ACCEPT
    # Acceptance is a human workflow record only: the ledger contains
    # exactly the issuance and the response, and no other artifact exists.
    ledger = JsonlEventLedger(evidence_paths(tmp_path)["ledger"])
    assert [item.event_type for item in ledger.records()] == [
        "recommendation_issued",
        "recommendation_response",
    ]
    assert pilot_runtime.status().pending_decision_count == 0


def test_reject_keeps_the_original_recommendation_immutable(pilot_runtime):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    queue.reject(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="risk_accepted",
    )
    updated = queue.entry_for(entry.recommendation_id)
    assert updated.case_status is CaseStatus.REJECTED
    assert updated.summary == entry.summary
    assert updated.execute_before == entry.execute_before


def test_modify_preserves_the_original_recommendation_verbatim(
    pilot_runtime, tmp_path
):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    replacement_deadline = datetime(2026, 8, 8, 20, 0, tzinfo=timezone.utc)
    queue.modify(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="extended_window",
        replacement_action=RecommendationAction.OPERATOR_INTERVENTION,
        replacement_execute_before=replacement_deadline,
    )
    updated = queue.entry_for(entry.recommendation_id)
    assert updated.case_status is CaseStatus.MODIFIED
    # The queue entry still shows the immutable original recommendation.
    assert updated.execute_before == entry.execute_before
    ledger = JsonlEventLedger(evidence_paths(tmp_path)["ledger"])
    case = ledger.replay().case_for(entry.recommendation_id)
    modified = case.response.modified_recommendation
    assert modified.original_recommendation == case.recommendation
    assert modified.execute_before == replacement_deadline
    assert case.effective_recommendation_id == (
        modified.modified_recommendation_id
    )


def test_pending_queue_reconstructs_deterministically_from_the_ledger(
    pilot_runtime, tmp_path
):
    paths = evidence_paths(tmp_path)
    rebuilt = ManagerDecisionQueue(
        ledger=JsonlEventLedger(paths["ledger"]),
        journal=EvaluationJournal(paths["journal"]),
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    assert rebuilt.pending() == pilot_runtime.queue.pending()


def test_illegal_second_response_is_rejected_by_the_ledger(pilot_runtime):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    queue.accept(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="staffing_confirmed",
    )
    with pytest.raises(LedgerTransitionError):
        queue.reject(
            entry.recommendation_id,
            operator_id="manager-2",
            responded_at=RESPONDED_AT,
            reason_code="conflicting_change",
        )
    assert queue.entry_for(entry.recommendation_id).case_status is (
        CaseStatus.ACCEPTED
    )


def test_unknown_recommendation_is_rejected(pilot_runtime):
    with pytest.raises(ManagerDecisionQueueError):
        pilot_runtime.queue.accept(
            "rec_000000000000000000000000",
            operator_id="manager-1",
            responded_at=RESPONDED_AT,
            reason_code="unknown",
        )


def test_defer_is_scheduling_metadata_only(pilot_runtime, tmp_path):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    ledger_path = evidence_paths(tmp_path)["ledger"]
    ledger_bytes = ledger_path.read_bytes()
    deferred = queue.defer(
        entry.recommendation_id,
        deferred_until=DEFER_UNTIL,
        note="await evening staffing decision",
    )
    assert deferred.deferred_until == DEFER_UNTIL
    assert deferred.case_status is CaseStatus.PENDING
    # Deferral writes no canonical evidence and hides nothing.
    assert ledger_path.read_bytes() == ledger_bytes
    assert len(queue.pending()) == 1
    assert queue.due(as_of=RESPONDED_AT) == ()
    assert len(queue.due(as_of=DEFER_UNTIL)) == 1
    assert pilot_runtime.status().deferred_decision_count == 1


def test_deferral_is_cleared_by_a_response_and_by_restart(
    pilot_runtime, tmp_path
):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    queue.defer(entry.recommendation_id, deferred_until=DEFER_UNTIL)
    queue.accept(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="staffing_confirmed",
    )
    assert queue.entry_for(entry.recommendation_id).deferred_until is None

    paths = evidence_paths(tmp_path)
    rebuilt = ManagerDecisionQueue(
        ledger=JsonlEventLedger(paths["ledger"]),
        journal=EvaluationJournal(paths["journal"]),
        site_id=SITE_ID,
        deployment_id=DEPLOYMENT_ID,
    )
    assert all(
        item.deferred_until is None for item in rebuilt.entries()
    )


def test_defer_requires_a_pending_recommendation(pilot_runtime):
    queue = pilot_runtime.queue
    entry = queue.pending()[0]
    queue.accept(
        entry.recommendation_id,
        operator_id="manager-1",
        responded_at=RESPONDED_AT,
        reason_code="staffing_confirmed",
    )
    with pytest.raises(ManagerDecisionQueueError):
        queue.defer(entry.recommendation_id, deferred_until=DEFER_UNTIL)
