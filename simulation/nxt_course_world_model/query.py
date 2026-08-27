"""The pure, deterministic, read-only Map Query Service.

Every query is a pure function of one validated ``CourseWorldModel``:
no mutation, no file, no network, no clock, no randomness.  Every
result carries a compact model reference (identity, version, content
digest, frame, resolution) so a stale or foreign map can never answer
silently, and never the model's own geometry payload.

The service answers spatial questions only.  It is not a
route planner, not a navigation stack, not a geofence enforcer, and
not a shot-physics simulator: the trajectory query intersects an
already-computed sample sequence with terrain and refuses to fabricate
an answer it cannot prove.

Malformed questions (non-finite input, invalid radius, malformed
trajectories, frame mismatches) raise ``CourseModelQueryError``.
Valid questions with negative answers return explicit statuses
(``OUT_OF_BOUNDS``, ``UNCLASSIFIED``, ``NO_HOLE``,
``NO_INTERSECTION``, ``UNPROVABLE``) instead of optimistic values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .errors import CourseModelQueryError
from .features import (
    HAZARD_SURFACE_TYPES,
    RestrictionCategory,
    SURFACE_TIE_BREAK_ORDER,
    SurfaceFeature,
    SurfaceType,
)
from .geometry import MAX_ABS_COORDINATE_M
from .model import CourseWorldModel

SUPPORTED_QUERY_KINDS = (
    "elevation",
    "hole_context",
    "nearby_hazards",
    "restricted_area",
    "slope",
    "surface",
    "trajectory_terrain_intersection",
)

_SURFACE_RANK = {
    surface_type: index
    for index, surface_type in enumerate(SURFACE_TIE_BREAK_ORDER)
}


class QueryStatus(StrEnum):
    """The closed query-outcome vocabulary."""

    OK = "ok"
    OUT_OF_BOUNDS = "out_of_bounds"
    UNCLASSIFIED = "unclassified"
    NO_HOLE = "no_hole"
    NO_INTERSECTION = "no_intersection"
    UNPROVABLE = "unprovable"


def _require_query_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CourseModelQueryError(
            f"{field_name} must be an int or float, not "
            f"{type(value).__name__}"
        )
    try:
        numeric = float(value)
    except OverflowError as exc:
        raise CourseModelQueryError(f"{field_name} must be finite") from exc
    if not math.isfinite(numeric):
        raise CourseModelQueryError(f"{field_name} must be finite")
    if abs(numeric) > MAX_ABS_COORDINATE_M:
        raise CourseModelQueryError(
            f"{field_name} magnitude must not exceed "
            f"{MAX_ABS_COORDINATE_M!r}; the course frame is metric and "
            f"bounded, got {numeric!r}"
        )
    return numeric


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Compact model identity carried by every query result."""

    course_model_id: str
    model_version: str
    content_digest: str
    frame_id: str
    resolution_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "course_model_id": self.course_model_id,
            "model_version": self.model_version,
            "content_digest": self.content_digest,
            "frame_id": self.frame_id,
            "resolution_m": self.resolution_m,
        }


@dataclass(frozen=True, slots=True)
class ElevationResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    elevation_m: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "elevation_m": self.elevation_m,
        }


@dataclass(frozen=True, slots=True)
class SurfaceResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    surface_type: SurfaceType | None
    feature_id: str | None
    hole_id: str | None
    cart_path_ids: tuple[str, ...]
    restricted_zone_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "surface_type": (
                None if self.surface_type is None else self.surface_type.value
            ),
            "feature_id": self.feature_id,
            "hole_id": self.hole_id,
            "cart_path_ids": list(self.cart_path_ids),
            "restricted_zone_ids": list(self.restricted_zone_ids),
        }


@dataclass(frozen=True, slots=True)
class SlopeResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    dz_dx: float | None
    dz_dy: float | None
    slope_magnitude: float | None
    grade_percent: float | None
    aspect_deg: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "dz_dx": self.dz_dx,
            "dz_dy": self.dz_dy,
            "slope_magnitude": self.slope_magnitude,
            "grade_percent": self.grade_percent,
            "aspect_deg": self.aspect_deg,
        }


@dataclass(frozen=True, slots=True)
class HoleContextResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    hole_id: str | None
    hole_number: int | None
    surface_type: SurfaceType | None
    distance_to_green_m: float | None
    distance_to_tee_m: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "hole_id": self.hole_id,
            "hole_number": self.hole_number,
            "surface_type": (
                None if self.surface_type is None else self.surface_type.value
            ),
            "distance_to_green_m": self.distance_to_green_m,
            "distance_to_tee_m": self.distance_to_tee_m,
        }


