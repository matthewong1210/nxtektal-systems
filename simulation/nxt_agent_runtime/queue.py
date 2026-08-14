"""Deterministic pending manager-decision view over Shadow Ops records.

The queue is a read-model plus thin operation helpers.  It owns no
decision semantics: recommendations, responses, transition legality, and
evidence identity all belong to ``nxt_pilot_ops``.  The queue joins the
ledger's replayed workflow cases with the runtime evaluation journal so a
manager console can see which envelope produced each pending
recommendation.

Deferral is runtime queue scheduling metadata only.  It creates no ledger
event, never mutates the immutable recommendation, and is intentionally
non-persistent: a restart reconstructs the queue from the ledger with all
deferrals cleared.  Canonical decision evidence and operational
scheduling metadata stay distinct by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from nxt_pilot_ops.contracts import Recommendation, RecommendationAction
from nxt_pilot_ops.ledger import (
    JsonlEventLedger,
    LedgerRecord,
    recommendation_response_event,
)
from nxt_pilot_ops.workflow import (
    CaseStatus,
    RecommendationCase,
    RecommendationResponse,
    ResponseKind,
)

from .journal import EvaluationJournal
from .records import EvaluationRecord


def _require_text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class DeferralNote:
    """Non-persistent runtime scheduling metadata for one pending decision."""

    deferred_until: datetime
    note: str | None

    def __post_init__(self) -> None:
        _require_aware("deferred_until", self.deferred_until)
        if self.note is not None:
            _require_text("note", self.note)


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """One deterministic queue entry projected from existing records."""

    recommendation_id: str
    action: RecommendationAction
    target_robot_id: str | None
    trace_id: str
    policy_id: str
    policy_version: str
    issued_at: datetime
    execute_before: datetime
    summary: str
    case_status: CaseStatus
    response_kind: ResponseKind | None
    source_envelope_id: str | None
    source_sequence: int | None
    evaluation_id: str | None
    deferred_until: datetime | None
    deferral_note: str | None


class ManagerDecisionQueueError(ValueError):
    pass


class ManagerDecisionQueue:
    """Pending-recommendation view plus existing workflow operations."""

    def __init__(
        self,
        *,
        ledger: JsonlEventLedger,
        journal: EvaluationJournal | None = None,
        site_id: str | None = None,
        deployment_id: str | None = None,
    ) -> None:
        if not isinstance(ledger, JsonlEventLedger):
            raise TypeError("ledger must be a JsonlEventLedger")
        if journal is not None and not isinstance(journal, EvaluationJournal):
            raise TypeError("journal must be an EvaluationJournal or None")
        if (site_id is None) != (deployment_id is None):
            raise ValueError(
                "site_id and deployment_id must be provided together"
            )
        if site_id is not None:
            _require_text("site_id", site_id)
            _require_text("deployment_id", deployment_id)
        self._ledger = ledger
        self._journal = journal
        self._site_id = site_id
        self._deployment_id = deployment_id
        self._deferrals: dict[str, DeferralNote] = {}

    def _journal_index(self) -> Mapping[str, EvaluationRecord]:
        if self._journal is None:
            return {}
        return {
            record.recommendation_id: record
            for record in self._journal.read()
            if record.recommendation_id is not None
        }

    def _cases(self) -> tuple[RecommendationCase, ...]:
        cases = self._ledger.replay().cases
        if self._site_id is None:
            return cases
        # A scoped queue never shows or operates on another identity's
        # recommendations, even if a mis-composed ledger contains them.
        return tuple(
            case
            for case in cases
            if (case.recommendation.site_id, case.recommendation.deployment_id)
            == (self._site_id, self._deployment_id)
        )

    def _case_for(self, recommendation_id: str) -> RecommendationCase:
        _require_text("recommendation_id", recommendation_id)
        for case in self._cases():
            if case.recommendation.recommendation_id == recommendation_id:
                return case
        raise ManagerDecisionQueueError(
            f"unknown recommendation: {recommendation_id}"
        )

    def _entry(self, case: RecommendationCase) -> PendingDecision:
        recommendation: Recommendation = case.recommendation
        journal_record = self._journal_index().get(
            recommendation.recommendation_id
        )
        deferral = self._deferrals.get(recommendation.recommendation_id)
        return PendingDecision(
            recommendation_id=recommendation.recommendation_id,
            action=recommendation.action,
            target_robot_id=recommendation.target_robot_id,
            trace_id=recommendation.decision_trace_id,
            policy_id=recommendation.policy_id,
            policy_version=recommendation.policy_version,
            issued_at=recommendation.issued_at,
            execute_before=recommendation.execute_before,
            summary=recommendation.summary,
            case_status=case.status,
            response_kind=None if case.response is None else case.response.kind,
            source_envelope_id=(
                None if journal_record is None else journal_record.envelope_id
            ),
            source_sequence=(
                None
                if journal_record is None
                else journal_record.sequence_number
            ),
            evaluation_id=(
                None if journal_record is None else journal_record.evaluation_id
            ),
            deferred_until=None if deferral is None else deferral.deferred_until,
            deferral_note=None if deferral is None else deferral.note,
        )

    def entries(self) -> tuple[PendingDecision, ...]:
        """Every recommendation case in deterministic order."""
        entries = tuple(self._entry(case) for case in self._cases())
        return tuple(
            sorted(
                entries,
                key=lambda entry: (entry.issued_at, entry.recommendation_id),
            )
        )

    def pending(self) -> tuple[PendingDecision, ...]:
        """Recommendations still awaiting a manager response."""
        return tuple(
            entry
            for entry in self.entries()
            if entry.case_status is CaseStatus.PENDING
        )

    def due(self, *, as_of: datetime) -> tuple[PendingDecision, ...]:
        """Pending entries not deferred past ``as_of`` (scenario time)."""
        _require_aware("as_of", as_of)
        return tuple(
            entry
            for entry in self.pending()
            if entry.deferred_until is None or entry.deferred_until <= as_of
        )

    def entry_for(self, recommendation_id: str) -> PendingDecision:
        return self._entry(self._case_for(recommendation_id))

    def defer(
        self,
        recommendation_id: str,
        *,
        deferred_until: datetime,
        note: str | None = None,
    ) -> PendingDecision:
        """Attach non-persistent scheduling metadata to a pending decision.

        Deferral is not a policy verdict, does not modify the immutable
        recommendation, and is cleared by restart or by any recorded
        manager response.
        """
        case = self._case_for(recommendation_id)
        if case.status is not CaseStatus.PENDING:
            raise ManagerDecisionQueueError(
                "only pending recommendations can be deferred"
            )
        self._deferrals[recommendation_id] = DeferralNote(
            deferred_until=deferred_until, note=note
        )
        return self._entry(case)

    def clear_deferral(self, recommendation_id: str) -> None:
        self._deferrals.pop(recommendation_id, None)

    def _respond(
        self,
        recommendation_id: str,
        *,
        kind: ResponseKind,
        operator_id: str,
        responded_at: datetime,
        reason_code: str,
        note: str | None,
        replacement_action: RecommendationAction | None = None,
        replacement_robot_id: str | None = None,
        replacement_execute_before: datetime | None = None,
    ) -> LedgerRecord:
        case = self._case_for(recommendation_id)
        response = RecommendationResponse.create(
            recommendation_id=recommendation_id,
            responded_at=responded_at,
            operator_id=operator_id,
            kind=kind,
            reason_code=reason_code,
            note=note,
            original_recommendation=(
                case.recommendation if kind is ResponseKind.MODIFY else None
            ),
            replacement_action=replacement_action,
            replacement_robot_id=replacement_robot_id,
            replacement_execute_before=replacement_execute_before,
        )
        event = recommendation_response_event(
            response=response,
            site_id=case.recommendation.site_id,
            deployment_id=case.recommendation.deployment_id,
        )
        record = self._ledger.append(event)
        self._deferrals.pop(recommendation_id, None)
        return record

    def accept(
        self,
        recommendation_id: str,
        *,
        operator_id: str,
        responded_at: datetime,
        reason_code: str,
        note: str | None = None,
    ) -> LedgerRecord:
        """Record a human acceptance.  This is workflow evidence only; it
        does not create, imply, or schedule any robot command."""
        return self._respond(
            recommendation_id,
            kind=ResponseKind.ACCEPT,
            operator_id=operator_id,
            responded_at=responded_at,
            reason_code=reason_code,
            note=note,
        )

    def reject(
        self,
        recommendation_id: str,
        *,
        operator_id: str,
        responded_at: datetime,
        reason_code: str,
        note: str | None = None,
    ) -> LedgerRecord:
        return self._respond(
            recommendation_id,
            kind=ResponseKind.REJECT,
            operator_id=operator_id,
            responded_at=responded_at,
            reason_code=reason_code,
            note=note,
        )

    def modify(
        self,
        recommendation_id: str,
        *,
        operator_id: str,
        responded_at: datetime,
        reason_code: str,
        replacement_action: RecommendationAction,
        replacement_execute_before: datetime,
        replacement_robot_id: str | None = None,
        note: str | None = None,
    ) -> LedgerRecord:
        return self._respond(
            recommendation_id,
            kind=ResponseKind.MODIFY,
            operator_id=operator_id,
            responded_at=responded_at,
            reason_code=reason_code,
            note=note,
            replacement_action=replacement_action,
            replacement_robot_id=replacement_robot_id,
            replacement_execute_before=replacement_execute_before,
        )
