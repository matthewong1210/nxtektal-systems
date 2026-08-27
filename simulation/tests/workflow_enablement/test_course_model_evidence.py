"""Course Model evidence: shared spatial evidence for course workflows.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The Course World Model package never appears here as an import of the
enablement layer: composition roots derive plain-data
``CourseModelEvidence`` from the validated model, and the evaluators
cross-check every claim they can against the validated commissioned
site.  Range Operations never consumes this evidence at all.
"""

from __future__ import annotations

import dataclasses

import pytest

from nxt_commissioning import canonical_projection_json

from nxt_workflow_enablement import (
    CourseModelEvidence,
    GROUNDS_REQUIREMENTS_VERSION,
    GROUNDS_WORKFLOW_ID,
    PLAYER_CADDY_REQUIREMENTS_VERSION,
    PLAYER_CADDY_WORKFLOW_ID,
    RANGE_OPS_WORKFLOW_ID,
    REQUIRED_MAP_QUERY_KINDS,
    ReadinessVerdict,
    RequirementStatus,
    WorkflowDefinition,
    WorkflowEnablementError,
    WorkflowRegistry,
    evaluate_pilot_site,
    pilot_workflow_registry,
    plan_range_ops_launch,
)

from scripts.pilot_course_a_course_model_fixture import (
    course_model_evidence,
    pilot_course_world_model,
)

GROUNDS_MAP_PREREQUISITES = (
    "course_coordinate_reference",
    "course_model_version",
    "map_version",
)


@pytest.fixture(scope="module")
def valid_evidence() -> CourseModelEvidence:
    return course_model_evidence(pilot_course_world_model())


def evaluate(payload, expectation, context, evidence, *, course_model=None):
    return evaluate_pilot_site(
        payload,
        expectation=expectation,
        context=context,
        range_ops_evidence=evidence,
        registry=pilot_workflow_registry(),
        course_model_evidence=course_model,
    )


def by_workflow(evaluation):
    return {item.workflow_id: item for item in evaluation.workflows}


def statuses(readiness) -> dict[str, RequirementStatus]:
    return {
        item.requirement_id: item.status for item in readiness.requirements
    }


class TestEvidenceContract:
    def test_valid_evidence_is_constructed(self, valid_evidence):
        assert valid_evidence.site_id == "pilot-course-a"
        assert valid_evidence.deployment_id == "pilot-a-enablement-v0"
        assert valid_evidence.content_digest.startswith("sha256:")
        assert valid_evidence.supported_queries == REQUIRED_MAP_QUERY_KINDS

    def test_blank_identity_fields_are_rejected(self, valid_evidence):
        for field in (
            "course_model_id",
            "model_version",
            "site_id",
            "deployment_id",
            "frame_id",
            "crs_identifier",
        ):
            with pytest.raises(WorkflowEnablementError):
                dataclasses.replace(valid_evidence, **{field: "  "})

    def test_malformed_digest_shape_is_rejected(self, valid_evidence):
        for digest in ("sha256:short", "md5:" + "0" * 32, "0" * 64):
            with pytest.raises(WorkflowEnablementError):
                dataclasses.replace(valid_evidence, content_digest=digest)

    def test_non_positive_or_non_finite_resolution_is_rejected(
        self, valid_evidence
    ):
        for resolution in (0.0, -1.0, float("nan"), float("inf"), True):
            with pytest.raises(WorkflowEnablementError):
                dataclasses.replace(
                    valid_evidence, resolution_m=resolution
                )

    def test_unsorted_or_duplicate_queries_are_rejected(self, valid_evidence):
        with pytest.raises(WorkflowEnablementError):
            dataclasses.replace(
                valid_evidence,
                supported_queries=("surface", "elevation"),
            )
        with pytest.raises(WorkflowEnablementError):
            dataclasses.replace(
                valid_evidence,
                supported_queries=("elevation", "elevation"),
            )

    def test_untyped_evidence_is_rejected_by_evaluation(
        self, payload, expectation, context, range_ops_evidence
    ):
        with pytest.raises(WorkflowEnablementError):
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model={"course_model_id": "pilot-course-a.course-map"},
            )


