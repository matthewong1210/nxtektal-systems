"""Elevation surface: regular finite grid, bilinear queries, slope.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import math

import pytest

from nxt_course_world_model import CourseWorldModelError, ElevationGrid

from tests.course_world_model.conftest import fixture_height, make_grid


class TestGridValidation:
    def test_a_valid_grid_is_accepted(self):
        grid = make_grid()
        assert grid.n_rows == 21
        assert grid.n_cols == 31
        assert grid.resolution_m == 10.0
        assert grid.min_x == 0.0
        assert grid.max_x == 300.0
        assert grid.min_y == 0.0
        assert grid.max_y == 200.0

    def test_height_count_must_match_dimensions(self):
        with pytest.raises(CourseWorldModelError):
            make_grid(heights=(1.0, 2.0, 3.0))

    def test_dimensions_below_two_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_grid(n_rows=1, heights=tuple(float(i) for i in range(31)))
        with pytest.raises(CourseWorldModelError):
            make_grid(n_cols=1, heights=tuple(float(i) for i in range(21)))

    def test_non_positive_or_non_finite_cell_size_is_rejected(self):
        for cell in (0.0, -1.0, math.nan, math.inf):
            with pytest.raises(CourseWorldModelError):
                make_grid(cell_size_m=cell)

    def test_non_finite_heights_are_rejected(self):
        heights = list(make_grid().heights)
        heights[5] = math.nan
        with pytest.raises(CourseWorldModelError):
            make_grid(heights=tuple(heights))
        heights[5] = math.inf
        with pytest.raises(CourseWorldModelError):
            make_grid(heights=tuple(heights))

    def test_boolean_heights_are_rejected(self):
        heights = list(make_grid().heights)
        heights[0] = True
        with pytest.raises(CourseWorldModelError):
            make_grid(heights=tuple(heights))

    def test_non_finite_origin_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_grid(origin_x=math.nan)


class TestElevationQueries:
    def test_exact_grid_node_returns_the_stored_height(self):
        grid = make_grid()
        assert grid.elevation_at(0.0, 0.0) == fixture_height(0.0, 0.0)
        assert grid.elevation_at(100.0, 50.0) == fixture_height(100.0, 50.0)

    def test_bilinear_interpolation_between_nodes(self):
        # The fixture surface is planar (1.5 + 0.01 x + 0.005 y), so
        # bilinear interpolation must reproduce the plane exactly at any
        # interior point, not only at grid nodes.
        grid = make_grid()
        assert grid.elevation_at(5.0, 5.0) == pytest.approx(
            1.5 + 0.01 * 5.0 + 0.005 * 5.0
        )
        assert grid.elevation_at(123.4, 87.6) == pytest.approx(
            1.5 + 0.01 * 123.4 + 0.005 * 87.6
        )

    def test_bilinear_interpolation_of_a_non_planar_cell(self):
        heights = (0.0, 0.0, 0.0, 4.0)  # 2x2 grid, one raised corner
        grid = ElevationGrid(
            origin_x=0.0,
            origin_y=0.0,
            cell_size_m=10.0,
            n_rows=2,
            n_cols=2,
            heights=heights,
        )
        # Midpoint blends all four corners: (0 + 0 + 0 + 4) / 4.
        assert grid.elevation_at(5.0, 5.0) == pytest.approx(1.0)
        # Quarter point: u = v = 0.25 -> uv weight 0.0625.
        assert grid.elevation_at(2.5, 2.5) == pytest.approx(0.25)

    def test_the_closed_maximum_edges_are_inside_the_grid(self):
        grid = make_grid()
        assert grid.covers(300.0, 200.0)
        assert grid.elevation_at(300.0, 200.0) == fixture_height(300.0, 200.0)

    def test_out_of_bounds_is_not_covered_and_fails_loudly(self):
        grid = make_grid()
        for x, y in ((-0.001, 0.0), (300.001, 0.0), (0.0, 200.5), (1e9, 1e9)):
            assert not grid.covers(x, y)
            with pytest.raises(CourseWorldModelError):
                grid.elevation_at(x, y)

    def test_no_silent_extrapolation_beyond_the_boundary(self):
        grid = make_grid()
        with pytest.raises(CourseWorldModelError):
            grid.elevation_at(300.0000001, 100.0)


class TestSlope:
    def test_slope_of_the_planar_fixture_surface(self):
        grid = make_grid()
        dz_dx, dz_dy = grid.slope_at(150.0, 100.0)
        assert dz_dx == pytest.approx(0.01)
        assert dz_dy == pytest.approx(0.005)

    def test_slope_is_deterministic_at_cell_boundaries(self):
        grid = make_grid()
        first = grid.slope_at(10.0, 10.0)
        second = grid.slope_at(10.0, 10.0)
        assert first == second

    def test_slope_at_the_closed_maximum_corner_is_defined(self):
        grid = make_grid()
        dz_dx, dz_dy = grid.slope_at(300.0, 200.0)
        assert math.isfinite(dz_dx) and math.isfinite(dz_dy)

    def test_slope_out_of_bounds_fails_loudly(self):
        grid = make_grid()
        with pytest.raises(CourseWorldModelError):
            grid.slope_at(-1.0, 0.0)

    def test_the_fixture_surface_is_not_flat(self):
        grid = make_grid()
        dz_dx, dz_dy = grid.slope_at(42.0, 17.0)
        assert math.hypot(dz_dx, dz_dy) > 0.0


class TestGridSerialization:
    def test_round_trip_is_lossless(self):
        grid = make_grid()
        assert ElevationGrid.from_dict(grid.to_dict()) == grid

    def test_row_ordering_is_row_major_from_the_south_west(self):
        grid = ElevationGrid(
            origin_x=0.0,
            origin_y=0.0,
            cell_size_m=1.0,
            n_rows=2,
            n_cols=3,
            heights=(0.0, 1.0, 2.0, 10.0, 11.0, 12.0),
        )
        assert grid.elevation_at(0.0, 0.0) == 0.0  # south-west node
        assert grid.elevation_at(2.0, 0.0) == 2.0  # south-east node
        assert grid.elevation_at(0.0, 1.0) == 10.0  # north-west node
        assert grid.elevation_at(2.0, 1.0) == 12.0  # north-east node

    def test_malformed_payload_keys_are_rejected(self):
        payload = make_grid().to_dict()
        payload["surprise"] = 1
        with pytest.raises((CourseWorldModelError, ValueError, TypeError)):
            ElevationGrid.from_dict(payload)
