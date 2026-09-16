"""Pilot Site Agent Service V0 — the local application boundary.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

``nxt_site_agent`` is the fixture-backed, loopback-only service shell
around the existing Site OS composition: it verifies a READY workflow
enablement report, drives the existing Agent Runtime one bounded cycle
at a time, persists the fixture source resume cursor, projects
existing canonical evidence for a local Manager Console, and
transports the existing manager workflow operations.

It owns no observation, state, assembly, policy, recommendation,
trace, workflow, ledger, or checkpoint semantics, and it has no
physical execution surface of any kind: no robot, actuator, motion,
charging, navigation, field-bus, or emergency stop path exists here,
and manager acceptance remains human workflow evidence only.  The
service binds loopback only, ships no authentication, and must not be
exposed beyond the local machine.
"""

from .api import SiteAgentApiServer
from .briefing import briefing_projection, scenario_time_label
from .contracts import (
    API_SCHEMA_VERSION,
    DISCLAIMER,
    LOOPBACK_HOSTS,
    SERVICE_EVENTS_SCHEMA,
    SERVICE_MODE_LABEL,
    SERVICE_STATE_SCHEMA,
    ComposedRuntime,
    CompositionSeam,
    LaunchMaterials,
    LaunchRefusedError,
    MaterialsFactory,
    RuntimeComposer,
    ServiceState,
    SiteAgentError,
    SourceCursor,
)
from .projections import (
    evaluation_projection,
    no_state_projection,
    recommendation_projection,
    state_projection,
)
from .service import SiteAgentService
from .state_files import ServiceStorage

__all__ = [
    "API_SCHEMA_VERSION",
    "DISCLAIMER",
    "LOOPBACK_HOSTS",
    "SERVICE_EVENTS_SCHEMA",
    "SERVICE_MODE_LABEL",
    "SERVICE_STATE_SCHEMA",
    "ComposedRuntime",
    "CompositionSeam",
    "LaunchMaterials",
    "LaunchRefusedError",
    "MaterialsFactory",
    "RuntimeComposer",
    "ServiceState",
    "ServiceStorage",
    "SiteAgentApiServer",
    "SiteAgentError",
    "SiteAgentService",
    "SourceCursor",
    "briefing_projection",
    "evaluation_projection",
    "no_state_projection",
    "recommendation_projection",
    "scenario_time_label",
    "state_projection",
]