class TestGroundsWithCourseModelEvidence:
    def test_without_evidence_the_map_prerequisites_stay_missing(
        self, payload, expectation, context, range_ops_evidence
    ):
        grounds = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )[GROUNDS_WORKFLOW_ID]
        for requirement_id in GROUNDS_MAP_PREREQUISITES:
            assert statuses(grounds)[requirement_id] is (
                RequirementStatus.MISSING
            )
        assert grounds.verdict is ReadinessVerdict.NOT_READY

    def test_valid_evidence_satisfies_exactly_the_map_prerequisites(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        without = by_workflow(
            evaluate(payload, expectation, context, range_ops_evidence)
        )[GROUNDS_WORKFLOW_ID]
        grounds = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=valid_evidence,
            )
        )[GROUNDS_WORKFLOW_ID]
        for requirement_id in GROUNDS_MAP_PREREQUISITES:
            assert statuses(grounds)[requirement_id] is (
                RequirementStatus.SATISFIED
            )
        for item in grounds.requirements:
            if item.requirement_id in GROUNDS_MAP_PREREQUISITES:
                continue
            assert item.status is statuses(without)[item.requirement_id]
            assert item.status is not RequirementStatus.SATISFIED
        assert grounds.verdict is ReadinessVerdict.NOT_READY
        assert grounds.runtime_assembly_eligible is False

    def test_satisfied_details_carry_the_model_identity(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        grounds = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=valid_evidence,
            )
        )[GROUNDS_WORKFLOW_ID]
        details = {
            item.requirement_id: item.detail
            for item in grounds.requirements
        }
        assert valid_evidence.model_version in details["course_model_version"]
        assert valid_evidence.content_digest in details["map_version"]
        assert (
            valid_evidence.crs_identifier
            in details["course_coordinate_reference"]
        )

    @pytest.mark.parametrize(
        "field, value",
        (
            ("site_id", "another-site"),
            ("deployment_id", "another-deployment"),
            ("crs_identifier", "EPSG:32650"),
            ("crs_kind", "local_cartesian"),
            ("origin_crs_x", 123.0),
        ),
    )
    def test_identity_mismatches_leave_map_prerequisites_unsatisfied(
        self,
        payload,
        expectation,
        context,
        range_ops_evidence,
        valid_evidence,
        field,
        value,
    ):
        mismatched = dataclasses.replace(valid_evidence, **{field: value})
        grounds = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=mismatched,
            )
        )[GROUNDS_WORKFLOW_ID]
        for requirement_id in GROUNDS_MAP_PREREQUISITES:
            assert statuses(grounds)[requirement_id] is (
                RequirementStatus.MISSING
            )
        assert grounds.verdict is ReadinessVerdict.NOT_READY

    def test_a_shared_site_failure_still_blocks_everything(
        self, payload, expectation, context, valid_evidence
    ):
        from tests.workflow_enablement.conftest import (
            make_range_ops_evidence,
        )

        payload["site_id"] = "tampered site id"  # whitespace: invalid
        evidence = make_range_ops_evidence(payload)
        evaluation = evaluate(
            payload,
            expectation,
            context,
            evidence,
            course_model=valid_evidence,
        )
        for readiness in evaluation.workflows:
            assert readiness.verdict is ReadinessVerdict.NOT_READY
            assert "shared_site_invalid" in readiness.failures
            assert all(
                item.status is not RequirementStatus.SATISFIED
                for item in readiness.requirements
            )


