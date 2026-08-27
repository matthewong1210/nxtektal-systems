"""Shift Briefing projection for the Manager Console.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The briefing is a read-only, noncanonical summary of facts that
already exist in canonical evidence: published snapshot envelopes,
evaluation records, decision traces, recommendation and workflow
records, and the service's own noncanonical diagnostics.  It is not a
policy engine, it invents no outcomes, it never ranks or reconciles
recommendations, and it distinguishes every entry with an explicit
tag: OBSERVED, DETECTED, RECOMMENDED, MANAGER_DECISION, MISSING,
STALE, SERVICE, SIMULATED.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

TAG_OBSERVED = "OBSERVED"
TAG_DETECTED = "DETECTED"
TAG_RECOMMENDED = "RECOMMENDED"
TAG_MANAGER_DECISION = "MANAGER_DECISION"
TAG_MISSING = "MISSING"
TAG_STALE = "STALE"
TAG_SERVICE = "SERVICE"
TAG_SIMULATED = "SIMULATED"

_TAG_ORDER = {
    TAG_SERVICE: 0,
    TAG_OBSERVED: 1,
    TAG_MISSING: 2,
    TAG_STALE: 3,
    TAG_DETECTED: 4,
    TAG_RECOMMENDED: 5,
    TAG_MANAGER_DECISION: 6,
}


def scenario_time_label(t_s: float | None) -> str | None:
    """Render scenario seconds-since-midnight as HH:MM (pure arithmetic)."""
    if t_s is None or isinstance(t_s, bool):
        return None
    if not isinstance(t_s, (int, float)) or t_s < 0:
        return None
    minutes = int(t_s // 60)
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


def _entry(
    tag: str,
    text: str,
    *,
    t_s: float | None,
    order: int,
    references: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "tag": tag,
        "text": text,
        "scenario_t_s": t_s,
        "scenario_time": scenario_time_label(t_s),
        "order": order,
        "references": dict(references) if references else {},
    }


def _cycle_entries(
    service_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, event in enumerate(service_events):
        kind = event.get("event")
        t_s = event.get("observation_timestamp_s")
        if kind == "cycle":
            outcome = event.get("outcome")
            sequence = event.get("sequence_number")
            label = event.get("cycle_label") or "fixture cycle"
            if outcome == "evaluated" or outcome == "replay_skipped":
                entries.append(
                    _entry(
                        TAG_OBSERVED,
                        f"Cycle admitted at sequence {sequence}: {label}.",
                        t_s=t_s,
                        order=index,
                        references={
                            "envelope_id": event.get("envelope_id"),
                            "evaluation_id": event.get("evaluation_id"),
                        },
                    )
                )
            elif outcome == "rejected":
                code = event.get("failure_code")
                detail = event.get("failure_detail") or ""
                tag = TAG_MISSING
                if code == "stale_observation":
                    tag = TAG_STALE
                entries.append(
                    _entry(
                        tag,
                        (
                            f"Cycle rejected before publication "
                            f"({code}): {detail}"
                        ).strip(),
                        t_s=t_s,
                        order=index,
                        references={
                            "failure_code": code,
                            "sequence_number": sequence,
                            "cycle_label": label,
                        },
                    )
                )
            elif outcome == "source_exhausted":
                entries.append(
                    _entry(
                        TAG_SERVICE,
                        "Fixture source exhausted: the declared storyline "
                        "is complete.",
                        t_s=t_s,
                        order=index,
                    )
                )
            elif outcome == "evaluation_deferred":
                entries.append(
                    _entry(
                        TAG_SERVICE,
                        "Cycle deferred: a retryable store failure left the "
                        "frame unacknowledged for deterministic retry.",
                        t_s=t_s,
                        order=index,
                        references={
                            "failure_code": event.get("failure_code")
                        },
                    )
                )
        elif kind in ("launched", "resumed", "restarted", "reset", "stopped"):
            entries.append(
                _entry(
                    TAG_SERVICE,
                    event.get("text") or f"Service {kind}.",
                    t_s=t_s,
                    order=index,
                )
            )
        elif kind == "failure":
            entries.append(
                _entry(
                    TAG_SERVICE,
                    f"Service failure recorded: {event.get('detail')}",
                    t_s=t_s,
                    order=index,
                    references={"code": event.get("code")},
                )
            )
    return entries


def _evaluation_entries(
    evaluations: Sequence[Mapping[str, Any]], base_order: int
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, evaluation in enumerate(evaluations):
        t_s = evaluation.get("observation_timestamp_s")
        trace = evaluation.get("trace") or {}
        rationale = trace.get("rationale") or []
        missing = trace.get("missing_data_reasons") or []
        if evaluation.get("verdict") == "no_action":
            text = "Policy evaluation: NO_ACTION."
            if rationale:
                text = f"Policy evaluation: NO_ACTION — {rationale[0]}"
            entries.append(
                _entry(
                    TAG_DETECTED,
                    text,
                    t_s=t_s,
                    order=base_order + index,
                    references={
                        "evaluation_id": evaluation.get("evaluation_id"),
                        "trace_id": evaluation.get("trace_id"),
                        "missing_data_reasons": list(missing),
                    },
                )
            )
        else:
            action = evaluation.get("recommendation_action")
            entries.append(
                _entry(
                    TAG_RECOMMENDED,
                    (
                        f"Recommendation issued: {action} "
                        f"(fails closed rather than fabricating missing "
                        f"dispatch facts)."
                        if action == "operator_intervention"
                        else f"Recommendation issued: {action}."
                    ),
                    t_s=t_s,
                    order=base_order + index,
                    references={
                        "evaluation_id": evaluation.get("evaluation_id"),
                        "recommendation_id": evaluation.get(
                            "recommendation_id"
                        ),
                        "missing_data_reasons": list(missing),
                    },
                )
            )
    return entries


def _decision_entries(
    recommendations: Sequence[Mapping[str, Any]],
    base_order: int,
    scenario_seconds_for: Mapping[str, float],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(recommendations):
        response = item.get("manager_response")
        if not response:
            continue
        t_s = scenario_seconds_for.get(item.get("recommendation_id") or "")
        entries.append(
            _entry(
                TAG_MANAGER_DECISION,
                (
                    f"Manager recorded {response.get('kind')} for "
                    f"{item.get('action')} "
                    f"(reason code: {response.get('reason_code')}). "
                    "Workflow evidence only — no command was created."
                ),
                t_s=t_s,
                order=base_order + index,
                references={
                    "recommendation_id": item.get("recommendation_id"),
                    "response_id": response.get("response_id"),
                    "case_status": item.get("case_status"),
                },
            )
        )
    return entries


def briefing_projection(
    *,
    identity: Mapping[str, Any],
    state: Mapping[str, Any],
    health: Mapping[str, Any],
    evaluations: Sequence[Mapping[str, Any]],
    recommendations: Sequence[Mapping[str, Any]],
    service_events: Sequence[Mapping[str, Any]],
    response_scenario_seconds: Mapping[str, float],
    disclaimer: str,
) -> dict[str, Any]:
    """Assemble the Shift Briefing from already-projected evidence."""
    timeline = _cycle_entries(service_events)
    timeline.extend(_evaluation_entries(evaluations, len(timeline)))
    timeline.extend(
        _decision_entries(
            recommendations, len(timeline), response_scenario_seconds
        )
    )
    timeline.sort(
        key=lambda item: (
            item["scenario_t_s"] if item["scenario_t_s"] is not None else -1.0,
            _TAG_ORDER.get(item["tag"], 9),
            item["order"],
        )
    )

    admitted = sum(
        1
        for event in service_events
        if event.get("event") == "cycle"
        and event.get("outcome") in ("evaluated", "replay_skipped")
    )
    rejected_events = [
        event
        for event in service_events
        if event.get("event") == "cycle" and event.get("outcome") == "rejected"
    ]

    exceptions: list[dict[str, Any]] = []
    for event in rejected_events:
        exceptions.append(
            {
                "kind": "rejected_cycle",
                "tag": (
                    TAG_STALE
                    if event.get("failure_code") == "stale_observation"
                    else TAG_MISSING
                ),
                "failure_code": event.get("failure_code"),
                "detail": event.get("failure_detail"),
                "scenario_time": scenario_time_label(
                    event.get("observation_timestamp_s")
                ),
                "cycle_label": event.get("cycle_label"),
            }
        )
    quality = state.get("quality") or {}
    report = quality.get("assembly_report") or {}
    for channel in report.get("missing_channels") or []:
        exceptions.append(
            {"kind": "missing_channel", "tag": TAG_MISSING, "channel": channel}
        )
    for channel in report.get("stale_channels") or []:
        exceptions.append(
            {"kind": "stale_channel", "tag": TAG_STALE, "channel": channel}
        )
    if health.get("degraded"):
        exceptions.append(
            {
                "kind": "service_degraded",
                "tag": TAG_SERVICE,
                "detail": health.get("last_failure_detail"),
            }
        )
    if health.get("service_state") == "failed":
        exceptions.append(
            {
                "kind": "service_failed",
                "tag": TAG_SERVICE,
                "detail": health.get("last_failure_detail"),
            }
        )

    pending = [
        item
        for item in recommendations
        if item.get("case_status") == "pending"
    ]
    decided = [
        item for item in recommendations if item.get("manager_response")
    ]
    no_action = [
        item for item in evaluations if item.get("verdict") == "no_action"
    ]

    unresolved: list[str] = []
    for item in pending:
        unresolved.append(
            f"Recommendation awaiting manager review: {item.get('summary')}"
        )
    for exception in exceptions:
        if exception["kind"] == "rejected_cycle":
            unresolved.append(
                "Rejected fixture cycle needs attention: "
                f"{exception.get('failure_code')}"
            )
    if health.get("service_state") == "failed":
        unresolved.append(
            "Service is in a failed state and refuses further cycles."
        )

    return {
        "disclaimer": disclaimer,
        "identity": dict(identity),
        "labels": {
            "mode": TAG_SIMULATED,
            "source": "fixture",
        },
        "current_state": state,
        "cycles": {
            "admitted": admitted,
            "rejected": len(rejected_events),
        },
        "timeline": timeline,
        "no_action_records": no_action,
        "pending_review": pending,
        "manager_decisions": decided,
        "exceptions": exceptions,
        "unresolved": unresolved,
    }


__all__ = [
    "TAG_DETECTED",
    "TAG_MANAGER_DECISION",
    "TAG_MISSING",
    "TAG_OBSERVED",
    "TAG_RECOMMENDED",
    "TAG_SERVICE",
    "TAG_SIMULATED",
    "TAG_STALE",
    "briefing_projection",
    "scenario_time_label",
]
