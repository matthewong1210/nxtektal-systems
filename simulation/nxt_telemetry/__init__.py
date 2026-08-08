"""NXTektal Site OS observation layer (Phase 4A).

Observation Layer -> FacilityState -> Decision Rules -> Operational Memory
-> Digital Twin. Observations are the INPUT boundary — hardware sensors,
simulation, external systems, or human inputs all emit the same contract —
and FacilityState remains the only operational truth. The synthetic bank
in this package behaves like future real sensors so the same
FacilityState -> Decision -> Memory stack runs unchanged when hardware
arrives (the parity guarantee, test-enforced).
"""

from .observations import (
    SENSED_FIELD_PREFIXES,
    Observation,
    ObservationFrame,
    ObservationStatus,
    SiteConfig,
    SourceType,
    UpstreamInputs,
    sensed_diff_paths,
)

__all__ = [
    "SENSED_FIELD_PREFIXES",
    "Observation",
    "ObservationFrame",
    "ObservationStatus",
    "SiteConfig",
    "SourceType",
    "UpstreamInputs",
    "sensed_diff_paths",
]
