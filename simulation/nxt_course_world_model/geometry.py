"""Deterministic 2D geometry primitives in the course-local frame.

Validation predicates (orientation, segment intersection, area, and
the interior-overlap decision) use exact rational arithmetic
(``Fraction`` over the string form of each coordinate) so
large-magnitude inputs can produce neither sign errors through float
cancellation nor false zeroes through fixed-precision rounding.  Query
arithmetic (containment ray casts, distances) uses plain float math
over the already-validated, bounded course-local coordinates;
identical inputs always produce identical results.

Convention (versioned with the model schema):

* rings are implicitly closed -- the last vertex must not repeat the
  first, and the closing edge is implied;
* rings are closed point sets -- a point on an edge or vertex is
  contained;
* either winding is accepted; area is reported as a positive value;
* coordinates are finite metres in the course-local frame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from .errors import CourseWorldModelError

_MINIMUM_RING_VERTICES = 3
_MINIMUM_POLYLINE_VERTICES = 2

# The metric contract bound: every coordinate, height, and length in the
# course-local frame (and the commissioned CRS values the frame records)
# must stay within one gigametre.  Any terrestrial coordinate fits with
# enormous margin, and the bound guarantees that every downstream
# interpolation, slope, distance, and clearance computation stays far
# from float overflow, so a finite-but-absurd value can never turn into
# a silently wrong result.
MAX_ABS_COORDINATE_M = 1e9


def require_finite_number(value: object, field_name: str) -> float:
    """Validate one finite, contract-bounded number; return it as float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CourseWorldModelError(
            f"{field_name} must be an int or float, not {type(value).__name__}"
        )
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise CourseWorldModelError(f"{field_name} must be finite") from exc
    if not math.isfinite(numeric):
        raise CourseWorldModelError(f"{field_name} must be finite")
    if abs(numeric) > MAX_ABS_COORDINATE_M:
        raise CourseWorldModelError(
            f"{field_name} magnitude must not exceed "
            f"{MAX_ABS_COORDINATE_M!r} metres; got {numeric!r}"
        )
    return numeric


def _validated_vertices(
    vertices: object, *, minimum: int, contract: str
) -> tuple[tuple[float, float], ...]:
    if not isinstance(vertices, tuple):
        raise CourseWorldModelError(f"{contract} vertices must be a tuple")
    if len(vertices) < minimum:
        raise CourseWorldModelError(
            f"{contract} requires at least {minimum} vertices; "
            f"got {len(vertices)}"
        )
    cleaned: list[tuple[float, float]] = []
    for index, vertex in enumerate(vertices):
        if not isinstance(vertex, tuple) or len(vertex) != 2:
            raise CourseWorldModelError(
                f"{contract} vertices[{index}] must be an (x, y) pair"
            )
        cleaned.append(
            (
                require_finite_number(vertex[0], f"vertices[{index}].x"),
                require_finite_number(vertex[1], f"vertices[{index}].y"),
            )
        )
    for index in range(1, len(cleaned)):
        if cleaned[index] == cleaned[index - 1]:
            raise CourseWorldModelError(
                f"{contract} vertices[{index}] duplicates its predecessor"
            )
    return tuple(cleaned)


def _dec(value: float) -> Fraction:
    # str() preserves the shortest exact decimal form, and Fraction
    # arithmetic is exact with no precision context, so the predicates
    # below can neither cancel nor round a true nonzero cross product
    # to zero.
    return Fraction(str(value))


