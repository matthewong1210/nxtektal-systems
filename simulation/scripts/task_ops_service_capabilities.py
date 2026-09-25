"""Static write-route capabilities for the two pilot task service modes.

These values describe which HTTP operations a composition installs.  They are
not scheduler health, record-level eligibility, execution authority, or device
capability.  Callers receive a fresh mapping so a projection consumer cannot
mutate the declaration used by a later read.
"""

from __future__ import annotations

from typing import Literal


TASK_OPS_CAPABILITIES_V1_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v1"
TASK_OPS_CAPABILITIES_V2_SCHEMA = "nxt-pilot-dispatch/service-capabilities/v2"
TASK_OPS_CAPABILITIES_SCHEMA = TASK_OPS_CAPABILITIES_V1_SCHEMA
TaskOpsServiceMode = Literal[
    "FIXED_V3_EXECUTION",
    "LEGACY_PILOT_DISPATCH",
    "CONTINUOUS_V3_EXECUTION",
]

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
_CONTINUOUS_V3_OPERATIONS = {
    "planning_inputs_create": "SUPPORTED",
    "planning_plans_create": "SUPPORTED",
    "planning_confirmations_create": "SUPPORTED",
    "planning_outcomes_create": "SUPPORTED",
    "schedules_create": "UNAVAILABLE",
    "schedules_cancel": "SUPPORTED",
    "notifications_acknowledge": "SUPPORTED",
    "notifications_resolve": "SUPPORTED",
}


def task_ops_service_capabilities(mode: TaskOpsServiceMode) -> dict[str, object]:
    """Return the frozen declaration for one explicit service composition."""

    if mode == "FIXED_V3_EXECUTION":
        schema, operations = TASK_OPS_CAPABILITIES_V1_SCHEMA, _FIXED_V3_OPERATIONS
    elif mode == "LEGACY_PILOT_DISPATCH":
        schema, operations = TASK_OPS_CAPABILITIES_V1_SCHEMA, _LEGACY_OPERATIONS
    elif mode == "CONTINUOUS_V3_EXECUTION":
        schema, operations = TASK_OPS_CAPABILITIES_V2_SCHEMA, _CONTINUOUS_V3_OPERATIONS
    else:
        raise ValueError(f"unknown task operations service mode: {mode!r}")
    return {
        "schema": schema,
        "mode": mode,
        "operations": dict(operations),
    }
