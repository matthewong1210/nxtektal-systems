"""Trajectory/terrain intersection: narrow, geometric, fabrication-free.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import math

import pytest

from nxt_course_world_model import (
    CourseModelQueryError,
    MapQueryService,
    QueryStatus,
    SurfaceType,
    TrajectorySample,
)

from tests.course_world_model.conftest import FRAME_ID, fixture_height


@pytest.fixture(scope="module")
def service(session_model) -> MapQueryService:
    return MapQueryService(session_model)


def sample(t: float, x: float, y: float, z: float) -> TrajectorySample:
    return TrajectorySample(t_s=t, x=x, y=y, z=z)


def descending_track() -> tuple[TrajectorySample, ...]:
    """A synthetic descent toward the fairway around x = 150, y = 100."""
    return (
        sample(0.0, 100.0, 100.0, 40.0),
        sample(1.0, 120.0, 100.0, 25.0),
        sample(2.0, 140.0, 100.0, 10.0),
        sample(3.0, 160.0, 100.0, 0.5),
        sample(4.0, 180.0, 100.0, -5.0),
    )


class TestIntersection:
    def test_a_simple_descending_trajectory_intersects_terrain(
        self, service
    ):
        result = service.intersect_trajectory_with_terrain(
            descending_track(), frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.OK
        # Terrain height near x=160 is ~3.6 m, so the crossing happens
        # in segment 2 -> 3 (z drops 10 -> 0.5 across x 140 -> 160).
        assert result.segment_index == 2
        assert 140.0 < result.x < 160.0
        assert result.y == pytest.approx(100.0)
        assert result.z == pytest.approx(
            fixture_height(result.x, result.y), abs=1e-3
        )
        assert result.surface_type is SurfaceType.FAIRWAY
        assert result.surface_feature_id == "hole-7-fairway"

    def test_the_first_intersection_wins(self, service):
        # The track dips below terrain in segment 1, climbs, then dips
        # again; the reported intersection must be the first one.
        track = (
            sample(0.0, 100.0, 100.0, 40.0),
            sample(1.0, 110.0, 100.0, 1.0),  # below terrain (~3.1 m)
            sample(2.0, 120.0, 100.0, 30.0),
            sample(3.0, 130.0, 100.0, 0.0),
        )
        result = service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.OK
        assert result.segment_index == 0

    def test_intersection_is_deterministic(self, service):
        first = service.intersect_trajectory_with_terrain(
            descending_track(), frame_id=FRAME_ID
        ).to_dict()
        second = service.intersect_trajectory_with_terrain(
            descending_track(), frame_id=FRAME_ID
        ).to_dict()
        assert first == second

    def test_a_high_track_reports_no_intersection(self, service):
        track = (
            sample(0.0, 100.0, 100.0, 50.0),
            sample(1.0, 200.0, 100.0, 45.0),
            sample(2.0, 290.0, 100.0, 40.0),
        )
        result = service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.NO_INTERSECTION
        assert result.x is None
        assert result.segment_index is None

    def test_leaving_the_model_airborne_is_unprovable_not_invented(
        self, service
    ):
        track = (
            sample(0.0, 250.0, 100.0, 50.0),
            sample(1.0, 290.0, 100.0, 40.0),
            sample(2.0, 350.0, 100.0, 5.0),  # beyond max x = 300
        )
        result = service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.UNPROVABLE
        assert result.x is None


class TestTrajectoryRejection:
    def test_fewer_than_two_samples_is_rejected(self, service):
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                (sample(0.0, 100.0, 100.0, 40.0),), frame_id=FRAME_ID
            )

    def test_non_finite_samples_are_rejected(self, service):
        with pytest.raises(CourseModelQueryError):
            TrajectorySample(t_s=0.0, x=math.nan, y=0.0, z=10.0)
        with pytest.raises(CourseModelQueryError):
            TrajectorySample(t_s=math.inf, x=0.0, y=0.0, z=10.0)

    def test_unordered_sample_times_are_rejected(self, service):
        track = (
            sample(1.0, 100.0, 100.0, 40.0),
            sample(0.5, 120.0, 100.0, 30.0),
        )
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                track, frame_id=FRAME_ID
            )

    def test_duplicate_sample_times_are_rejected(self, service):
        track = (
            sample(1.0, 100.0, 100.0, 40.0),
            sample(1.0, 120.0, 100.0, 30.0),
        )
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                track, frame_id=FRAME_ID
            )

    def test_a_frame_mismatch_is_rejected(self, service):
        with pytest.raises(CourseModelQueryError) as excinfo:
            service.intersect_trajectory_with_terrain(
                descending_track(), frame_id="another-frame"
            )
        assert "frame" in str(excinfo.value)

    def test_a_trajectory_entirely_outside_the_model_is_rejected(
        self, service
    ):
        track = (
            sample(0.0, 500.0, 500.0, 40.0),
            sample(1.0, 520.0, 500.0, 30.0),
        )
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                track, frame_id=FRAME_ID
            )

    def test_a_track_starting_at_or_below_terrain_is_ambiguous(
        self, service
    ):
        track = (
            sample(0.0, 100.0, 100.0, 0.0),  # below terrain (~3.0 m)
            sample(1.0, 120.0, 100.0, 10.0),
        )
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                track, frame_id=FRAME_ID
            )

    def test_a_track_starting_out_of_bounds_is_rejected(self, service):
        track = (
            sample(0.0, -50.0, 100.0, 40.0),
            sample(1.0, 100.0, 100.0, 30.0),
        )
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                track, frame_id=FRAME_ID
            )

    def test_a_non_tuple_of_samples_is_rejected(self, service):
        with pytest.raises(CourseModelQueryError):
            service.intersect_trajectory_with_terrain(
                ((0.0, 100.0, 100.0, 40.0), (1.0, 120.0, 100.0, 30.0)),
                frame_id=FRAME_ID,
            )


def ridge_model():
    """A small model whose terrain has two 30 m ridges (x=10 and x=30).

    Terrain along any west-east line is piecewise linear:
    0 -> 30 -> 0 -> 30 -> 0 at x = 0, 10, 20, 30, 40.  A chord between
    two above-terrain samples can pass straight through a ridge, so
    endpoint-only clearance checks are provably insufficient here.
    """
    from nxt_course_world_model import (
        ElevationGrid,
        HoleDefinition,
        PolygonRing,
        SurfaceFeature,
        build_course_world_model,
    )
    from tests.course_world_model.conftest import (
        fixture_scan_sources,
        make_frame,
        rectangle,
    )

    column_heights = (0.0, 30.0, 0.0, 30.0, 0.0)
    grid = ElevationGrid(
        origin_x=0.0,
        origin_y=0.0,
        cell_size_m=10.0,
        n_rows=3,
        n_cols=5,
        heights=column_heights * 3,
    )
    return build_course_world_model(
        course_model_id="pilot-course-a.ridge-map",
        model_version="v1",
        supersedes_version=None,
        effective_from="2026-07-20T00:00:00+08:00",
        site_id="pilot-course-a",
        deployment_id="pilot-a-enablement-v0",
        display_name="Synthetic ridge terrain regression fixture",
        frame=make_frame(),
        elevation=grid,
        course_boundary=PolygonRing(vertices=rectangle(1.0, 1.0, 39.0, 19.0)),
        holes=(
            HoleDefinition(
                hole_id="hole-r",
                hole_number=1,
                boundary=PolygonRing(
                    vertices=rectangle(2.0, 2.0, 38.0, 18.0)
                ),
            ),
        ),
        surfaces=(
            SurfaceFeature(
                feature_id="hole-r-fairway",
                surface_type=SurfaceType.FAIRWAY,
                polygon=PolygonRing(vertices=rectangle(2.0, 2.0, 38.0, 18.0)),
                hole_id="hole-r",
            ),
        ),
        cart_paths=(),
        restricted_zones=(),
        scan_sources=fixture_scan_sources(),
    )


@pytest.fixture(scope="module")
def ridge_service():
    return MapQueryService(ridge_model())


class TestNonPlanarTerrainHonesty:
    """Regression coverage: endpoint-only clearance checks are dishonest.

    Terrain is bilinear per cell, so clearance along a straight segment
    is piecewise quadratic; the service must find the first contact
    analytically instead of sampling segment endpoints.
    """

    def test_a_chord_through_a_ridge_is_an_intersection(
        self, ridge_service
    ):
        # Both endpoints sit 5 m above the terrain, but the chord passes
        # straight through the first 30 m ridge.
        track = (
            sample(0.0, 5.0, 10.0, 20.0),
            sample(1.0, 35.0, 10.0, 20.0),
        )
        result = ridge_service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.OK
        assert result.segment_index == 0
        # Terrain rises 3 m per metre from x=0, so z=20 is reached at
        # x = 20/3 on the first upslope.
        assert result.x == pytest.approx(20.0 / 3.0, abs=1e-6)
        assert result.z == pytest.approx(20.0, abs=1e-6)

    def test_the_first_crossing_wins_inside_one_segment(
        self, ridge_service
    ):
        # One descending segment whose clearance profile crosses zero
        # several times (+, -, +, -): the reported hit must be the
        # first crossing on the first ridge, not a later one.
        track = (
            sample(0.0, 5.0, 10.0, 20.0),
            sample(1.0, 35.0, 10.0, 2.0),
        )
        result = ridge_service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.OK
        assert result.segment_index == 0
        # z(u) = 20 - 18 u and terrain = 15 + 90 u on the first
        # upslope, so the first contact is at u = 5/108.
        expected_x = 5.0 + 30.0 * (5.0 / 108.0)
        assert result.x == pytest.approx(expected_x, abs=1e-6)

    def test_a_track_above_every_ridge_is_a_true_negative(
        self, ridge_service
    ):
        track = (
            sample(0.0, 5.0, 10.0, 40.0),
            sample(1.0, 35.0, 10.0, 35.0),
        )
        result = ridge_service.intersect_trajectory_with_terrain(
            track, frame_id=FRAME_ID
        )
        assert result.status is QueryStatus.NO_INTERSECTION

    def test_astronomical_sample_values_are_rejected(self, ridge_service):
        with pytest.raises(CourseModelQueryError):
            TrajectorySample(t_s=0.0, x=10**400, y=0.0, z=10.0)
        with pytest.raises(CourseModelQueryError):
            TrajectorySample(t_s=0.0, x=5.0, y=10.0, z=1e308)
