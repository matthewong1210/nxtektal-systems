"""Versioned per-workflow requirement definitions.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import pytest

from nxt_workflow_enablement import (
    GROUNDS_REQUIREMENTS_VERSION,
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_REQUIREMENTS_VERSION,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_REQUIREMENTS_VERSION,
    RANGE_OPS_WORKFLOW_ID,
    RequirementStatus,
    grounds_prerequisites,
    pilot_workflow_registry,
    player_caddy_prerequisites,
    required_range_ops_channels,
)


class TestRequirementVersions:
    def test_requirement_versions_are_workflow_scoped_and_versioned(self):
        assert RANGE_OPS_REQUIREMENTS_VERSION == (
            "range.closed_loop_collection_handoff/requirements/v1"
        )
        assert GROUNDS_REQUIREMENTS_VERSION == (
            "course.grounds_condition_intelligence/requirements/v1"
        )
        assert PLAYER_CADDY_REQUIREMENTS_VERSION == (
            "course.player_caddy_experience/requirements/v1"
        )

    def test_registry_definitions_carry_their_requirement_versions(self):
        registry = pilot_workflow_registry()
        assert (
            registry.definition(RANGE_OPS_WORKFLOW_ID).requirements_version
            == RANGE_OPS_REQUIREMENTS_VERSION
        )
        assert (
            registry.definition(GROUNDS_WORKFLOW_ID).requirements_version
            == GROUNDS_REQUIREMENTS_VERSION
        )
        assert (
            registry.definition(PLAYER_CADDY_WORKFLOW_ID).requirements_version
            == PLAYER_CADDY_REQUIREMENTS_VERSION
        )


class TestRequirementStatus:
    def test_status_vocabulary_distinguishes_missing_unsupported_deferred(self):
        assert RequirementStatus.SATISFIED.value == "satisfied"
        assert RequirementStatus.MISSING.value == "missing"
        assert RequirementStatus.UNSUPPORTED_IN_V0.value == "unsupported_in_v0"
        assert RequirementStatus.DEFERRED.value == "deferred"


class TestRangeOpsChannelRequirements:
    def test_required_channels_for_the_pilot_topology(self):
        channels = required_range_ops_channels(
            zone_ids=("Z1",), station_ids=("ST1",), robot_ids=("R1", "R2")
        )
        assert channels == tuple(
            sorted(
                [
                    "inventory.dispenser.count",
                    "inventory.dispenser.sensed",
                    "wash.washer.wip",
                    "charger.site.queue_length",
                    "staff.site.busy",
                    "staff.site.queued",
                    "scan.zone.Z1.balls",
                    "zone.Z1.is_open",
                    "station.ST1.is_open",
                    "station.ST1.docked",
                    "station.ST1.queue_length",
                    "inventory.station.ST1.buffer_balls",
                ]
                + [
                    f"robot.{robot_id}.{field}"
                    for robot_id in ("R1", "R2")
                    for field in (
                        "activity",
                        "health",
                        "battery_frac",
                        "payload_balls",
                        "location",
                        "destination",
                        "assigned_zone",
                        "estop_latched",
                        "awaiting_human",
                    )
                ]
            )
        )

    def test_required_channels_are_deterministic_and_sorted(self):
        first = required_range_ops_channels(
            zone_ids=("Z1", "Z2"), station_ids=("ST1",), robot_ids=("R1",)
        )
        second = required_range_ops_channels(
            zone_ids=("Z2", "Z1"), station_ids=("ST1",), robot_ids=("R1",)
        )
        assert first == second
        assert list(first) == sorted(first)

    def test_empty_topology_ids_are_rejected(self):
        from nxt_workflow_enablement import WorkflowEnablementError

        with pytest.raises(WorkflowEnablementError):
            required_range_ops_channels(
                zone_ids=(), station_ids=("ST1",), robot_ids=("R1",)
            )
        with pytest.raises(WorkflowEnablementError):
            required_range_ops_channels(
                zone_ids=("Z1",), station_ids=("ST1",), robot_ids=()
            )


class TestGroundsPrerequisiteScaffold:
    def test_every_grounds_prerequisite_is_declared_and_unsatisfied(self):
        prerequisites = grounds_prerequisites()
        by_id = {item.requirement_id: item for item in prerequisites}
        expected_ids = {
            "course_model_version",
            "course_coordinate_reference",
            "map_version",
            "cart_node_identity",
            "cart_pose_binding",
            "camera_device_binding",
            "camera_intrinsic_calibration_reference",
            "camera_extrinsic_calibration_reference",
            "camera_to_cart_transform",
            "timestamp_sync_profile",
            "inspection_zone_definition",
            "inspection_coverage_contract",
            "condition_observation_contract",
            "condition_issue_registry_contract",
            "maintenance_briefing_policy",
            "human_review_workflow",
            "repair_verification_semantics",
        }
        assert set(by_id) == expected_ids
        assert all(
            item.status is not RequirementStatus.SATISFIED
            for item in prerequisites
        )

    def test_grounds_classification_distinguishes_the_three_kinds(self):
        by_id = {
            item.requirement_id: item.status for item in grounds_prerequisites()
        }
        # No canonical owner exists anywhere in the repository yet.
        assert by_id["course_model_version"] is RequirementStatus.MISSING
        # A commissioning vocabulary exists but cannot express this today.
        assert (
            by_id["cart_pose_binding"] is RequirementStatus.UNSUPPORTED_IN_V0
        )
        assert (
            by_id["camera_device_binding"]
            is RequirementStatus.UNSUPPORTED_IN_V0
        )
        # Deliberately deferred future contracts (explicit non-goals).
        assert (
            by_id["inspection_coverage_contract"] is RequirementStatus.DEFERRED
        )
        assert (
            by_id["condition_observation_contract"]
            is RequirementStatus.DEFERRED
        )

    def test_every_prerequisite_carries_a_non_blank_detail(self):
        assert all(
            item.detail.strip() for item in grounds_prerequisites()
        )


class TestPlayerCaddyPrerequisiteScaffold:
    def test_every_caddy_prerequisite_is_declared_and_unsatisfied(self):
        prerequisites = player_caddy_prerequisites()
        by_id = {item.requirement_id: item for item in prerequisites}
        expected_ids = {
            "course_world_model_map_query",
            "cart_pose",
            "caddy_session_contract",
            "session_event_contract",
            "player_consent_privacy_policy",
            "pseudonymous_player_identity",
            "launch_monitor_adapter_or_manual_fallback",
            "ball_found_event",
            "deterministic_landing_model_owner",
            "player_recommendation_owner",
            "session_retention_deletion_policy",
        }
        assert set(by_id) == expected_ids
        assert all(
            item.status is not RequirementStatus.SATISFIED
            for item in prerequisites
        )

    def test_caddy_session_contract_is_deferred_not_missing(self):
        by_id = {
            item.requirement_id: item.status
            for item in player_caddy_prerequisites()
        }
        assert by_id["caddy_session_contract"] is RequirementStatus.DEFERRED
        assert (
            by_id["launch_monitor_adapter_or_manual_fallback"]
            is RequirementStatus.UNSUPPORTED_IN_V0
        )
        assert (
            by_id["player_consent_privacy_policy"]
            is RequirementStatus.MISSING
        )
