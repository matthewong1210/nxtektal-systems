"""The immutable, versioned Course World Model contract.

One model is one controlled revision of slow-changing spatial truth
for one commissioned deployment: a course-local coordinate frame bound
to the commissioned spatial reference, a finite elevation surface,
semantic playing surfaces, overlays, and processed-scan provenance.

Models are immutable.  A course change produces a new ``model_version``
that names the version it supersedes; content addressing makes silent
mutation detectable.  ``content_digest`` proves content consistency
only -- it is not a signature and proves nothing about authorship,
surveying accuracy, or authenticity.  Display labels are presentation
only and never contribute to identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nxt_commissioning import CommissionedSite, canonical_projection_json

from .elevation import ElevationGrid
from .errors import CourseWorldModelError
from .features import (
    CartPath,
    HoleDefinition,
    RestrictedZone,
    ScanSourceReference,
    SurfaceFeature,
    require_feature_id,
)
from .frame import CourseCoordinateFrame
from .geometry import (
    PolygonRing,
    require_finite_number,
    rings_interiors_overlap,
)

COURSE_WORLD_MODEL_SCHEMA = "nxt-course-world-model/model/v0"

_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CONTENT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_MODEL_KEYS = frozenset(
    {
        "schema",
        "course_model_id",
        "model_version",
        "supersedes_version",
        "effective_from",
        "site_id",
        "deployment_id",
        "display_name",
        "frame",
        "bounds",
        "elevation",
        "course_boundary",
        "holes",
        "surfaces",
        "cart_paths",
        "restricted_zones",
        "scan_sources",
        "content_digest",
    }
)

_BOUNDS_KEYS = frozenset({"min_x", "min_y", "max_x", "max_y"})


def _require_non_blank(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise CourseWorldModelError(
            f"{field_name} must be a non-blank trimmed string"
        )
    return value


def _require_version(value: object, field_name: str) -> str:
    if type(value) is not str or not _VERSION_PATTERN.fullmatch(value):
        raise CourseWorldModelError(
            f"{field_name} must match {_VERSION_PATTERN.pattern!r}; "
            f"got {value!r}"
        )
    return value


def _parse_effective_from(value: object) -> datetime:
    text = _require_non_blank(value, "effective_from")
    if "T" not in text:
        raise CourseWorldModelError(
            "effective_from must be an ISO 8601 date-time containing 'T'"
        )
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CourseWorldModelError(
            "effective_from must be a valid ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CourseWorldModelError(
            "effective_from must be timezone-aware"
        )
    return parsed


@dataclass(frozen=True, slots=True)
class ModelBounds:
    """The closed course-local rectangle the model covers."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        for field_name, value in (
            ("min_x", self.min_x),
            ("min_y", self.min_y),
            ("max_x", self.max_x),
            ("max_y", self.max_y),
        ):
            require_finite_number(value, field_name)
        if self.min_x >= self.max_x or self.min_y >= self.max_y:
            raise CourseWorldModelError(
                "bounds must satisfy min_x < max_x and min_y < max_y"
            )

    def contains(self, x: float, y: float) -> bool:
        x = require_finite_number(x, "x")
        y = require_finite_number(y, "y")
        return (
            self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "min_x": self.min_x,
            "min_y": self.min_y,
            "max_x": self.max_x,
            "max_y": self.max_y,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelBounds":
        if not isinstance(data, Mapping) or set(data) != _BOUNDS_KEYS:
            raise CourseWorldModelError(
                "bounds payload keys must be exactly "
                f"{sorted(_BOUNDS_KEYS)}"
            )
        return cls(
            min_x=data["min_x"],
            min_y=data["min_y"],
            max_x=data["max_x"],
            max_y=data["max_y"],
        )


def _sorted_holes(
    holes: tuple[HoleDefinition, ...],
) -> tuple[HoleDefinition, ...]:
    return tuple(sorted(holes, key=lambda hole: hole.hole_id))


def _sorted_by_feature_id(features):
    return tuple(sorted(features, key=lambda feature: feature.feature_id))


def _sorted_sources(
    sources: tuple[ScanSourceReference, ...],
) -> tuple[ScanSourceReference, ...]:
    return tuple(sorted(sources, key=lambda source: source.source_id))


def _identity_payload_from_parts(
    *,
    course_model_id: str,
    model_version: str,
    supersedes_version: str | None,
    effective_from: str,
    site_id: str,
    deployment_id: str,
    frame: CourseCoordinateFrame,
    bounds: ModelBounds,
    elevation: ElevationGrid,
    course_boundary: PolygonRing,
    holes: tuple[HoleDefinition, ...],
    surfaces: tuple[SurfaceFeature, ...],
    cart_paths: tuple[CartPath, ...],
    restricted_zones: tuple[RestrictedZone, ...],
    scan_sources: tuple[ScanSourceReference, ...],
) -> dict[str, Any]:
    return {
        "schema": COURSE_WORLD_MODEL_SCHEMA,
        "course_model_id": course_model_id,
        "model_version": model_version,
        "supersedes_version": supersedes_version,
        "effective_from": effective_from,
        "site_id": site_id,
        "deployment_id": deployment_id,
        "frame": frame.to_dict(),
        "bounds": bounds.to_dict(),
        "elevation": elevation.to_dict(),
        "course_boundary": course_boundary.to_dict(),
        "holes": [hole.to_dict() for hole in _sorted_holes(holes)],
        "surfaces": [
            surface.to_dict() for surface in _sorted_by_feature_id(surfaces)
        ],
        "cart_paths": [
            path.to_dict() for path in _sorted_by_feature_id(cart_paths)
        ],
        "restricted_zones": [
            zone.to_dict()
            for zone in _sorted_by_feature_id(restricted_zones)
        ],
        "scan_sources": [
            source.to_dict() for source in _sorted_sources(scan_sources)
        ],
    }


def _compute_content_digest(identity_payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_projection_json(dict(identity_payload)).encode("utf-8")
    ).hexdigest()


def _require_within_bounds(
    bounds: ModelBounds,
    vertices: tuple[tuple[float, float], ...],
    label: str,
) -> None:
    for vertex in vertices:
        if not bounds.contains(vertex[0], vertex[1]):
            raise CourseWorldModelError(
                f"{label} has vertex {vertex!r} outside the model bounds"
            )


@dataclass(frozen=True, slots=True)
class CourseWorldModel:
    """One immutable Course World Model revision."""

    course_model_id: str
    model_version: str
    supersedes_version: str | None
    effective_from: str
    site_id: str
    deployment_id: str
    display_name: str
    frame: CourseCoordinateFrame
    bounds: ModelBounds
    elevation: ElevationGrid
    course_boundary: PolygonRing
    holes: tuple[HoleDefinition, ...]
    surfaces: tuple[SurfaceFeature, ...]
    cart_paths: tuple[CartPath, ...]
    restricted_zones: tuple[RestrictedZone, ...]
    scan_sources: tuple[ScanSourceReference, ...]
    content_digest: str

    @property
    def schema(self) -> str:
        return COURSE_WORLD_MODEL_SCHEMA

    def __post_init__(self) -> None:
        require_feature_id(self.course_model_id, "course_model_id")
        _require_version(self.model_version, "model_version")
        if self.supersedes_version is not None:
            _require_version(self.supersedes_version, "supersedes_version")
            if self.supersedes_version == self.model_version:
                raise CourseWorldModelError(
                    "a model version cannot supersede itself"
                )
        _parse_effective_from(self.effective_from)
        _require_non_blank(self.site_id, "site_id")
        _require_non_blank(self.deployment_id, "deployment_id")
        _require_non_blank(self.display_name, "display_name")
        if not isinstance(self.frame, CourseCoordinateFrame):
            raise CourseWorldModelError(
                "frame must be a CourseCoordinateFrame"
            )
        if not isinstance(self.bounds, ModelBounds):
            raise CourseWorldModelError("bounds must be a ModelBounds")
        if not isinstance(self.elevation, ElevationGrid):
            raise CourseWorldModelError(
                "elevation must be an ElevationGrid"
            )
        if not isinstance(self.course_boundary, PolygonRing):
            raise CourseWorldModelError(
                "course_boundary must be a PolygonRing"
            )
        for name, collection, member_type in (
            ("holes", self.holes, HoleDefinition),
            ("surfaces", self.surfaces, SurfaceFeature),
            ("cart_paths", self.cart_paths, CartPath),
            ("restricted_zones", self.restricted_zones, RestrictedZone),
            ("scan_sources", self.scan_sources, ScanSourceReference),
        ):
            if not isinstance(collection, tuple) or any(
                not isinstance(member, member_type) for member in collection
            ):
                raise CourseWorldModelError(
                    f"{name} must be a tuple of {member_type.__name__}"
                )
        if not self.holes:
            raise CourseWorldModelError(
                "a course model requires at least one hole"
            )
        if not self.scan_sources:
            raise CourseWorldModelError(
                "a course model requires at least one scan source reference"
            )
        object.__setattr__(self, "holes", _sorted_holes(self.holes))
        object.__setattr__(
            self, "surfaces", _sorted_by_feature_id(self.surfaces)
        )
        object.__setattr__(
            self, "cart_paths", _sorted_by_feature_id(self.cart_paths)
        )
        object.__setattr__(
            self,
            "restricted_zones",
            _sorted_by_feature_id(self.restricted_zones),
        )
        object.__setattr__(
            self, "scan_sources", _sorted_sources(self.scan_sources)
        )

        expected_bounds = ModelBounds(
            min_x=self.elevation.min_x,
            min_y=self.elevation.min_y,
            max_x=self.elevation.max_x,
            max_y=self.elevation.max_y,
        )
        if self.bounds != expected_bounds:
            raise CourseWorldModelError(
                "model bounds must equal the elevation-grid coverage "
                f"{expected_bounds.to_dict()!r}; got "
                f"{self.bounds.to_dict()!r}"
            )

        spatial_ids: set[str] = set()
        for label, identifier in (
            *(
                (f"hole {hole.hole_id!r}", hole.hole_id)
                for hole in self.holes
            ),
            *(
                (f"surface {feature.feature_id!r}", feature.feature_id)
                for feature in self.surfaces
            ),
            *(
                (f"cart path {path.feature_id!r}", path.feature_id)
                for path in self.cart_paths
            ),
            *(
                (f"restricted zone {zone.feature_id!r}", zone.feature_id)
                for zone in self.restricted_zones
            ),
        ):
            if identifier in spatial_ids:
                raise CourseWorldModelError(
                    f"duplicate feature identifier: {label}"
                )
            spatial_ids.add(identifier)
        source_ids = [source.source_id for source in self.scan_sources]
        if len(set(source_ids)) != len(source_ids):
            raise CourseWorldModelError(
                "scan source identifiers must be unique"
            )
        hole_numbers = [hole.hole_number for hole in self.holes]
        if len(set(hole_numbers)) != len(hole_numbers):
            raise CourseWorldModelError("hole numbers must be unique")

        _require_within_bounds(
            self.bounds, self.course_boundary.vertices, "course boundary"
        )
        hole_ids = {hole.hole_id for hole in self.holes}
        for hole in self.holes:
            _require_within_bounds(
                self.bounds,
                hole.boundary.vertices,
                f"hole {hole.hole_id!r} boundary",
            )
        for feature in self.surfaces:
            _require_within_bounds(
                self.bounds,
                feature.polygon.vertices,
                f"surface {feature.feature_id!r}",
            )
            if feature.hole_id is not None and feature.hole_id not in hole_ids:
                raise CourseWorldModelError(
                    f"surface {feature.feature_id!r} references unknown "
                    f"hole {feature.hole_id!r}"
                )
        for path in self.cart_paths:
            _require_within_bounds(
                self.bounds,
                path.centerline.vertices,
                f"cart path {path.feature_id!r}",
            )
            if path.hole_id is not None and path.hole_id not in hole_ids:
                raise CourseWorldModelError(
                    f"cart path {path.feature_id!r} references unknown "
                    f"hole {path.hole_id!r}"
                )
        for zone in self.restricted_zones:
            _require_within_bounds(
                self.bounds,
                zone.polygon.vertices,
                f"restricted zone {zone.feature_id!r}",
            )

        for first_index in range(len(self.surfaces)):
            for second_index in range(
                first_index + 1, len(self.surfaces)
            ):
                first = self.surfaces[first_index]
                second = self.surfaces[second_index]
                if rings_interiors_overlap(first.polygon, second.polygon):
                    raise CourseWorldModelError(
                        "primary surface interiors overlap: "
                        f"{first.feature_id!r} and {second.feature_id!r}; "
                        "shared edges are legal, shared interior area is "
                        "not"
                    )
        for first_index in range(len(self.holes)):
            for second_index in range(first_index + 1, len(self.holes)):
                first_hole = self.holes[first_index]
                second_hole = self.holes[second_index]
                if rings_interiors_overlap(
                    first_hole.boundary, second_hole.boundary
                ):
                    raise CourseWorldModelError(
                        "hole boundary interiors overlap: "
                        f"{first_hole.hole_id!r} and "
                        f"{second_hole.hole_id!r}"
                    )

        expected_digest = _compute_content_digest(self._identity_payload())
        if type(
            self.content_digest
        ) is not str or not _CONTENT_DIGEST_PATTERN.fullmatch(
            self.content_digest
        ):
            raise CourseWorldModelError(
                "content_digest must match 'sha256:<64 lowercase hex>'"
            )
        if self.content_digest != expected_digest:
            raise CourseWorldModelError(
                f"content_digest {self.content_digest!r} does not match "
                f"the model content {expected_digest!r}; the model bytes "
                "no longer match their declared identity"
            )

    def _identity_payload(self) -> dict[str, Any]:
        return _identity_payload_from_parts(
            course_model_id=self.course_model_id,
            model_version=self.model_version,
            supersedes_version=self.supersedes_version,
            effective_from=self.effective_from,
            site_id=self.site_id,
            deployment_id=self.deployment_id,
            frame=self.frame,
            bounds=self.bounds,
            elevation=self.elevation,
            course_boundary=self.course_boundary,
            holes=self.holes,
            surfaces=self.surfaces,
            cart_paths=self.cart_paths,
            restricted_zones=self.restricted_zones,
            scan_sources=self.scan_sources,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["display_name"] = self.display_name
        payload["content_digest"] = self.content_digest
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CourseWorldModel":
        if not isinstance(data, Mapping):
            raise CourseWorldModelError("model payload must be a mapping")
        actual = set(data)
        if actual != _MODEL_KEYS:
            missing = sorted(_MODEL_KEYS - actual)
            extra = sorted(actual - _MODEL_KEYS)
            raise CourseWorldModelError(
                f"model payload keys must be exactly {sorted(_MODEL_KEYS)}; "
                f"missing={missing}, extra={extra}"
            )
        if data["schema"] != COURSE_WORLD_MODEL_SCHEMA:
            raise CourseWorldModelError(
                f"unsupported course model schema {data['schema']!r}; "
                f"expected {COURSE_WORLD_MODEL_SCHEMA!r}"
            )
        for name in ("holes", "surfaces", "cart_paths", "restricted_zones",
                     "scan_sources"):
            if not isinstance(data[name], list):
                raise CourseWorldModelError(f"{name} must be a list")
        return cls(
            course_model_id=data["course_model_id"],
            model_version=data["model_version"],
            supersedes_version=data["supersedes_version"],
            effective_from=data["effective_from"],
            site_id=data["site_id"],
            deployment_id=data["deployment_id"],
            display_name=data["display_name"],
            frame=CourseCoordinateFrame.from_dict(data["frame"]),
            bounds=ModelBounds.from_dict(data["bounds"]),
            elevation=ElevationGrid.from_dict(data["elevation"]),
            course_boundary=PolygonRing.from_dict(data["course_boundary"]),
            holes=tuple(
                HoleDefinition.from_dict(item) for item in data["holes"]
            ),
            surfaces=tuple(
                SurfaceFeature.from_dict(item) for item in data["surfaces"]
            ),
            cart_paths=tuple(
                CartPath.from_dict(item) for item in data["cart_paths"]
            ),
            restricted_zones=tuple(
                RestrictedZone.from_dict(item)
                for item in data["restricted_zones"]
            ),
            scan_sources=tuple(
                ScanSourceReference.from_dict(item)
                for item in data["scan_sources"]
            ),
            content_digest=data["content_digest"],
        )


def build_course_world_model(
    *,
    course_model_id: str,
    model_version: str,
    supersedes_version: str | None,
    effective_from: str,
    site_id: str,
    deployment_id: str,
    display_name: str,
    frame: CourseCoordinateFrame,
    elevation: ElevationGrid,
    course_boundary: PolygonRing,
    holes: tuple[HoleDefinition, ...],
    surfaces: tuple[SurfaceFeature, ...],
    cart_paths: tuple[CartPath, ...],
    restricted_zones: tuple[RestrictedZone, ...],
    scan_sources: tuple[ScanSourceReference, ...],
) -> CourseWorldModel:
    """Build a model with derived bounds and a computed content digest."""
    if not isinstance(elevation, ElevationGrid):
        raise CourseWorldModelError("elevation must be an ElevationGrid")
    if not isinstance(frame, CourseCoordinateFrame):
        raise CourseWorldModelError("frame must be a CourseCoordinateFrame")
    for name, collection in (
        ("holes", holes),
        ("surfaces", surfaces),
        ("cart_paths", cart_paths),
        ("restricted_zones", restricted_zones),
        ("scan_sources", scan_sources),
    ):
        if not isinstance(collection, tuple):
            raise CourseWorldModelError(f"{name} must be a tuple")
    bounds = ModelBounds(
        min_x=elevation.min_x,
        min_y=elevation.min_y,
        max_x=elevation.max_x,
        max_y=elevation.max_y,
    )
    digest = _compute_content_digest(
        _identity_payload_from_parts(
            course_model_id=course_model_id,
            model_version=model_version,
            supersedes_version=supersedes_version,
            effective_from=effective_from,
            site_id=site_id,
            deployment_id=deployment_id,
            frame=frame,
            bounds=bounds,
            elevation=elevation,
            course_boundary=course_boundary,
            holes=holes,
            surfaces=surfaces,
            cart_paths=cart_paths,
            restricted_zones=restricted_zones,
            scan_sources=scan_sources,
        )
    )
    return CourseWorldModel(
        course_model_id=course_model_id,
        model_version=model_version,
        supersedes_version=supersedes_version,
        effective_from=effective_from,
        site_id=site_id,
        deployment_id=deployment_id,
        display_name=display_name,
        frame=frame,
        bounds=bounds,
        elevation=elevation,
        course_boundary=course_boundary,
        holes=holes,
        surfaces=surfaces,
        cart_paths=cart_paths,
        restricted_zones=restricted_zones,
        scan_sources=scan_sources,
        content_digest=digest,
    )


def dumps_model(model: CourseWorldModel) -> str:
    """Canonical UTF-8 JSON with one trailing newline."""
    if not isinstance(model, CourseWorldModel):
        raise CourseWorldModelError("model must be a CourseWorldModel")
    return canonical_projection_json(model.to_dict()) + "\n"


def verify_model_payload(payload: Mapping[str, Any]) -> None:
    """Fail closed on schema, shape, or content-digest violations.

    This is *content* verification only: a matching digest proves the
    payload still matches its own declared content.  It is not a
    signature and proves nothing about who produced the model, whether
    a survey was accurate, or whether issuance was trusted; a consumer
    that needs provenance must obtain the model from a trusted
    composition root.
    """
    if not isinstance(payload, Mapping):
        raise CourseWorldModelError("payload must be a mapping")
    if payload.get("schema") != COURSE_WORLD_MODEL_SCHEMA:
        raise CourseWorldModelError(
            f"unsupported course model schema {payload.get('schema')!r}; "
            f"expected {COURSE_WORLD_MODEL_SCHEMA!r}"
        )
    actual = set(payload)
    if actual != _MODEL_KEYS:
        missing = sorted(_MODEL_KEYS - actual)
        extra = sorted(actual - _MODEL_KEYS)
        raise CourseWorldModelError(
            f"model payload keys must be exactly {sorted(_MODEL_KEYS)}; "
            f"missing={missing}, extra={extra}"
        )
    declared = payload["content_digest"]
    if type(declared) is not str or not _CONTENT_DIGEST_PATTERN.fullmatch(
        declared
    ):
        raise CourseWorldModelError(
            "payload carries no well-formed content_digest to verify"
        )
    identity = {
        key: value
        for key, value in payload.items()
        if key not in ("content_digest", "display_name")
    }
    expected = _compute_content_digest(identity)
    if declared != expected:
        raise CourseWorldModelError(
            f"content_digest {declared!r} does not match the payload "
            f"digest {expected!r}; the payload no longer matches its own "
            "content digest"
        )


def validate_revision(
    *, current: CourseWorldModel, candidate: CourseWorldModel
) -> None:
    """Fail closed unless the candidate is a well-formed next revision."""
    for name, value in (("current", current), ("candidate", candidate)):
        if not isinstance(value, CourseWorldModel):
            raise CourseWorldModelError(
                f"{name} must be a CourseWorldModel"
            )
    if candidate.course_model_id != current.course_model_id:
        raise CourseWorldModelError(
            "a revision must keep the course_model_id: "
            f"{current.course_model_id!r} vs "
            f"{candidate.course_model_id!r}"
        )
    if (
        candidate.site_id != current.site_id
        or candidate.deployment_id != current.deployment_id
    ):
        raise CourseWorldModelError(
            "a revision must keep the commissioned site and deployment "
            "identity"
        )
    if candidate.frame != current.frame:
        raise CourseWorldModelError(
            "a revision must keep the coordinate frame identity; "
            "re-referencing a course model is not supported in V0 and "
            "requires its own explicit revision semantics"
        )
    if candidate.model_version == current.model_version:
        raise CourseWorldModelError(
            "a revision must declare a new model_version"
        )
    if candidate.supersedes_version != current.model_version:
        raise CourseWorldModelError(
            f"candidate supersedes {candidate.supersedes_version!r} but "
            f"the current version is {current.model_version!r}"
        )
    current_from = _parse_effective_from(current.effective_from)
    candidate_from = _parse_effective_from(candidate.effective_from)
    if candidate_from <= current_from:
        raise CourseWorldModelError(
            "a revision's effective_from must strictly increase; "
            f"{candidate.effective_from!r} does not follow "
            f"{current.effective_from!r}"
        )


def require_consistent_content(
    first: CourseWorldModel, second: CourseWorldModel
) -> None:
    """Reject two models that claim one version with different content."""
    for name, value in (("first", first), ("second", second)):
        if not isinstance(value, CourseWorldModel):
            raise CourseWorldModelError(
                f"{name} must be a CourseWorldModel"
            )
    if (
        first.course_model_id == second.course_model_id
        and first.model_version == second.model_version
        and first.content_digest != second.content_digest
    ):
        raise CourseWorldModelError(
            f"two models claim {first.course_model_id!r} "
            f"{first.model_version!r} with different content digests "
            f"({first.content_digest!r} vs {second.content_digest!r}); "
            "a version identity is immutable"
        )


def validate_model_against_site(
    model: CourseWorldModel, site: CommissionedSite
) -> None:
    """Fail closed unless the model binds to this validated site.

    Checks the commissioned identity, the coordinate-reference
    identity, the commissioned facility origin, and every referenced
    commissioned zone.  The commissioned manifest stays authoritative
    for all of them; the model only references.
    """
    if not isinstance(model, CourseWorldModel):
        raise CourseWorldModelError("model must be a CourseWorldModel")
    if not isinstance(site, CommissionedSite):
        raise CourseWorldModelError("site must be a CommissionedSite")
    if model.site_id != site.site_id:
        raise CourseWorldModelError(
            f"model site_id {model.site_id!r} does not match the "
            f"commissioned site {site.site_id!r}"
        )
    if model.deployment_id != site.deployment_id:
        raise CourseWorldModelError(
            f"model deployment_id {model.deployment_id!r} does not match "
            f"the commissioned deployment {site.deployment_id!r}"
        )
    commissioned = site.spatial_reference.coordinate_system
    frame = model.frame
    mismatches: list[str] = []
    if frame.crs_kind != commissioned.kind.value:
        mismatches.append(
            f"kind {frame.crs_kind!r} vs {commissioned.kind.value!r}"
        )
    if frame.crs_identifier != commissioned.identifier:
        mismatches.append(
            f"identifier {frame.crs_identifier!r} vs "
            f"{commissioned.identifier!r}"
        )
    if frame.crs_horizontal_unit != commissioned.horizontal_unit:
        mismatches.append(
            f"horizontal unit {frame.crs_horizontal_unit!r} vs "
            f"{commissioned.horizontal_unit!r}"
        )
    if frame.crs_vertical_unit != commissioned.vertical_unit:
        mismatches.append(
            f"vertical unit {frame.crs_vertical_unit!r} vs "
            f"{commissioned.vertical_unit!r}"
        )
    if frame.crs_axes != tuple(commissioned.axes):
        mismatches.append(
            f"axes {frame.crs_axes!r} vs {tuple(commissioned.axes)!r}"
        )
    if mismatches:
        raise CourseWorldModelError(
            "model coordinate reference does not match the commissioned "
            "spatial reference: " + "; ".join(mismatches)
        )
    origin = site.spatial_reference.facility_origin
    if (
        frame.origin_crs_x != origin.x
        or frame.origin_crs_y != origin.y
        or frame.origin_crs_z != origin.z
    ):
        raise CourseWorldModelError(
            "model frame origin "
            f"({frame.origin_crs_x!r}, {frame.origin_crs_y!r}, "
            f"{frame.origin_crs_z!r}) does not match the commissioned "
            f"facility origin ({origin.x!r}, {origin.y!r}, {origin.z!r})"
        )
    commissioned_zones = {
        zone.zone_id for zone in site.spatial_reference.zone_definitions
    }
    for zone in model.restricted_zones:
        if (
            zone.commissioned_zone_id is not None
            and zone.commissioned_zone_id not in commissioned_zones
        ):
            raise CourseWorldModelError(
                f"restricted zone {zone.feature_id!r} references "
                f"commissioned zone {zone.commissioned_zone_id!r}, which "
                "this site does not declare"
            )


__all__ = [
    "COURSE_WORLD_MODEL_SCHEMA",
    "CourseWorldModel",
    "ModelBounds",
    "build_course_world_model",
    "dumps_model",
    "require_consistent_content",
    "validate_model_against_site",
    "validate_revision",
    "verify_model_payload",
]
