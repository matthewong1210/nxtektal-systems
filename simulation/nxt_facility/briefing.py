"""Human/Agent Interface: render FacilityState + Recommendations as a
plain-text manager briefing.

Architecture position: FacilityState -> Decision Rules -> Recommendation ->
**Human/Agent Interface** -> future execution layer. The human interface is
this text renderer; the agent interface is ``Recommendation.to_dict()``.
Pure string formatting — no simulator access, no RNG, no side effects.
"""

from __future__ import annotations

from typing import Optional, Sequence

from .analysis import classify_state, estimate_stockout
from .decisions import Confidence, Recommendation, Urgency, recommend
from .state import FacilityState

_TIER_HEADINGS = {
    Urgency.NOW: "DO NOW",
    Urgency.SOON: "DO NEXT HOUR",
    Urgency.WATCH: "KEEP AN EYE ON",
}

_FOOTER = (
    "Thresholds are engineering placeholders; projections use the frozen day "
    "forecast and are estimates, not measurements."
)


def _clock(minute_of_day: float) -> str:
    minutes = int(minute_of_day) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _stockout_line(state: FacilityState) -> str:
    est = estimate_stockout(state)
    if est.eta_minutes is None:
        return f"no projected stockout within {est.horizon_minutes:.0f} min"
    if est.eta_minutes <= 0:
        return "STOCKOUT IN PROGRESS"
    return f"projected stockout in ~{est.eta_minutes:.0f} min ({est.limited_by}-limited)"


def _confidence_suffix(rec: Recommendation) -> str:
    if rec.confidence is Confidence.HIGH:
        return ""
    return f" [confidence: {rec.confidence.value} — forecast-derived]"


def render_briefing(
    state: FacilityState,
    recommendations: Optional[Sequence[Recommendation]] = None,
) -> str:
    """The 20-second answer to "what should the manager do next?"."""
    recs = tuple(recommendations) if recommendations is not None else recommend(state)
    mode = classify_state(state)
    flow = state.ball_flow

    clean_pct = (
        f"{flow.clean_available / flow.total_balls * 100.0:.0f}%"
        if flow.total_balls
        else "n/a"
    )
    lines = [
        (
            f"FACILITY BRIEFING — {state.meta.scenario_name}, "
            f"{_clock(state.meta.minute_of_day)} "
            f"(day minute {state.meta.minute_of_day:.0f})"
        ),
        (
            f"Status: {mode.name} · Clean stock {flow.clean_available:,}/"
            f"{flow.total_balls:,} ({clean_pct}) · {_stockout_line(state)}"
        ),
        "",
    ]

    if not recs:
        lines.append(f"No action needed — facility {mode.name}.")
    else:
        counter = 0
        for tier in Urgency:
            tier_recs = [r for r in recs if r.urgency is tier]
            if not tier_recs:
                continue
            lines.append(_TIER_HEADINGS[tier])
            for rec in tier_recs:
                counter += 1
                lines.append(
                    f" {counter}. {rec.action} — {rec.rationale}."
                    f" Outcome: {rec.expected_outcome}.{_confidence_suffix(rec)}"
                )
        lines.append("")

    lines.append(_FOOTER)
    return "\n".join(lines)
