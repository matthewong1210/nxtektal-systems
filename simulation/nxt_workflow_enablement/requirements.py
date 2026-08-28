"""Versioned per-workflow requirement definitions and prerequisite scaffolds.

This module owns *requirement declarations*, not the facts themselves:

* the canonical telemetry channel vocabulary stays owned by the
  commissioning package's validation surface;
* the assembler's channel read-set stays owned by the telemetry package —
  the Range Operations channel templates below only declare which of
  those channels the workflow requires, and a behavioral parity test
  drives the real assembler to detect drift in either direction;
* the Grounds and Player Caddy prerequisite scaffolds declare what is
  absent.  They create no placeholder domain objects: registering a
  prerequisite never implies the capability exists.

The course-workflow requirement sets are at version 2.  Version 1
declared the map prerequisites as definitionally missing because no
Course World Model owner existed anywhere in the repository; a
canonical owner now exists, so version 2 makes exactly those
prerequisites evidence-evaluable (declared, identity-cross-checked
Course Model evidence from a composition root).  The evaluation
meaning of version 1 was never mutated in place -- evaluators pin the
version they implement and fail closed on any other, so a stale
version-1 registry can never be silently evaluated under version-2
semantics.  The baseline functions below remain the no-evidence
result: without declared evidence every prerequisite is still
unsatisfied, and the Range Operations requirement set is unchanged at
version 1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .identity import (
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
    _require_non_blank,
)

RANGE_OPS_REQUIREMENTS_VERSION = f"{RANGE_OPS_WORKFLOW_ID}/requirements/v1"
GROUNDS_REQUIREMENTS_VERSION = f"{GROUNDS_WORKFLOW_ID}/requirements/v2"
PLAYER_CADDY_REQUIREMENTS_VERSION = (
    f"{PLAYER_CADDY_WORKFLOW_ID}/requirements/v2"
)

# The map-query kinds the Player Caddy workflow requires from a
# declared Course World Model map-query surface.  This declaration is
# this layer's own fact; a two-directional parity test pins it against
# the real query-service surface so drift in either direction fails
# the suite.
REQUIRED_MAP_QUERY_KINDS = (
    "elevation",
    "hole_context",
    "nearby_hazards",
    "restricted_area",
    "slope",
    "surface",
    "trajectory_terrain_intersection",
)

# The Grounds prerequisites that valid, identity-matched Course Model
# evidence can satisfy in requirements v2.  Everything else in the
# scaffold stays independently unsatisfied.
GROUNDS_MAP_PREREQUISITE_IDS = (
    "course_coordinate_reference",
    "course_model_version",
    "map_version",
)

_REQUIREMENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class RequirementStatus(StrEnum):
    """The closed prerequisite vocabulary the report must distinguish."""

    SATISFIED = "satisfied"
    MISSING = "missing"
    UNSUPPORTED_IN_V0 = "unsupported_in_v0"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class RequirementResult:
    """One evaluated requirement: identity, status, and honest detail."""

    requirement_id: str
    status: RequirementStatus
    detail: str

    def __post_init__(self) -> None:
        _require_non_blank("requirement_id", self.requirement_id)
        if not _REQUIREMENT_ID_PATTERN.fullmatch(self.requirement_id):
            raise WorkflowEnablementError(
                f"requirement_id {self.requirement_id!r} must be lowercase "
                "letters, digits, and underscores"
            )
        if not isinstance(self.status, RequirementStatus):
            raise WorkflowEnablementError(
                "status must be a RequirementStatus"
            )
        _require_non_blank("detail", self.detail)


def pilot_workflow_registry() -> WorkflowRegistry:
    """The exact three first-version pilot workflow registrations."""
    return WorkflowRegistry(
        (
            WorkflowDefinition(
                workflow_id=RANGE_OPS_WORKFLOW_ID,
                display_label="Range Operations — Closed-Loop Collection Handoff",
                requirements_version=RANGE_OPS_REQUIREMENTS_VERSION,
            ),
            WorkflowDefinition(
                workflow_id=GROUNDS_WORKFLOW_ID,
                display_label="Grounds Condition Intelligence",
                requirements_version=GROUNDS_REQUIREMENTS_VERSION,
            ),
            WorkflowDefinition(
                workflow_id=PLAYER_CADDY_WORKFLOW_ID,
                display_label="Player Caddy Experience",
                requirements_version=PLAYER_CADDY_REQUIREMENTS_VERSION,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Range Operations required channels (requirements v1)
# ---------------------------------------------------------------------------

RANGE_OPS_SITE_CHANNELS = (
    "charger.site.queue_length",
    "inventory.dispenser.count",
    "inventory.dispenser.sensed",
    "staff.site.busy",
    "staff.site.queued",
    "wash.washer.wip",
)
RANGE_OPS_ZONE_CHANNEL_TEMPLATES = (
    "scan.zone.{zone_id}.balls",
    "zone.{zone_id}.is_open",
)
RANGE_OPS_STATION_CHANNEL_TEMPLATES = (
    "inventory.station.{station_id}.buffer_balls",
    "station.{station_id}.docked",
    "station.{station_id}.is_open",
    "station.{station_id}.queue_length",
)
RANGE_OPS_ROBOT_FIELDS = (
    "activity",
    "assigned_zone",
    "awaiting_human",
    "battery_frac",
    "destination",
    "estop_latched",
    "health",
    "location",
    "payload_balls",
)


def _validated_ids(name: str, values: object) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise WorkflowEnablementError(
            f"{name} must be a non-empty tuple of identifiers"
        )
    seen: set[str] = set()
    for value in values:
        if type(value) is not str or not _IDENTIFIER_PATTERN.fullmatch(value):
            raise WorkflowEnablementError(
                f"{name} entry {value!r} is not a valid identifier"
            )
        if value in seen:
            raise WorkflowEnablementError(
                f"{name} entry {value!r} is duplicated"
            )
        seen.add(value)
    return tuple(values)


def required_range_ops_channels(
    *,
    zone_ids: tuple[str, ...],
    station_ids: tuple[str, ...],
    robot_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """The complete channel set Range Operations v1 requires for a site.

    Every channel here must arrive with status OK for the publication
    quality gate to admit a frame, so readiness demands that each one is
    covered by either a commissioned adapter binding or an explicitly
    declared non-adapter fixture input.
    """
    zones = _validated_ids("zone_ids", zone_ids)
    stations = _validated_ids("station_ids", station_ids)
    robots = _validated_ids("robot_ids", robot_ids)
    channels = list(RANGE_OPS_SITE_CHANNELS)
    channels.extend(
        template.format(zone_id=zone_id)
        for zone_id in zones
        for template in RANGE_OPS_ZONE_CHANNEL_TEMPLATES
    )
    channels.extend(
        template.format(station_id=station_id)
        for station_id in stations
        for template in RANGE_OPS_STATION_CHANNEL_TEMPLATES
    )
    channels.extend(
        f"robot.{robot_id}.{field}"
        for robot_id in robots
        for field in RANGE_OPS_ROBOT_FIELDS
    )
    return tuple(sorted(channels))


# ---------------------------------------------------------------------------
# Grounds Condition Intelligence prerequisite scaffold (requirements v2)
#
# Registration is not implementation: every entry below reports why the
# prerequisite is unsatisfied without evidence.  Nothing here creates a
# course model, a camera, an inspection record, or any other
# placeholder domain object.  The three map prerequisites are the
# no-evidence baseline: declared, identity-matched Course Model
# evidence can flip exactly them to SATISFIED during evaluation.
# ---------------------------------------------------------------------------

_GROUNDS_PREREQUISITES: tuple[tuple[str, RequirementStatus, str], ...] = (
    (
        "course_model_version",
        RequirementStatus.MISSING,
        "a canonical versioned Course World Model owner exists, but no "
        "identity-matched Course Model evidence was declared for this "
        "site",
    ),
    (
        "course_coordinate_reference",
        RequirementStatus.MISSING,
        "the shared site declares a surveyed coordinate reference system, "
        "but no declared Course Model evidence binds a course frame to it",
    ),
    (
        "map_version",
        RequirementStatus.MISSING,
        "map identity is owned by the Course World Model owner; no "
        "identity-matched map-version evidence was declared for this site",
    ),
    (
        "cart_node_identity",
        RequirementStatus.MISSING,
        "no cart-node identity exists in the commissioned asset "
        "vocabulary; carts are not commissioned assets in v0",
    ),
    (
        "cart_pose_binding",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "the closed canonical telemetry channel vocabulary has no pose "
        "channel, so a cart pose cannot be commissioned today",
    ),
    (
        "camera_device_binding",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "the commissioning sensor vocabulary declares a camera sensor "
        "type, but the closed channel vocabulary has no camera channel, "
        "so a camera binding cannot be commissioned today",
    ),
    (
        "camera_intrinsic_calibration_reference",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "commissioned calibration records carry identity and validity "
        "only; no intrinsic-calibration reference contract exists",
    ),
    (
        "camera_extrinsic_calibration_reference",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "commissioned calibration records carry identity and validity "
        "only; no extrinsic-calibration reference contract exists",
    ),
    (
        "camera_to_cart_transform",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "no rigid-transform contract exists between commissioned assets; "
        "spatial declarations cover site geometry only",
    ),
    (
        "timestamp_sync_profile",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "no timestamp synchronization profile contract exists for "
        "multi-device capture",
    ),
    (
        "inspection_zone_definition",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "commissioned zone definitions exist, but no inspection-area "
        "semantics or coverage vocabulary exists for them",
    ),
    (
        "inspection_coverage_contract",
        RequirementStatus.DEFERRED,
        "Inspection Coverage is a deliberately deferred future contract; "
        "it is an explicit non-goal of workflow-enablement v0",
    ),
    (
        "condition_observation_contract",
        RequirementStatus.DEFERRED,
        "Course Condition Observation is a deliberately deferred future "
        "contract; it is an explicit non-goal of workflow-enablement v0",
    ),
    (
        "condition_issue_registry_contract",
        RequirementStatus.DEFERRED,
        "the Condition Issue Registry is a deliberately deferred future "
        "contract; it is an explicit non-goal of workflow-enablement v0",
    ),
    (
        "maintenance_briefing_policy",
        RequirementStatus.DEFERRED,
        "the Maintenance Briefing policy is a deliberately deferred "
        "future contract; it is an explicit non-goal of v0",
    ),
    (
        "human_review_workflow",
        RequirementStatus.DEFERRED,
        "a grounds-specific human review workflow is deliberately "
        "deferred until the condition contracts it reviews exist",
    ),
    (
        "repair_verification_semantics",
        RequirementStatus.DEFERRED,
        "repair outcome and verification semantics are deliberately "
        "deferred until the issue registry they verify exists",
    ),
)


def grounds_prerequisites() -> tuple[RequirementResult, ...]:
    """The Grounds no-evidence baseline: nothing satisfied without it."""
    return tuple(
        RequirementResult(
            requirement_id=requirement_id, status=status, detail=detail
        )
        for requirement_id, status, detail in _GROUNDS_PREREQUISITES
    )


# ---------------------------------------------------------------------------
# Player Caddy Experience prerequisite scaffold (requirements v2)
# ---------------------------------------------------------------------------

_PLAYER_CADDY_PREREQUISITES: tuple[
    tuple[str, RequirementStatus, str], ...
] = (
    (
        "course_world_model_map_query",
        RequirementStatus.MISSING,
        "a deterministic Course World Model map-query capability exists, "
        "but no identity-matched Course Model evidence was declared for "
        "this site",
    ),
    (
        "cart_pose",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "the closed canonical telemetry channel vocabulary has no pose "
        "channel, so cart pose cannot be commissioned today",
    ),
    (
        "caddy_session_contract",
        RequirementStatus.DEFERRED,
        "the CaddySessionState contract is a deliberately deferred future "
        "contract; it is an explicit non-goal of workflow-enablement v0",
    ),
    (
        "session_event_contract",
        RequirementStatus.DEFERRED,
        "the player session event contract is deliberately deferred "
        "alongside the session state contract",
    ),
    (
        "player_consent_privacy_policy",
        RequirementStatus.MISSING,
        "no player consent or privacy policy contract exists; a "
        "player-facing workflow cannot be enabled without one",
    ),
    (
        "pseudonymous_player_identity",
        RequirementStatus.MISSING,
        "no pseudonymous player identity rules exist in this repository",
    ),
    (
        "launch_monitor_adapter_or_manual_fallback",
        RequirementStatus.UNSUPPORTED_IN_V0,
        "no launch-monitor adapter family or manual shot-event fallback "
        "exists in the v0 edge adapter kit",
    ),
    (
        "ball_found_event",
        RequirementStatus.MISSING,
        "no ball-found event contract exists in the observation "
        "vocabulary",
    ),
    (
        "deterministic_landing_model_owner",
        RequirementStatus.MISSING,
        "no owner exists for a deterministic landing model; naming one "
        "requires its own architecture review",
    ),
    (
        "player_recommendation_owner",
        RequirementStatus.MISSING,
        "no owner exists for player-facing recommendations; the existing "
        "decision engines are manager-advisory only",
    ),
    (
        "session_retention_deletion_policy",
        RequirementStatus.MISSING,
        "no session retention or deletion policy contract exists",
    ),
)


def player_caddy_prerequisites() -> tuple[RequirementResult, ...]:
    """The Player Caddy no-evidence baseline: nothing satisfied without it."""
    return tuple(
        RequirementResult(
            requirement_id=requirement_id, status=status, detail=detail
        )
        for requirement_id, status, detail in _PLAYER_CADDY_PREREQUISITES
    )
