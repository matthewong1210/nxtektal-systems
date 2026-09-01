"""Course World Model identity, semantic validation, digest, revisions.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import json

import pytest

from nxt_course_world_model import (
    COURSE_WORLD_MODEL_SCHEMA,
    CourseWorldModel,
    CourseWorldModelError,
    PolygonRing,
    RestrictedZone,
    RestrictionCategory,
    SurfaceFeature,
    SurfaceType,
    dumps_model,
    validate_model_against_site,
    validate_revision,
    verify_model_payload,
)

from scripts.pilot_course_a_enablement_fixture import enablement_site

from tests.course_world_model.conftest import (
    DEPLOYMENT_ID,
    SITE_ID,
    build_fixture_model,
    fixture_cart_paths,
    fixture_scan_sources,
    fixture_surfaces,
    make_frame,
    rectangle,
)


class TestIdentity:
    def test_a_valid_model_is_constructed(self, model):
        assert model.schema == COURSE_WORLD_MODEL_SCHEMA
        assert model.course_model_id == "pilot-course-a.course-map"
        assert model.model_version == "v1"
        assert model.site_id == SITE_ID
        assert model.deployment_id == DEPLOYMENT_ID
        assert model.content_digest.startswith("sha256:")
        assert len(model.content_digest) == len("sha256:") + 64

    def test_blank_or_padded_identity_fields_are_rejected(self):
        for field in ("course_model_id", "model_version", "site_id"):
            with pytest.raises(CourseWorldModelError):
                build_fixture_model(**{field: "  "})
            with pytest.raises(CourseWorldModelError):
                build_fixture_model(**{field: " padded "})

    def test_self_supersession_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(supersedes_version="v1")

    def test_effective_from_must_be_timezone_aware_iso(self):
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(effective_from="2026-07-20T00:00:00")
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(effective_from="not-a-time")

    def test_at_least_one_scan_source_is_required(self):
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(scan_sources=())

    def test_malformed_scan_source_digest_is_rejected(self):
        source = fixture_scan_sources()[0]
        import dataclasses

        # The contract rejects a malformed digest at construction time,
        # before a model could even be assembled around it.
        with pytest.raises(CourseWorldModelError):
            dataclasses.replace(source, source_digest="sha256:short")


class TestSemanticValidation:
    def test_duplicate_feature_ids_are_rejected(self):
        surfaces = fixture_surfaces()
        duplicate = SurfaceFeature(
            feature_id=surfaces[0].feature_id,
            surface_type=SurfaceType.ROUGH,
            polygon=PolygonRing(vertices=rectangle(0.0, 0.0, 2.0, 2.0)),
            hole_id=None,
        )
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(surfaces=surfaces + (duplicate,))

    def test_overlapping_primary_surface_interiors_are_rejected(self):
        surfaces = fixture_surfaces()
        overlapping = SurfaceFeature(
            feature_id="hole-7-overlap",
            surface_type=SurfaceType.ROUGH,
            polygon=PolygonRing(
                vertices=rectangle(100.0, 90.0, 140.0, 110.0)
            ),
            hole_id="hole-7",
        )
        with pytest.raises(CourseWorldModelError) as excinfo:
            build_fixture_model(surfaces=surfaces + (overlapping,))
        assert "overlap" in str(excinfo.value)

    def test_edge_sharing_primary_surfaces_are_allowed(self, model):
        # The fixture fairway (max y 120) and north rough (min y 122) do
        # not share an edge, but tee (x 20..40) and fairway (x 45..) are
        # disjoint; build a variant where two surfaces share one edge.
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="hole-7-rough-collar",
                surface_type=SurfaceType.ROUGH,
                polygon=PolygonRing(
                    vertices=rectangle(45.0, 120.0, 230.0, 122.0)
                ),
                hole_id="hole-7",
            ),
        )
        variant = build_fixture_model(surfaces=surfaces)
        assert any(
            feature.feature_id == "hole-7-rough-collar"
            for feature in variant.surfaces
        )

    def test_feature_outside_model_bounds_is_rejected(self):
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="hole-7-escapee",
                surface_type=SurfaceType.ROUGH,
                polygon=PolygonRing(
                    vertices=rectangle(290.0, 190.0, 310.0, 210.0)
                ),
                hole_id=None,
            ),
        )
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(surfaces=surfaces)

    def test_surface_referencing_an_unknown_hole_is_rejected(self):
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="hole-9-fairway",
                surface_type=SurfaceType.FAIRWAY,
                polygon=PolygonRing(
                    vertices=rectangle(0.0, 170.0, 20.0, 190.0)
                ),
                hole_id="hole-9",
            ),
        )
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(surfaces=surfaces)

    def test_overlapping_hole_boundaries_are_rejected(self):
        from tests.course_world_model.conftest import fixture_holes
        from nxt_course_world_model import HoleDefinition

        holes = fixture_holes() + (
            HoleDefinition(
                hole_id="hole-8",
                hole_number=8,
                boundary=PolygonRing(
                    vertices=rectangle(200.0, 100.0, 294.0, 194.0)
                ),
            ),
        )
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(holes=holes)

    def test_restricted_zones_may_overlap_primary_surfaces(self, model):
        # Overlays are documented to coexist with primary surfaces: the
        # fixture cart path crosses rough and fairway, and a restricted
        # zone may cover any surface.
        zones = model.restricted_zones + (
            RestrictedZone(
                feature_id="restricted-fairway-closure",
                category=RestrictionCategory.MAINTENANCE_ONLY,
                polygon=PolygonRing(
                    vertices=rectangle(60.0, 85.0, 90.0, 115.0)
                ),
                commissioned_zone_id=None,
            ),
        )
        variant = build_fixture_model(restricted_zones=zones)
        assert len(variant.restricted_zones) == 3

    def test_bounds_must_match_the_elevation_grid_coverage(self, model):
        payload = model.to_dict()
        payload["bounds"]["max_x"] = 250.0
        with pytest.raises(CourseWorldModelError):
            CourseWorldModel.from_dict(payload)

    def test_features_are_canonically_ordered(self, model):
        surface_ids = [feature.feature_id for feature in model.surfaces]
        assert surface_ids == sorted(surface_ids)
        zone_ids = [zone.feature_id for zone in model.restricted_zones]
        assert zone_ids == sorted(zone_ids)


class TestDigestAndSerialization:
    def test_serialization_is_byte_stable(self, model):
        assert dumps_model(model) == dumps_model(model)
        rebuilt = build_fixture_model()
        assert dumps_model(rebuilt) == dumps_model(model)

    def test_round_trip_preserves_the_model(self, model):
        payload = json.loads(dumps_model(model))
        assert CourseWorldModel.from_dict(payload) == model

    def test_display_labels_do_not_affect_identity(self, model):
        relabeled = build_fixture_model(display_name="Some Other Label")
        assert relabeled.content_digest == model.content_digest
        assert relabeled.display_name != model.display_name

    def test_content_changes_change_the_digest(self, model):
        variant = build_fixture_model(model_version="v2")
        assert variant.content_digest != model.content_digest

    def test_tampered_payload_fails_verification(self, model_payload):
        verify_model_payload(model_payload)  # the untouched payload passes
        model_payload["site_id"] = "tampered-site"
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(model_payload)

    def test_tampered_heights_fail_verification(self, model_payload):
        model_payload["elevation"]["heights"][0] = 99.0
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(model_payload)

    def test_foreign_schema_fails_verification(self, model_payload):
        model_payload["schema"] = "nxt-course-world-model/model/v999"
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(model_payload)

    def test_from_dict_rejects_a_tampered_digest(self, model_payload):
        model_payload["content_digest"] = "sha256:" + "0" * 64
        with pytest.raises(CourseWorldModelError):
            CourseWorldModel.from_dict(model_payload)

    def test_direct_construction_with_a_wrong_digest_is_rejected(self, model):
        import dataclasses

        with pytest.raises(CourseWorldModelError):
            dataclasses.replace(model, content_digest="sha256:" + "1" * 64)


class TestRevisionSemantics:
    def test_a_valid_revision_chain_is_accepted(self, model):
        revision = build_fixture_model(
            model_version="v2",
            supersedes_version="v1",
            effective_from="2026-08-01T00:00:00+08:00",
        )
        validate_revision(current=model, candidate=revision)

    def test_supersedes_must_name_the_current_version(self, model):
        stray = build_fixture_model(
            model_version="v3",
            supersedes_version="v2",
            effective_from="2026-08-01T00:00:00+08:00",
        )
        with pytest.raises(CourseWorldModelError):
            validate_revision(current=model, candidate=stray)

    def test_a_revision_must_change_the_version(self, model):
        with pytest.raises(CourseWorldModelError):
            validate_revision(current=model, candidate=model)

    def test_effective_from_must_strictly_increase(self, model):
        stale = build_fixture_model(
            model_version="v2",
            supersedes_version="v1",
            effective_from="2026-07-01T00:00:00+08:00",
        )
        with pytest.raises(CourseWorldModelError):
            validate_revision(current=model, candidate=stale)

    def test_coordinate_reference_drift_is_rejected(self, model):
        drifted = build_fixture_model(
            model_version="v2",
            supersedes_version="v1",
            effective_from="2026-08-01T00:00:00+08:00",
            frame=make_frame(crs_identifier="EPSG:32650"),
        )
        with pytest.raises(CourseWorldModelError):
            validate_revision(current=model, candidate=drifted)

    def test_site_or_deployment_drift_is_rejected(self, model):
        moved = build_fixture_model(
            model_version="v2",
            supersedes_version="v1",
            effective_from="2026-08-01T00:00:00+08:00",
            site_id="another-site",
        )
        with pytest.raises(CourseWorldModelError):
            validate_revision(current=model, candidate=moved)

    def test_same_version_with_different_content_is_rejected(self, model):
        from nxt_course_world_model import require_consistent_content

        imposter = build_fixture_model(
            effective_from="2026-07-21T00:00:00+08:00"
        )
        assert imposter.model_version == model.model_version
        assert imposter.content_digest != model.content_digest
        with pytest.raises(CourseWorldModelError):
            require_consistent_content(model, imposter)

    def test_same_version_with_identical_content_is_consistent(self, model):
        from nxt_course_world_model import require_consistent_content

        twin = build_fixture_model()
        require_consistent_content(model, twin)


class TestSiteBinding:
    def test_the_fixture_model_matches_the_commissioned_site(self, model):
        validate_model_against_site(model, enablement_site())

    def test_site_identity_mismatch_is_rejected(self):
        moved = build_fixture_model(site_id="another-site")
        with pytest.raises(CourseWorldModelError):
            validate_model_against_site(moved, enablement_site())

    def test_deployment_identity_mismatch_is_rejected(self):
        moved = build_fixture_model(deployment_id="another-deployment")
        with pytest.raises(CourseWorldModelError):
            validate_model_against_site(moved, enablement_site())

    def test_coordinate_reference_mismatch_is_rejected(self):
        drifted = build_fixture_model(
            frame=make_frame(crs_identifier="EPSG:32650")
        )
        with pytest.raises(CourseWorldModelError):
            validate_model_against_site(drifted, enablement_site())

    def test_origin_mismatch_is_rejected(self):
        shifted = build_fixture_model(frame=make_frame(origin_crs_x=1.0))
        with pytest.raises(CourseWorldModelError):
            validate_model_against_site(shifted, enablement_site())

    def test_unknown_commissioned_zone_reference_is_rejected(self, model):
        import dataclasses

        zones = tuple(
            dataclasses.replace(zone, commissioned_zone_id="Z99")
            if zone.commissioned_zone_id is not None
            else zone
            for zone in model.restricted_zones
        )
        stray = build_fixture_model(restricted_zones=zones)
        with pytest.raises(CourseWorldModelError):
            validate_model_against_site(stray, enablement_site())


def shifted(vertices, dx, dy):
    return tuple((x + dx, y + dy) for x, y in vertices)


class TestAdversarialGeometryRejection:
    """Regression coverage: witness-evading overlaps must fail validation."""

    U_SHAPE = (
        (0.0, 0.0),
        (30.0, 0.0),
        (30.0, 30.0),
        (20.0, 30.0),
        (20.0, 10.0),
        (10.0, 10.0),
        (10.0, 30.0),
        (0.0, 30.0),
    )
    ROTATED_U_SHAPE = (
        (30.0, 30.0),
        (0.0, 30.0),
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 20.0),
        (20.0, 20.0),
        (20.0, 0.0),
        (30.0, 0.0),
    )

    def test_interlocking_u_surfaces_are_rejected(self):
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="adv-green",
                surface_type=SurfaceType.GREEN,
                polygon=PolygonRing(
                    vertices=shifted(self.U_SHAPE, 250.0, 120.0)
                ),
                hole_id=None,
            ),
            SurfaceFeature(
                feature_id="adv-water",
                surface_type=SurfaceType.WATER,
                polygon=PolygonRing(
                    vertices=shifted(self.ROTATED_U_SHAPE, 250.0, 120.0)
                ),
                hole_id=None,
            ),
        )
        with pytest.raises(CourseWorldModelError) as excinfo:
            build_fixture_model(surfaces=surfaces)
        assert "overlap" in str(excinfo.value)

    def test_identical_interiors_with_extra_collinear_vertex_rejected(self):
        first = (
            (250.0, 120.0),
            (290.0, 120.0),
            (290.0, 130.0),
            (260.0, 130.0),
            (260.0, 160.0),
            (250.0, 160.0),
        )
        second = (
            (250.0, 120.0),
            (270.0, 120.0),
            (290.0, 120.0),
            (290.0, 130.0),
            (260.0, 130.0),
            (260.0, 160.0),
            (250.0, 160.0),
        )
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="adv-green",
                surface_type=SurfaceType.GREEN,
                polygon=PolygonRing(vertices=first),
                hole_id=None,
            ),
            SurfaceFeature(
                feature_id="adv-water",
                surface_type=SurfaceType.WATER,
                polygon=PolygonRing(vertices=second),
                hole_id=None,
            ),
        )
        with pytest.raises(CourseWorldModelError) as excinfo:
            build_fixture_model(surfaces=surfaces)
        assert "overlap" in str(excinfo.value)


class TestHoleAttributionConsistency:
    """A declared hole reference must be geometrically consistent."""

    def test_a_surface_outside_its_declared_hole_is_rejected(self):
        surfaces = fixture_surfaces() + (
            SurfaceFeature(
                feature_id="hole-7-stray-rough",
                surface_type=SurfaceType.ROUGH,
                polygon=PolygonRing(
                    vertices=rectangle(292.0, 170.0, 296.0, 180.0)
                ),
                hole_id="hole-7",
            ),
        )
        with pytest.raises(CourseWorldModelError) as excinfo:
            build_fixture_model(surfaces=surfaces)
        assert "hole" in str(excinfo.value)

    def test_a_cart_path_outside_its_declared_hole_is_rejected(self):
        from nxt_course_world_model import CartPath, Polyline

        paths = fixture_cart_paths() + (
            CartPath(
                feature_id="stray-path",
                centerline=Polyline(
                    vertices=((292.0, 170.0), (296.0, 180.0))
                ),
                width_m=2.0,
                hole_id="hole-7",
            ),
        )
        with pytest.raises(CourseWorldModelError):
            build_fixture_model(cart_paths=paths)


class TestZoneReferenceReconciliation:
    """A commissioned-zone reference must match the surveyed extent."""

    def test_a_disjoint_zone_reference_is_rejected(self, model):
        import dataclasses

        zones = tuple(
            dataclasses.replace(
                zone,
                polygon=PolygonRing(
                    vertices=rectangle(250.0, 150.0, 252.0, 152.0)
                ),
            )
            if zone.commissioned_zone_id == "Z1"
            else zone
            for zone in model.restricted_zones
        )
        stray = build_fixture_model(restricted_zones=zones)
        with pytest.raises(CourseWorldModelError) as excinfo:
            validate_model_against_site(stray, enablement_site())
        assert "Z1" in str(excinfo.value)


class TestVerificationAndDigestHardening:
    def test_verify_rejects_structurally_invalid_payloads(
        self, model_payload
    ):
        import hashlib

        from nxt_commissioning import canonical_projection_json

        model_payload["holes"] = "not-a-list-at-all"
        identity = {
            key: value
            for key, value in model_payload.items()
            if key not in ("content_digest", "display_name")
        }
        model_payload["content_digest"] = "sha256:" + hashlib.sha256(
            canonical_projection_json(identity).encode("utf-8")
        ).hexdigest()
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(model_payload)

    def test_verify_wraps_non_finite_payload_values(self, model_payload):
        model_payload["elevation"]["heights"][0] = float("nan")
        with pytest.raises(CourseWorldModelError):
            verify_model_payload(model_payload)

    def test_numeric_representation_does_not_change_identity(self, model):
        from nxt_course_world_model import require_consistent_content

        as_int = build_fixture_model(frame=make_frame(origin_crs_z=5))
        as_float = build_fixture_model(frame=make_frame(origin_crs_z=5.0))
        assert as_int.content_digest == as_float.content_digest
        require_consistent_content(as_int, as_float)

    def test_integer_heights_digest_like_their_float_values(self):
        from tests.course_world_model.conftest import make_grid

        flat_int = make_grid(heights=(2,) * (21 * 31))
        flat_float = make_grid(heights=(2.0,) * (21 * 31))
        int_model = build_fixture_model(elevation=flat_int)
        float_model = build_fixture_model(elevation=flat_float)
        assert int_model.content_digest == float_model.content_digest

    def test_astronomical_query_inputs_raise_the_contract_error(self, model):
        from nxt_course_world_model import (
            CourseModelQueryError,
            MapQueryService,
        )

        service = MapQueryService(model)
        with pytest.raises(CourseModelQueryError):
            service.get_elevation(10**400, 0.0)
        with pytest.raises(CourseModelQueryError):
            service.get_nearby_hazards(150.0, 100.0, 10**400)
