"""One-way projections from commissioning into downstream configuration data.

The functions in this module return new JSON-ready dictionaries and import no
downstream runtime packages.  A projection is always disposable: the
``CommissionedSite`` remains the source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import CommissionedSite
from .equipment import AssetType, CapabilityKind
from .provenance import MeasuredValue, Provenance
from .sensors import SensorType
from .spatial import SpatialPoint
from .validation import validate_commissioned_site

SITE_CONFIG_PROJECTION_SCHEMA = "nxt-commissioning/site-config-projection/v0"
DIGITAL_TWIN_LAYOUT_SCHEMA = "nxt-commissioning/digital-twin-layout/v0"
TELEMETRY_ADAPTER_CONFIG_SCHEMA = "nxt-commissioning/telemetry-adapter-config/v0"


class ProjectionError(ValueError):
    """A commissioned fact cannot be represented safely by a projection."""


@dataclass(frozen=True, slots=True)
class LegacySiteConfigContext:
    """Explicit non-manifest inputs required by the current SiteConfig.

    The current consumer contract mixes commissioned statics with simulation
    and service inputs.  This context is deliberately separate from
    ``CommissionedSite`` so none of those values become authoritative facility
    facts by accident.  No field has a default.
    """

    scenario_name: str
    seed: int
    total_balls: int
    staff_capacity: int
    wet_ground_speed_multiplier: float
    open_minute: int
    close_minute: int
    forecast_bucket_minutes: int
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_name, str) or not self.scenario_name.strip():
            raise ProjectionError("scenario_name must be a non-blank string")
        integer_fields = (
            ("seed", self.seed, False),
            ("total_balls", self.total_balls, True),
            ("staff_capacity", self.staff_capacity, True),
            ("forecast_bucket_minutes", self.forecast_bucket_minutes, True),
        )
        for name, value, positive in integer_fields:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProjectionError(f"{name} must be an integer")
            if (positive and value <= 0) or (not positive and value < 0):
                qualifier = "greater than zero" if positive else "non-negative"
                raise ProjectionError(f"{name} must be {qualifier}")
        if isinstance(self.wet_ground_speed_multiplier, bool) or not isinstance(
            self.wet_ground_speed_multiplier, (int, float)
        ):
            raise ProjectionError("wet_ground_speed_multiplier must be numeric")
        if not 0 < float(self.wet_ground_speed_multiplier) <= 1:
            raise ProjectionError(
                "wet_ground_speed_multiplier must be within (0, 1]"
            )
        for name, value in (
            ("open_minute", self.open_minute),
            ("close_minute", self.close_minute),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ProjectionError(f"{name} must be an integer")
        if not 0 <= self.open_minute < self.close_minute <= 1440:
            raise ProjectionError(
                "opening minutes must satisfy 0 <= open_minute < close_minute <= 1440"
            )
        if not isinstance(self.provenance, Provenance):
            raise ProjectionError("legacy projection context requires provenance")


def _capability_projection(capability) -> dict[str, Any]:
    return {
        "capability": capability.capability.value,
        "parameters": [
            parameter.to_dict()
            for parameter in sorted(capability.parameters, key=lambda item: item.name)
        ],
        "provenance": capability.provenance.to_dict(),
    }


def _equipment_projection(asset) -> dict[str, Any]:
    return {
        "asset_id": asset.asset_id,
        "asset_type": asset.asset_type.value,
        "manufacturer": asset.manufacturer,
        "model": asset.model,
        "capabilities": [
            _capability_projection(item)
            for item in sorted(asset.capabilities, key=lambda item: item.capability.value)
        ],
        "capacity": [
            item.to_dict() for item in sorted(asset.capacity, key=lambda item: item.name)
        ],
        "location_reference": asset.location_reference,
        "provenance": asset.provenance.to_dict(),
    }


def _robot_projection(robot) -> dict[str, Any]:
    charging = robot.charging_requirements
    return {
        "robot_id": robot.robot_id,
        "capabilities": [
            _capability_projection(item)
            for item in sorted(robot.capabilities, key=lambda item: item.capability.value)
        ],
        "payload_limits": [
            item.to_dict()
            for item in sorted(robot.payload_limits, key=lambda item: item.name)
        ],
        "operating_zones": sorted(robot.operating_zones),
        "charging_requirements": {
            "charging_station_ids": sorted(charging.charging_station_ids),
            "connector_type": charging.connector_type,
            "electrical_requirements": [
                item.to_dict()
                for item in sorted(
                    charging.electrical_requirements, key=lambda item: item.name
                )
            ],
            "provenance": charging.provenance.to_dict(),
        },
        "safety_constraints": [
            item.to_dict()
            for item in sorted(
                robot.safety_constraints, key=lambda item: item.constraint_id
            )
        ],
        "provenance": robot.provenance.to_dict(),
    }


def project_site_config(site: CommissionedSite) -> dict[str, Any]:
    """Project the static, SiteConfig-compatible portion of a manifest.

    The result intentionally excludes simulator seeds, current inventory,
    forecasts, and other runtime-only inputs.  Its versioned shape provides a
    stable integration boundary without pretending those values were
    commissioned facts.
    """

    validate_commissioned_site(site)
    equipment = sorted(site.equipment_assets, key=lambda item: item.asset_id)
    robots = sorted(site.robot_assets, key=lambda item: item.robot_id)
    zones = sorted(
        site.spatial_reference.zone_definitions, key=lambda item: item.zone_id
    )
    return {
        "schema": SITE_CONFIG_PROJECTION_SCHEMA,
        "site_id": site.site_id,
        "deployment_id": site.deployment_id,
        "facility_name": site.facility_name,
        "timezone": site.timezone,
        "coordinate_reference_system": site.coordinate_reference_system.to_dict(),
        "location_metadata": site.location_metadata.to_dict(),
        "zone_ids": [item.zone_id for item in zones],
        "station_ids": [
            item.asset_id
            for item in equipment
            if item.asset_type is AssetType.COLLECTION_STATION
        ],
        "robot_ids": [item.robot_id for item in robots],
        "equipment": [_equipment_projection(item) for item in equipment],
        "robots": [_robot_projection(item) for item in robots],
        "operating_constraints": [
            item.to_dict()
            for item in sorted(
                site.operating_constraints, key=lambda item: item.constraint_id
            )
        ],
        "compatibility": {
            "target": "SiteConfig",
            "status": "commissioned_static_only",
            "constructor_ready": False,
            "constructor_projection": "project_legacy_site_config",
            "reason": (
                "the current constructor also requires explicit simulation and "
                "service context"
            ),
        },
        "provenance": site.provenance.to_dict(),
    }


def _matching_measurement(
    values: list[MeasuredValue],
    *,
    names: frozenset[str],
    units: frozenset[str],
    path: str,
) -> MeasuredValue:
    matches = [item for item in values if item.name in names and item.unit in units]
    if len(matches) != 1:
        raise ProjectionError(
            f"{path} requires exactly one commissioned measurement with "
            f"name in {sorted(names)} and unit in {sorted(units)}; found {len(matches)}"
        )
    return matches[0]


def _positive_int(measurement: MeasuredValue, path: str) -> int:
    value = measurement.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectionError(f"{path} must be numeric")
    converted = int(value)
    if converted != value or converted <= 0:
        raise ProjectionError(f"{path} must be a positive whole number")
    return converted


def project_legacy_site_config(
    site: CommissionedSite, context: LegacySiteConfigContext
) -> dict[str, Any]:
    """Return the exact constructor shape of the current SiteConfig contract.

    Consumer-only values are mandatory in ``context`` and never written back
    into the commissioning manifest.  Equipment values must use the explicit
    v0 measurement vocabulary below; missing or ambiguous facts fail loudly.
    The returned dictionary intentionally contains no extra provenance keys
    because the current consumer constructor rejects them.
    """

    validate_commissioned_site(site)
    if not isinstance(context, LegacySiteConfigContext):
        raise TypeError("context must be LegacySiteConfigContext")

    washers = [
        item for item in site.equipment_assets if item.asset_type is AssetType.WASHER
    ]
    chargers = [
        item
        for item in site.equipment_assets
        if item.asset_type is AssetType.CHARGING_STATION
    ]
    if len(washers) != 1:
        raise ProjectionError(
            f"legacy SiteConfig requires exactly one commissioned washer; found {len(washers)}"
        )
    if len(chargers) != 1:
        raise ProjectionError(
            f"legacy SiteConfig requires exactly one commissioned charging station; found {len(chargers)}"
        )

    washer = washers[0]
    washing_parameters = [
        parameter
        for declaration in washer.capabilities
        if declaration.capability is CapabilityKind.BALL_WASHING
        for parameter in declaration.parameters
    ]
    throughput = _matching_measurement(
        washing_parameters + list(washer.capacity),
        names=frozenset(
            {
                "rated_throughput",
                "throughput_balls_per_minute",
                "washer_throughput_bpm",
            }
        ),
        units=frozenset({"balls/min", "balls/minute"}),
        path=f"equipment_assets.{washer.asset_id}.washer_throughput_bpm",
    )
    batch_size = _matching_measurement(
        list(washer.capacity),
        names=frozenset({"batch_capacity", "batch_size_balls", "washer_batch_size"}),
        units=frozenset({"balls"}),
        path=f"equipment_assets.{washer.asset_id}.washer_batch_size",
    )
    charger_slots = _matching_measurement(
        list(chargers[0].capacity),
        names=frozenset({"charger_slots", "slot_count", "slots"}),
        units=frozenset({"count", "slots"}),
        path=f"equipment_assets.{chargers[0].asset_id}.charger_slots",
    )

    robot_payload_capacity = []
    for robot in sorted(site.robot_assets, key=lambda item: item.robot_id):
        payload = _matching_measurement(
            list(robot.payload_limits),
            names=frozenset({"max_payload", "payload_capacity", "payload_capacity_balls"}),
            units=frozenset({"balls"}),
            path=f"robot_assets.{robot.robot_id}.payload_capacity_balls",
        )
        robot_payload_capacity.append(
            (robot.robot_id, _positive_int(payload, f"robot_assets.{robot.robot_id}.payload"))
        )

    stations = sorted(
        (
            item
            for item in site.equipment_assets
            if item.asset_type is AssetType.COLLECTION_STATION
        ),
        key=lambda item: item.asset_id,
    )
    station_buffer_capacity = []
    for station in stations:
        capacity = _matching_measurement(
            list(station.capacity),
            names=frozenset(
                {"buffer_capacity", "buffer_capacity_balls", "station_buffer_capacity"}
            ),
            units=frozenset({"balls"}),
            path=f"equipment_assets.{station.asset_id}.buffer_capacity_balls",
        )
        station_buffer_capacity.append(
            (station.asset_id, _positive_int(capacity, f"equipment_assets.{station.asset_id}.buffer_capacity"))
        )

    return {
        "scenario_name": context.scenario_name,
        "seed": context.seed,
        "total_balls": context.total_balls,
        "washer_throughput_bpm": float(throughput.value),
        "washer_batch_size": _positive_int(batch_size, "washer_batch_size"),
        "charger_slots": _positive_int(charger_slots, "charger_slots"),
        "staff_capacity": context.staff_capacity,
        "wet_ground_speed_multiplier": float(context.wet_ground_speed_multiplier),
        "open_minute": context.open_minute,
        "close_minute": context.close_minute,
        "forecast_bucket_minutes": context.forecast_bucket_minutes,
        "zone_ids": tuple(
            sorted(item.zone_id for item in site.spatial_reference.zone_definitions)
        ),
        "station_ids": tuple(item.asset_id for item in stations),
        "robot_ids": tuple(
            sorted(item.robot_id for item in site.robot_assets)
        ),
        "robot_payload_capacity": tuple(robot_payload_capacity),
        "station_buffer_capacity": tuple(station_buffer_capacity),
    }


def _local_point(point: SpatialPoint, origin: SpatialPoint) -> dict[str, Any]:
    return {
        "x_m": point.x - origin.x,
        "y_m": point.y - origin.y,
        "z_m": point.z - origin.z,
        "provenance": point.provenance.to_dict(),
    }


def project_digital_twin_layout(site: CommissionedSite) -> dict[str, Any]:
    """Project measured facility geometry into local metric layout metadata.

    No geographic transform is guessed.  Geometry must already use metres and
    east/north/up (or x/y/z) axes; otherwise the projection fails explicitly.
    """

    validate_commissioned_site(site)
    spatial = site.spatial_reference
    crs = spatial.coordinate_system
    axes = tuple(axis.casefold() for axis in crs.axes)
    if crs.horizontal_unit != "m" or crs.vertical_unit != "m":
        raise ProjectionError(
            "digital twin layout projection requires horizontal and vertical metres"
        )
    if axes not in {("east", "north", "up"), ("x", "y", "z")}:
        raise ProjectionError(
            "digital twin layout projection requires east/north/up or x/y/z axes"
        )

    origin = spatial.facility_origin
    geometries = []
    for geometry in sorted(
        spatial.geometry_references, key=lambda item: item.geometry_id
    ):
        geometries.append(
            {
                "geometry_id": geometry.geometry_id,
                "geometry_type": geometry.geometry_type.value,
                "points": [_local_point(point, origin) for point in geometry.points],
                "source_uri": geometry.source_uri,
                "provenance": geometry.provenance.to_dict(),
            }
        )

    return {
        "schema": DIGITAL_TWIN_LAYOUT_SCHEMA,
        "site_id": site.site_id,
        "deployment_id": site.deployment_id,
        "facility_name": site.facility_name,
        "coordinate_frame": {
            "source_crs": crs.to_dict(),
            "units": "m",
            "origin": origin.to_dict(),
        },
        "geometry_references": geometries,
        "zone_definitions": [
            item.to_dict()
            for item in sorted(
                spatial.zone_definitions, key=lambda item: item.zone_id
            )
        ],
        "equipment": [
            _equipment_projection(item)
            for item in sorted(site.equipment_assets, key=lambda item: item.asset_id)
        ],
        # Robots have declared operating coverage and limits, never a current pose.
        "robots": [
            _robot_projection(item)
            for item in sorted(site.robot_assets, key=lambda item: item.robot_id)
        ],
        "provenance": site.provenance.to_dict(),
    }


def _observation_source_type(sensor_type: SensorType) -> str:
    if sensor_type is SensorType.EXTERNAL_SYSTEM:
        return "external_system"
    if sensor_type is SensorType.MANUAL_INPUT:
        return "human"
    return "sensor"


def project_telemetry_adapter_config(site: CommissionedSite) -> dict[str, Any]:
    """Project static sensor bindings for future physical telemetry adapters."""

    validate_commissioned_site(site)
    bindings = []
    for binding in sorted(
        site.sensor_bindings, key=lambda item: (item.channel, item.sensor_id)
    ):
        calibration = binding.calibration.to_dict()
        bindings.append(
            {
                "sensor_id": binding.sensor_id,
                "sensor_type": binding.sensor_type.value,
                "source": binding.source,
                "channel": binding.channel,
                "associated_asset_id": binding.associated_asset_id,
                "units": binding.units,
                "observation_source_type": _observation_source_type(
                    binding.sensor_type
                ),
                "observation_source_id": binding.sensor_id,
                "calibration": calibration,
                "provenance": binding.provenance.to_dict(),
            }
        )
    return {
        "schema": TELEMETRY_ADAPTER_CONFIG_SCHEMA,
        "site_id": site.site_id,
        "deployment_id": site.deployment_id,
        "bindings": bindings,
        "provenance": site.provenance.to_dict(),
    }


def canonical_projection_json(payload: Mapping[str, Any]) -> str:
    """Render a projection deterministically and reject NaN/Infinity."""

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


__all__ = [
    "DIGITAL_TWIN_LAYOUT_SCHEMA",
    "LegacySiteConfigContext",
    "ProjectionError",
    "SITE_CONFIG_PROJECTION_SCHEMA",
    "TELEMETRY_ADAPTER_CONFIG_SCHEMA",
    "canonical_projection_json",
    "project_digital_twin_layout",
    "project_legacy_site_config",
    "project_site_config",
    "project_telemetry_adapter_config",
]
