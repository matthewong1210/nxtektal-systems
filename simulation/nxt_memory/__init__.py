"""NXTektal Site OS operational memory (Phase 3).

An auditable, append-only episode store for the chain
State -> Recommendation -> Human Decision -> Execution -> Outcome.

Strictly observational: the schema cannot express causal attribution, and
the live loop (simulator, decision rules) can never read this store — that
boundary is test-enforced in both directions. Human decisions are recorded
inputs supplied by whatever drives the loop; this layer never generates
them.
"""

from .queries import (
    NON_CAUSAL_CAVEAT,
    outcome_deltas_by_decision,
    rule_acceptance_rates,
    stockout_eta_calibration,
)
from .recorder import EpisodeMemoryRecorder
from .records import (
    DISCLAIMER,
    SCHEMA_VERSION,
    EpisodeMeta,
    HumanVerdict,
    MemoryWindow,
    Verdict,
    WindowStatus,
    decision_section,
    execution_section,
    iter_windows,
    load_meta,
    observation_section,
    outcome_section,
    validate_window,
)

__all__ = [
    "DISCLAIMER",
    "SCHEMA_VERSION",
    "NON_CAUSAL_CAVEAT",
    "EpisodeMemoryRecorder",
    "outcome_deltas_by_decision",
    "rule_acceptance_rates",
    "stockout_eta_calibration",
    "EpisodeMeta",
    "HumanVerdict",
    "MemoryWindow",
    "Verdict",
    "WindowStatus",
    "decision_section",
    "execution_section",
    "iter_windows",
    "load_meta",
    "observation_section",
    "outcome_section",
    "validate_window",
]
