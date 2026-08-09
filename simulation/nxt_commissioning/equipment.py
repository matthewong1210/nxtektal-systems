"""Immutable commissioned equipment and robot asset contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeVar

from .provenance import MeasuredValue, Provenance


class AssetType(str, Enum):
    WASHER = "washer"
    DISPENSER = "dispenser"
    CHARGING_STATION = "charging_station"
    COLLECTION_STATION = "collection_station"
    STORAGE_AREA = "storage_area"
    OTHER = "other"


class CapabilityKind(str, Enum):
    BALL_WASHING = "ball_washing"
    BALL_DISPENSING = "ball_dispensing"
    ROBOT_CHARGING = "robot_charging"
    STORAGE = "storage"
    DOCKING = "docking"
    MATERIAL_TRANSPORT = "material_transport"
    AUTONOMOUS_NAVIGATION = "autonomous_navigation"
    EMERGENCY_STOP = "emergency_stop"
    INVENTORY_MEASUREMENT = "inventory_measurement"


_ALL_CAPABILITIES = frozenset(CapabilityKind)

# Aggregate validation consumes these immutable compatibility declarations.
# ``OTHER`` remains an explicit extension point but still requires every
# capability to use the commissioned vocabulary above.
EQUIPMENT_CAPABILITY_COMPATIBILITY: Mapping[
    AssetType, frozenset[CapabilityKind]
] = MappingProxyType(
    {
        AssetType.WASHER: frozenset(
            {
                CapabilityKind.BALL_WASHING,
                CapabilityKind.DOCKING,
                CapabilityKind.EMERGENCY_STOP,
                CapabilityKind.INVENTORY_MEASUREMENT,
            }
        ),
        AssetType.DISPENSER: frozenset(
            {
                CapabilityKind.BALL_DISPENSING,
                CapabilityKind.STORAGE,
                CapabilityKind.DOCKING,
                CapabilityKind.EMERGENCY_STOP,
                CapabilityKind.INVENTORY_MEASUREMENT,
            }
        ),
        AssetType.CHARGING_STATION: frozenset(
            {
                CapabilityKind.ROBOT_CHARGING,
                CapabilityKind.DOCKING,
                CapabilityKind.EMERGENCY_STOP,
            }
        ),
        AssetType.COLLECTION_STATION: frozenset(
            {
                CapabilityKind.STORAGE,
                CapabilityKind.DOCKING,
                CapabilityKind.MATERIAL_TRANSPORT,
                CapabilityKind.EMERGENCY_STOP,
                CapabilityKind.INVENTORY_MEASUREMENT,
            }
        ),
        AssetType.STORAGE_AREA: frozenset(
            {
                CapabilityKind.STORAGE,
                CapabilityKind.EMERGENCY_STOP,
                CapabilityKind.INVENTORY_MEASUREMENT,
            }
        ),
        AssetType.OTHER: _ALL_CAPABILITIES,
    }
)

ROBOT_CAPABILITY_COMPATIBILITY: frozenset[CapabilityKind] = frozenset(
    {
        CapabilityKind.DOCKING,
        CapabilityKind.MATERIAL_TRANSPORT,
        CapabilityKind.AUTONOMOUS_NAVIGATION,
        CapabilityKind.EMERGENCY_STOP,
        CapabilityKind.INVENTORY_MEASUREMENT,
    }
)


def _mapping_with_exact_keys(
    data: object,
    expected: frozenset[str],
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError(f"{label} keys must be strings")
    keys = set(data)
    missing = sorted(expected - keys)
    unknown = sorted(keys - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing keys: {missing}")
        if unknown:
            details.append(f"unknown keys: {unknown}")
        raise ValueError(f"invalid {label}: {'; '.join(details)}")
    return data


def _nonempty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def _optional_nonempty_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _nonempty_string(value, field_name)


T = TypeVar("T")


def _typed_tuple(value: object, item_type: type[T], field_name: str) -> tuple[T, ...]:
    # Sets would discard duplicates before aggregate validation can report
    # them, while generic iterators make repeatable serialization harder to
    # reason about. JSON arrays and already-normalized tuples are the only
    # accepted collection forms.
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list or tuple of {item_type.__name__}")
    items = tuple(value)
    for index, item in enumerate(items):
        if not isinstance(item, item_type):
            raise ValueError(
                f"{field_name}[{index}] must be a {item_type.__name__}"
            )
    return items


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    items = _typed_tuple(value, str, field_name)
    for index, item in enumerate(items):
        _nonempty_string(item, f"{field_name}[{index}]")
    return items


def _enum_value(enum_type: type[T], value: object, field_name: str) -> T:
    raw = _nonempty_string(value, field_name)
    try:
        return enum_type(raw)
    except ValueError as exc:
        allowed = sorted(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise ValueError(
            f"unknown {field_name} {raw!r}; expected one of {allowed}"
        ) from exc


def _records_from_sequence(
    value: object,
    record_type: type[T],
    field_name: str,
) -> tuple[T, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    records = []
    for index, item in enumerate(value):
        try:
            records.append(record_type.from_dict(item))  # type: ignore[attr-defined]
        except ValueError as exc:
            raise ValueError(f"invalid {field_name}[{index}]: {exc}") from exc
    return tuple(records)


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    capability: CapabilityKind
    parameters: tuple[MeasuredValue, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityKind):
            raise ValueError("capability must be a CapabilityKind")
        object.__setattr__(
            self,
            "parameters",
            tuple(
                sorted(
                    _typed_tuple(self.parameters, MeasuredValue, "parameters"),
                    key=lambda item: item.name,
                )
            ),
        )
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability.value,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CapabilityDeclaration":
        payload = _mapping_with_exact_keys(
            data,
            frozenset({"capability", "parameters", "provenance"}),
            "CapabilityDeclaration",
        )
        return cls(
            capability=_enum_value(
                CapabilityKind, payload["capability"], "capability"
            ),
            parameters=_records_from_sequence(
                payload["parameters"], MeasuredValue, "parameters"
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class ChargingRequirements:
    charging_station_ids: tuple[str, ...]
    connector_type: str
    electrical_requirements: tuple[MeasuredValue, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "charging_station_ids",
            tuple(sorted(_string_tuple(self.charging_station_ids, "charging_station_ids"))),
        )
        _nonempty_string(self.connector_type, "connector_type")
        object.__setattr__(
            self,
            "electrical_requirements",
            tuple(
                sorted(
                    _typed_tuple(
                        self.electrical_requirements,
                        MeasuredValue,
                        "electrical_requirements",
                    ),
                    key=lambda item: item.name,
                )
            ),
        )
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "charging_station_ids": list(self.charging_station_ids),
            "connector_type": self.connector_type,
            "electrical_requirements": [
                requirement.to_dict() for requirement in self.electrical_requirements
            ],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "ChargingRequirements":
        payload = _mapping_with_exact_keys(
            data,
            frozenset(
                {
                    "charging_station_ids",
                    "connector_type",
                    "electrical_requirements",
                    "provenance",
                }
            ),
            "ChargingRequirements",
        )
        station_ids = payload["charging_station_ids"]
        if not isinstance(station_ids, (list, tuple)):
            raise ValueError("charging_station_ids must be a list")
        return cls(
            charging_station_ids=_string_tuple(
                station_ids, "charging_station_ids"
            ),
            connector_type=_nonempty_string(
                payload["connector_type"], "connector_type"
            ),
            electrical_requirements=_records_from_sequence(
                payload["electrical_requirements"],
                MeasuredValue,
                "electrical_requirements",
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class SafetyConstraint:
    constraint_id: str
    description: str
    limit: MeasuredValue | None
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty_string(self.constraint_id, "constraint_id")
        _nonempty_string(self.description, "description")
        if self.limit is not None and not isinstance(self.limit, MeasuredValue):
            raise ValueError("limit must be a MeasuredValue or None")
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "constraint_id": self.constraint_id,
            "description": self.description,
            "limit": self.limit.to_dict() if self.limit is not None else None,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "SafetyConstraint":
        payload = _mapping_with_exact_keys(
            data,
            frozenset({"constraint_id", "description", "limit", "provenance"}),
            "SafetyConstraint",
        )
        limit_payload = payload["limit"]
        return cls(
            constraint_id=_nonempty_string(
                payload["constraint_id"], "constraint_id"
            ),
            description=_nonempty_string(payload["description"], "description"),
            limit=(
                None
                if limit_payload is None
                else MeasuredValue.from_dict(limit_payload)
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class EquipmentAsset:
    asset_id: str
    asset_type: AssetType
    manufacturer: str | None
    model: str | None
    capabilities: tuple[CapabilityDeclaration, ...]
    capacity: tuple[MeasuredValue, ...]
    location_reference: str
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty_string(self.asset_id, "asset_id")
        if not isinstance(self.asset_type, AssetType):
            raise ValueError("asset_type must be an AssetType")
        _optional_nonempty_string(self.manufacturer, "manufacturer")
        _optional_nonempty_string(self.model, "model")
        object.__setattr__(
            self,
            "capabilities",
            tuple(
                sorted(
                    _typed_tuple(
                        self.capabilities, CapabilityDeclaration, "capabilities"
                    ),
                    key=lambda item: item.capability.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "capacity",
            tuple(
                sorted(
                    _typed_tuple(self.capacity, MeasuredValue, "capacity"),
                    key=lambda item: item.name,
                )
            ),
        )
        _nonempty_string(self.location_reference, "location_reference")
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "asset_type": self.asset_type.value,
            "manufacturer": self.manufacturer,
            "model": self.model,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "capacity": [quantity.to_dict() for quantity in self.capacity],
            "location_reference": self.location_reference,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "EquipmentAsset":
        payload = _mapping_with_exact_keys(
            data,
            frozenset(
                {
                    "asset_id",
                    "asset_type",
                    "manufacturer",
                    "model",
                    "capabilities",
                    "capacity",
                    "location_reference",
                    "provenance",
                }
            ),
            "EquipmentAsset",
        )
        return cls(
            asset_id=_nonempty_string(payload["asset_id"], "asset_id"),
            asset_type=_enum_value(AssetType, payload["asset_type"], "asset_type"),
            manufacturer=_optional_nonempty_string(
                payload["manufacturer"], "manufacturer"
            ),
            model=_optional_nonempty_string(payload["model"], "model"),
            capabilities=_records_from_sequence(
                payload["capabilities"], CapabilityDeclaration, "capabilities"
            ),
            capacity=_records_from_sequence(
                payload["capacity"], MeasuredValue, "capacity"
            ),
            location_reference=_nonempty_string(
                payload["location_reference"], "location_reference"
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class RobotAsset:
    robot_id: str
    capabilities: tuple[CapabilityDeclaration, ...]
    payload_limits: tuple[MeasuredValue, ...]
    operating_zones: tuple[str, ...]
    charging_requirements: ChargingRequirements
    safety_constraints: tuple[SafetyConstraint, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        _nonempty_string(self.robot_id, "robot_id")
        object.__setattr__(
            self,
            "capabilities",
            tuple(
                sorted(
                    _typed_tuple(
                        self.capabilities, CapabilityDeclaration, "capabilities"
                    ),
                    key=lambda item: item.capability.value,
                )
            ),
        )
        object.__setattr__(
            self,
            "payload_limits",
            tuple(
                sorted(
                    _typed_tuple(self.payload_limits, MeasuredValue, "payload_limits"),
                    key=lambda item: item.name,
                )
            ),
        )
        object.__setattr__(
            self,
            "operating_zones",
            tuple(sorted(_string_tuple(self.operating_zones, "operating_zones"))),
        )
        if not isinstance(self.charging_requirements, ChargingRequirements):
            raise ValueError(
                "charging_requirements must be a ChargingRequirements"
            )
        object.__setattr__(
            self,
            "safety_constraints",
            tuple(
                sorted(
                    _typed_tuple(
                        self.safety_constraints, SafetyConstraint, "safety_constraints"
                    ),
                    key=lambda item: item.constraint_id,
                )
            ),
        )
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance must be a Provenance")

    def to_dict(self) -> dict[str, object]:
        return {
            "robot_id": self.robot_id,
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "payload_limits": [limit.to_dict() for limit in self.payload_limits],
            "operating_zones": list(self.operating_zones),
            "charging_requirements": self.charging_requirements.to_dict(),
            "safety_constraints": [
                constraint.to_dict() for constraint in self.safety_constraints
            ],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "RobotAsset":
        payload = _mapping_with_exact_keys(
            data,
            frozenset(
                {
                    "robot_id",
                    "capabilities",
                    "payload_limits",
                    "operating_zones",
                    "charging_requirements",
                    "safety_constraints",
                    "provenance",
                }
            ),
            "RobotAsset",
        )
        operating_zones = payload["operating_zones"]
        if not isinstance(operating_zones, (list, tuple)):
            raise ValueError("operating_zones must be a list")
        return cls(
            robot_id=_nonempty_string(payload["robot_id"], "robot_id"),
            capabilities=_records_from_sequence(
                payload["capabilities"], CapabilityDeclaration, "capabilities"
            ),
            payload_limits=_records_from_sequence(
                payload["payload_limits"], MeasuredValue, "payload_limits"
            ),
            operating_zones=_string_tuple(operating_zones, "operating_zones"),
            charging_requirements=ChargingRequirements.from_dict(
                payload["charging_requirements"]
            ),
            safety_constraints=_records_from_sequence(
                payload["safety_constraints"], SafetyConstraint, "safety_constraints"
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )


__all__ = [
    "AssetType",
    "CapabilityDeclaration",
    "CapabilityKind",
    "ChargingRequirements",
    "EQUIPMENT_CAPABILITY_COMPATIBILITY",
    "EquipmentAsset",
    "ROBOT_CAPABILITY_COMPATIBILITY",
    "RobotAsset",
    "SafetyConstraint",
]
