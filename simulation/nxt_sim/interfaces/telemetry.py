"""Optional telemetry interface for adapters.

Telemetry is instrumentation, not task logic: the controller never reads it.
The scenario runner collects it after a cycle to enrich the metrics record
(collision counts, minimum clearance, contact forces when physics data is
available, ...).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TelemetryProvider(Protocol):
    """Implemented by adapters that can report instrumentation data."""

    def get_telemetry(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of instrumentation data.

        Recommended keys (adapters omit what they cannot measure):
        sim_time_s, collision_count, min_clearance_m, peak_contact_force_n,
        contact_force_available, min_stability_margin, stability_model,
        emergency_stop_triggered, emergency_stop_trigger_distance_m,
        emergency_stop_stopping_distance_m, emergency_stop_final_clearance_m,
        residual_lateral_error_m, residual_yaw_error_deg.
        """
        ...