def _orientation(
    a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]
) -> int:
    cross = (_dec(b[0]) - _dec(a[0])) * (_dec(c[1]) - _dec(a[1])) - (
        _dec(b[1]) - _dec(a[1])
    ) * (_dec(c[0]) - _dec(a[0]))
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def _within_bounding_box(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    return (
        min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    )


def point_on_segment(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    """Exact test: does the point lie on the closed segment a-b?"""
    return _orientation(a, b, point) == 0 and _within_bounding_box(
        point, a, b
    )


def segments_intersect(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
) -> bool:
    """General segment intersection, including touching and collinear."""
    o1 = _orientation(p1, p2, q1)
    o2 = _orientation(p1, p2, q2)
    o3 = _orientation(q1, q2, p1)
    o4 = _orientation(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _within_bounding_box(q1, p1, p2):
        return True
    if o2 == 0 and _within_bounding_box(q2, p1, p2):
        return True
    if o3 == 0 and _within_bounding_box(p1, q1, q2):
        return True
    if o4 == 0 and _within_bounding_box(p2, q1, q2):
        return True
    return False


def segments_properly_cross(
    p1: tuple[float, float],
    p2: tuple[float, float],
    q1: tuple[float, float],
    q2: tuple[float, float],
) -> bool:
    """Strict interior crossing: touching endpoints or edges is not one."""
    o1 = _orientation(p1, p2, q1)
    o2 = _orientation(p1, p2, q2)
    o3 = _orientation(q1, q2, p1)
    o4 = _orientation(q1, q2, p2)
    return 0 not in (o1, o2, o3, o4) and o1 != o2 and o3 != o4


def _point_segment_distance(
    x: float,
    y: float,
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    delta_x = b[0] - a[0]
    delta_y = b[1] - a[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0.0:  # degenerate segments are rejected upstream
        return math.hypot(x - a[0], y - a[1])
    parameter = ((x - a[0]) * delta_x + (y - a[1]) * delta_y) / length_squared
    parameter = min(1.0, max(0.0, parameter))
    return math.hypot(
        x - (a[0] + parameter * delta_x), y - (a[1] + parameter * delta_y)
    )


@dataclass(frozen=True, slots=True)
class PolygonRing:
    """One simple, implicitly closed polygon ring."""

    vertices: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        vertices = _validated_vertices(
            self.vertices, minimum=_MINIMUM_RING_VERTICES, contract="ring"
        )
        if vertices[0] == vertices[-1]:
            raise CourseWorldModelError(
                "rings are implicitly closed; the last vertex must not "
                "repeat the first"
            )
        object.__setattr__(self, "vertices", vertices)
        if self._self_intersects():
            raise CourseWorldModelError("ring must not self-intersect")
        if self._twice_signed_area() == 0:
            raise CourseWorldModelError("ring must enclose a non-zero area")

    def _edges(
        self,
    ) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
        count = len(self.vertices)
        return tuple(
            (self.vertices[index], self.vertices[(index + 1) % count])
            for index in range(count)
        )

    def _twice_signed_area(self) -> Fraction:
        total = Fraction(0)
        count = len(self.vertices)
        origin_x = _dec(self.vertices[0][0])
        origin_y = _dec(self.vertices[0][1])
        for index in range(count):
            ax = _dec(self.vertices[index][0]) - origin_x
            ay = _dec(self.vertices[index][1]) - origin_y
            bx = _dec(self.vertices[(index + 1) % count][0]) - origin_x
            by = _dec(self.vertices[(index + 1) % count][1]) - origin_y
            total += ax * by - ay * bx
        return total

    def _self_intersects(self) -> bool:
        edges = self._edges()
        count = len(edges)
        for first in range(count):
            for second in range(first + 1, count):
                if second == first + 1:
                    continue
                if first == 0 and second == count - 1:
                    continue
                if segments_intersect(*edges[first], *edges[second]):
                    return True
        return False

    @property
    def area_m2(self) -> float:
        return float(abs(self._twice_signed_area()) / 2)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [vertex[0] for vertex in self.vertices]
        ys = [vertex[1] for vertex in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))

    def on_boundary(self, x: float, y: float) -> bool:
        point = (
            require_finite_number(x, "x"),
            require_finite_number(y, "y"),
        )
        return any(
            point_on_segment(point, *edge) for edge in self._edges()
        )

    def contains(self, x: float, y: float) -> bool:
        """Closed containment: boundary points are inside."""
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        min_x, min_y, max_x, max_y = self.bounds
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            return False
        if self.on_boundary(x, y):
            return True
        inside = False
        count = len(self.vertices)
        previous = count - 1
        for index in range(count):
            xi, yi = self.vertices[index]
            xj, yj = self.vertices[previous]
            if (yi > y) != (yj > y):
                crossing_x = (xj - xi) * (y - yi) / (yj - yi) + xi
                if x < crossing_x:
                    inside = not inside
            previous = index
        return inside

    def strictly_contains(self, x: float, y: float) -> bool:
        return self.contains(x, y) and not self.on_boundary(x, y)

    def distance_to(self, x: float, y: float) -> float:
        """0.0 inside or on the boundary; else distance to the boundary."""
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        if self.contains(x, y):
            return 0.0
        return min(
            _point_segment_distance(x, y, *edge) for edge in self._edges()
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "vertices": [[vertex[0], vertex[1]] for vertex in self.vertices]
        }

    @classmethod
    def from_dict(cls, data: object) -> "PolygonRing":
        if not isinstance(data, dict) or set(data) != {"vertices"}:
            raise CourseWorldModelError(
                "ring payload must be a mapping with exactly a 'vertices' key"
            )
        raw = data["vertices"]
        if not isinstance(raw, list):
            raise CourseWorldModelError("ring vertices must be a list")
        vertices: list[tuple[float, float]] = []
        for index, pair in enumerate(raw):
            if not isinstance(pair, list) or len(pair) != 2:
                raise CourseWorldModelError(
                    f"ring vertices[{index}] must be an [x, y] pair"
                )
            vertices.append((pair[0], pair[1]))
        return cls(vertices=tuple(vertices))


# ---------------------------------------------------------------------------
# Exact-rational predicates for validation-time region decisions.
#
# ``Fraction(str(value))`` preserves the shortest exact decimal form of
# every coordinate, so intersection parameters, partition midpoints,
# and containment ray casts below are computed without any rounding at
# all: the interior-overlap decision is exact, not sampled.
# ---------------------------------------------------------------------------

_FractionPoint = tuple[Fraction, Fraction]


def _fraction_vertices(
    vertices: tuple[tuple[float, float], ...],
) -> tuple[_FractionPoint, ...]:
    return tuple(
        (Fraction(str(vertex[0])), Fraction(str(vertex[1])))
        for vertex in vertices
    )


def _exact_edges(
    vertices: tuple[_FractionPoint, ...], *, closed: bool
) -> tuple[tuple[_FractionPoint, _FractionPoint], ...]:
    count = len(vertices)
    pairs = [
        (vertices[index], vertices[index + 1]) for index in range(count - 1)
    ]
    if closed:
        pairs.append((vertices[-1], vertices[0]))
    return tuple(pairs)


def _orientation_exact(
    a: _FractionPoint, b: _FractionPoint, c: _FractionPoint
) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0


def _within_box_exact(
    point: _FractionPoint, a: _FractionPoint, b: _FractionPoint
) -> bool:
    return (
        min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    )


def _on_segment_exact(
    point: _FractionPoint, a: _FractionPoint, b: _FractionPoint
) -> bool:
    return _orientation_exact(a, b, point) == 0 and _within_box_exact(
        point, a, b
    )


def _proper_cross_exact(
    p1: _FractionPoint,
    p2: _FractionPoint,
    q1: _FractionPoint,
    q2: _FractionPoint,
) -> bool:
    o1 = _orientation_exact(p1, p2, q1)
    o2 = _orientation_exact(p1, p2, q2)
    o3 = _orientation_exact(q1, q2, p1)
    o4 = _orientation_exact(q1, q2, p2)
    return 0 not in (o1, o2, o3, o4) and o1 != o2 and o3 != o4


def _ring_contains_exact(
    vertices: tuple[_FractionPoint, ...],
    point: _FractionPoint,
    *,
    strict: bool,
) -> bool:
    for a, b in _exact_edges(vertices, closed=True):
        if _on_segment_exact(point, a, b):
            return not strict
    inside = False
    count = len(vertices)
    previous = count - 1
    for index in range(count):
        xi, yi = vertices[index]
        xj, yj = vertices[previous]
        if (yi > point[1]) != (yj > point[1]):
            crossing_x = xi + (xj - xi) * (point[1] - yi) / (yj - yi)
            if point[0] < crossing_x:
                inside = not inside
        previous = index
    return inside


def _segment_intersection_params(
    a: _FractionPoint,
    b: _FractionPoint,
    c: _FractionPoint,
    d: _FractionPoint,
) -> tuple[Fraction, ...]:
    """Parameters on segment a-b (in [0, 1]) where it meets segment c-d."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    offset = (c[0] - a[0], c[1] - a[1])
    if denominator != 0:
        t = (offset[0] * s[1] - offset[1] * s[0]) / denominator
        u = (offset[0] * r[1] - offset[1] * r[0]) / denominator
        if 0 <= t <= 1 and 0 <= u <= 1:
            return (t,)
        return ()
    if _orientation_exact(a, b, c) != 0:
        return ()  # parallel but not collinear
    length_squared = r[0] * r[0] + r[1] * r[1]
    t_c = ((c[0] - a[0]) * r[0] + (c[1] - a[1]) * r[1]) / length_squared
    t_d = ((d[0] - a[0]) * r[0] + (d[1] - a[1]) * r[1]) / length_squared
    low, high = (t_c, t_d) if t_c <= t_d else (t_d, t_c)
    low = max(low, Fraction(0))
    high = min(high, Fraction(1))
    if low > high:
        return ()
    if low == high:
        return (low,)
    return (low, high)


def _collinear_overlap_interval(
    a: _FractionPoint,
    b: _FractionPoint,
    c: _FractionPoint,
    d: _FractionPoint,
) -> tuple[Fraction, Fraction] | None:
    """The positive-length collinear overlap of c-d on a-b, or None."""
    r = (b[0] - a[0], b[1] - a[1])
    s = (d[0] - c[0], d[1] - c[1])
    if r[0] * s[1] - r[1] * s[0] != 0:
        return None
    if _orientation_exact(a, b, c) != 0:
        return None
    length_squared = r[0] * r[0] + r[1] * r[1]
    t_c = ((c[0] - a[0]) * r[0] + (c[1] - a[1]) * r[1]) / length_squared
    t_d = ((d[0] - a[0]) * r[0] + (d[1] - a[1]) * r[1]) / length_squared
    low, high = (t_c, t_d) if t_c <= t_d else (t_d, t_c)
    low = max(low, Fraction(0))
    high = min(high, Fraction(1))
    if low >= high:
        return None
    return (low, high)


def _ring_is_ccw(vertices: tuple[_FractionPoint, ...]) -> bool:
    total = Fraction(0)
    count = len(vertices)
    for index in range(count):
        ax, ay = vertices[index]
        bx, by = vertices[(index + 1) % count]
        total += ax * by - ay * bx
    return total > 0


def _interior_normal(
    a: _FractionPoint, b: _FractionPoint, *, ccw: bool
) -> _FractionPoint:
    direction = (b[0] - a[0], b[1] - a[1])
    if ccw:
        return (-direction[1], direction[0])
    return (direction[1], -direction[0])


def _partition_midpoint_strictly_inside(
    edges: tuple[tuple[_FractionPoint, _FractionPoint], ...],
    other_vertices: tuple[_FractionPoint, ...],
) -> bool:
    other_edges = _exact_edges(other_vertices, closed=True)
    for a, b in edges:
        parameters = {Fraction(0), Fraction(1)}
        for c, d in other_edges:
            parameters.update(_segment_intersection_params(a, b, c, d))
        ordered = sorted(parameters)
        for low, high in zip(ordered, ordered[1:]):
            midpoint_parameter = (low + high) / 2
            midpoint = (
                a[0] + (b[0] - a[0]) * midpoint_parameter,
                a[1] + (b[1] - a[1]) * midpoint_parameter,
            )
            if _ring_contains_exact(other_vertices, midpoint, strict=True):
                return True
    return False


def rings_interiors_overlap(first: PolygonRing, second: PolygonRing) -> bool:
    """True exactly when two simple rings share interior area.

    Shared edges and touching vertices are never interior overlap.  The
    decision is complete for simple rings, by case analysis on the
    shared region's boundary: if the interiors share area then either
    (1) two boundary edges properly cross; or (2) one ring's vertex
    lies strictly inside the other; or the shared region's boundary
    contains an arc of one ring that (3) runs collinearly along the
    other ring's boundary with both interiors on the same side, or
    (4) lies strictly inside the other ring, in which case the midpoint
    of that edge's partition segment (edges split at every exact
    intersection with the other boundary) is strictly interior.  All
    four tests run in exact rational arithmetic.
    """
    if not isinstance(first, PolygonRing) or not isinstance(
        second, PolygonRing
    ):
        raise CourseWorldModelError("interior overlap requires two rings")
    a_min_x, a_min_y, a_max_x, a_max_y = first.bounds
    b_min_x, b_min_y, b_max_x, b_max_y = second.bounds
    if (
        a_max_x < b_min_x
        or b_max_x < a_min_x
        or a_max_y < b_min_y
        or b_max_y < a_min_y
    ):
        return False
    first_vertices = _fraction_vertices(first.vertices)
    second_vertices = _fraction_vertices(second.vertices)
    first_edges = _exact_edges(first_vertices, closed=True)
    second_edges = _exact_edges(second_vertices, closed=True)
    for edge_a in first_edges:
        for edge_b in second_edges:
            if _proper_cross_exact(*edge_a, *edge_b):
                return True
    for vertex in first_vertices:
        if _ring_contains_exact(second_vertices, vertex, strict=True):
            return True
    for vertex in second_vertices:
        if _ring_contains_exact(first_vertices, vertex, strict=True):
            return True
    first_ccw = _ring_is_ccw(first_vertices)
    second_ccw = _ring_is_ccw(second_vertices)
    for a, b in first_edges:
        for c, d in second_edges:
            interval = _collinear_overlap_interval(a, b, c, d)
            if interval is None:
                continue
            normal_first = _interior_normal(a, b, ccw=first_ccw)
            normal_second = _interior_normal(c, d, ccw=second_ccw)
            dot = (
                normal_first[0] * normal_second[0]
                + normal_first[1] * normal_second[1]
            )
            if dot > 0:
                return True
    if _partition_midpoint_strictly_inside(first_edges, second_vertices):
        return True
    if _partition_midpoint_strictly_inside(second_edges, first_vertices):
        return True
    return False


def chain_properly_crosses_ring(
    vertices: tuple[tuple[float, float], ...],
    ring: PolygonRing,
    *,
    closed: bool,
) -> bool:
    """True when any chain edge properly crosses the ring boundary.

    Touching the boundary or running along it is not a crossing; a
    proper crossing means the chain passes from one side of the ring
    boundary to the other through an edge interior, which (together
    with vertex containment) is how a feature is proven to escape its
    declared hole.
    """
    if not isinstance(ring, PolygonRing):
        raise CourseWorldModelError(
            "chain crossing requires a PolygonRing boundary"
        )
    chain = _fraction_vertices(tuple(vertices))
    if len(chain) < 2:
        return False
    ring_edges = _exact_edges(_fraction_vertices(ring.vertices), closed=True)
    for edge in _exact_edges(chain, closed=closed):
        for boundary_edge in ring_edges:
            if _proper_cross_exact(*edge, *boundary_edge):
                return True
    return False


@dataclass(frozen=True, slots=True)
class Polyline:
    """One open polyline with at least two distinct consecutive vertices."""

    vertices: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "vertices",
            _validated_vertices(
                self.vertices,
                minimum=_MINIMUM_POLYLINE_VERTICES,
                contract="polyline",
            ),
        )

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        xs = [vertex[0] for vertex in self.vertices]
        ys = [vertex[1] for vertex in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))

    def distance_to(self, x: float, y: float) -> float:
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        return min(
            _point_segment_distance(
                x, y, self.vertices[index], self.vertices[index + 1]
            )
            for index in range(len(self.vertices) - 1)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "vertices": [[vertex[0], vertex[1]] for vertex in self.vertices]
        }

    @classmethod
    def from_dict(cls, data: object) -> "Polyline":
        if not isinstance(data, dict) or set(data) != {"vertices"}:
            raise CourseWorldModelError(
                "polyline payload must be a mapping with exactly a "
                "'vertices' key"
            )
        raw = data["vertices"]
        if not isinstance(raw, list):
            raise CourseWorldModelError("polyline vertices must be a list")
        vertices: list[tuple[float, float]] = []
        for index, pair in enumerate(raw):
            if not isinstance(pair, list) or len(pair) != 2:
                raise CourseWorldModelError(
                    f"polyline vertices[{index}] must be an [x, y] pair"
                )
            vertices.append((pair[0], pair[1]))
        return cls(vertices=tuple(vertices))


__all__ = [
    "MAX_ABS_COORDINATE_M",
    "PolygonRing",
    "Polyline",
    "chain_properly_crosses_ring",
    "point_on_segment",
    "require_finite_number",
    "rings_interiors_overlap",
    "segments_intersect",
    "segments_properly_cross",
]
