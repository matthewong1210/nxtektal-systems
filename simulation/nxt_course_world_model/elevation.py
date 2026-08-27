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

_GRID_KEYS = frozenset(
    {"origin_x", "origin_y", "cell_size_m", "n_rows", "n_cols", "heights"}
)


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
        require_finite_number(self.origin_x, "origin_x")
        require_finite_number(self.origin_y, "origin_y")
        cell = require_finite_number(self.cell_size_m, "cell_size_m")
        if cell <= 0.0:
            raise CourseWorldModelError(
                f"cell_size_m must be positive; got {self.cell_size_m!r}"
            )
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
        for index, height in enumerate(self.heights):
            require_finite_number(height, f"heights[{index}]")

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