@dataclass(frozen=True, slots=True)
class HazardHit:
    feature_id: str
    hazard_type: SurfaceType
    distance_m: float
    hole_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "hazard_type": self.hazard_type.value,
            "distance_m": self.distance_m,
            "hole_id": self.hole_id,
        }


@dataclass(frozen=True, slots=True)
class NearbyHazardsResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    radius_m: float
    hazards: tuple[HazardHit, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "radius_m": self.radius_m,
            "hazards": [hit.to_dict() for hit in self.hazards],
        }


@dataclass(frozen=True, slots=True)
class RestrictedMatch:
    feature_id: str
    category: RestrictionCategory
    commissioned_zone_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "category": self.category.value,
            "commissioned_zone_id": self.commissioned_zone_id,
        }


@dataclass(frozen=True, slots=True)
class RestrictedResult:
    model: ModelRef
    status: QueryStatus
    x: float
    y: float
    restricted: bool | None
    matches: tuple[RestrictedMatch, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "x": self.x,
            "y": self.y,
            "restricted": self.restricted,
            "matches": [match.to_dict() for match in self.matches],
        }


@dataclass(frozen=True, slots=True)
class TrajectorySample:
    """One already-computed trajectory sample in the course frame."""

    t_s: float
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("t_s", self.t_s),
            ("x", self.x),
            ("y", self.y),
            ("z", self.z),
        ):
            _require_query_number(value, field_name)


@dataclass(frozen=True, slots=True)
class TrajectoryIntersectionResult:
    model: ModelRef
    status: QueryStatus
    sample_count: int
    segment_index: int | None
    x: float | None
    y: float | None
    z: float | None
    surface_type: SurfaceType | None
    surface_feature_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.to_dict(),
            "status": self.status.value,
            "sample_count": self.sample_count,
            "segment_index": self.segment_index,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "surface_type": (
                None if self.surface_type is None else self.surface_type.value
            ),
            "surface_feature_id": self.surface_feature_id,
        }


