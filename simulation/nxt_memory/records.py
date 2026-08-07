"""Operational-memory contracts: one MemoryWindow per decision cycle.

Four structurally distinct sections — observation, decision, execution,
outcome — linked by living on the same window row, never merged or
inferred into each other. The schema deliberately cannot express causal
attribution: there is no score, reward, or success flag anywhere.

Determinism discipline (inherited from the episode logger): no wall-clock
values, no uuids — ids derive from (scenario, seed, seq) and timestamps
are simulation time. Records are byte-reproducible from their inputs.

This module is a pure-Python contract: stdlib imports only, so the store
can be read and written without the simulation stack installed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional, Sequence

SCHEMA_VERSION = "1.0.0"

DISCLAIMER = (
    "Placeholder-driven simulation output; does NOT represent "
    "real facility performance."
)

# Curated operational-impact dimensions (founder adjustment: outcomes must
# be operationally meaningful, never a binary success/failure).
_LABOR_KEYS = ("human_interventions_requested", "human_interventions_completed")
_SAFETY_KEYS = ("estops", "hard_failures", "unsafe_rejections")


class Verdict(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class WindowStatus(str, Enum):
    COMPLETE = "complete"
    TRUNCATED = "truncated_by_episode_end"


@dataclass(frozen=True)
class EpisodeMeta:
    """Identity + provenance for one recorded episode.

    ``site_id`` and ``deployment_id`` are driver-supplied identity for
    future multi-site deployments; simulation drivers pass stable labels.
    """

    site_id: str
    deployment_id: str
    episode_id: str
    scenario_name: str
    seed: int
    simulator_version: str
    git_commit: str
    driver_name: str
    driver_version: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class HumanVerdict:
    """One recorded human decision about one recommendation.

    Always a recorded input from the driving harness (UI, demo, test) —
    the memory layer never generates or simulates decisions. ``rec_index``
    references the deterministic tuple order of ``recommend()``;
    ``rule_id`` echoes the referenced recommendation and is checked at
    validation time.
    """

    rec_index: int
    rule_id: str
    verdict: Verdict
    decided_by: str
    note: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["verdict"] = self.verdict.value
        return payload


@dataclass(frozen=True)
class MemoryWindow:
    """One decision cycle: observation, decision, execution, outcome."""

    episode_id: str
    seq: int
    t_start_s: float
    t_end_s: float
    status: WindowStatus
    observation: dict
    decision: dict
    execution: dict
    outcome: dict

    @property
    def record_id(self) -> str:
        return f"{self.episode_id}:w{self.seq:06d}"

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_id": self.record_id,
            "episode_id": self.episode_id,
            "seq": self.seq,
            "t_start_s": self.t_start_s,
            "t_end_s": self.t_end_s,
            "status": self.status.value,
            "observation": self.observation,
            "decision": self.decision,
            "execution": self.execution,
            "outcome": self.outcome,
        }


# ---------------------------------------------------------------------------
# Section builders — the only sanctioned way to assemble window sections,
# keeping the shape discipline in one place.
# ---------------------------------------------------------------------------


def observation_section(
    state: dict,
    *,
    operational_state: str,
    stockout_eta_minutes: Optional[float],
    stockout_limited_by: str,
    indicators: dict,
) -> dict:
    """Full state verbatim, plus the derived values as the human saw them.

    The derived fields are capture-time data on purpose: analysis
    thresholds are placeholders and will drift, so recomputation under
    future code would silently rewrite the prediction the operator
    actually acted on. The stored raw state remains the recompute-audit
    fallback.
    """
    return {
        "state": state,
        "operational_state": operational_state,
        "stockout_eta_minutes": stockout_eta_minutes,
        "stockout_limited_by": stockout_limited_by,
        "indicators": indicators,
    }


def decision_section(
    recommendations: Sequence[dict],
    rule_params: dict,
    decided_by: str,
    verdicts: Sequence[HumanVerdict],
) -> dict:
    return {
        "recommendations": list(recommendations),
        "rule_params": dict(rule_params),
        "human": {
            "decided_by": decided_by,
            "verdicts": [v.to_dict() for v in verdicts],
        },
    }


def execution_section(events: Sequence[dict], cursor_start: int, cursor_end: int) -> dict:
    """What actually happened, verbatim from the event log. No
    interpretation, no matching of events to recommendations."""
    return {
        "events": list(events),
        "event_cursor_start": cursor_start,
        "event_cursor_end": cursor_end,
    }


def _availability(metrics: dict) -> float:
    """Mirrors OpsMetrics.service_availability (kept formula-identical)."""
    open_minutes = metrics.get("open_minutes_elapsed", 0.0)
    if open_minutes <= 0:
        return 1.0
    return max(0.0, 1.0 - metrics.get("stockout_minutes", 0.0) / open_minutes)


def outcome_section(metrics_before: dict, metrics_after: dict) -> dict:
    """Measured operational outcome over the window. No judgment fields."""
    deltas = {
        key: metrics_after[key] - metrics_before[key]
        for key in sorted(metrics_before)
        if key in metrics_after
        and isinstance(metrics_before[key], (int, float))
        and not isinstance(metrics_before[key], bool)
    }
    impact = {
        "availability_change": _availability(metrics_after) - _availability(metrics_before),
        "stockout_minutes": deltas.get("stockout_minutes", 0.0),
        "labor": {key: deltas.get(key, 0) for key in _LABOR_KEYS},
        "energy_wh": deltas.get("energy_wh", 0.0),
        "safety": {key: deltas.get(key, 0) for key in _SAFETY_KEYS},
    }
    return {
        "metrics_before": dict(metrics_before),
        "metrics_after": dict(metrics_after),
        "deltas": deltas,
        "impact": impact,
    }


# ---------------------------------------------------------------------------
# Validation (shared by the recorder and by tests)
# ---------------------------------------------------------------------------


def validate_window(window: MemoryWindow) -> None:
    """Integrity checks; raises ValueError on the first violation."""
    if window.seq < 0:
        raise ValueError("seq must be non-negative")
    if window.t_end_s < window.t_start_s:
        raise ValueError("t_end_s must be >= t_start_s")
    for section_name, banned_key in (("decision", "events"), ("execution", "recommendations")):
        section = getattr(window, section_name)
        if banned_key in section:
            raise ValueError(
                f"section separation violated: {section_name!r} must not "
                f"contain {banned_key!r}"
            )
    if "state" not in window.observation:
        raise ValueError("observation section must carry the full state")
    recommendations = window.decision.get("recommendations", [])
    for verdict in window.decision.get("human", {}).get("verdicts", []):
        index = verdict["rec_index"]
        if not 0 <= index < len(recommendations):
            raise ValueError(f"verdict rec_index {index} out of range")
        expected = recommendations[index].get("rule_id")
        if verdict["rule_id"] != expected:
            raise ValueError(
                f"verdict rule_id {verdict['rule_id']!r} does not echo "
                f"recommendation {index} ({expected!r})"
            )


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def iter_windows(episode_dir: str | Path) -> Iterator[dict]:
    """Yield window dicts from an episode directory, in file order."""
    path = Path(episode_dir) / "windows.jsonl"
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_meta(episode_dir: str | Path) -> dict:
    return json.loads((Path(episode_dir) / "episode.meta.json").read_text())
