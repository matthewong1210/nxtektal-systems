"""Aggregate validation for commissioned facility manifests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import CommissionedSite
from .equipment import (
    EQUIPMENT_CAPABILITY_COMPATIBILITY,
    ROBOT_CAPABILITY_COMPATIBILITY,
    AssetType,
    CapabilityDeclaration,
    CapabilityKind,
    EquipmentAsset,
    RobotAsset,
)
from .provenance import MeasuredValue, Provenance
from .sensors import CalibrationInfo, CalibrationStatus, SensorBinding, SensorType
from .spatial import (
    CoordinateReferenceSystem,
    CoordinateSystemKind,
    GeometryReference,
    GeometryType,
    SpatialPoint,
    SpatialReference,
    ZoneDefinition,
)

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_LOCAL_CRS = re.compile(r"^LOCAL:[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EPSG_CRS = re.compile(r"^EPSG:[1-9][0-9]*$")
_CHANNEL = re.compile(r"^[a-z][a-z0-9_]*(?:\.[A-Za-z0-9_:-]+)+$")
_SUPPORTED_HORIZONTAL_UNITS = frozenset({"m", "ft", "degree"})
_SUPPORTED_VERTICAL_UNITS = frozenset({"m", "ft"})

_CORE_CAPABILITY_BY_ASSET: dict[AssetType, CapabilityKind | None] = {
    AssetType.WASHER: CapabilityKind.BALL_WASHING,
    AssetType.DISPENSER: CapabilityKind.BALL_DISPENSING,
    AssetType.CHARGING_STATION: CapabilityKind.ROBOT_CHARGING,
    AssetType.COLLECTION_STATION: CapabilityKind.STORAGE,
    AssetType.STORAGE_AREA: CapabilityKind.STORAGE,
    AssetType.OTHER: None,
}

_CAPABILITY_REQUIRED_PARAMETER: dict[
    CapabilityKind, tuple[frozenset[str], frozenset[str]]
] = {
    CapabilityKind.BALL_WASHING: (
        frozenset(
            {
                "rated_throughput",
                "throughput_balls_per_minute",
                "washer_throughput_bpm",
            }
        ),
        frozenset({"balls/min", "balls/minute"}),
    ),
    CapabilityKind.ROBOT_CHARGING: (
        frozenset({"nominal_voltage"}),
        frozenset({"V"}),
    ),
    CapabilityKind.AUTONOMOUS_NAVIGATION: (
        frozenset({"max_speed"}),
        frozenset({"m/s"}),
    ),
}

_PAYLOAD_MEASUREMENT_NAMES = frozenset(
    {"max_payload", "payload_capacity", "payload_capacity_balls"}
)
_PAYLOAD_UNITS = frozenset({"balls", "kg"})
_ELECTRICAL_MEASUREMENT_UNITS: dict[str, frozenset[str]] = {
    "nominal_voltage": frozenset({"V"}),
    "max_voltage": frozenset({"V"}),
    "max_current": frozenset({"A"}),
    "charge_power": frozenset({"W", "kW"}),
}
_SAFETY_MEASUREMENT_UNITS: dict[str, frozenset[str]] = {
    "max_speed": frozenset({"m/s"}),
    "minimum_commanded_speed": frozenset({"m/s"}),
    "max_slope": frozenset({"deg"}),
    "max_ground_slope": frozenset({"deg"}),
    "min_clearance": frozenset({"m"}),
    "minimum_clearance": frozenset({"m"}),
    "emergency_stop_distance": frozenset({"m"}),
    "max_payload": frozenset({"balls", "kg"}),
    "payload_capacity": frozenset({"balls", "kg"}),
    "max_temperature": frozenset({"C"}),
    "min_temperature": frozenset({"C"}),
    "max_voltage": frozenset({"V"}),
}
_SIGNED_SAFETY_MEASUREMENTS = frozenset({"max_temperature", "min_temperature"})
_STRICTLY_POSITIVE_SAFETY_MEASUREMENTS = frozenset(
    {
        "max_speed",
        "min_clearance",
        "minimum_clearance",
        "emergency_stop_distance",
        "max_payload",
        "payload_capacity",
        "max_voltage",
    }
)
_CALIBRATION_REQUIRED_SENSOR_TYPES = frozenset(
    {
        SensorType.LOAD_CELL,
        SensorType.LIDAR,
        SensorType.CAMERA,
        SensorType.RFID_READER,
        SensorType.BATTERY_MONITOR,
        SensorType.ENCODER,
        SensorType.TEMPERATURE,
    }
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class CommissioningValidationError(ValueError):
    """Raised when an authoritative manifest violates one or more rules."""

    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        rendered = "\n".join(f"- {issue}" for issue in issues)
        super().__init__(f"commissioning manifest is invalid:\n{rendered}")


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _check_text(
    issues: list[ValidationIssue], path: str, value: object, *, optional: bool = False
) -> None:
    if optional and value is None:
        return
    if not _is_text(value):
        issues.append(ValidationIssue(path, "must be a non-blank string"))


def _check_identifier(
    issues: list[ValidationIssue], path: str, value: object
) -> None:
    if not _is_text(value) or not _IDENTIFIER.fullmatch(value):
        issues.append(
            ValidationIssue(
                path,
                "must be a non-blank identifier without whitespace or path separators",
            )
        )
    elif value in {".", ".."}:
        issues.append(ValidationIssue(path, "path traversal identifiers are forbidden"))


def _check_provenance(
    issues: list[ValidationIssue], path: str, provenance: object
) -> None:
    if not isinstance(provenance, Provenance):
        issues.append(ValidationIssue(path, "provenance is required"))
        return
    _check_text(issues, f"{path}.source_id", provenance.source_id)
    _check_text(issues, f"{path}.captured_at", provenance.captured_at)
    _check_text(issues, f"{path}.captured_by", provenance.captured_by)


def _check_measurement(
    issues: list[ValidationIssue], path: str, measurement: object, *, positive: bool
) -> None:
    if not isinstance(measurement, MeasuredValue):
        issues.append(ValidationIssue(path, "must be a MeasuredValue"))
        return
    _check_text(issues, f"{path}.name", measurement.name)
    _check_text(issues, f"{path}.unit", measurement.unit)
    value = measurement.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(ValidationIssue(f"{path}.value", "must be a number, not a boolean"))
    elif not math.isfinite(float(value)):
        issues.append(ValidationIssue(f"{path}.value", "must be finite"))
    elif positive and value <= 0:
        issues.append(ValidationIssue(f"{path}.value", "must be greater than zero"))
    _check_provenance(issues, f"{path}.provenance", measurement.provenance)


def _check_measurement_collection(
    issues: list[ValidationIssue], path: str, values: object, *, positive: bool
) -> None:
    if not isinstance(values, tuple):
        issues.append(ValidationIssue(path, "must be an immutable tuple"))
        return
    names: list[str] = []
    for index, value in enumerate(values):
        _check_measurement(issues, f"{path}[{index}]", value, positive=positive)
        if isinstance(value, MeasuredValue) and isinstance(value.name, str):
            names.append(value.name)
    for name in sorted(_duplicates(names)):
        issues.append(ValidationIssue(path, f"duplicate measured value name {name!r}"))


def _check_safety_measurement(
    issues: list[ValidationIssue], path: str, measurement: MeasuredValue
) -> None:
    allowed_units = _SAFETY_MEASUREMENT_UNITS.get(measurement.name)
    if allowed_units is None:
        issues.append(
            ValidationIssue(
                path,
                f"unknown safety measurement {measurement.name!r}; use the v0 safety vocabulary",
            )
        )
    elif measurement.unit not in allowed_units:
        issues.append(
            ValidationIssue(
                f"{path}.unit",
                f"safety measurement {measurement.name!r} requires unit in {sorted(allowed_units)}",
            )
        )
    if isinstance(measurement.value, (int, float)) and not isinstance(
        measurement.value, bool
    ):
        if (
            measurement.name in _STRICTLY_POSITIVE_SAFETY_MEASUREMENTS
            and measurement.value <= 0
        ):
            issues.append(
                ValidationIssue(
                    f"{path}.value",
                    f"safety measurement {measurement.name!r} must be greater than zero",
                )
            )
        elif (
            measurement.name not in _SIGNED_SAFETY_MEASUREMENTS
            and measurement.value < 0
        ):
            issues.append(
                ValidationIssue(
                    f"{path}.value",
                    f"safety measurement {measurement.name!r} must be non-negative",
                )
            )


def _check_safety_relationships(
    issues: list[ValidationIssue],
    path: str,
    measurements: tuple[MeasuredValue, ...],
) -> None:
    by_name = {measurement.name: measurement for measurement in measurements}
    for lower_name, upper_name in (
        ("minimum_commanded_speed", "max_speed"),
        ("min_temperature", "max_temperature"),
    ):
        lower = by_name.get(lower_name)
        upper = by_name.get(upper_name)
        if lower is not None and upper is not None and lower.value > upper.value:
            issues.append(
                ValidationIssue(
                    path,
                    f"{lower_name} must not exceed {upper_name}",
                )
            )


def _check_capabilities(
    issues: list[ValidationIssue],
    path: str,
    capabilities: object,
    allowed: frozenset[CapabilityKind],
) -> None:
    if not isinstance(capabilities, tuple) or not capabilities:
        issues.append(ValidationIssue(path, "must contain at least one capability"))
        return
    names: list[str] = []
    for index, declaration in enumerate(capabilities):
        item_path = f"{path}[{index}]"
        if not isinstance(declaration, CapabilityDeclaration):
            issues.append(ValidationIssue(item_path, "invalid capability declaration"))
            continue
        capability = declaration.capability
        if not isinstance(capability, CapabilityKind):
            issues.append(ValidationIssue(f"{item_path}.capability", "unknown capability"))
        else:
            names.append(capability.value)
            if capability not in allowed:
                issues.append(
                    ValidationIssue(
                        f"{item_path}.capability",
                        f"{capability.value!r} is incompatible with this asset type",
                    )
                )
        _check_measurement_collection(
            issues, f"{item_path}.parameters", declaration.parameters, positive=False
        )
        required = _CAPABILITY_REQUIRED_PARAMETER.get(capability)
        if required is not None:
            required_names, required_units = required
            matches = [
                parameter
                for parameter in declaration.parameters
                if parameter.name in required_names and parameter.unit in required_units
            ]
            if len(matches) != 1:
                issues.append(
                    ValidationIssue(
                        f"{item_path}.parameters",
                        "capability requires exactly one positive parameter with "
                        f"name in {sorted(required_names)} and unit in {sorted(required_units)}",
                    )
                )
            elif matches[0].value <= 0:
                issues.append(
                    ValidationIssue(
                        f"{item_path}.parameters", "required capability parameter must be positive"
                    )
                )
        _check_provenance(issues, f"{item_path}.provenance", declaration.provenance)
    for name in sorted(_duplicates(names)):
        issues.append(ValidationIssue(path, f"duplicate capability {name!r}"))


def _check_crs(
    issues: list[ValidationIssue], path: str, crs: object
) -> None:
    if not isinstance(crs, CoordinateReferenceSystem):
        issues.append(ValidationIssue(path, "coordinate reference system is required"))
        return
    if crs.kind is CoordinateSystemKind.LOCAL_CARTESIAN:
        if not isinstance(crs.identifier, str) or not _LOCAL_CRS.fullmatch(crs.identifier):
            issues.append(
                ValidationIssue(
                    f"{path}.identifier", "local CRS identifiers must match LOCAL:<id>"
                )
            )
        if crs.horizontal_unit == "degree":
            issues.append(
                ValidationIssue(
                    f"{path}.horizontal_unit",
                    "local Cartesian coordinates cannot use angular degrees",
                )
            )
        local_axes = tuple(axis.casefold() for axis in crs.axes)
        if local_axes not in {("x", "y", "z"), ("east", "north", "up")}:
            issues.append(
                ValidationIssue(
                    f"{path}.axes",
                    "local Cartesian axes must be x/y/z or east/north/up",
                )
            )
    elif crs.kind is CoordinateSystemKind.EPSG:
        if not isinstance(crs.identifier, str) or not _EPSG_CRS.fullmatch(crs.identifier):
            issues.append(
                ValidationIssue(
                    f"{path}.identifier", "EPSG identifiers must match EPSG:<positive-code>"
                )
            )
        else:
            code = int(crs.identifier.split(":", 1)[1])
            axes = tuple(axis.casefold() for axis in crs.axes)
            if code == 4326:
                if crs.horizontal_unit != "degree":
                    issues.append(
                        ValidationIssue(
                            f"{path}.horizontal_unit",
                            "EPSG:4326 horizontal coordinates must use degrees",
                        )
                    )
                if axes not in {
                    ("longitude", "latitude", "height"),
                    ("longitude", "latitude", "up"),
                }:
                    issues.append(
                        ValidationIssue(
                            f"{path}.axes",
                            "EPSG:4326 axes must be longitude/latitude/height",
                        )
                    )
            elif code == 3857 or 32601 <= code <= 32660 or 32701 <= code <= 32760:
                if crs.horizontal_unit != "m":
                    issues.append(
                        ValidationIssue(
                            f"{path}.horizontal_unit",
                            "supported projected EPSG coordinates must use metres",
                        )
                    )
                if axes != ("east", "north", "up"):
                    issues.append(
                        ValidationIssue(
                            f"{path}.axes",
                            "supported projected EPSG axes must be east/north/up",
                        )
                    )
            else:
                issues.append(
                    ValidationIssue(
                        f"{path}.identifier",
                        "EPSG code is not supported by the offline v0 CRS registry",
                    )
                )
    else:
        issues.append(ValidationIssue(f"{path}.kind", "unsupported coordinate system kind"))
    if crs.horizontal_unit not in _SUPPORTED_HORIZONTAL_UNITS:
        issues.append(
            ValidationIssue(f"{path}.horizontal_unit", "unsupported horizontal unit")
        )
    if crs.vertical_unit not in _SUPPORTED_VERTICAL_UNITS:
        issues.append(ValidationIssue(f"{path}.vertical_unit", "unsupported vertical unit"))
    if not isinstance(crs.axes, tuple) or len(crs.axes) != 3:
        issues.append(ValidationIssue(f"{path}.axes", "exactly three axes are required"))
    elif any(not _is_text(axis) for axis in crs.axes) or len(set(crs.axes)) != 3:
        issues.append(ValidationIssue(f"{path}.axes", "axes must be unique non-blank strings"))
    _check_provenance(issues, f"{path}.provenance", crs.provenance)


def _check_point(
    issues: list[ValidationIssue],
    path: str,
    point: object,
    *,
    geographic: bool,
) -> None:
    if not isinstance(point, SpatialPoint):
        issues.append(ValidationIssue(path, "must be a SpatialPoint"))
        return
    coordinates = (("x", point.x), ("y", point.y), ("z", point.z))
    for name, value in coordinates:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(ValidationIssue(f"{path}.{name}", "must be numeric"))
        elif not math.isfinite(float(value)):
            issues.append(ValidationIssue(f"{path}.{name}", "must be finite"))
    if geographic:
        if isinstance(point.x, (int, float)) and not isinstance(point.x, bool):
            if not -180 <= point.x <= 180:
                issues.append(ValidationIssue(f"{path}.x", "longitude must be within [-180, 180]"))
        if isinstance(point.y, (int, float)) and not isinstance(point.y, bool):
            if not -90 <= point.y <= 90:
                issues.append(ValidationIssue(f"{path}.y", "latitude must be within [-90, 90]"))
    _check_provenance(issues, f"{path}.provenance", point.provenance)


def _coordinate_decimal(value: int | float) -> Decimal:
    # Converting through ``str`` retains the authored decimal value while
    # avoiding catastrophic cancellation from multiplying large projected
    # coordinates (for example UTM eastings/northings) directly.
    return Decimal(str(value))


def _polygon_area(points: tuple[SpatialPoint, ...]) -> Decimal:
    origin_x = _coordinate_decimal(points[0].x)
    origin_y = _coordinate_decimal(points[0].y)
    translated = tuple(
        (
            _coordinate_decimal(point.x) - origin_x,
            _coordinate_decimal(point.y) - origin_y,
        )
        for point in points
    )
    twice_signed_area = sum(
        translated[index][0] * translated[(index + 1) % len(points)][1]
        - translated[(index + 1) % len(points)][0] * translated[index][1]
        for index in range(len(points))
    )
    return abs(twice_signed_area) / Decimal(2)


def _orientation(
    first: SpatialPoint, second: SpatialPoint, third: SpatialPoint
) -> int:
    first_x = _coordinate_decimal(first.x)
    first_y = _coordinate_decimal(first.y)
    cross_product = (
        (_coordinate_decimal(second.x) - first_x)
        * (_coordinate_decimal(third.y) - first_y)
        - (_coordinate_decimal(second.y) - first_y)
        * (_coordinate_decimal(third.x) - first_x)
    )
    if cross_product == 0:
        return 0
    return 1 if cross_product > 0 else -1


def _point_on_segment(
    start: SpatialPoint, point: SpatialPoint, end: SpatialPoint
) -> bool:
    start_x = _coordinate_decimal(start.x)
    start_y = _coordinate_decimal(start.y)
    point_x = _coordinate_decimal(point.x)
    point_y = _coordinate_decimal(point.y)
    end_x = _coordinate_decimal(end.x)
    end_y = _coordinate_decimal(end.y)
    return (
        min(start_x, end_x) <= point_x <= max(start_x, end_x)
        and min(start_y, end_y) <= point_y <= max(start_y, end_y)
    )


def _segments_intersect(
    first_start: SpatialPoint,
    first_end: SpatialPoint,
    second_start: SpatialPoint,
    second_end: SpatialPoint,
) -> bool:
    first_to_second_start = _orientation(first_start, first_end, second_start)
    first_to_second_end = _orientation(first_start, first_end, second_end)
    second_to_first_start = _orientation(second_start, second_end, first_start)
    second_to_first_end = _orientation(second_start, second_end, first_end)

    if (
        first_to_second_start * first_to_second_end < 0
        and second_to_first_start * second_to_first_end < 0
    ):
        return True
    return any(
        (
            orientation == 0
            and _point_on_segment(segment_start, point, segment_end)
        )
        for orientation, segment_start, point, segment_end in (
            (first_to_second_start, first_start, second_start, first_end),
            (first_to_second_end, first_start, second_end, first_end),
            (second_to_first_start, second_start, first_start, second_end),
            (second_to_first_end, second_start, first_end, second_end),
        )
    )


def _polygon_self_intersects(points: tuple[SpatialPoint, ...]) -> bool:
    vertices = list(points)
    if len(vertices) > 1 and (vertices[0].x, vertices[0].y) == (
        vertices[-1].x,
        vertices[-1].y,
    ):
        vertices.pop()
    edge_count = len(vertices)
    for first_index in range(edge_count):
        first_start = vertices[first_index]
        first_end = vertices[(first_index + 1) % edge_count]
        if (first_start.x, first_start.y) == (first_end.x, first_end.y):
            return True
        for second_index in range(first_index + 1, edge_count):
            if second_index == first_index + 1 or (
                first_index == 0 and second_index == edge_count - 1
            ):
                continue
            second_start = vertices[second_index]
            second_end = vertices[(second_index + 1) % edge_count]
            if _segments_intersect(
                first_start,
                first_end,
                second_start,
                second_end,
            ):
                return True
    return False


def _check_geometry(
    issues: list[ValidationIssue],
    path: str,
    geometry: object,
    *,
    geographic: bool,
) -> None:
    if not isinstance(geometry, GeometryReference):
        issues.append(ValidationIssue(path, "must be a GeometryReference"))
        return
    _check_identifier(issues, f"{path}.geometry_id", geometry.geometry_id)
    if not isinstance(geometry.geometry_type, GeometryType):
        issues.append(ValidationIssue(f"{path}.geometry_type", "unsupported geometry type"))
        return
    if not isinstance(geometry.points, tuple):
        issues.append(ValidationIssue(f"{path}.points", "must be an immutable tuple"))
        return
    for index, point in enumerate(geometry.points):
        _check_point(issues, f"{path}.points[{index}]", point, geographic=geographic)
    count = len(geometry.points)
    if geometry.geometry_type is GeometryType.POINT and count != 1:
        issues.append(ValidationIssue(f"{path}.points", "point geometry requires exactly one point"))
    elif geometry.geometry_type is GeometryType.POLYLINE and count < 2:
        issues.append(ValidationIssue(f"{path}.points", "polyline geometry requires at least two points"))
    elif geometry.geometry_type is GeometryType.POLYGON:
        distinct = {(point.x, point.y) for point in geometry.points}
        if count < 3 or len(distinct) < 3:
            issues.append(ValidationIssue(f"{path}.points", "polygon requires three distinct points"))
        elif all(isinstance(point, SpatialPoint) for point in geometry.points):
            if _polygon_area(geometry.points) == 0:
                issues.append(ValidationIssue(f"{path}.points", "polygon must have non-zero area"))
            elif _polygon_self_intersects(geometry.points):
                issues.append(ValidationIssue(f"{path}.points", "polygon must not self-intersect"))
    _check_text(issues, f"{path}.source_uri", geometry.source_uri, optional=True)
    _check_provenance(issues, f"{path}.provenance", geometry.provenance)


def _check_spatial(
    issues: list[ValidationIssue], spatial: object
) -> tuple[set[str], set[str]]:
    if not isinstance(spatial, SpatialReference):
        issues.append(ValidationIssue("spatial_reference", "SpatialReference is required"))
        return set(), set()
    _check_crs(issues, "spatial_reference.coordinate_system", spatial.coordinate_system)
    geographic = (
        isinstance(spatial.coordinate_system, CoordinateReferenceSystem)
        and spatial.coordinate_system.horizontal_unit == "degree"
    )
    _check_point(
        issues,
        "spatial_reference.facility_origin",
        spatial.facility_origin,
        geographic=geographic,
    )
    geometry_ids = [item.geometry_id for item in spatial.geometry_references]
    for duplicate in sorted(_duplicates(geometry_ids)):
        issues.append(
            ValidationIssue("spatial_reference.geometry_references", f"duplicate geometry ID {duplicate!r}")
        )
    for index, geometry in enumerate(spatial.geometry_references):
        _check_geometry(
            issues,
            f"spatial_reference.geometry_references[{index}]",
            geometry,
            geographic=geographic,
        )
    zone_ids = [item.zone_id for item in spatial.zone_definitions]
    for duplicate in sorted(_duplicates(zone_ids)):
        issues.append(
            ValidationIssue("spatial_reference.zone_definitions", f"duplicate zone ID {duplicate!r}")
        )
    geometry_id_set = set(geometry_ids)
    for index, zone in enumerate(spatial.zone_definitions):
        path = f"spatial_reference.zone_definitions[{index}]"
        if not isinstance(zone, ZoneDefinition):
            issues.append(ValidationIssue(path, "must be a ZoneDefinition"))
            continue
        _check_identifier(issues, f"{path}.zone_id", zone.zone_id)
        _check_text(issues, f"{path}.name", zone.name)
        _check_text(issues, f"{path}.zone_type", zone.zone_type)
        _check_text(issues, f"{path}.safety_classification", zone.safety_classification)
        if zone.geometry_reference not in geometry_id_set:
            issues.append(
                ValidationIssue(
                    f"{path}.geometry_reference",
                    f"unknown geometry reference {zone.geometry_reference!r}",
                )
            )
        _check_provenance(issues, f"{path}.provenance", zone.provenance)
    return geometry_id_set, set(zone_ids)


def _check_equipment(
    issues: list[ValidationIssue],
    equipment: EquipmentAsset,
    path: str,
    geometry_ids: set[str],
) -> None:
    _check_identifier(issues, f"{path}.asset_id", equipment.asset_id)
    if not isinstance(equipment.asset_type, AssetType):
        issues.append(ValidationIssue(f"{path}.asset_type", "unknown equipment asset type"))
        allowed = frozenset()
    else:
        allowed = EQUIPMENT_CAPABILITY_COMPATIBILITY[equipment.asset_type]
    _check_text(issues, f"{path}.manufacturer", equipment.manufacturer, optional=True)
    _check_text(issues, f"{path}.model", equipment.model, optional=True)
    _check_capabilities(issues, f"{path}.capabilities", equipment.capabilities, allowed)
    if isinstance(equipment.asset_type, AssetType):
        required_capability = _CORE_CAPABILITY_BY_ASSET[equipment.asset_type]
        declared_capabilities = {
            item.capability
            for item in equipment.capabilities
            if isinstance(item, CapabilityDeclaration)
        }
        if required_capability is not None and required_capability not in declared_capabilities:
            issues.append(
                ValidationIssue(
                    f"{path}.capabilities",
                    f"{equipment.asset_type.value} requires defining capability "
                    f"{required_capability.value!r}",
                )
            )
    _check_measurement_collection(issues, f"{path}.capacity", equipment.capacity, positive=True)
    if not equipment.capacity:
        issues.append(ValidationIssue(f"{path}.capacity", "at least one explicit capacity is required"))
    if equipment.asset_type is AssetType.WASHER and not any(
        item.name in {"batch_capacity", "batch_size_balls", "washer_batch_size"}
        and item.unit == "balls"
        for item in equipment.capacity
    ):
        issues.append(
            ValidationIssue(
                f"{path}.capacity",
                "washer requires a commissioned batch capacity in balls",
            )
        )
    if equipment.asset_type is AssetType.CHARGING_STATION and not any(
        item.name in {"charger_slots", "slot_count", "slots"}
        and item.unit in {"count", "slots"}
        for item in equipment.capacity
    ):
        issues.append(
            ValidationIssue(
                f"{path}.capacity",
                "charging station requires a commissioned slot capacity",
            )
        )
    if equipment.asset_type is AssetType.COLLECTION_STATION and not any(
        item.name in {"buffer_capacity", "buffer_capacity_balls", "station_buffer_capacity"}
        and item.unit == "balls"
        for item in equipment.capacity
    ):
        issues.append(
            ValidationIssue(
                f"{path}.capacity",
                "collection station requires a commissioned buffer capacity in balls",
            )
        )
    _check_identifier(issues, f"{path}.location_reference", equipment.location_reference)
    if equipment.location_reference not in geometry_ids:
        issues.append(
            ValidationIssue(
                f"{path}.location_reference",
                f"unknown geometry reference {equipment.location_reference!r}",
            )
        )
    _check_provenance(issues, f"{path}.provenance", equipment.provenance)


def _check_robot(
    issues: list[ValidationIssue],
    robot: RobotAsset,
    path: str,
    zone_ids: set[str],
    charging_assets: set[str],
) -> None:
    _check_identifier(issues, f"{path}.robot_id", robot.robot_id)
    _check_capabilities(
        issues,
        f"{path}.capabilities",
        robot.capabilities,
        ROBOT_CAPABILITY_COMPATIBILITY,
    )
    _check_measurement_collection(issues, f"{path}.payload_limits", robot.payload_limits, positive=True)
    if not robot.payload_limits:
        issues.append(ValidationIssue(f"{path}.payload_limits", "at least one payload limit is required"))
    else:
        for index, item in enumerate(robot.payload_limits):
            if item.name not in _PAYLOAD_MEASUREMENT_NAMES or item.unit not in _PAYLOAD_UNITS:
                issues.append(
                    ValidationIssue(
                        f"{path}.payload_limits[{index}]",
                        "must be a payload capacity measured in balls or kg",
                    )
                )
    declared_robot_capabilities = {
        item.capability
        for item in robot.capabilities
        if isinstance(item, CapabilityDeclaration)
    }
    if CapabilityKind.EMERGENCY_STOP not in declared_robot_capabilities:
        issues.append(
            ValidationIssue(
                f"{path}.capabilities",
                "robot requires the emergency_stop safety capability",
            )
        )
    if not isinstance(robot.operating_zones, tuple) or not robot.operating_zones:
        issues.append(ValidationIssue(f"{path}.operating_zones", "at least one operating zone is required"))
    else:
        for duplicate in sorted(_duplicates(list(robot.operating_zones))):
            issues.append(ValidationIssue(f"{path}.operating_zones", f"duplicate zone {duplicate!r}"))
        for index, zone_id in enumerate(robot.operating_zones):
            if zone_id not in zone_ids:
                issues.append(
                    ValidationIssue(
                        f"{path}.operating_zones[{index}]", f"unknown zone {zone_id!r}"
                    )
                )
    charging = robot.charging_requirements
    if not hasattr(charging, "charging_station_ids"):
        issues.append(ValidationIssue(f"{path}.charging_requirements", "is required"))
    else:
        _check_text(issues, f"{path}.charging_requirements.connector_type", charging.connector_type)
        if not charging.charging_station_ids:
            issues.append(
                ValidationIssue(
                    f"{path}.charging_requirements.charging_station_ids",
                    "at least one compatible charging station is required",
                )
            )
        for station_id in charging.charging_station_ids:
            if station_id not in charging_assets:
                issues.append(
                    ValidationIssue(
                        f"{path}.charging_requirements.charging_station_ids",
                        f"unknown or non-charging asset {station_id!r}",
                    )
                )
        _check_measurement_collection(
            issues,
            f"{path}.charging_requirements.electrical_requirements",
            charging.electrical_requirements,
            positive=True,
        )
        if not charging.electrical_requirements:
            issues.append(
                ValidationIssue(
                    f"{path}.charging_requirements.electrical_requirements",
                    "explicit electrical requirements are required",
                )
            )
        else:
            for index, requirement in enumerate(charging.electrical_requirements):
                allowed_units = _ELECTRICAL_MEASUREMENT_UNITS.get(requirement.name)
                if allowed_units is None or requirement.unit not in allowed_units:
                    issues.append(
                        ValidationIssue(
                            f"{path}.charging_requirements.electrical_requirements[{index}]",
                            "must use a recognized electrical quantity and unit",
                        )
                    )
            if not any(
                item.name == "nominal_voltage" and item.unit == "V"
                for item in charging.electrical_requirements
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.charging_requirements.electrical_requirements",
                        "nominal_voltage in V is required",
                    )
                )
        _check_provenance(
            issues,
            f"{path}.charging_requirements.provenance",
            charging.provenance,
        )
    if not isinstance(robot.safety_constraints, tuple) or not robot.safety_constraints:
        issues.append(
            ValidationIssue(
                f"{path}.safety_constraints", "explicit safety constraints are required"
            )
        )
    else:
        safety_ids: list[str] = []
        safety_limits: list[MeasuredValue] = []
        for index, constraint in enumerate(robot.safety_constraints):
            item_path = f"{path}.safety_constraints[{index}]"
            _check_identifier(issues, f"{item_path}.constraint_id", constraint.constraint_id)
            _check_text(issues, f"{item_path}.description", constraint.description)
            if constraint.limit is not None:
                _check_measurement(issues, f"{item_path}.limit", constraint.limit, positive=False)
                if isinstance(constraint.limit, MeasuredValue):
                    safety_limits.append(constraint.limit)
                    _check_safety_measurement(
                        issues,
                        f"{item_path}.limit",
                        constraint.limit,
                    )
            _check_provenance(issues, f"{item_path}.provenance", constraint.provenance)
            safety_ids.append(constraint.constraint_id)
        for duplicate in sorted(_duplicates(safety_ids)):
            issues.append(ValidationIssue(f"{path}.safety_constraints", f"duplicate safety constraint {duplicate!r}"))
        _check_safety_relationships(
            issues,
            f"{path}.safety_constraints",
            tuple(safety_limits),
        )
    _check_provenance(issues, f"{path}.provenance", robot.provenance)


def _check_sensor(
    issues: list[ValidationIssue],
    sensor: SensorBinding,
    path: str,
    equipment_by_id: dict[str, EquipmentAsset],
    robot_ids: set[str],
    zone_ids: set[str],
    site_id: str,
    commissioned_at: datetime | None,
) -> None:
    _check_identifier(issues, f"{path}.sensor_id", sensor.sensor_id)
    if not isinstance(sensor.sensor_type, SensorType):
        issues.append(ValidationIssue(f"{path}.sensor_type", "unknown sensor type"))
    _check_text(issues, f"{path}.source", sensor.source)
    if not isinstance(sensor.channel, str) or not _CHANNEL.fullmatch(sensor.channel):
        issues.append(
            ValidationIssue(
                f"{path}.channel", "must be a canonical dotted channel name"
            )
        )
    _check_identifier(issues, f"{path}.associated_asset_id", sensor.associated_asset_id)
    asset_ids = set(equipment_by_id) | robot_ids | {site_id}
    if sensor.associated_asset_id not in asset_ids:
        issues.append(
            ValidationIssue(
                f"{path}.associated_asset_id",
                f"unknown associated asset {sensor.associated_asset_id!r}",
            )
        )
    _check_text(issues, f"{path}.units", sensor.units)

    count_sensors = frozenset(
        {
            SensorType.LOAD_CELL,
            SensorType.RFID_READER,
            SensorType.CAMERA,
            SensorType.EXTERNAL_SYSTEM,
            SensorType.MANUAL_INPUT,
            SensorType.OTHER,
        }
    )
    boolean_sensors = frozenset(
        {
            SensorType.PROXIMITY_SWITCH,
            SensorType.EXTERNAL_SYSTEM,
            SensorType.MANUAL_INPUT,
            SensorType.OTHER,
        }
    )
    system_sensors = frozenset(
        {SensorType.EXTERNAL_SYSTEM, SensorType.MANUAL_INPUT, SensorType.OTHER}
    )
    allowed_types: frozenset[SensorType] | None = None
    expected_unit: str | None = None
    expected_equipment_type: AssetType | None = None
    expected_asset_id: str | None = None
    referenced_zone: str | None = None

    channel = sensor.channel
    if channel in {"inventory.dispenser.count", "inventory.dispenser.sensed"}:
        allowed_types = count_sensors
        expected_unit = "balls"
        expected_equipment_type = AssetType.DISPENSER
    elif channel == "wash.washer.wip":
        allowed_types = count_sensors
        expected_unit = "balls"
        expected_equipment_type = AssetType.WASHER
    elif channel == "charger.site.queue_length":
        allowed_types = count_sensors
        expected_unit = "count"
        expected_equipment_type = AssetType.CHARGING_STATION
    elif channel in {"staff.site.busy", "staff.site.queued"}:
        allowed_types = system_sensors
        expected_unit = "count"
        expected_asset_id = site_id
    else:
        station_inventory = re.fullmatch(
            r"inventory\.station\.(.+)\.buffer_balls", channel
        )
        zone_scan = re.fullmatch(r"scan\.zone\.(.+)\.balls", channel)
        zone_status = re.fullmatch(r"zone\.(.+)\.is_open", channel)
        station_status = re.fullmatch(
            r"station\.(.+)\.(is_open|docked|queue_length)", channel
        )
        robot_channel = re.fullmatch(
            r"robot\.(.+)\.(activity|health|battery_frac|payload_balls|location|"
            r"destination|assigned_zone|estop_latched|awaiting_human)",
            channel,
        )
        if station_inventory:
            allowed_types = count_sensors
            expected_unit = "balls"
            expected_asset_id = station_inventory.group(1)
            expected_equipment_type = AssetType.COLLECTION_STATION
        elif zone_scan:
            allowed_types = frozenset(
                {
                    SensorType.LIDAR,
                    SensorType.CAMERA,
                    SensorType.RFID_READER,
                    SensorType.EXTERNAL_SYSTEM,
                    SensorType.MANUAL_INPUT,
                    SensorType.OTHER,
                }
            )
            expected_unit = "balls"
            referenced_zone = zone_scan.group(1)
        elif zone_status:
            allowed_types = boolean_sensors
            expected_unit = "1"
            referenced_zone = zone_status.group(1)
        elif station_status:
            allowed_types = (
                boolean_sensors
                if station_status.group(2) == "is_open"
                else boolean_sensors | system_sensors
            )
            expected_unit = "1" if station_status.group(2) == "is_open" else "count"
            expected_asset_id = station_status.group(1)
            expected_equipment_type = AssetType.COLLECTION_STATION
        elif robot_channel:
            field = robot_channel.group(2)
            expected_asset_id = robot_channel.group(1)
            if field == "battery_frac":
                allowed_types = frozenset(
                    {SensorType.BATTERY_MONITOR, SensorType.EXTERNAL_SYSTEM, SensorType.OTHER}
                )
                expected_unit = "1"
            elif field == "payload_balls":
                allowed_types = count_sensors
                expected_unit = "balls"
            elif field in {"estop_latched", "awaiting_human"}:
                allowed_types = boolean_sensors
                expected_unit = "1"
            else:
                allowed_types = system_sensors
                expected_unit = "1"

    if allowed_types is None:
        issues.append(
            ValidationIssue(f"{path}.channel", "is not a supported canonical telemetry channel")
        )
    else:
        if sensor.sensor_type not in allowed_types:
            issues.append(
                ValidationIssue(
                    f"{path}.sensor_type",
                    f"{sensor.sensor_type.value!r} is incompatible with channel {channel!r}",
                )
            )
        if sensor.units != expected_unit:
            issues.append(
                ValidationIssue(
                    f"{path}.units",
                    f"channel {channel!r} requires canonical unit {expected_unit!r}",
                )
            )
        if expected_asset_id is not None and sensor.associated_asset_id != expected_asset_id:
            issues.append(
                ValidationIssue(
                    f"{path}.associated_asset_id",
                    f"must match channel asset {expected_asset_id!r}",
                )
            )
        if expected_equipment_type is not None:
            equipment = equipment_by_id.get(sensor.associated_asset_id)
            if equipment is None or equipment.asset_type is not expected_equipment_type:
                issues.append(
                    ValidationIssue(
                        f"{path}.associated_asset_id",
                        f"channel requires associated {expected_equipment_type.value} equipment",
                    )
                )
        if referenced_zone is not None and referenced_zone not in zone_ids:
            issues.append(
                ValidationIssue(
                    f"{path}.channel", f"references unknown zone {referenced_zone!r}"
                )
            )

    if not isinstance(sensor.calibration, CalibrationInfo):
        issues.append(
            ValidationIssue(
                f"{path}.calibration", "explicit calibration information is required"
            )
        )
    else:
        if (
            sensor.sensor_type in _CALIBRATION_REQUIRED_SENSOR_TYPES
            and sensor.calibration.status is not CalibrationStatus.CALIBRATED
        ):
            issues.append(
                ValidationIssue(
                    f"{path}.calibration.status",
                    f"{sensor.sensor_type.value} requires a calibrated record",
                )
            )
        if sensor.calibration.status is CalibrationStatus.CALIBRATED:
            calibrated_at = _parse_timestamp(sensor.calibration.calibrated_at)
            valid_until = _parse_timestamp(sensor.calibration.valid_until)
            if (
                calibrated_at is not None
                and commissioned_at is not None
                and calibrated_at > commissioned_at
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.calibration.calibrated_at",
                        "calibration was performed after manifest commissioning approval",
                    )
                )
            if (
                valid_until is not None
                and commissioned_at is not None
                and valid_until < commissioned_at
            ):
                issues.append(
                    ValidationIssue(
                        f"{path}.calibration.valid_until",
                        "calibration expired before manifest commissioning approval",
                    )
                )
    _check_provenance(issues, f"{path}.provenance", sensor.provenance)


def collect_validation_issues(site: CommissionedSite) -> tuple[ValidationIssue, ...]:
    """Return every detectable manifest violation in deterministic order."""

    issues: list[ValidationIssue] = []
    _check_identifier(issues, "site_id", site.site_id)
    _check_identifier(issues, "deployment_id", site.deployment_id)
    _check_text(issues, "facility_name", site.facility_name)
    _check_text(issues, "timezone", site.timezone)
    if _is_text(site.timezone):
        try:
            ZoneInfo(site.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            issues.append(ValidationIssue("timezone", "must be a valid IANA timezone"))

    location = site.location_metadata
    if not hasattr(location, "country_code"):
        issues.append(ValidationIssue("location_metadata", "LocationMetadata is required"))
    else:
        if not isinstance(location.address_lines, tuple):
            issues.append(ValidationIssue("location_metadata.address_lines", "must be an immutable tuple"))
        else:
            for index, line in enumerate(location.address_lines):
                _check_text(issues, f"location_metadata.address_lines[{index}]", line)
        for name in ("locality", "region", "postal_code", "site_reference"):
            _check_text(
                issues,
                f"location_metadata.{name}",
                getattr(location, name),
                optional=True,
            )
        if not isinstance(location.country_code, str) or not re.fullmatch(
            r"[A-Z]{2}", location.country_code
        ):
            issues.append(
                ValidationIssue(
                    "location_metadata.country_code", "must be an uppercase ISO alpha-2 code"
                )
            )
        if not location.address_lines and not location.locality and not location.site_reference:
            issues.append(
                ValidationIssue(
                    "location_metadata", "must include an address, locality, or site reference"
                )
            )
        _check_provenance(issues, "location_metadata.provenance", location.provenance)

    geometry_ids, zone_ids = _check_spatial(issues, site.spatial_reference)

    equipment_ids = [item.asset_id for item in site.equipment_assets]
    robot_ids = [item.robot_id for item in site.robot_assets]
    for duplicate in sorted(_duplicates(equipment_ids + robot_ids)):
        issues.append(ValidationIssue("assets", f"duplicate asset ID {duplicate!r}"))
    charging_assets = {
        item.asset_id
        for item in site.equipment_assets
        if item.asset_type is AssetType.CHARGING_STATION
    }
    for index, equipment in enumerate(site.equipment_assets):
        _check_equipment(
            issues,
            equipment,
            f"equipment_assets[{index}]",
            geometry_ids,
        )
    for index, robot in enumerate(site.robot_assets):
        _check_robot(
            issues,
            robot,
            f"robot_assets[{index}]",
            zone_ids,
            charging_assets,
        )

    constraint_ids = [item.constraint_id for item in site.operating_constraints]
    for duplicate in sorted(_duplicates(constraint_ids)):
        issues.append(
            ValidationIssue("operating_constraints", f"duplicate constraint ID {duplicate!r}")
        )
    reference_ids = {"site", *equipment_ids, *robot_ids, *zone_ids}
    for index, constraint in enumerate(site.operating_constraints):
        path = f"operating_constraints[{index}]"
        _check_identifier(issues, f"{path}.constraint_id", constraint.constraint_id)
        _check_text(issues, f"{path}.category", constraint.category)
        _check_text(issues, f"{path}.description", constraint.description)
        if not isinstance(constraint.safety_relevant, bool):
            issues.append(ValidationIssue(f"{path}.safety_relevant", "must be explicit boolean"))
        if not constraint.applies_to:
            issues.append(ValidationIssue(f"{path}.applies_to", "must name at least one target"))
        for target in constraint.applies_to:
            if target not in reference_ids:
                issues.append(ValidationIssue(f"{path}.applies_to", f"unknown target {target!r}"))
        for duplicate in sorted(_duplicates(list(constraint.applies_to))):
            issues.append(ValidationIssue(f"{path}.applies_to", f"duplicate target {duplicate!r}"))
        _check_measurement_collection(
            issues, f"{path}.parameters", constraint.parameters, positive=False
        )
        if constraint.safety_relevant is True:
            for parameter_index, parameter in enumerate(constraint.parameters):
                if isinstance(parameter, MeasuredValue):
                    _check_safety_measurement(
                        issues,
                        f"{path}.parameters[{parameter_index}]",
                        parameter,
                    )
            _check_safety_relationships(
                issues,
                f"{path}.parameters",
                constraint.parameters,
            )
        _check_provenance(issues, f"{path}.provenance", constraint.provenance)

    sensor_ids = [item.sensor_id for item in site.sensor_bindings]
    channels = [item.channel for item in site.sensor_bindings]
    for duplicate in sorted(_duplicates(sensor_ids)):
        issues.append(ValidationIssue("sensor_bindings", f"duplicate sensor ID {duplicate!r}"))
    for duplicate in sorted(_duplicates(channels)):
        issues.append(ValidationIssue("sensor_bindings", f"duplicate channel {duplicate!r}"))
    equipment_by_id = {item.asset_id: item for item in site.equipment_assets}
    robot_id_set = set(robot_ids)
    commissioned_at = _parse_timestamp(site.provenance.captured_at)
    for index, sensor in enumerate(site.sensor_bindings):
        _check_sensor(
            issues,
            sensor,
            f"sensor_bindings[{index}]",
            equipment_by_id,
            robot_id_set,
            zone_ids,
            site.site_id,
            commissioned_at,
        )

    _check_provenance(issues, "provenance", site.provenance)
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.message)))


def validate_commissioned_site(site: CommissionedSite) -> None:
    """Raise one aggregate error when any commissioned fact is invalid."""

    issues = collect_validation_issues(site)
    if issues:
        raise CommissioningValidationError(issues)
