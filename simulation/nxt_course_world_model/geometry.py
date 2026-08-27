"""Deterministic 2D geometry primitives in the course-local frame.

Validation predicates (orientation, segment intersection, area) use
exact ``Decimal`` arithmetic over the string form of each coordinate so
large-magnitude inputs cannot produce sign errors through float
cancellation.  Query arithmetic (containment ray casts, distances)
uses plain float math over the already-validated, bounded course-local
coordinates; identical inputs always produce identical results.

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
from decimal import Decimal

from .errors import CourseWorldModelError

_MINIMUM_RING_VERTICES = 3
_MINIMUM_POLYLINE_VERTICES = 2


def require_finite_number(value: object, field_name: str) -> float:
    """Validate one finite numeric value and return it as a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CourseWorldModelError(
            f"{field_name} must be an int or float, not {type(value).__name__}"
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise CourseWorldModelError(f"{field_name} must be finite")
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


def _dec(value: float) -> Decimal:
    # str() preserves the shortest exact decimal form, so the exact
    # predicates below cannot suffer float cancellation.
    return Decimal(str(value))


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

    def _twice_signed_area(self) -> Decimal:
        total = Decimal(0)
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

    def _interior_witnesses(self) -> tuple[tuple[float, float], ...]:
        witnesses = list(self.vertices)
        witnesses.extend(
            ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            for a, b in self._edges()
        )
        centroid = (
            sum(vertex[0] for vertex in self.vertices) / len(self.vertices),
            sum(vertex[1] for vertex in self.vertices) / len(self.vertices),
        )
        if self.strictly_contains(*centroid):
            witnesses.append(centroid)
        return tuple(witnesses)

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


def rings_interiors_overlap(first: PolygonRing, second: PolygonRing) -> bool:
    """True when two rings share interior area.

    Shared edges and touching vertices are not interior overlap.  The
    V0 detection combines proper edge crossings, strict containment of
    the other ring's vertices, edge midpoints, and interior centroids,
    and an identical-vertex-set check; it is exhaustive for the simple
    convex-and-mildly-concave rings this contract stores.
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
    if len(first.vertices) == len(second.vertices) and set(
        first.vertices
    ) == set(second.vertices):
        return True
    for edge_a in first._edges():
        for edge_b in second._edges():
            if segments_properly_cross(*edge_a, *edge_b):
                return True
    for witness in first._interior_witnesses():
        if second.strictly_contains(*witness) and first.contains(*witness):
            return True
    for witness in second._interior_witnesses():
        if first.strictly_contains(*witness) and second.contains(*witness):
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
    "PolygonRing",
    "Polyline",
    "point_on_segment",
    "require_finite_number",
    "rings_interiors_overlap",
    "segments_intersect",
    "segments_properly_cross",
]
