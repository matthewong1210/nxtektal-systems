"""Read-only Manager API projections over existing canonical evidence.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Every function here is a pure presentation projection: it reshapes
already-published evidence (snapshot envelopes, evaluation records,
pending manager decisions, ledger trace payloads) into the versioned
local Manager API's JSON shapes.  Nothing is invented: a missing value
stays explicitly missing, a stale value stays labeled stale, and no
projection ranks, merges, or reconciles decision outputs.

These payloads are noncanonical.  They are never inputs to assembly,
policy, workflow, or execution, and they carry the fixture disclaimer
end to end.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from nxt_agent_runtime import EvaluationRecord, PendingDecision
from nxt_pilot_ops.serialization import to_primitive

_DISPENSER_COUNT_CHANNEL = "inventory.dispenser.count"
_DISPENSER_SENSED_CHANNEL = "inventory.dispenser.sensed"


def _reference_for_channel(
    envelope: Mapping[str, Any], channel: str
) -> Mapping[str, Any] | None:
    references = envelope.get("source_references")
    if not isinstance(references, list):
        return None
    for reference in references:
        if isinstance(reference, Mapping) and reference.get("channel") == channel:
            return reference
    return None


def _reading_age_s(
    reference: Mapping[str, Any] | None, scenario_now_s: float | None
) -> float | None:
    if reference is None or scenario_now_s is None:
        return None
    sample = reference.get("sample_timestamp_s")
    if not isinstance(sample, (int, float)) or isinstance(sample, bool):
        return None
    if not math.isfinite(float(sample)):
        return None
    age = float(scenario_now_s) - float(sample)
    return age if age >= 0.0 else None


def no_state_projection(reason: str) -> dict[str, Any]:
    """The explicit shape served before any envelope was published."""
    return {
        "available": False,
        "reason": reason,
        "envelope": None,
        "dispenser": None,
        "quality": None,
    }


def state_projection(
    envelope: Mapping[str, Any],
    *,
    scenario_now_s: float | None,
) -> dict[str, Any]:
    """Project the latest published snapshot envelope for the console.

    ``envelope`` is one published envelope dictionary exactly as the
    snapshot stream stores it.  The projection surfaces the dispenser
    inventory alongside its per-channel source status so a missing or
    stale reading can never be confused with a healthy zero.
    """
    facility_state = envelope.get("facility_state")
    ball_flow: Mapping[str, Any] = {}
    meta: Mapping[str, Any] = {}
    if isinstance(facility_state, Mapping):
        raw_flow = facility_state.get("ball_flow")
        if isinstance(raw_flow, Mapping):
            ball_flow = raw_flow
        raw_meta = facility_state.get("meta")
        if isinstance(raw_meta, Mapping):
            meta = raw_meta

    count_reference = _reference_for_channel(envelope, _DISPENSER_COUNT_CHANNEL)
    sensed_reference = _reference_for_channel(
        envelope, _DISPENSER_SENSED_CHANNEL
    )
    assembly_report = envelope.get("assembly_report")
    runtime_quality = envelope.get("runtime_quality")

    return {
        "available": True,
        "reason": None,
        "envelope": {
            "envelope_id": envelope.get("envelope_id"),
            "schema_version": envelope.get("schema_version"),
            "site_id": envelope.get("site_id"),
            "deployment_id": envelope.get("deployment_id"),
            "sequence_number": envelope.get("sequence_number"),
            "observation_timestamp_s": envelope.get(
                "observation_timestamp_s"
            ),
        },
        "dispenser": {
            "clean_available_balls": ball_flow.get("clean_available"),
            "clean_sensed_balls": ball_flow.get("clean_sensed"),
            "count_source": dict(count_reference) if count_reference else None,
            "sensed_source": (
                dict(sensed_reference) if sensed_reference else None
            ),
            "reading_age_s": _reading_age_s(count_reference, scenario_now_s),
        },
        "facility_meta": {
            "t_s": meta.get("t_s"),
            "minute_of_day": meta.get("minute_of_day"),
            "facility_open": meta.get("facility_open"),
            "scenario_name": meta.get("scenario_name"),
        },
        "quality": {
            "assembly_report": (
                dict(assembly_report)
                if isinstance(assembly_report, Mapping)
                else None
            ),
            "runtime_quality": (
                dict(runtime_quality)
                if isinstance(runtime_quality, Mapping)
                else None
            ),
        },
        "source_references": envelope.get("source_references"),
    }


def _trace_summary(trace: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Compact, owner-preserving view of one decision-trace payload."""
    if not isinstance(trace, Mapping):
        return None
    candidates = trace.get("candidates")
    exclusions: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            exclusions.append(
                {
                    "robot_id": candidate.get("robot_id"),
                    "eligible": candidate.get("eligible"),
                    "exclusion_reasons": candidate.get(
                        "exclusion_reasons", []
                    ),
                }
            )
    return {
        "trace_id": trace.get("trace_id"),
        "policy_id": trace.get("policy_id"),
        "policy_version": trace.get("policy_version"),
        "rationale": trace.get("rationale", []),
        "missing_data_reasons": trace.get("missing_data_reasons", []),
        "data_completeness_score": trace.get("data_completeness_score"),
        "selected_robot_id": trace.get("selected_robot_id"),
        "candidates": exclusions,
        "projected_stockout_without_action_minutes": trace.get(
            "projected_stockout_without_action_minutes"
        ),
    }


