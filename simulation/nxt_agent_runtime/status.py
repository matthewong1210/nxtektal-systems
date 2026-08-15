"""Read-only runtime health/status contract.

Status is noncanonical operational diagnostics: it references canonical
IDs but is never evidence, never facility truth, and never an input to
any decision.  All timestamps are observation/scenario time carried from
existing records; the status object itself reads no clock.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class RuntimeState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    """One JSON-ready snapshot of runtime health."""

    site_id: str
    deployment_id: str
    runtime_state: RuntimeState
    degraded: bool
    cycles_completed: int
    evaluations_completed: int
    source_exhausted: bool
    last_observed_sequence: int | None
    last_published_sequence: int | None
    last_published_envelope_id: str | None
    site_checkpoint_has_pending_publish: bool
    last_evaluated_sequence: int | None
    last_evaluated_envelope_id: str | None
    last_evaluation_id: str | None
    last_verdict: str | None
    evaluation_checkpoint_has_pending: bool
    pending_decision_count: int
    deferred_decision_count: int
    last_observation_timestamp_s: float | None
    last_effective_confidence: float | None
    last_failure_code: str | None
    last_failure_detail: str | None

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["runtime_state"] = self.runtime_state.value
        return payload