class TestPlayerCaddyWithCourseModelEvidence:
    def test_valid_evidence_satisfies_only_the_map_query_prerequisite(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        caddy = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=valid_evidence,
            )
        )[PLAYER_CADDY_WORKFLOW_ID]
        assert statuses(caddy)["course_world_model_map_query"] is (
            RequirementStatus.SATISFIED
        )
        for item in caddy.requirements:
            if item.requirement_id == "course_world_model_map_query":
                continue
            assert item.status is not RequirementStatus.SATISFIED
        assert caddy.verdict is ReadinessVerdict.NOT_READY
        assert caddy.runtime_assembly_eligible is False

    def test_partial_query_support_is_never_marked_satisfied(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        partial = dataclasses.replace(
            valid_evidence,
            supported_queries=tuple(
                kind
                for kind in valid_evidence.supported_queries
                if kind != "trajectory_terrain_intersection"
            ),
        )
        caddy = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=partial,
            )
        )[PLAYER_CADDY_WORKFLOW_ID]
        result = {
            item.requirement_id: item for item in caddy.requirements
        }["course_world_model_map_query"]
        assert result.status is RequirementStatus.MISSING
        assert "trajectory_terrain_intersection" in result.detail

    def test_identity_mismatch_leaves_the_map_query_unsatisfied(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        mismatched = dataclasses.replace(
            valid_evidence, deployment_id="another-deployment"
        )
        caddy = by_workflow(
            evaluate(
                payload,
                expectation,
                context,
                range_ops_evidence,
                course_model=mismatched,
            )
        )[PLAYER_CADDY_WORKFLOW_ID]
        assert statuses(caddy)["course_world_model_map_query"] is (
            RequirementStatus.MISSING
        )


class TestRangeOpsIsolation:
    def test_range_ops_results_are_byte_identical_with_and_without(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        without = evaluate(
            payload, expectation, context, range_ops_evidence
        )
        with_evidence = evaluate(
            payload,
            expectation,
            context,
            range_ops_evidence,
            course_model=valid_evidence,
        )
        range_without = by_workflow(without)[RANGE_OPS_WORKFLOW_ID]
        range_with = by_workflow(with_evidence)[RANGE_OPS_WORKFLOW_ID]
        assert range_without == range_with
        assert canonical_projection_json(
            {
                "requirements": [
                    dataclasses.asdict(item)
                    for item in range_without.requirements
                ],
                "verdict": range_without.verdict.value,
                "failures": list(range_without.failures),
            }
        ) == canonical_projection_json(
            {
                "requirements": [
                    dataclasses.asdict(item)
                    for item in range_with.requirements
                ],
                "verdict": range_with.verdict.value,
                "failures": list(range_with.failures),
            }
        )
        assert range_with.verdict is (
            ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        )

    def test_a_broken_course_model_cannot_break_range_ops(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        mismatched = dataclasses.replace(
            valid_evidence, site_id="another-site"
        )
        evaluation = evaluate(
            payload,
            expectation,
            context,
            range_ops_evidence,
            course_model=mismatched,
        )
        assert by_workflow(evaluation)[RANGE_OPS_WORKFLOW_ID].verdict is (
            ReadinessVerdict.READY_FOR_FIXTURE_SHADOW_MODE
        )

    def test_course_evidence_never_yields_a_course_launch_plan(
        self, payload, expectation, context, range_ops_evidence, valid_evidence
    ):
        evaluation = evaluate(
            payload,
            expectation,
            context,
            range_ops_evidence,
            course_model=valid_evidence,
        )
        for workflow_id in (GROUNDS_WORKFLOW_ID, PLAYER_CADDY_WORKFLOW_ID):
            readiness = by_workflow(evaluation)[workflow_id]
            with pytest.raises(WorkflowEnablementError):
                plan_range_ops_launch(
                    readiness=readiness,
                    shared=evaluation.shared,
                    context=context,
                    evidence=range_ops_evidence,
                )


class TestRequirementVersioning:
    def test_course_workflows_are_pinned_to_requirements_v2(self):
        assert GROUNDS_REQUIREMENTS_VERSION == (
            "course.grounds_condition_intelligence/requirements/v2"
        )
        assert PLAYER_CADDY_REQUIREMENTS_VERSION == (
            "course.player_caddy_experience/requirements/v2"
        )

    def test_a_stale_requirements_version_fails_closed(
        self, payload, expectation, context, range_ops_evidence
    ):
        registry = WorkflowRegistry(
            (
                pilot_workflow_registry().definition(RANGE_OPS_WORKFLOW_ID),
                WorkflowDefinition(
                    workflow_id=GROUNDS_WORKFLOW_ID,
                    display_label="Grounds Condition Intelligence",
                    requirements_version=(
                        "course.grounds_condition_intelligence/requirements/v1"
                    ),
                ),
                pilot_workflow_registry().definition(
                    PLAYER_CADDY_WORKFLOW_ID
                ),
            )
        )
        with pytest.raises(
            WorkflowEnablementError, match="requirements version"
        ):
            evaluate_pilot_site(
                payload,
                expectation=expectation,
                context=context,
                range_ops_evidence=range_ops_evidence,
                registry=registry,
            )