def evaluation_projection(
    record: EvaluationRecord,
    *,
    ledger_trace: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project one existing evaluation record.

    A NO_ACTION record embeds its canonical trace; a RECOMMEND record
    references the ledger issuance event, so its trace payload is
    passed in by the caller after reading the existing ledger.
    """
    embedded = record.decision_trace
    trace = embedded if embedded is not None else ledger_trace
    return {
        "evaluation_id": record.evaluation_id,
        "schema_version": record.schema_version,
        "sequence_number": record.sequence_number,
        "envelope_id": record.envelope_id,
        "observation_timestamp_s": record.observation_timestamp_s,
        "observed_at": to_primitive(record.observed_at),
        "verdict": record.verdict.value,
        "policy_id": record.policy_id,
        "policy_version": record.policy_version,
        "trace_id": record.trace_id,
        "recommendation_id": record.recommendation_id,
        "recommendation_action": (
            None
            if record.recommendation_action is None
            else record.recommendation_action.value
        ),
        "ledger_event_id": record.ledger_event_id,
        "trace": _trace_summary(trace),
    }


def recommendation_projection(
    entry: PendingDecision,
    *,
    trace: Mapping[str, Any] | None,
    recommendation: Mapping[str, Any] | None,
    response: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project one pending-manager-decision queue entry.

    The queue view already preserves owner identity; this projection
    adds the canonical trace and recommendation payloads read from the
    existing ledger, plus the recorded manager response when one
    exists.  Nothing is ranked, merged, or reconciled.
    """
    return {
        "recommendation_id": entry.recommendation_id,
        "action": entry.action.value,
        "target_robot_id": entry.target_robot_id,
        "summary": entry.summary,
        "policy_id": entry.policy_id,
        "policy_version": entry.policy_version,
        "trace_id": entry.trace_id,
        "issued_at": to_primitive(entry.issued_at),
        "execute_before": to_primitive(entry.execute_before),
        "case_status": entry.case_status.value,
        "response_kind": (
            None if entry.response_kind is None else entry.response_kind.value
        ),
        "source_envelope_id": entry.source_envelope_id,
        "source_sequence": entry.source_sequence,
        "evaluation_id": entry.evaluation_id,
        "deferred_until": to_primitive(entry.deferred_until)
        if entry.deferred_until is not None
        else None,
        "deferral_note": entry.deferral_note,
        "recommendation": (
            dict(recommendation) if recommendation is not None else None
        ),
        "trace": _trace_summary(trace),
        "manager_response": dict(response) if response is not None else None,
    }


__all__ = [
    "evaluation_projection",
    "no_state_projection",
    "recommendation_projection",
    "state_projection",
]
