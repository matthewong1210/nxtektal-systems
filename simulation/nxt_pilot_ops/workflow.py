"""Immutable human-workflow records for Shadow Ops recommendations.

Nothing in this module is an execution or robot-command interface.  Execution
request and acknowledgement objects record human workflow only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .contracts import Recommendation, RecommendationAction
from .serialization import stable_digest


def _require_text(name: str, value: object) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_text(name, value)


def _require_aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _require_bool(name: str, value: object) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a boolean")
    return value


def _require_int(name: str, value: object, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _require_number(name: str, value: object, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    if converted < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return converted


def _require_id(name: str, value: object, prefix: str, seed: object) -> str:
    identifier = _require_text(name, value)
    expected = f"{prefix}_{stable_digest(seed)[:24]}"
    if identifier != expected:
        raise ValueError(f"{name} does not match its deterministic content")
    return identifier


class ResponseKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"


class CaseStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXECUTION_REQUESTED = "execution_requested"
    EXECUTION_ACKNOWLEDGED = "execution_acknowledged"
    OUTCOME_RECORDED = "outcome_recorded"


@dataclass(frozen=True, slots=True)
class ModifiedRecommendation:
    """An operator-authored alternative that preserves the policy output verbatim."""

    modified_recommendation_id: str
    original_recommendation: Recommendation
    modified_at: datetime
    operator_id: str
    action: RecommendationAction
    target_robot_id: str | None
    execute_before: datetime
    reason_code: str
    note: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.original_recommendation, Recommendation):
            raise TypeError("original_recommendation must be a Recommendation")
        _require_aware("modified_at", self.modified_at)
        _require_aware("execute_before", self.execute_before)
        _require_text("operator_id", self.operator_id)
        _require_text("reason_code", self.reason_code)
        _require_optional_text("note", self.note)
        if not isinstance(self.action, RecommendationAction):
            raise TypeError("action must be a RecommendationAction")
        if self.action is RecommendationAction.DISPATCH_COLLECTOR:
            _require_text("target_robot_id", self.target_robot_id)
        elif self.target_robot_id is not None:
            raise ValueError("operator intervention cannot target a robot")
        if self.modified_at < self.original_recommendation.issued_at:
            raise ValueError("modification cannot precede recommendation issuance")
        if self.execute_before < self.modified_at:
            raise ValueError("modified execute_before cannot precede modification")
        _require_id(
            "modified_recommendation_id",
            self.modified_recommendation_id,
            "mod",
            self._id_seed(),
        )

    def _id_seed(self) -> dict[str, object]:
        return {
            "original_recommendation_id": self.original_recommendation.recommendation_id,
            "modified_at": self.modified_at,
            "operator_id": self.operator_id,
            "action": self.action,
            "target_robot_id": self.target_robot_id,
            "execute_before": self.execute_before,
            "reason_code": self.reason_code,
            "note": self.note,
        }

    @classmethod
    def create(
        cls,
        *,
        original_recommendation: Recommendation,
        modified_at: datetime,
        operator_id: str,
        action: RecommendationAction,
        target_robot_id: str | None,
        execute_before: datetime,
        reason_code: str,
        note: str | None = None,
    ) -> "ModifiedRecommendation":
        seed = {
            "original_recommendation_id": original_recommendation.recommendation_id,
            "modified_at": modified_at,
            "operator_id": operator_id,
            "action": action,
            "target_robot_id": target_robot_id,
            "execute_before": execute_before,
            "reason_code": reason_code,
            "note": note,
        }
        return cls(
            modified_recommendation_id=f"mod_{stable_digest(seed)[:24]}",
            original_recommendation=original_recommendation,
            modified_at=modified_at,
            operator_id=operator_id,
            action=action,
            target_robot_id=target_robot_id,
            execute_before=execute_before,
            reason_code=reason_code,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class RecommendationResponse:
    """One immutable accept, reject, or modify response to a recommendation."""

    response_id: str
    recommendation_id: str
    responded_at: datetime
    operator_id: str
    kind: ResponseKind
    reason_code: str
    note: str | None = None
    modified_recommendation: ModifiedRecommendation | None = None

    def __post_init__(self) -> None:
        _require_text("recommendation_id", self.recommendation_id)
        _require_aware("responded_at", self.responded_at)
        _require_text("operator_id", self.operator_id)
        _require_text("reason_code", self.reason_code)
        _require_optional_text("note", self.note)
        if not isinstance(self.kind, ResponseKind):
            raise TypeError("kind must be a ResponseKind")
        if self.kind is ResponseKind.MODIFY:
            modified = self.modified_recommendation
            if modified is None:
                raise ValueError("modify responses require a modified recommendation")
            if modified.original_recommendation.recommendation_id != self.recommendation_id:
                raise ValueError("modified recommendation does not preserve the response original")
            if modified.modified_at != self.responded_at:
                raise ValueError("modification time must equal response time")
            if modified.operator_id != self.operator_id:
                raise ValueError("modification operator must equal response operator")
            if modified.reason_code != self.reason_code or modified.note != self.note:
                raise ValueError("modification reason and note must equal the response")
        elif self.modified_recommendation is not None:
            raise ValueError("only modify responses may include a modified recommendation")
        _require_id("response_id", self.response_id, "resp", self._id_seed())

    def _id_seed(self) -> dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "responded_at": self.responded_at,
            "operator_id": self.operator_id,
            "kind": self.kind,
            "reason_code": self.reason_code,
            "note": self.note,
            "modified_recommendation": self.modified_recommendation,
        }

    @property
    def replacement_action(self) -> RecommendationAction | None:
        modified = self.modified_recommendation
        return None if modified is None else modified.action

    @property
    def replacement_robot_id(self) -> str | None:
        modified = self.modified_recommendation
        return None if modified is None else modified.target_robot_id

    @property
    def replacement_execute_before(self) -> datetime | None:
        modified = self.modified_recommendation
        return None if modified is None else modified.execute_before

    @classmethod
    def create(
        cls,
        *,
        recommendation_id: str,
        responded_at: datetime,
        operator_id: str,
        kind: ResponseKind,
        reason_code: str,
        note: str | None = None,
        original_recommendation: Recommendation | None = None,
        replacement_action: RecommendationAction | None = None,
        replacement_robot_id: str | None = None,
        replacement_execute_before: datetime | None = None,
    ) -> "RecommendationResponse":
        modified: ModifiedRecommendation | None = None
        replacement_values = (
            replacement_action,
            replacement_robot_id,
            replacement_execute_before,
        )
        if kind is ResponseKind.MODIFY:
            if original_recommendation is None:
                raise ValueError("modify responses require original_recommendation")
            if original_recommendation.recommendation_id != recommendation_id:
                raise ValueError("original recommendation ID does not match response")
            if replacement_action is None or replacement_execute_before is None:
                raise ValueError("modify responses require replacement action and execute_before")
            modified = ModifiedRecommendation.create(
                original_recommendation=original_recommendation,
                modified_at=responded_at,
                operator_id=operator_id,
                action=replacement_action,
                target_robot_id=replacement_robot_id,
                execute_before=replacement_execute_before,
                reason_code=reason_code,
                note=note,
            )
        elif original_recommendation is not None or any(
            value is not None for value in replacement_values
        ):
            raise ValueError("replacement fields are only valid for modify responses")
        seed = {
            "recommendation_id": recommendation_id,
            "responded_at": responded_at,
            "operator_id": operator_id,
            "kind": kind,
            "reason_code": reason_code,
            "note": note,
            "modified_recommendation": modified,
        }
        return cls(
            response_id=f"resp_{stable_digest(seed)[:24]}",
            recommendation_id=recommendation_id,
            responded_at=responded_at,
            operator_id=operator_id,
            kind=kind,
            reason_code=reason_code,
            note=note,
            modified_recommendation=modified,
        )


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Human workflow record; creating it does not issue an execution command."""

    request_id: str
    recommendation_id: str
    effective_recommendation_id: str
    requested_at: datetime
    requested_by: str
    note: str | None = None

    def __post_init__(self) -> None:
        _require_text("recommendation_id", self.recommendation_id)
        _require_text("effective_recommendation_id", self.effective_recommendation_id)
        _require_aware("requested_at", self.requested_at)
        _require_text("requested_by", self.requested_by)
        _require_optional_text("note", self.note)
        _require_id("request_id", self.request_id, "exec_req", self._id_seed())

    def _id_seed(self) -> dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "effective_recommendation_id": self.effective_recommendation_id,
            "requested_at": self.requested_at,
            "requested_by": self.requested_by,
            "note": self.note,
        }

    @classmethod
    def create(
        cls,
        *,
        recommendation_id: str,
        effective_recommendation_id: str,
        requested_at: datetime,
        requested_by: str,
        note: str | None = None,
    ) -> "ExecutionRequest":
        seed = {
            "recommendation_id": recommendation_id,
            "effective_recommendation_id": effective_recommendation_id,
            "requested_at": requested_at,
            "requested_by": requested_by,
            "note": note,
        }
        return cls(
            request_id=f"exec_req_{stable_digest(seed)[:24]}",
            recommendation_id=recommendation_id,
            effective_recommendation_id=effective_recommendation_id,
            requested_at=requested_at,
            requested_by=requested_by,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class ExecutionAcknowledgement:
    """Human acknowledgement record; it is not a robot or actuator acknowledgement."""

    acknowledgement_id: str
    recommendation_id: str
    effective_recommendation_id: str
    request_id: str
    acknowledged_at: datetime
    acknowledged_by: str
    note: str | None = None

    def __post_init__(self) -> None:
        _require_text("recommendation_id", self.recommendation_id)
        _require_text("effective_recommendation_id", self.effective_recommendation_id)
        _require_text("request_id", self.request_id)
        _require_aware("acknowledged_at", self.acknowledged_at)
        _require_text("acknowledged_by", self.acknowledged_by)
        _require_optional_text("note", self.note)
        _require_id(
            "acknowledgement_id",
            self.acknowledgement_id,
            "exec_ack",
            self._id_seed(),
        )

    def _id_seed(self) -> dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "effective_recommendation_id": self.effective_recommendation_id,
            "request_id": self.request_id,
            "acknowledged_at": self.acknowledged_at,
            "acknowledged_by": self.acknowledged_by,
            "note": self.note,
        }

    @classmethod
    def create(
        cls,
        *,
        recommendation_id: str,
        effective_recommendation_id: str,
        request_id: str,
        acknowledged_at: datetime,
        acknowledged_by: str,
        note: str | None = None,
    ) -> "ExecutionAcknowledgement":
        seed = {
            "recommendation_id": recommendation_id,
            "effective_recommendation_id": effective_recommendation_id,
            "request_id": request_id,
            "acknowledged_at": acknowledged_at,
            "acknowledged_by": acknowledged_by,
            "note": note,
        }
        return cls(
            acknowledgement_id=f"exec_ack_{stable_digest(seed)[:24]}",
            recommendation_id=recommendation_id,
            effective_recommendation_id=effective_recommendation_id,
            request_id=request_id,
            acknowledged_at=acknowledged_at,
            acknowledged_by=acknowledged_by,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class RecommendationOutcome:
    outcome_id: str
    recommendation_id: str
    effective_recommendation_id: str
    recorded_at: datetime
    executed: bool
    actual_start_at: datetime | None
    actual_completed_at: datetime | None
    balls_returned: int
    stockout_occurred: bool
    stockout_minutes: float
    note: str | None = None

    def __post_init__(self) -> None:
        _require_text("recommendation_id", self.recommendation_id)
        _require_text("effective_recommendation_id", self.effective_recommendation_id)
        _require_aware("recorded_at", self.recorded_at)
        _require_bool("executed", self.executed)
        _require_bool("stockout_occurred", self.stockout_occurred)
        _require_int("balls_returned", self.balls_returned)
        stockout_minutes = _require_number("stockout_minutes", self.stockout_minutes)
        object.__setattr__(self, "stockout_minutes", stockout_minutes)
        _require_optional_text("note", self.note)
        if self.actual_start_at is not None:
            _require_aware("actual_start_at", self.actual_start_at)
        if self.actual_completed_at is not None:
            _require_aware("actual_completed_at", self.actual_completed_at)
        if self.executed:
            if self.actual_start_at is None or self.actual_completed_at is None:
                raise ValueError("executed outcomes require start and completion times")
        elif (
            self.actual_start_at is not None
            or self.actual_completed_at is not None
            or self.balls_returned != 0
        ):
            raise ValueError("non-executed outcomes cannot contain execution results")
        if (
            self.actual_start_at is not None
            and self.actual_completed_at is not None
            and self.actual_completed_at < self.actual_start_at
        ):
            raise ValueError("actual_completed_at cannot precede actual_start_at")
        if self.actual_completed_at is not None and self.actual_completed_at > self.recorded_at:
            raise ValueError("actual_completed_at cannot follow outcome recording")
        if not self.stockout_occurred and stockout_minutes != 0.0:
            raise ValueError("stockout_minutes must be zero when no stockout occurred")
        _require_id("outcome_id", self.outcome_id, "out", self._id_seed())

    def _id_seed(self) -> dict[str, object]:
        return {
            "recommendation_id": self.recommendation_id,
            "effective_recommendation_id": self.effective_recommendation_id,
            "recorded_at": self.recorded_at,
            "executed": self.executed,
            "actual_start_at": self.actual_start_at,
            "actual_completed_at": self.actual_completed_at,
            "balls_returned": self.balls_returned,
            "stockout_occurred": self.stockout_occurred,
            "stockout_minutes": self.stockout_minutes,
            "note": self.note,
        }

    @classmethod
    def create(
        cls,
        *,
        recommendation_id: str,
        effective_recommendation_id: str | None = None,
        recorded_at: datetime,
        executed: bool,
        actual_start_at: datetime | None,
        actual_completed_at: datetime | None,
        balls_returned: int,
        stockout_occurred: bool,
        stockout_minutes: float,
        note: str | None = None,
    ) -> "RecommendationOutcome":
        effective_id = (
            recommendation_id
            if effective_recommendation_id is None
            else effective_recommendation_id
        )
        normalized_minutes = _require_number("stockout_minutes", stockout_minutes)
        seed = {
            "recommendation_id": recommendation_id,
            "effective_recommendation_id": effective_id,
            "recorded_at": recorded_at,
            "executed": executed,
            "actual_start_at": actual_start_at,
            "actual_completed_at": actual_completed_at,
            "balls_returned": balls_returned,
            "stockout_occurred": stockout_occurred,
            "stockout_minutes": normalized_minutes,
            "note": note,
        }
        return cls(
            outcome_id=f"out_{stable_digest(seed)[:24]}",
            recommendation_id=recommendation_id,
            effective_recommendation_id=effective_id,
            recorded_at=recorded_at,
            executed=executed,
            actual_start_at=actual_start_at,
            actual_completed_at=actual_completed_at,
            balls_returned=balls_returned,
            stockout_occurred=stockout_occurred,
            stockout_minutes=normalized_minutes,
            note=note,
        )


@dataclass(frozen=True, slots=True)
class RecommendationCase:
    recommendation: Recommendation
    response: RecommendationResponse | None = None
    execution_request: ExecutionRequest | None = None
    execution_acknowledgement: ExecutionAcknowledgement | None = None
    outcome: RecommendationOutcome | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.recommendation, Recommendation):
            raise TypeError("recommendation must be a Recommendation")
        if self.response is not None:
            if not isinstance(self.response, RecommendationResponse):
                raise TypeError("response must be a RecommendationResponse")
            if self.response.recommendation_id != self.recommendation.recommendation_id:
                raise ValueError("response does not belong to this recommendation")
            if self.response.responded_at < self.recommendation.issued_at:
                raise ValueError("response cannot precede recommendation issuance")
            if self.response.kind is ResponseKind.MODIFY:
                assert self.response.modified_recommendation is not None
                if (
                    self.response.modified_recommendation.original_recommendation
                    != self.recommendation
                ):
                    raise ValueError("modified response did not preserve this recommendation")
        if self.execution_request is not None:
            if self.response is None or self.response.kind is ResponseKind.REJECT:
                raise ValueError("execution request requires an accepted or modified response")
            self._validate_case_reference(
                self.execution_request.recommendation_id,
                self.execution_request.effective_recommendation_id,
                "execution request",
            )
            if self.execution_request.requested_at < self.response.responded_at:
                raise ValueError("execution request cannot precede the response")
        if self.execution_acknowledgement is not None:
            if self.execution_request is None:
                raise ValueError("execution acknowledgement requires an execution request")
            self._validate_case_reference(
                self.execution_acknowledgement.recommendation_id,
                self.execution_acknowledgement.effective_recommendation_id,
                "execution acknowledgement",
            )
            if self.execution_acknowledgement.request_id != self.execution_request.request_id:
                raise ValueError("execution acknowledgement references the wrong request")
            if (
                self.execution_acknowledgement.acknowledged_at
                < self.execution_request.requested_at
            ):
                raise ValueError("execution acknowledgement cannot precede its request")
        if self.outcome is not None:
            if self.response is None:
                raise ValueError("outcome requires an operator response")
            self._validate_case_reference(
                self.outcome.recommendation_id,
                self.outcome.effective_recommendation_id,
                "outcome",
            )
            latest = self.response.responded_at
            if self.execution_request is not None:
                latest = self.execution_request.requested_at
            if self.execution_acknowledgement is not None:
                latest = self.execution_acknowledgement.acknowledged_at
            if self.outcome.recorded_at < latest:
                raise ValueError("outcome cannot precede the prior workflow event")
            if self.response.kind is ResponseKind.REJECT and self.outcome.executed:
                raise ValueError("a rejected recommendation cannot have an executed outcome")
            if self.outcome.executed:
                if self.execution_acknowledgement is None:
                    raise ValueError("executed outcome requires execution acknowledgement")
                assert self.outcome.actual_start_at is not None
                if (
                    self.outcome.actual_start_at
                    < self.execution_acknowledgement.acknowledged_at
                ):
                    raise ValueError("actual execution cannot precede acknowledgement")

    @property
    def effective_recommendation_id(self) -> str:
        if self.response is not None and self.response.kind is ResponseKind.MODIFY:
            assert self.response.modified_recommendation is not None
            return self.response.modified_recommendation.modified_recommendation_id
        return self.recommendation.recommendation_id

    @property
    def status(self) -> CaseStatus:
        if self.outcome is not None:
            return CaseStatus.OUTCOME_RECORDED
        if self.execution_acknowledgement is not None:
            return CaseStatus.EXECUTION_ACKNOWLEDGED
        if self.execution_request is not None:
            return CaseStatus.EXECUTION_REQUESTED
        if self.response is None:
            return CaseStatus.PENDING
        return {
            ResponseKind.ACCEPT: CaseStatus.ACCEPTED,
            ResponseKind.REJECT: CaseStatus.REJECTED,
            ResponseKind.MODIFY: CaseStatus.MODIFIED,
        }[self.response.kind]

    def _validate_case_reference(
        self,
        recommendation_id: str,
        effective_recommendation_id: str,
        label: str,
    ) -> None:
        if recommendation_id != self.recommendation.recommendation_id:
            raise ValueError(f"{label} does not belong to this recommendation")
        if effective_recommendation_id != self.effective_recommendation_id:
            raise ValueError(f"{label} references the wrong effective recommendation")

    def apply_response(self, response: RecommendationResponse) -> "RecommendationCase":
        if self.response is not None:
            raise ValueError("a recommendation can have only one operator response")
        if self.outcome is not None:
            raise ValueError("cannot respond after an outcome")
        return replace(self, response=response)

    def apply_execution_request(self, request: ExecutionRequest) -> "RecommendationCase":
        if self.execution_request is not None:
            raise ValueError("a recommendation can have only one execution request")
        if self.outcome is not None:
            raise ValueError("cannot request execution after an outcome")
        return replace(self, execution_request=request)

    def apply_execution_acknowledgement(
        self,
        acknowledgement: ExecutionAcknowledgement,
    ) -> "RecommendationCase":
        if self.execution_acknowledgement is not None:
            raise ValueError("an execution request can have only one acknowledgement")
        if self.outcome is not None:
            raise ValueError("cannot acknowledge execution after an outcome")
        return replace(self, execution_acknowledgement=acknowledgement)

    def apply_outcome(self, outcome: RecommendationOutcome) -> "RecommendationCase":
        if self.outcome is not None:
            raise ValueError("a recommendation can have only one recorded outcome")
        return replace(self, outcome=outcome)