class MapQueryService:
    """Deterministic read-only queries over one validated model."""

    def __init__(self, model: CourseWorldModel) -> None:
        if not isinstance(model, CourseWorldModel):
            raise CourseModelQueryError(
                "the query service requires a CourseWorldModel"
            )
        self._model = model
        self._ref = ModelRef(
            course_model_id=model.course_model_id,
            model_version=model.model_version,
            content_digest=model.content_digest,
            frame_id=model.frame.frame_id,
            resolution_m=model.elevation.resolution_m,
        )

    @property
    def model_ref(self) -> ModelRef:
        return self._ref

    def _validate_point(self, x: object, y: object) -> tuple[float, float]:
        return (
            _require_query_number(x, "x"),
            _require_query_number(y, "y"),
        )

    def _classify_primary(
        self, x: float, y: float
    ) -> SurfaceFeature | None:
        containing = [
            feature
            for feature in self._model.surfaces
            if feature.polygon.contains(x, y)
        ]
        if not containing:
            return None
        containing.sort(
            key=lambda feature: (
                _SURFACE_RANK[feature.surface_type],
                feature.feature_id,
            )
        )
        return containing[0]

    def _containing_hole(self, x: float, y: float):
        for hole in self._model.holes:  # canonically sorted by hole_id
            if hole.boundary.contains(x, y):
                return hole
        return None

    def get_elevation(self, x: object, y: object) -> ElevationResult:
        x, y = self._validate_point(x, y)
        if not self._model.bounds.contains(x, y):
            return ElevationResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                elevation_m=None,
            )
        return ElevationResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            elevation_m=self._model.elevation.elevation_at(x, y),
        )

    def get_surface(self, x: object, y: object) -> SurfaceResult:
        x, y = self._validate_point(x, y)
        if not self._model.bounds.contains(x, y):
            return SurfaceResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                surface_type=None,
                feature_id=None,
                hole_id=None,
                cart_path_ids=(),
                restricted_zone_ids=(),
            )
        winner = self._classify_primary(x, y)
        cart_path_ids = tuple(
            path.feature_id
            for path in self._model.cart_paths
            if path.covers(x, y)
        )
        restricted_zone_ids = tuple(
            zone.feature_id
            for zone in self._model.restricted_zones
            if zone.polygon.contains(x, y)
        )
        if winner is None:
            hole = self._containing_hole(x, y)
            return SurfaceResult(
                model=self._ref,
                status=QueryStatus.UNCLASSIFIED,
                x=x,
                y=y,
                surface_type=None,
                feature_id=None,
                hole_id=None if hole is None else hole.hole_id,
                cart_path_ids=cart_path_ids,
                restricted_zone_ids=restricted_zone_ids,
            )
        hole_id = winner.hole_id
        if hole_id is None:
            hole = self._containing_hole(x, y)
            hole_id = None if hole is None else hole.hole_id
        return SurfaceResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            surface_type=winner.surface_type,
            feature_id=winner.feature_id,
            hole_id=hole_id,
            cart_path_ids=cart_path_ids,
            restricted_zone_ids=restricted_zone_ids,
        )

    def get_slope(self, x: object, y: object) -> SlopeResult:
        x, y = self._validate_point(x, y)
        if not self._model.bounds.contains(x, y):
            return SlopeResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                dz_dx=None,
                dz_dy=None,
                slope_magnitude=None,
                grade_percent=None,
                aspect_deg=None,
            )
        dz_dx, dz_dy = self._model.elevation.slope_at(x, y)
        magnitude = math.hypot(dz_dx, dz_dy)
        if magnitude == 0.0:
            aspect: float | None = None
        else:
            # Azimuth of steepest descent, degrees clockwise from north.
            aspect = math.degrees(math.atan2(-dz_dx, -dz_dy)) % 360.0
        return SlopeResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            dz_dx=dz_dx,
            dz_dy=dz_dy,
            slope_magnitude=magnitude,
            grade_percent=100.0 * magnitude,
            aspect_deg=aspect,
        )

    def get_hole_context(self, x: object, y: object) -> HoleContextResult:
        x, y = self._validate_point(x, y)
        if not self._model.bounds.contains(x, y):
            return HoleContextResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                hole_id=None,
                hole_number=None,
                surface_type=None,
                distance_to_green_m=None,
                distance_to_tee_m=None,
            )
        hole = self._containing_hole(x, y)
        if hole is None:
            return HoleContextResult(
                model=self._ref,
                status=QueryStatus.NO_HOLE,
                x=x,
                y=y,
                hole_id=None,
                hole_number=None,
                surface_type=None,
                distance_to_green_m=None,
                distance_to_tee_m=None,
            )
        winner = self._classify_primary(x, y)

        def nearest(surface_type: SurfaceType) -> float | None:
            distances = [
                feature.polygon.distance_to(x, y)
                for feature in self._model.surfaces
                if feature.hole_id == hole.hole_id
                and feature.surface_type is surface_type
            ]
            return min(distances) if distances else None

        return HoleContextResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            hole_id=hole.hole_id,
            hole_number=hole.hole_number,
            surface_type=None if winner is None else winner.surface_type,
            distance_to_green_m=nearest(SurfaceType.GREEN),
            distance_to_tee_m=nearest(SurfaceType.TEE),
        )

    def get_nearby_hazards(
        self, x: object, y: object, radius_m: object
    ) -> NearbyHazardsResult:
        x, y = self._validate_point(x, y)
        radius = _require_query_number(radius_m, "radius_m")
        if radius <= 0.0:
            raise CourseModelQueryError(
                f"radius_m must be positive and bounded; got {radius_m!r}"
            )
        if not self._model.bounds.contains(x, y):
            return NearbyHazardsResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                radius_m=radius,
                hazards=(),
            )
        hits = []
        for feature in self._model.surfaces:
            if feature.surface_type not in HAZARD_SURFACE_TYPES:
                continue
            distance = feature.polygon.distance_to(x, y)
            if distance <= radius:
                hits.append(
                    HazardHit(
                        feature_id=feature.feature_id,
                        hazard_type=feature.surface_type,
                        distance_m=distance,
                        hole_id=feature.hole_id,
                    )
                )
        hits.sort(key=lambda hit: (hit.distance_m, hit.feature_id))
        return NearbyHazardsResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            radius_m=radius,
            hazards=tuple(hits),
        )

    def is_restricted(self, x: object, y: object) -> RestrictedResult:
        x, y = self._validate_point(x, y)
        if not self._model.bounds.contains(x, y):
            return RestrictedResult(
                model=self._ref,
                status=QueryStatus.OUT_OF_BOUNDS,
                x=x,
                y=y,
                restricted=None,
                matches=(),
            )
        matches = tuple(
            RestrictedMatch(
                feature_id=zone.feature_id,
                category=zone.category,
                commissioned_zone_id=zone.commissioned_zone_id,
            )
            for zone in self._model.restricted_zones
            if zone.polygon.contains(x, y)
        )
        return RestrictedResult(
            model=self._ref,
            status=QueryStatus.OK,
            x=x,
            y=y,
            restricted=bool(matches),
            matches=matches,
        )

    def intersect_trajectory_with_terrain(
        self,
        samples: object,
        *,
        frame_id: str,
    ) -> TrajectoryIntersectionResult:
        if frame_id != self._model.frame.frame_id:
            raise CourseModelQueryError(
                f"trajectory frame {frame_id!r} does not match the model "
                f"frame {self._model.frame.frame_id!r}; a foreign frame "
                "cannot be silently reinterpreted"
            )
        if not isinstance(samples, tuple):
            raise CourseModelQueryError(
                "samples must be a tuple of TrajectorySample"
            )
        if len(samples) < 2:
            raise CourseModelQueryError(
                "a trajectory requires at least two samples"
            )
        for index, sample in enumerate(samples):
            if not isinstance(sample, TrajectorySample):
                raise CourseModelQueryError(
                    f"samples[{index}] must be a TrajectorySample"
                )
        for index in range(1, len(samples)):
            if samples[index].t_s <= samples[index - 1].t_s:
                raise CourseModelQueryError(
                    "trajectory sample times must strictly increase; "
                    f"samples[{index}] does not follow its predecessor"
                )
        bounds = self._model.bounds
        if all(
            not bounds.contains(sample.x, sample.y) for sample in samples
        ):
            raise CourseModelQueryError(
                "the trajectory lies entirely outside the model bounds"
            )
        first = samples[0]
        if not bounds.contains(first.x, first.y):
            raise CourseModelQueryError(
                "the trajectory must begin inside the model bounds"
            )
        elevation = self._model.elevation
        first_clearance = first.z - elevation.elevation_at(first.x, first.y)
        if first_clearance <= 0.0:
            raise CourseModelQueryError(
                "the first trajectory sample is at or below terrain; the "
                "intersection is ambiguous and will not be fabricated"
            )

        for index in range(len(samples) - 1):
            start = samples[index]
            end = samples[index + 1]
            if not bounds.contains(end.x, end.y):
                return TrajectoryIntersectionResult(
                    model=self._ref,
                    status=QueryStatus.UNPROVABLE,
                    sample_count=len(samples),
                    segment_index=None,
                    x=None,
                    y=None,
                    z=None,
                    surface_type=None,
                    surface_feature_id=None,
                )
            # Terrain is bilinear per cell, so the clearance along the
            # straight segment is piecewise quadratic; the grid solves
            # each piece analytically, which catches a crossing inside
            # the segment even when both sample endpoints are above the
            # terrain, and always reports the first contact.
            parameter = elevation.first_descent_crossing(
                start.x, start.y, start.z, end.x, end.y, end.z
            )
            if parameter is not None:
                hit_x = start.x + parameter * (end.x - start.x)
                hit_y = start.y + parameter * (end.y - start.y)
                hit_z = elevation.elevation_at(hit_x, hit_y)
                winner = self._classify_primary(hit_x, hit_y)
                return TrajectoryIntersectionResult(
                    model=self._ref,
                    status=QueryStatus.OK,
                    sample_count=len(samples),
                    segment_index=index,
                    x=hit_x,
                    y=hit_y,
                    z=hit_z,
                    surface_type=(
                        None if winner is None else winner.surface_type
                    ),
                    surface_feature_id=(
                        None if winner is None else winner.feature_id
                    ),
                )
        return TrajectoryIntersectionResult(
            model=self._ref,
            status=QueryStatus.NO_INTERSECTION,
            sample_count=len(samples),
            segment_index=None,
            x=None,
            y=None,
            z=None,
            surface_type=None,
            surface_feature_id=None,
        )


__all__ = [
    "ElevationResult",
    "HazardHit",
    "HoleContextResult",
    "MapQueryService",
    "ModelRef",
    "NearbyHazardsResult",
    "QueryStatus",
    "RestrictedMatch",
    "RestrictedResult",
    "SUPPORTED_QUERY_KINDS",
    "SlopeResult",
    "SurfaceResult",
    "TrajectoryIntersectionResult",
    "TrajectorySample",
]
