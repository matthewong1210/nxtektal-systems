"""Static write-route capabilities for the two pilot task service modes.

These values describe which HTTP operations a composition installs.  They are
not scheduler health, record-level eligibility, execution authority, or device
capability.  Callers receive a fresh mapping so a projection consumer cannot
mutate the declaration used by a later read.
"""

from __future__ import annotations

from typing import Literal


TASK_OPS_CAPABILITIES_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v1"
TaskOpsServiceMode = Literal["FIXED_V3_EXECUTION", "LEGACY_PILOT_DISPATCH"]

_FIXED_V3_OPERATIONS = {
    "planning_inputs_create": "UNAVAILABLE",
    "planning_plans_create": "UNAVAILABLE",
    "planning_confirmations_create": "UNAVAILABLE",
    "planning_outcomes_create": "SUPPORTED",
    "schedules_create": "UNAVAILABLE",
    "schedules_cancel": "UNAVAILABLE",
    "notifications_acknowledge": "SUPPORTED",
    "notifications_resolve": "SUPPORTED",
}
_LEGACY_OPERATIONS = {name: "SUPPORTED" for name in _FIXED_V3_OPERATIONS}


def task_ops_service_capabilities(mode: TaskOpsServiceMode) -> dict[str, object]:
    """Return the frozen v1 declaration for one explicit service composition."""

    if mode == "FIXED_V3_EXECUTION":
        operations = _FIXED_V3_OPERATIONS
    elif mode == "LEGACY_PILOT_DISPATCH":
        operations = _LEGACY_OPERATIONS
    else:
        raise ValueError(f"unknown task operations service mode: {mode!r}")
    return {
        "schema": TASK_OPS_CAPABILITIES_SCHEMA,
        "mode": mode,
        "operations": dict(operations),
    }
