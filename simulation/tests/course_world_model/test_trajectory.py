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
