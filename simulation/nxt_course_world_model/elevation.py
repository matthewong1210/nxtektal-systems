"""Regular finite elevation grid with deterministic bilinear queries.

Heights are metres in the course-local vertical basis, sampled at grid
*nodes* (vertices), stored row-major from the south-west corner: row 0
is the southernmost row and column 0 the westernmost column.  The grid
covers the closed rectangle from its origin to
``origin + (n - 1) * cell_size`` on each axis; interpolation inside a
cell is bilinear over its four corner nodes, points on the maximum
edges belong to the last cell, and anything outside the coverage fails
loudly -- there is no extrapolation.

Slope is the analytic gradient of the bilinear patch selected by the
same deterministic cell rule, so a point on a shared cell edge always
uses one defined patch.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from .errors import CourseWorldModelError
from .geometry import require_finite_number

_MINIMUM_NODES_PER_AXIS = 2

# The metric contract's resolution floor: a cell finer than a
# micrometre has no physical meaning for a course surface, and the
# floor keeps every division by the cell size far from float overflow.
_MIN_CELL_SIZE_M = 1e-6

_GRID_KEYS = frozenset(
    {"origin_x", "origin_y", "cell_size_m", "n_rows", "n_cols", "heights"}
)


def _smallest_clearance_root(
    *,
    quadratic: float,
    linear: float,
    constant: float,
    low: float,
    high: float,
) -> float | None:
    """The smallest u in (low, high] where the clearance is <= 0.

    The clearance g(u) = quadratic*u^2 + linear*u + constant is
    strictly positive at ``low`` on entry (each earlier piece certified
    that before handing over); a float disagreement at a shared piece
    boundary is resolved deterministically as contact at ``low``.
    """

    def clearance(u: float) -> float:
        return (quadratic * u + linear) * u + constant

    if clearance(low) <= 0.0:
        return low
    candidates: list[float] = []
    if quadratic == 0.0:
        if linear != 0.0:
            root = -constant / linear
            if low < root <= high:
                candidates.append(root)
    else:
        discriminant = linear * linear - 4.0 * quadratic * constant
        if discriminant >= 0.0:
            square_root = math.sqrt(discriminant)
            if linear >= 0.0:
                q = -(linear + square_root) / 2.0
            else:
                q = -(linear - square_root) / 2.0
            roots = {q / quadratic}
            if q != 0.0:
                roots.add(constant / q)
            else:
                roots.add(0.0)
            for root in roots:
                if low < root <= high:
                    candidates.append(root)
    if candidates:
        return min(candidates)
    if clearance(high) <= 0.0:
        # Continuity guarantees a crossing that float root-finding
        # missed; a fixed-count bisection resolves it deterministically.
        bracket_low, bracket_high = low, high
        for _ in range(60):
            midpoint = (bracket_low + bracket_high) / 2.0
            if clearance(midpoint) > 0.0:
                bracket_low = midpoint
            else:
                bracket_high = midpoint
        return bracket_high
    return None


def _require_axis_count(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CourseWorldModelError(f"{field_name} must be an integer")
    if value < _MINIMUM_NODES_PER_AXIS:
        raise CourseWorldModelError(
            f"{field_name} must be at least {_MINIMUM_NODES_PER_AXIS}; "
            f"got {value}"
        )
    return value


@dataclass(frozen=True, slots=True)
class ElevationGrid:
    """One immutable regular elevation grid."""

    origin_x: float
    origin_y: float
    cell_size_m: float
    n_rows: int
    n_cols: int
    heights: tuple[float, ...]

    def __post_init__(self) -> None:
        # Numeric fields are stored as floats so canonical serialization
        # (and therefore the content digest) never depends on whether a
        # producer supplied an int or its equal float.
        object.__setattr__(
            self, "origin_x", require_finite_number(self.origin_x, "origin_x")
        )
        object.__setattr__(
            self, "origin_y", require_finite_number(self.origin_y, "origin_y")
        )
        cell = require_finite_number(self.cell_size_m, "cell_size_m")
        if cell < _MIN_CELL_SIZE_M:
            raise CourseWorldModelError(
                f"cell_size_m must be at least {_MIN_CELL_SIZE_M!r} m; "
                f"got {self.cell_size_m!r}"
            )
        object.__setattr__(self, "cell_size_m", cell)
        _require_axis_count(self.n_rows, "n_rows")
        _require_axis_count(self.n_cols, "n_cols")
        if not isinstance(self.heights, tuple):
            raise CourseWorldModelError("heights must be a tuple")
        expected = self.n_rows * self.n_cols
        if len(self.heights) != expected:
            raise CourseWorldModelError(
                f"heights must contain exactly n_rows * n_cols = {expected} "
                f"values; got {len(self.heights)}"
            )
        object.__setattr__(
            self,
            "heights",
            tuple(
                require_finite_number(height, f"heights[{index}]")
                for index, height in enumerate(self.heights)
            ),
        )
        require_finite_number(self.max_x, "grid coverage max_x")
        require_finite_number(self.max_y, "grid coverage max_y")

    @property
    def resolution_m(self) -> float:
        return self.cell_size_m

    @property
    def min_x(self) -> float:
        return self.origin_x

    @property
    def min_y(self) -> float:
        return self.origin_y

    @property
    def max_x(self) -> float:
        return self.origin_x + (self.n_cols - 1) * self.cell_size_m

    @property
    def max_y(self) -> float:
        return self.origin_y + (self.n_rows - 1) * self.cell_size_m

    def covers(self, x: float, y: float) -> bool:
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        return (
            self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y
        )

    def _node_height(self, row: int, column: int) -> float:
        return float(self.heights[row * self.n_cols + column])

    def _cell_for(self, x: float, y: float) -> tuple[int, int, float, float]:
        column = int(math.floor((x - self.origin_x) / self.cell_size_m))
        row = int(math.floor((y - self.origin_y) / self.cell_size_m))
        column = min(max(column, 0), self.n_cols - 2)
        row = min(max(row, 0), self.n_rows - 2)
        u = (x - (self.origin_x + column * self.cell_size_m)) / (
            self.cell_size_m
        )
        v = (y - (self.origin_y + row * self.cell_size_m)) / self.cell_size_m
        return row, column, u, v

    def _require_covered(self, x: float, y: float) -> tuple[float, float]:
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        if not self.covers(x, y):
            raise CourseWorldModelError(
                f"point ({x!r}, {y!r}) is outside the elevation coverage "
                f"[{self.min_x}, {self.max_x}] x [{self.min_y}, "
                f"{self.max_y}]; the grid never extrapolates"
            )
        return x, y

    def elevation_at(self, x: float, y: float) -> float:
        x, y = self._require_covered(x, y)
        row, column, u, v = self._cell_for(x, y)
        z00 = self._node_height(row, column)
        z10 = self._node_height(row, column + 1)
        z01 = self._node_height(row + 1, column)
        z11 = self._node_height(row + 1, column + 1)
        return (
            (1.0 - u) * (1.0 - v) * z00
            + u * (1.0 - v) * z10
            + (1.0 - u) * v * z01
            + u * v * z11
        )

    def slope_at(self, x: float, y: float) -> tuple[float, float]:
        """The bilinear patch gradient (dz/dx, dz/dy) at the point."""
        x, y = self._require_covered(x, y)
        row, column, u, v = self._cell_for(x, y)
        z00 = self._node_height(row, column)
        z10 = self._node_height(row, column + 1)
        z01 = self._node_height(row + 1, column)
        z11 = self._node_height(row + 1, column + 1)
        dz_dx = ((1.0 - v) * (z10 - z00) + v * (z11 - z01)) / self.cell_size_m
        dz_dy = ((1.0 - u) * (z01 - z00) + u * (z11 - z10)) / self.cell_size_m
        return dz_dx, dz_dy

    def first_descent_crossing(
        self,
        x0: float,
        y0: float,
        z0: float,
        x1: float,
        y1: float,
        z1: float,
    ) -> float | None:
        """The smallest u in (0, 1] where the segment first meets terrain.

        The straight segment from (x0, y0, z0) to (x1, y1, z1) must have
        both endpoints inside the grid coverage and must start strictly
        above the terrain (the caller checks both).  Terrain is bilinear
        per cell, so the clearance along the segment is piecewise
        quadratic: the segment is split at every grid-line crossing and
        each piece's quadratic is solved analytically, which finds a
        crossing *inside* a piece even when both piece endpoints are
        above terrain.  Returns the first contact parameter, or None
        when the segment stays strictly above terrain throughout.
        """
        delta_x = x1 - x0
        delta_y = y1 - y0
        delta_z = z1 - z0
        breakpoints = {0.0, 1.0}
        for delta, origin, count, start in (
            (delta_x, self.origin_x, self.n_cols, x0),
            (delta_y, self.origin_y, self.n_rows, y0),
        ):
            if delta == 0.0:
                continue
            for index in range(count):
                parameter = (origin + index * self.cell_size_m - start) / (
                    delta
                )
                if 0.0 < parameter < 1.0:
                    breakpoints.add(parameter)
        ordered = sorted(breakpoints)
        for piece_start, piece_end in zip(ordered, ordered[1:]):
            midpoint = (piece_start + piece_end) / 2.0
            row, column, _, _ = self._cell_for(
                x0 + midpoint * delta_x, y0 + midpoint * delta_y
            )
            cell_x = self.origin_x + column * self.cell_size_m
            cell_y = self.origin_y + row * self.cell_size_m
            s0 = (x0 - cell_x) / self.cell_size_m
            su = delta_x / self.cell_size_m
            t0 = (y0 - cell_y) / self.cell_size_m
            tu = delta_y / self.cell_size_m
            z00 = self._node_height(row, column)
            z10 = self._node_height(row, column + 1)
            z01 = self._node_height(row + 1, column)
            z11 = self._node_height(row + 1, column + 1)
            twist = z11 - z10 - z01 + z00
            terrain_2 = twist * su * tu
            terrain_1 = (
                (z10 - z00) * su
                + (z01 - z00) * tu
                + twist * (s0 * tu + t0 * su)
            )
            terrain_0 = (
                z00 + (z10 - z00) * s0 + (z01 - z00) * t0 + twist * s0 * t0
            )
            root = _smallest_clearance_root(
                quadratic=-terrain_2,
                linear=delta_z - terrain_1,
                constant=z0 - terrain_0,
                low=piece_start,
                high=piece_end,
            )
            if root is not None:
                return root
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "origin_x": self.origin_x,
            "origin_y": self.origin_y,
            "cell_size_m": self.cell_size_m,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "heights": list(self.heights),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ElevationGrid":
        if not isinstance(data, Mapping):
            raise CourseWorldModelError(
                "elevation payload must be a mapping"
            )
        actual = set(data)
        if actual != _GRID_KEYS:
            missing = sorted(_GRID_KEYS - actual)
            extra = sorted(actual - _GRID_KEYS)
            raise CourseWorldModelError(
                f"elevation payload keys must be exactly "
                f"{sorted(_GRID_KEYS)}; missing={missing}, extra={extra}"
            )
        raw_heights = data["heights"]
        if not isinstance(raw_heights, list):
            raise CourseWorldModelError("heights must be a list")
        return cls(
            origin_x=data["origin_x"],  # type: ignore[arg-type]
            origin_y=data["origin_y"],  # type: ignore[arg-type]
            cell_size_m=data["cell_size_m"],  # type: ignore[arg-type]
            n_rows=data["n_rows"],  # type: ignore[arg-type]
            n_cols=data["n_cols"],  # type: ignore[arg-type]
            heights=tuple(raw_heights),
        )


__all__ = ["ElevationGrid"]
