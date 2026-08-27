"""Semantic course features: holes, playing surfaces, overlays, sources.

The closed vocabularies below are the Course World Model's own facts.
The commissioned zone vocabulary stays free-form and commissioning-
owned; a restricted zone may *reference* a commissioned zone identifier
but never redefines the zone itself.

Primary playing surfaces have mutually exclusive interiors (shared
edges are legal).  Cart paths and restricted zones are overlays: they
may coexist with any primary surface underneath them, and that overlay
relationship is reported by queries rather than resolved away.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from nxt_commissioning import Provenance

from .errors import CourseWorldModelError
from .geometry import PolygonRing, Polyline, require_finite_number

_FEATURE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SOURCE_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
# An absolute URI: a scheme, a colon, and a non-empty remainder with no
# whitespace anywhere.  Deliberately validated with a local pattern so
# this package needs no URL-handling import at all.
_ABSOLUTE_URI_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$")


class SurfaceType(StrEnum):
    """The closed primary playing-surface vocabulary."""

    TEE = "tee"
    FAIRWAY = "fairway"
    ROUGH = "rough"
    GREEN = "green"
    BUNKER = "bunker"
    WATER = "water"


# Deterministic tie-break for a point on a shared surface boundary:
# the most specific surface wins, then the lexicographically smallest
# feature identifier.
SURFACE_TIE_BREAK_ORDER = (
    SurfaceType.GREEN,
    SurfaceType.TEE,
    SurfaceType.BUNKER,
    SurfaceType.WATER,
    SurfaceType.FAIRWAY,
    SurfaceType.ROUGH,
)

HAZARD_SURFACE_TYPES = (SurfaceType.BUNKER, SurfaceType.WATER)


class RestrictionCategory(StrEnum):
    """The closed restricted-area vocabulary."""

    NO_GO = "no_go"
    MAINTENANCE_ONLY = "maintenance_only"


class ScanSourceType(StrEnum):
    """How a processed spatial source product was produced."""

    PROCESSED_SCAN_PRODUCT = "processed_scan_product"
    SYNTHETIC_FIXTURE = "synthetic_fixture"


def require_feature_id(value: object, field_name: str) -> str:
    if type(value) is not str or not _FEATURE_ID_PATTERN.fullmatch(value):
        raise CourseWorldModelError(
            f"{field_name} must match {_FEATURE_ID_PATTERN.pattern!r}; "
            f"got {value!r}"
        )
    return value


def _optional_feature_id(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise CourseWorldModelError(
            f"{field_name} must be a non-blank trimmed string or None"
        )
    return value


def _enum_from_value(enum_type: type, value: object, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = sorted(member.value for member in enum_type)
        raise CourseWorldModelError(
            f"{field_name} must be one of {choices}; got {value!r}"
        ) from exc


def _require_ring(value: object, field_name: str) -> PolygonRing:
    if not isinstance(value, PolygonRing):
        raise CourseWorldModelError(f"{field_name} must be a PolygonRing")
    return value


@dataclass(frozen=True, slots=True)
class HoleDefinition:
    """One hole identity and its boundary in the course-local frame."""

    hole_id: str
    hole_number: int
    boundary: PolygonRing

    def __post_init__(self) -> None:
        require_feature_id(self.hole_id, "hole_id")
        if isinstance(self.hole_number, bool) or not isinstance(
            self.hole_number, int
        ):
            raise CourseWorldModelError("hole_number must be an integer")
        if self.hole_number < 1:
            raise CourseWorldModelError(
                f"hole_number must be positive; got {self.hole_number}"
            )
        _require_ring(self.boundary, "boundary")

    def to_dict(self) -> dict[str, object]:
        return {
            "hole_id": self.hole_id,
            "hole_number": self.hole_number,
            "boundary": self.boundary.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "HoleDefinition":
        _require_exact_keys(
            data, {"hole_id", "hole_number", "boundary"}, "hole"
        )
        return cls(
            hole_id=data["hole_id"],  # type: ignore[arg-type]
            hole_number=data["hole_number"],  # type: ignore[arg-type]
            boundary=PolygonRing.from_dict(data["boundary"]),
        )


@dataclass(frozen=True, slots=True)
class SurfaceFeature:
    """One primary playing-surface polygon."""

    feature_id: str
    surface_type: SurfaceType
    polygon: PolygonRing
    hole_id: str | None

    def __post_init__(self) -> None:
        require_feature_id(self.feature_id, "feature_id")
        object.__setattr__(
            self,
            "surface_type",
            _enum_from_value(SurfaceType, self.surface_type, "surface_type"),
        )
        _require_ring(self.polygon, "polygon")
        _optional_feature_id(self.hole_id, "hole_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "surface_type": self.surface_type.value,
            "polygon": self.polygon.to_dict(),
            "hole_id": self.hole_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SurfaceFeature":
        _require_exact_keys(
            data,
            {"feature_id", "surface_type", "polygon", "hole_id"},
            "surface",
        )
        return cls(
            feature_id=data["feature_id"],  # type: ignore[arg-type]
            surface_type=data["surface_type"],  # type: ignore[arg-type]
            polygon=PolygonRing.from_dict(data["polygon"]),
            hole_id=data["hole_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class CartPath:
    """One cart-path overlay: a centerline with a finite width."""

    feature_id: str
    centerline: Polyline
    width_m: float
    hole_id: str | None

    def __post_init__(self) -> None:
        require_feature_id(self.feature_id, "feature_id")
        if not isinstance(self.centerline, Polyline):
            raise CourseWorldModelError("centerline must be a Polyline")
        width = require_finite_number(self.width_m, "width_m")
        if width <= 0.0:
            raise CourseWorldModelError(
                f"width_m must be positive; got {self.width_m!r}"
            )
        _optional_feature_id(self.hole_id, "hole_id")

    def covers(self, x: float, y: float) -> bool:
        return self.centerline.distance_to(x, y) <= self.width_m / 2.0

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "centerline": self.centerline.to_dict(),
            "width_m": self.width_m,
            "hole_id": self.hole_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CartPath":
        _require_exact_keys(
            data,
            {"feature_id", "centerline", "width_m", "hole_id"},
            "cart path",
        )
        return cls(
            feature_id=data["feature_id"],  # type: ignore[arg-type]
            centerline=Polyline.from_dict(data["centerline"]),
            width_m=data["width_m"],  # type: ignore[arg-type]
            hole_id=data["hole_id"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class RestrictedZone:
    """One restricted-area overlay.

    ``commissioned_zone_id`` optionally references a commissioned zone
    identifier; the referenced zone stays commissioning-owned and is
    verified against the validated site by
    ``validate_model_against_site``.  This overlay is spatial
    information only: it commands nothing.
    """

    feature_id: str
    category: RestrictionCategory
    polygon: PolygonRing
    commissioned_zone_id: str | None

    def __post_init__(self) -> None:
        require_feature_id(self.feature_id, "feature_id")
        object.__setattr__(
            self,
            "category",
            _enum_from_value(
                RestrictionCategory, self.category, "category"
            ),
        )
        _require_ring(self.polygon, "polygon")
        _optional_feature_id(
            self.commissioned_zone_id, "commissioned_zone_id"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "category": self.category.value,
            "polygon": self.polygon.to_dict(),
            "commissioned_zone_id": self.commissioned_zone_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "RestrictedZone":
        _require_exact_keys(
            data,
            {"feature_id", "category", "polygon", "commissioned_zone_id"},
            "restricted zone",
        )
        return cls(
            feature_id=data["feature_id"],  # type: ignore[arg-type]
            category=data["category"],  # type: ignore[arg-type]
            polygon=PolygonRing.from_dict(data["polygon"]),
            commissioned_zone_id=data[  # type: ignore[arg-type]
                "commissioned_zone_id"
            ],
        )


@dataclass(frozen=True, slots=True)
class ScanSourceReference:
    """One processed scan-product reference: identity, never raw bytes.

    Raw survey artifacts stay outside the model and the repository; the
    reference records a stable URI, the processing identity, a content
    digest of the processed product, and commissioned-style provenance.
    A digest here proves content consistency of the referenced product;
    it proves nothing about surveying accuracy or authenticity.
    """

    source_id: str
    source_type: ScanSourceType
    capture_id: str
    processing_pipeline_id: str
    source_uri: str
    source_digest: str
    provenance: Provenance

    def __post_init__(self) -> None:
        require_feature_id(self.source_id, "source_id")
        object.__setattr__(
            self,
            "source_type",
            _enum_from_value(
                ScanSourceType, self.source_type, "source_type"
            ),
        )
        for field_name, value in (
            ("capture_id", self.capture_id),
            ("processing_pipeline_id", self.processing_pipeline_id),
        ):
            if (
                type(value) is not str
                or not value.strip()
                or value != value.strip()
            ):
                raise CourseWorldModelError(
                    f"{field_name} must be a non-blank trimmed string"
                )
        if type(self.source_uri) is not str or not self.source_uri.strip():
            raise CourseWorldModelError("source_uri must be a non-blank string")
        if any(character.isspace() for character in self.source_uri):
            raise CourseWorldModelError(
                "source_uri must not contain whitespace"
            )
        if not _ABSOLUTE_URI_PATTERN.fullmatch(self.source_uri):
            raise CourseWorldModelError(
                "source_uri must be an absolute URI with a scheme"
            )
        if type(
            self.source_digest
        ) is not str or not _SOURCE_DIGEST_PATTERN.fullmatch(
            self.source_digest
        ):
            raise CourseWorldModelError(
                "source_digest must match 'sha256:<64 lowercase hex>'"
            )
        if not isinstance(self.provenance, Provenance):
            raise CourseWorldModelError(
                "provenance must be a commissioned Provenance record"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "capture_id": self.capture_id,
            "processing_pipeline_id": self.processing_pipeline_id,
            "source_uri": self.source_uri,
            "source_digest": self.source_digest,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ScanSourceReference":
        _require_exact_keys(
            data,
            {
                "source_id",
                "source_type",
                "capture_id",
                "processing_pipeline_id",
                "source_uri",
                "source_digest",
                "provenance",
            },
            "scan source",
        )
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise CourseWorldModelError("provenance must be a mapping")
        return cls(
            source_id=data["source_id"],  # type: ignore[arg-type]
            source_type=data["source_type"],  # type: ignore[arg-type]
            capture_id=data["capture_id"],  # type: ignore[arg-type]
            processing_pipeline_id=data[  # type: ignore[arg-type]
                "processing_pipeline_id"
            ],
            source_uri=data["source_uri"],  # type: ignore[arg-type]
            source_digest=data["source_digest"],  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


def _require_exact_keys(
    data: object, expected: set[str], contract: str
) -> None:
    if not isinstance(data, Mapping):
        raise CourseWorldModelError(f"{contract} payload must be a mapping")
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise CourseWorldModelError(
            f"{contract} payload keys must be exactly {sorted(expected)}; "
            f"missing={missing}, extra={extra}"
        )


__all__ = [
    "CartPath",
    "HAZARD_SURFACE_TYPES",
    "HoleDefinition",
    "RestrictedZone",
    "RestrictionCategory",
    "ScanSourceReference",
    "ScanSourceType",
    "SURFACE_TIE_BREAK_ORDER",
    "SurfaceFeature",
    "SurfaceType",
]
