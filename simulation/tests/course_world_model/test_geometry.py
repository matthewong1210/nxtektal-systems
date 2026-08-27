"""Deterministic 2D geometry primitives: rings, polylines, containment.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import math

import pytest

from nxt_course_world_model import (
    CourseWorldModelError,
    PolygonRing,
    Polyline,
)
from nxt_course_world_model.geometry import rings_interiors_overlap

from tests.course_world_model.conftest import make_ring, rectangle


class TestPolygonRingValidation:
    def test_a_valid_ring_is_accepted(self):
        ring = make_ring()
        assert len(ring.vertices) == 4
        assert ring.area_m2 == pytest.approx(100.0)

    def test_fewer_than_three_vertices_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            PolygonRing(vertices=((0.0, 0.0), (1.0, 0.0)))

    def test_duplicate_consecutive_vertices_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            PolygonRing(
                vertices=((0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (1.0, 1.0))
            )

    def test_explicitly_closed_rings_are_rejected(self):
        # The convention is implicit closure: the last vertex must not
        # repeat the first, and the closing edge is implied.
        with pytest.raises(CourseWorldModelError):
            PolygonRing(
                vertices=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0))
            )

    def test_self_intersecting_rings_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            PolygonRing(
                vertices=((0.0, 0.0), (2.0, 2.0), (2.0, 0.0), (0.0, 2.0))
            )

    def test_zero_area_rings_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            PolygonRing(vertices=((0.0, 0.0), (1.0, 1.0), (2.0, 2.0)))

    def test_non_finite_coordinates_are_rejected(self):
        for bad in (math.nan, math.inf, -math.inf):
            with pytest.raises(CourseWorldModelError):
                PolygonRing(
                    vertices=((0.0, 0.0), (1.0, 0.0), (bad, 1.0))
                )

    def test_boolean_coordinates_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            PolygonRing(vertices=((0.0, 0.0), (1.0, 0.0), (True, 1.0)))

    def test_both_windings_are_accepted(self):
        counter_clockwise = PolygonRing(
            vertices=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))
        )
        clockwise = PolygonRing(
            vertices=((0.0, 0.0), (0.0, 1.0), (1.0, 1.0))
        )
        assert counter_clockwise.area_m2 == pytest.approx(0.5)
        assert clockwise.area_m2 == pytest.approx(0.5)


class TestContainment:
    def test_interior_points_are_contained(self):
        ring = make_ring()
        assert ring.contains(5.0, 5.0)

    def test_exterior_points_are_not_contained(self):
        ring = make_ring()
        assert not ring.contains(10.5, 5.0)
        assert not ring.contains(-0.1, 5.0)

    def test_boundary_points_are_deterministically_inside(self):
        ring = make_ring()
        # Documented convention: rings are closed sets, so a point on
        # an edge or a vertex is contained.
        assert ring.contains(0.0, 5.0)
        assert ring.contains(0.0, 0.0)
        assert ring.contains(5.0, 10.0)

    def test_containment_of_a_concave_ring(self):
        ring = PolygonRing(
            vertices=(
                (0.0, 0.0),
                (4.0, 0.0),
                (4.0, 4.0),
                (2.0, 1.0),
                (0.0, 4.0),
            )
        )
        assert ring.contains(0.5, 0.5)
        assert not ring.contains(2.0, 3.5)  # in the concave notch


class TestDistance:
    def test_distance_is_zero_inside_and_on_the_boundary(self):
        ring = make_ring()
        assert ring.distance_to(5.0, 5.0) == 0.0
        assert ring.distance_to(0.0, 0.0) == 0.0

    def test_distance_to_a_nearby_exterior_point(self):
        ring = make_ring()
        assert ring.distance_to(13.0, 5.0) == pytest.approx(3.0)
        assert ring.distance_to(13.0, 14.0) == pytest.approx(5.0)


class TestInteriorOverlap:
    def test_disjoint_rings_do_not_overlap(self):
        a = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        b = PolygonRing(vertices=rectangle(20.0, 0.0, 30.0, 10.0))
        assert not rings_interiors_overlap(a, b)

    def test_edge_sharing_rings_do_not_overlap(self):
        a = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        b = PolygonRing(vertices=rectangle(10.0, 0.0, 20.0, 10.0))
        assert not rings_interiors_overlap(a, b)

    def test_crossing_rings_overlap(self):
        a = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        b = PolygonRing(vertices=rectangle(5.0, 5.0, 15.0, 15.0))
        assert rings_interiors_overlap(a, b)

    def test_a_ring_nested_inside_another_overlaps(self):
        outer = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        inner = PolygonRing(vertices=rectangle(2.0, 2.0, 4.0, 4.0))
        assert rings_interiors_overlap(outer, inner)
        assert rings_interiors_overlap(inner, outer)

    def test_identical_rings_overlap(self):
        a = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        b = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        assert rings_interiors_overlap(a, b)


class TestPolyline:
    def test_a_valid_polyline_is_accepted(self):
        line = Polyline(vertices=((0.0, 0.0), (10.0, 0.0), (10.0, 5.0)))
        assert len(line.vertices) == 3

    def test_fewer_than_two_vertices_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            Polyline(vertices=((0.0, 0.0),))

    def test_duplicate_consecutive_vertices_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            Polyline(vertices=((0.0, 0.0), (0.0, 0.0), (1.0, 0.0)))

    def test_non_finite_vertices_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            Polyline(vertices=((0.0, 0.0), (math.nan, 1.0)))

    def test_distance_to_a_segment_interior_and_endpoint(self):
        line = Polyline(vertices=((0.0, 0.0), (10.0, 0.0)))
        assert line.distance_to(5.0, 3.0) == pytest.approx(3.0)
        assert line.distance_to(-4.0, 0.0) == pytest.approx(4.0)
        assert line.distance_to(5.0, 0.0) == 0.0


class TestInteriorOverlapCompleteness:
    """Regression coverage: region overlaps that defeat witness sampling.

    Two simple rings can overlap in a region while every vertex, edge
    midpoint, and centroid of each lies outside or on the boundary of
    the other. The detector must be complete for simple rings, not a
    witness sampler.
    """

    def u_shape(self):
        return PolygonRing(
            vertices=(
                (0.0, 0.0),
                (3.0, 0.0),
                (3.0, 3.0),
                (2.0, 3.0),
                (2.0, 1.0),
                (1.0, 1.0),
                (1.0, 3.0),
                (0.0, 3.0),
            )
        )

    def rotated_u_shape(self):
        # The same U rotated 180 degrees about (1.5, 1.5).
        return PolygonRing(
            vertices=(
                (3.0, 3.0),
                (0.0, 3.0),
                (0.0, 0.0),
                (1.0, 0.0),
                (1.0, 2.0),
                (2.0, 2.0),
                (2.0, 0.0),
                (3.0, 0.0),
            )
        )

    def test_interlocking_u_shapes_overlap(self):
        first = self.u_shape()
        second = self.rotated_u_shape()
        # (0.5, 1.5) is strictly inside both rings: a true region overlap.
        assert first.strictly_contains(0.5, 1.5)
        assert second.strictly_contains(0.5, 1.5)
        assert rings_interiors_overlap(first, second)
        assert rings_interiors_overlap(second, first)

    def test_identical_interiors_with_an_extra_collinear_vertex(self):
        # Same L-shaped region; the second ring carries one extra
        # collinear vertex, so the identical-vertex shortcut cannot fire
        # and the vertex-average centroid falls outside the ring.
        first = PolygonRing(
            vertices=(
                (0.0, 0.0),
                (4.0, 0.0),
                (4.0, 1.0),
                (1.0, 1.0),
                (1.0, 4.0),
                (0.0, 4.0),
            )
        )
        second = PolygonRing(
            vertices=(
                (0.0, 0.0),
                (2.0, 0.0),
                (4.0, 0.0),
                (4.0, 1.0),
                (1.0, 1.0),
                (1.0, 4.0),
                (0.0, 4.0),
            )
        )
        assert rings_interiors_overlap(first, second)
        assert rings_interiors_overlap(second, first)

    def test_collinear_band_overlap_is_detected(self):
        first = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 4.0))
        second = PolygonRing(vertices=rectangle(0.0, 2.0, 10.0, 6.0))
        assert rings_interiors_overlap(first, second)

    def test_touch_only_inscribed_overlap_is_detected(self):
        # A diamond inscribed in a square: the boundaries meet only at
        # the square's edge midpoints (the diamond's vertices), so
        # every boundary intersection is a touch with no proper
        # crossing and no strictly-contained vertex, yet the diamond's
        # interior lies inside the square.
        square = PolygonRing(vertices=rectangle(0.0, 0.0, 4.0, 4.0))
        diamond = PolygonRing(
            vertices=((2.0, 0.0), (4.0, 2.0), (2.0, 4.0), (0.0, 2.0))
        )
        assert rings_interiors_overlap(square, diamond)
        assert rings_interiors_overlap(diamond, square)

    def test_abutting_rings_still_do_not_overlap(self):
        first = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        second = PolygonRing(vertices=rectangle(10.0, 0.0, 20.0, 10.0))
        assert not rings_interiors_overlap(first, second)
        # A shorter shared edge (a subset of the neighbour's edge) is
        # still only a touch, never an interior overlap.
        third = PolygonRing(vertices=rectangle(10.0, 2.0, 14.0, 8.0))
        assert not rings_interiors_overlap(first, third)

    def test_corner_touching_rings_do_not_overlap(self):
        first = PolygonRing(vertices=rectangle(0.0, 0.0, 10.0, 10.0))
        second = PolygonRing(vertices=rectangle(10.0, 10.0, 20.0, 20.0))
        assert not rings_interiors_overlap(first, second)


class TestNumericContractBounds:
    def test_astronomical_coordinates_are_rejected_with_the_contract_error(
        self,
    ):
        # A metres-based course frame bounds coordinate magnitude, so
        # downstream interpolation arithmetic can never overflow, and an
        # enormous int raises the contracted error, never a raw
        # OverflowError.
        with pytest.raises(CourseWorldModelError):
            PolygonRing(
                vertices=((0.0, 0.0), (10**400, 0.0), (1.0, 1.0))
            )
        with pytest.raises(CourseWorldModelError):
            PolygonRing(vertices=((0.0, 0.0), (1e10, 0.0), (1.0, 1.0)))


class TestExactPredicateArithmetic:
    def test_orientation_is_exact_for_adversarial_near_collinear_floats(
        self,
    ):
        # The exact cross product of these three accepted floats is a
        # tiny nonzero value that a fixed-precision decimal context
        # rounds to zero; the predicates must use exact rational
        # arithmetic, so this razor-thin triangle is a valid ring.
        x0 = 1e9
        x1 = math.nextafter(x0, 0.0)
        x2 = math.nextafter(x1, 0.0)
        ring = PolygonRing(vertices=((0.0, 0.0), (x0, x1), (x1, x2)))
        assert ring.area_m2 > 0.0
