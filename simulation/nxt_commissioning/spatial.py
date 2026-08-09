"""Immutable spatial contracts for commissioned facility facts.

This module describes declared geometry and its coordinate reference.  It does
not contain live poses, inferred positions, or digital-twin state.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from .provenance import Provenance


_EPSG_IDENTIFIER = re.compile(r"^EPSG:[1-9][0-9]*$")


def _require_exact_keys(
    data: Mapping[str, object], expected: frozenset[str], contract: str
) -> None:
    if not isinstance(data, Mapping):
        raise TypeError(f"{contract} must be decoded from a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError(f"{contract} keys must be strings")
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            f"{contract} keys must be exactly {sorted(expected)}; "
            f"missing={missing}, extra={extra}"
        )


def _require_non_blank(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")


def _require_provenance(value: object, field_name: str = "provenance") -> None:
    if not isinstance(value, Provenance):
        raise TypeError(f"{field_name} must be Provenance")


def _enum_from_value(enum_type: type[Enum], value: object, field_name: str) -> Enum:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = sorted(member.value for member in enum_type)
        raise ValueError(f"{field_name} must be one of {choices}; got {value!r}") from exc


class CoordinateSystemKind(str, Enum):
    """Kinds of coordinate systems accepted by commissioning."""

    LOCAL_CARTESIAN = "local_cartesian"
    EPSG = "epsg"


@dataclass(frozen=True, slots=True)
class CoordinateReferenceSystem:
    """The declared coordinate interpretation for all facility geometry."""

    kind: CoordinateSystemKind
    identifier: str
    horizontal_unit: str
    vertical_unit: str
    axes: tuple[str, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CoordinateSystemKind):
            raise TypeError("kind must be CoordinateSystemKind")
        _require_non_blank(self.identifier, "identifier")
        _require_non_blank(self.horizontal_unit, "horizontal_unit")
        _require_non_blank(self.vertical_unit, "vertical_unit")
        if not isinstance(self.axes, tuple):
            raise TypeError("axes must be a tuple")
        if len(self.axes) < 2:
            raise ValueError("axes must declare at least two axes")
        for index, axis in enumerate(self.axes):
            _require_non_blank(axis, f"axes[{index}]")
        normalized_axes = tuple(axis.casefold() for axis in self.axes)
        if len(set(normalized_axes)) != len(normalized_axes):
            raise ValueError("axes must not contain duplicate names")
        if (
            self.kind is CoordinateSystemKind.EPSG
            and _EPSG_IDENTIFIER.fullmatch(self.identifier) is None
        ):
            raise ValueError(
                "EPSG coordinate systems require identifier in 'EPSG:<code>' format"
            )
        _require_provenance(self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "identifier": self.identifier,
            "horizontal_unit": self.horizontal_unit,
            "vertical_unit": self.vertical_unit,
            "axes": list(self.axes),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CoordinateReferenceSystem":
        expected = frozenset(
            {
                "kind",
                "identifier",
                "horizontal_unit",
                "vertical_unit",
                "axes",
                "provenance",
            }
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_axes = data["axes"]
        if not isinstance(raw_axes, list):
            raise TypeError("axes must be a list")
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            kind=_enum_from_value(
                CoordinateSystemKind, data["kind"], "kind"
            ),  # type: ignore[arg-type]
            identifier=data["identifier"],  # type: ignore[arg-type]
            horizontal_unit=data["horizontal_unit"],  # type: ignore[arg-type]
            vertical_unit=data["vertical_unit"],  # type: ignore[arg-type]
            axes=tuple(raw_axes),  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


@dataclass(frozen=True, slots=True)
class SpatialPoint:
    """One immutable 3D point expressed in the enclosing spatial reference."""

    x: int | float
    y: int | float
    z: int | float
    provenance: Provenance

    def __post_init__(self) -> None:
        for field_name, value in (("x", self.x), ("y", self.y), ("z", self.z)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{field_name} must be an int or float, not bool")
            try:
                finite = math.isfinite(float(value))
            except OverflowError as exc:
                raise ValueError(f"{field_name} must be finite") from exc
            if not finite:
                raise ValueError(f"{field_name} must be finite")
        _require_provenance(self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SpatialPoint":
        expected = frozenset({"x", "y", "z", "provenance"})
        _require_exact_keys(data, expected, cls.__name__)
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            x=data["x"],  # type: ignore[arg-type]
            y=data["y"],  # type: ignore[arg-type]
            z=data["z"],  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


class GeometryType(str, Enum):
    POINT = "point"
    POLYLINE = "polyline"
    POLYGON = "polygon"


@dataclass(frozen=True, slots=True)
class GeometryReference:
    """Named inline geometry with optional evidence/source URI."""

    geometry_id: str
    geometry_type: GeometryType
    points: tuple[SpatialPoint, ...]
    source_uri: str | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _require_non_blank(self.geometry_id, "geometry_id")
        if not isinstance(self.geometry_type, GeometryType):
            raise TypeError("geometry_type must be GeometryType")
        if not isinstance(self.points, tuple):
            raise TypeError("points must be a tuple")
        for index, point in enumerate(self.points):
            if not isinstance(point, SpatialPoint):
                raise TypeError(f"points[{index}] must be SpatialPoint")
        if self.geometry_type is GeometryType.POINT and len(self.points) != 1:
            raise ValueError("point geometry must contain exactly one point")
        if self.geometry_type is GeometryType.POLYLINE and len(self.points) < 2:
            raise ValueError("polyline geometry must contain at least two points")
        if self.geometry_type is GeometryType.POLYGON:
            distinct = {(point.x, point.y, point.z) for point in self.points}
            if len(self.points) < 3 or len(distinct) < 3:
                raise ValueError("polygon geometry must contain at least three distinct points")
        if self.source_uri is not None:
            _require_non_blank(self.source_uri, "source_uri")
            if any(character.isspace() for character in self.source_uri):
                raise ValueError("source_uri must not contain whitespace")
            parsed = urlparse(self.source_uri)
            if not parsed.scheme or not (parsed.netloc or parsed.path):
                raise ValueError("source_uri must be an absolute URI with a scheme")
        _require_provenance(self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "geometry_id": self.geometry_id,
            "geometry_type": self.geometry_type.value,
            "points": [point.to_dict() for point in self.points],
            "source_uri": self.source_uri,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "GeometryReference":
        expected = frozenset(
            {"geometry_id", "geometry_type", "points", "source_uri", "provenance"}
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_points = data["points"]
        if not isinstance(raw_points, list):
            raise TypeError("points must be a list")
        points: list[SpatialPoint] = []
        for index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, Mapping):
                raise TypeError(f"points[{index}] must be a mapping")
            points.append(SpatialPoint.from_dict(raw_point))
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            geometry_id=data["geometry_id"],  # type: ignore[arg-type]
            geometry_type=_enum_from_value(
                GeometryType, data["geometry_type"], "geometry_type"
            ),  # type: ignore[arg-type]
            points=tuple(points),
            source_uri=data["source_uri"],  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


@dataclass(frozen=True, slots=True)
class ZoneDefinition:
    """A commissioned zone linked to one declared geometry identifier."""

    zone_id: str
    name: str
    zone_type: str
    geometry_reference: str
    safety_classification: str
    provenance: Provenance

    def __post_init__(self) -> None:
        _require_non_blank(self.zone_id, "zone_id")
        _require_non_blank(self.name, "name")
        _require_non_blank(self.zone_type, "zone_type")
        _require_non_blank(self.geometry_reference, "geometry_reference")
        _require_non_blank(self.safety_classification, "safety_classification")
        _require_provenance(self.provenance)

    def to_dict(self) -> dict[str, object]:
        return {
            "zone_id": self.zone_id,
            "name": self.name,
            "zone_type": self.zone_type,
            "geometry_reference": self.geometry_reference,
            "safety_classification": self.safety_classification,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ZoneDefinition":
        expected = frozenset(
            {
                "zone_id",
                "name",
                "zone_type",
                "geometry_reference",
                "safety_classification",
                "provenance",
            }
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_provenance = data["provenance"]
        if not isinstance(raw_provenance, Mapping):
            raise TypeError("provenance must be a mapping")
        return cls(
            zone_id=data["zone_id"],  # type: ignore[arg-type]
            name=data["name"],  # type: ignore[arg-type]
            zone_type=data["zone_type"],  # type: ignore[arg-type]
            geometry_reference=data["geometry_reference"],  # type: ignore[arg-type]
            safety_classification=data["safety_classification"],  # type: ignore[arg-type]
            provenance=Provenance.from_dict(raw_provenance),
        )


@dataclass(frozen=True, slots=True)
class SpatialReference:
    """Complete commissioned spatial declaration for a facility."""

    coordinate_system: CoordinateReferenceSystem
    facility_origin: SpatialPoint
    geometry_references: tuple[GeometryReference, ...]
    zone_definitions: tuple[ZoneDefinition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.coordinate_system, CoordinateReferenceSystem):
            raise TypeError("coordinate_system must be CoordinateReferenceSystem")
        if not isinstance(self.facility_origin, SpatialPoint):
            raise TypeError("facility_origin must be SpatialPoint")
        if not isinstance(self.geometry_references, tuple):
            raise TypeError("geometry_references must be a tuple")
        for index, geometry in enumerate(self.geometry_references):
            if not isinstance(geometry, GeometryReference):
                raise TypeError(
                    f"geometry_references[{index}] must be GeometryReference"
                )
        object.__setattr__(
            self,
            "geometry_references",
            tuple(sorted(self.geometry_references, key=lambda item: item.geometry_id)),
        )
        if not isinstance(self.zone_definitions, tuple):
            raise TypeError("zone_definitions must be a tuple")
        for index, zone in enumerate(self.zone_definitions):
            if not isinstance(zone, ZoneDefinition):
                raise TypeError(f"zone_definitions[{index}] must be ZoneDefinition")
        object.__setattr__(
            self,
            "zone_definitions",
            tuple(sorted(self.zone_definitions, key=lambda item: item.zone_id)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "coordinate_system": self.coordinate_system.to_dict(),
            "facility_origin": self.facility_origin.to_dict(),
            "geometry_references": [
                geometry.to_dict() for geometry in self.geometry_references
            ],
            "zone_definitions": [zone.to_dict() for zone in self.zone_definitions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SpatialReference":
        expected = frozenset(
            {
                "coordinate_system",
                "facility_origin",
                "geometry_references",
                "zone_definitions",
            }
        )
        _require_exact_keys(data, expected, cls.__name__)
        raw_coordinate_system = data["coordinate_system"]
        if not isinstance(raw_coordinate_system, Mapping):
            raise TypeError("coordinate_system must be a mapping")
        raw_origin = data["facility_origin"]
        if not isinstance(raw_origin, Mapping):
            raise TypeError("facility_origin must be a mapping")
        raw_geometries = data["geometry_references"]
        if not isinstance(raw_geometries, list):
            raise TypeError("geometry_references must be a list")
        geometries: list[GeometryReference] = []
        for index, raw_geometry in enumerate(raw_geometries):
            if not isinstance(raw_geometry, Mapping):
                raise TypeError(f"geometry_references[{index}] must be a mapping")
            geometries.append(GeometryReference.from_dict(raw_geometry))
        raw_zones = data["zone_definitions"]
        if not isinstance(raw_zones, list):
            raise TypeError("zone_definitions must be a list")
        zones: list[ZoneDefinition] = []
        for index, raw_zone in enumerate(raw_zones):
            if not isinstance(raw_zone, Mapping):
                raise TypeError(f"zone_definitions[{index}] must be a mapping")
            zones.append(ZoneDefinition.from_dict(raw_zone))
        return cls(
            coordinate_system=CoordinateReferenceSystem.from_dict(
                raw_coordinate_system
            ),
            facility_origin=SpatialPoint.from_dict(raw_origin),
            geometry_references=tuple(geometries),
            zone_definitions=tuple(zones),
        )


__all__ = [
    "CoordinateReferenceSystem",
    "CoordinateSystemKind",
    "GeometryReference",
    "GeometryType",
    "SpatialPoint",
    "SpatialReference",
    "ZoneDefinition",
]
