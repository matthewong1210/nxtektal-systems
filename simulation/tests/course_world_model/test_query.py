"""Read-only Map Query Service behavior over the fixture model.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import math

import pytest

from nxt_course_world_model import (
    CourseModelQueryError,
    MapQueryService,
    QueryStatus,
    SUPPORTED_QUERY_KINDS,
    SurfaceType,
    dumps_model,
)

from tests.course_world_model.conftest import fixture_height


@pytest.fixture(scope="module")
def service(session_model) -> MapQueryService:
    return MapQueryService(session_model)


class TestResultIdentity:
    def test_every_result_carries_the_model_identity(self, service, model):
        results = (
            service.get_elevation(150.0, 100.0),
            service.get_surface(150.0, 100.0),
            service.get_slope(150.0, 100.0),
            service.get_hole_context(150.0, 100.0),
            service.get_nearby_hazards(150.0, 100.0, 50.0),
            service.is_restricted(150.0, 100.0),
        )
        for result in results:
            assert result.model.course_model_id == model.course_model_id
            assert result.model.model_version == model.model_version
            assert result.model.content_digest == model.content_digest
            assert result.model.frame_id == model.frame.frame_id
            assert result.model.resolution_m == model.elevation.resolution_m

    def test_results_are_json_ready_and_compact(self, service, model):
        payload = service.get_elevation(150.0, 100.0).to_dict()
        assert payload["model"]["course_model_id"] == model.course_model_id
        # Compact identity, never the whole model.
        assert "elevation" not in payload["model"]
        assert "surfaces" not in payload["model"]


class TestElevationQuery:
    def test_elevation_inside_the_model(self, service):
        result = service.get_elevation(150.0, 100.0)
        assert result.status is QueryStatus.OK
        assert result.elevation_m == pytest.approx(
            fixture_height(150.0, 100.0)
        )

    def test_out_of_bounds_is_explicit(self, service):
        result = service.get_elevation(-50.0, 100.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS
        assert result.elevation_m is None

    def test_non_finite_input_is_rejected(self, service):
        for bad in (math.nan, math.inf, -math.inf):
            with pytest.raises(CourseModelQueryError):
                service.get_elevation(bad, 0.0)
            with pytest.raises(CourseModelQueryError):
                service.get_elevation(0.0, bad)

    def test_boolean_input_is_rejected(self, service):
        with pytest.raises(CourseModelQueryError):
            service.get_elevation(True, 0.0)


class TestSurfaceQuery:
    def test_fairway_point(self, service):
        result = service.get_surface(150.0, 100.0)
        assert result.status is QueryStatus.OK
        assert result.surface_type is SurfaceType.FAIRWAY
        assert result.feature_id == "hole-7-fairway"
        assert result.hole_id == "hole-7"

    def test_green_bunker_water_and_tee_points(self, service):
        assert (
            service.get_surface(250.0, 100.0).surface_type
            is SurfaceType.GREEN
        )
        assert (
            service.get_surface(240.0, 75.0).surface_type
            is SurfaceType.BUNKER
        )
        assert (
            service.get_surface(130.0, 150.0).surface_type
            is SurfaceType.WATER
        )
        assert (
            service.get_surface(30.0, 100.0).surface_type is SurfaceType.TEE
        )

    def test_unclassified_point_inside_bounds(self, service):
        result = service.get_surface(6.0, 190.0)
        assert result.status is QueryStatus.UNCLASSIFIED
        assert result.surface_type is None
        assert result.feature_id is None

    def test_out_of_bounds_point(self, service):
        result = service.get_surface(400.0, 100.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS

    def test_overlays_are_reported_with_the_primary_surface(self, service):
        # (100, 75) sits in the south rough under the cart path.
        result = service.get_surface(100.0, 75.0)
        assert result.surface_type is SurfaceType.ROUGH
        assert result.cart_path_ids == ("hole-7-cart-path",)

    def test_restricted_overlay_is_reported(self, service):
        result = service.get_surface(270.0, 30.0)
        assert result.restricted_zone_ids == (
            "restricted-maintenance-yard",
        )

    def test_boundary_points_resolve_deterministically(self, service):
        # (45, 100) lies on the shared vertical edge of the tee gap and
        # the fairway's west edge; the fairway contains it (closed set)
        # and repeated queries must agree byte for byte.
        first = service.get_surface(45.0, 100.0)
        second = service.get_surface(45.0, 100.0)
        assert first.to_dict() == second.to_dict()
        assert first.surface_type is SurfaceType.FAIRWAY


class TestSlopeQuery:
    def test_slope_on_the_planar_fixture(self, service):
        result = service.get_slope(150.0, 100.0)
        assert result.status is QueryStatus.OK
        assert result.dz_dx == pytest.approx(0.01)
        assert result.dz_dy == pytest.approx(0.005)
        assert result.slope_magnitude == pytest.approx(
            math.hypot(0.01, 0.005)
        )
        assert result.grade_percent == pytest.approx(
            100.0 * math.hypot(0.01, 0.005)
        )
        # Aspect: downhill azimuth. The fixture rises to the east and
        # north, so downhill faces south-west (between 180 and 270 deg).
        assert 180.0 < result.aspect_deg < 270.0

    def test_out_of_bounds_slope_is_explicit(self, service):
        result = service.get_slope(-1.0, 0.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS
        assert result.dz_dx is None
        assert result.aspect_deg is None

    def test_flat_surface_reports_no_aspect(self):
        from nxt_course_world_model import ElevationGrid
        from tests.course_world_model.conftest import build_fixture_model

        flat_heights = tuple(2.0 for _ in range(21 * 31))
        flat_model = build_fixture_model(
            elevation=ElevationGrid(
                origin_x=0.0,
                origin_y=0.0,
                cell_size_m=10.0,
                n_rows=21,
                n_cols=31,
                heights=flat_heights,
            )
        )
        result = MapQueryService(flat_model).get_slope(150.0, 100.0)
        assert result.status is QueryStatus.OK
        assert result.slope_magnitude == 0.0
        assert result.aspect_deg is None


class TestHoleContextQuery:
    def test_a_point_in_the_hole(self, service):
        result = service.get_hole_context(150.0, 100.0)
        assert result.status is QueryStatus.OK
        assert result.hole_id == "hole-7"
        assert result.hole_number == 7
        assert result.surface_type is SurfaceType.FAIRWAY
        assert result.distance_to_green_m == pytest.approx(85.0)
        assert result.distance_to_tee_m == pytest.approx(110.0)

    def test_no_hole_is_never_inferred(self, service):
        result = service.get_hole_context(7.0, 190.0)
        assert result.status is QueryStatus.NO_HOLE
        assert result.hole_id is None
        assert result.hole_number is None

    def test_out_of_bounds_point(self, service):
        result = service.get_hole_context(-10.0, -10.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS


class TestNearbyHazardsQuery:
    def test_hazards_are_found_and_sorted_deterministically(self, service):
        # (233, 84): just west of the green; the greenside bunker is a
        # couple of metres south-east, the pond ~60 m north-west.
        result = service.get_nearby_hazards(233.0, 84.0, 120.0)
        assert result.status is QueryStatus.OK
        ids = [hit.feature_id for hit in result.hazards]
        assert ids == ["hole-7-bunker-greenside", "hole-7-water-pond"]
        assert result.hazards[0].hazard_type is SurfaceType.BUNKER
        assert result.hazards[0].distance_m == pytest.approx(
            math.hypot(233.0 - 235.0, 84.0 - 82.0)
        )
        assert result.hazards[1].hazard_type is SurfaceType.WATER
        distances = [hit.distance_m for hit in result.hazards]
        assert distances == sorted(distances)

    def test_a_point_inside_a_hazard_reports_zero_distance(self, service):
        result = service.get_nearby_hazards(240.0, 75.0, 10.0)
        assert result.hazards[0].feature_id == "hole-7-bunker-greenside"
        assert result.hazards[0].distance_m == 0.0

    def test_radius_excludes_distant_hazards(self, service):
        result = service.get_nearby_hazards(233.0, 84.0, 5.0)
        ids = [hit.feature_id for hit in result.hazards]
        assert ids == ["hole-7-bunker-greenside"]

    def test_invalid_radius_is_rejected(self, service):
        for radius in (0.0, -1.0, math.nan, math.inf):
            with pytest.raises(CourseModelQueryError):
                service.get_nearby_hazards(150.0, 100.0, radius)

    def test_out_of_bounds_point_reports_no_hazards(self, service):
        result = service.get_nearby_hazards(-10.0, 0.0, 50.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS
        assert result.hazards == ()


class TestRestrictedQuery:
    def test_a_restricted_point(self, service):
        result = service.is_restricted(270.0, 30.0)
        assert result.status is QueryStatus.OK
        assert result.restricted is True
        assert [match.feature_id for match in result.matches] == [
            "restricted-maintenance-yard"
        ]
        assert result.matches[0].category.value == "no_go"

    def test_a_commissioned_zone_reference_is_carried(self, service):
        result = service.is_restricted(20.0, 20.0)
        assert result.restricted is True
        assert result.matches[0].commissioned_zone_id == "Z1"

    def test_an_unrestricted_point(self, service):
        result = service.is_restricted(150.0, 100.0)
        assert result.restricted is False
        assert result.matches == ()

    def test_out_of_bounds_is_explicit_not_optimistic(self, service):
        result = service.is_restricted(1000.0, 1000.0)
        assert result.status is QueryStatus.OUT_OF_BOUNDS
        assert result.restricted is None


class TestQueryPurity:
    def test_repeated_queries_do_not_mutate_the_model(self, model):
        before = dumps_model(model)
        service = MapQueryService(model)
        for _ in range(3):
            service.get_elevation(150.0, 100.0)
            service.get_surface(45.0, 100.0)
            service.get_slope(10.0, 10.0)
            service.get_hole_context(150.0, 100.0)
            service.get_nearby_hazards(233.0, 84.0, 120.0)
            service.is_restricted(270.0, 30.0)
        assert dumps_model(model) == before

    def test_repeated_queries_are_byte_identical(self, service):
        first = service.get_nearby_hazards(233.0, 84.0, 120.0).to_dict()
        second = service.get_nearby_hazards(233.0, 84.0, 120.0).to_dict()
        assert first == second

    def test_the_supported_query_kinds_are_declared_and_sorted(self):
        assert SUPPORTED_QUERY_KINDS == tuple(sorted(SUPPORTED_QUERY_KINDS))
        assert "elevation" in SUPPORTED_QUERY_KINDS
        assert "trajectory_terrain_intersection" in SUPPORTED_QUERY_KINDS
