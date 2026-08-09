"""Authoritative, immutable commissioning manifest contracts.

Commissioning records static facts about a deployed facility.  These
contracts deliberately contain no observations, task state, inventory state,
or other values that change while the facility operates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .equipment import EquipmentAsset, RobotAsset
from .provenance import MeasuredValue, Provenance
from .sensors import SensorBinding
from .spatial import CoordinateReferenceSystem, SpatialReference

COMMISSIONING_SCHEMA = "nxt-commissioning/manifest/v0"


def _require_exact_keys(
    payload: Mapping[str, Any], expected: set[str], contract: str
) -> None:
    if not isinstance(payload, Mapping):
        raise TypeError(f"{contract} must be an object")
    actual = set(payload)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing keys: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown keys: {', '.join(unknown)}")
        raise ValueError(f"invalid {contract}: {'; '.join(details)}")


@dataclass(frozen=True, slots=True)
class LocationMetadata:
    """Postal or operator-facing location metadata for the site.

    Every optional field must be supplied explicitly as a string or ``None``;
    the contract never guesses an address from coordinates.
    """

    address_lines: tuple[str, ...]
    locality: str | None
    region: str | None
    postal_code: str | None
    country_code: str
    site_reference: str | None
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.address_lines, (list, tuple)):
            raise TypeError("address_lines must be a list or tuple of strings")
        if any(not isinstance(line, str) for line in self.address_lines):
            raise TypeError("address_lines must contain only strings")
        object.__setattr__(self, "address_lines", tuple(self.address_lines))

    def to_dict(self) -> dict[str, Any]:
        return {
            "address_lines": list(self.address_lines),
            "locality": self.locality,
            "region": self.region,
            "postal_code": self.postal_code,
            "country_code": self.country_code,
            "site_reference": self.site_reference,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LocationMetadata":
        _require_exact_keys(
            payload,
            {
                "address_lines",
                "locality",
                "region",
                "postal_code",
                "country_code",
                "site_reference",
                "provenance",
            },
            "LocationMetadata",
        )
        address_lines = payload["address_lines"]
        if not isinstance(address_lines, list):
            raise TypeError("LocationMetadata.address_lines must be an array")
        return cls(
            address_lines=tuple(address_lines),
            locality=payload["locality"],
            region=payload["region"],
            postal_code=payload["postal_code"],
            country_code=payload["country_code"],
            site_reference=payload["site_reference"],
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class OperatingConstraint:
    """A declared, static operating boundary.

    ``parameters`` holds explicit measured limits (for example opening and
    closing minute, clearance, or a temperature range).  ``safety_relevant``
    is intentionally required rather than defaulted.
    """

    constraint_id: str
    category: str
    description: str
    applies_to: tuple[str, ...]
    parameters: tuple[MeasuredValue, ...]
    safety_relevant: bool
    provenance: Provenance

    def __post_init__(self) -> None:
        if not isinstance(self.applies_to, (list, tuple)):
            raise TypeError("applies_to must be a list or tuple of identifiers")
        if any(not isinstance(target, str) for target in self.applies_to):
            raise TypeError("applies_to must contain only strings")
        if not isinstance(self.parameters, (list, tuple)):
            raise TypeError("parameters must be a list or tuple of MeasuredValue")
        if any(not isinstance(item, MeasuredValue) for item in self.parameters):
            raise TypeError("parameters must contain only MeasuredValue records")
        object.__setattr__(self, "applies_to", tuple(sorted(tuple(self.applies_to))))
        object.__setattr__(
            self,
            "parameters",
            tuple(sorted(tuple(self.parameters), key=lambda item: item.name)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint_id": self.constraint_id,
            "category": self.category,
            "description": self.description,
            "applies_to": list(self.applies_to),
            "parameters": [
                value.to_dict()
                for value in sorted(self.parameters, key=lambda item: item.name)
            ],
            "safety_relevant": self.safety_relevant,
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OperatingConstraint":
        _require_exact_keys(
            payload,
            {
                "constraint_id",
                "category",
                "description",
                "applies_to",
                "parameters",
                "safety_relevant",
                "provenance",
            },
            "OperatingConstraint",
        )
        applies_to = payload["applies_to"]
        parameters = payload["parameters"]
        if not isinstance(applies_to, list):
            raise TypeError("OperatingConstraint.applies_to must be an array")
        if not isinstance(parameters, list):
            raise TypeError("OperatingConstraint.parameters must be an array")
        if not isinstance(payload["safety_relevant"], bool):
            raise TypeError("OperatingConstraint.safety_relevant must be a boolean")
        return cls(
            constraint_id=payload["constraint_id"],
            category=payload["category"],
            description=payload["description"],
            applies_to=tuple(applies_to),
            parameters=tuple(MeasuredValue.from_dict(item) for item in parameters),
            safety_relevant=payload["safety_relevant"],
            provenance=Provenance.from_dict(payload["provenance"]),
        )


@dataclass(frozen=True, slots=True)
class CommissionedSite:
    """One immutable deployment manifest for a physical facility."""

    site_id: str
    deployment_id: str
    facility_name: str
    timezone: str
    location_metadata: LocationMetadata
    operating_constraints: tuple[OperatingConstraint, ...]
    spatial_reference: SpatialReference
    equipment_assets: tuple[EquipmentAsset, ...]
    robot_assets: tuple[RobotAsset, ...]
    sensor_bindings: tuple[SensorBinding, ...]
    provenance: Provenance

    def __post_init__(self) -> None:
        # Tuple conversion closes the common frozen-dataclass loophole where a
        # caller supplies a mutable list.  Ordering is canonicalized only after
        # preserving all records, so duplicate detection remains effective.
        object.__setattr__(
            self,
            "operating_constraints",
            tuple(sorted(tuple(self.operating_constraints), key=lambda item: item.constraint_id)),
        )
        object.__setattr__(
            self,
            "equipment_assets",
            tuple(sorted(tuple(self.equipment_assets), key=lambda item: item.asset_id)),
        )
        object.__setattr__(
            self,
            "robot_assets",
            tuple(sorted(tuple(self.robot_assets), key=lambda item: item.robot_id)),
        )
        object.__setattr__(
            self,
            "sensor_bindings",
            tuple(
                sorted(
                    tuple(self.sensor_bindings),
                    key=lambda item: (item.channel, item.sensor_id),
                )
            ),
        )

        # Imported lazily to keep the module dependency direction explicit:
        # leaf contracts -> aggregate contract -> aggregate validation.
        from .validation import validate_commissioned_site

        validate_commissioned_site(self)

    @property
    def coordinate_reference_system(self) -> CoordinateReferenceSystem:
        """The site's CRS, exposed directly without duplicating its authority."""

        return self.spatial_reference.coordinate_system

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": COMMISSIONING_SCHEMA,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "facility_name": self.facility_name,
            "timezone": self.timezone,
            "location_metadata": self.location_metadata.to_dict(),
            "operating_constraints": [
                item.to_dict() for item in self.operating_constraints
            ],
            "spatial_reference": self.spatial_reference.to_dict(),
            "equipment_assets": [item.to_dict() for item in self.equipment_assets],
            "robot_assets": [item.to_dict() for item in self.robot_assets],
            "sensor_bindings": [item.to_dict() for item in self.sensor_bindings],
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CommissionedSite":
        _require_exact_keys(
            payload,
            {
                "schema",
                "site_id",
                "deployment_id",
                "facility_name",
                "timezone",
                "location_metadata",
                "operating_constraints",
                "spatial_reference",
                "equipment_assets",
                "robot_assets",
                "sensor_bindings",
                "provenance",
            },
            "CommissionedSite",
        )
        if payload["schema"] != COMMISSIONING_SCHEMA:
            raise ValueError(
                f"unsupported commissioning schema {payload['schema']!r}; "
                f"expected {COMMISSIONING_SCHEMA!r}"
            )
        sequence_keys = (
            "operating_constraints",
            "equipment_assets",
            "robot_assets",
            "sensor_bindings",
        )
        for key in sequence_keys:
            if not isinstance(payload[key], list):
                raise TypeError(f"CommissionedSite.{key} must be an array")
        return cls(
            site_id=payload["site_id"],
            deployment_id=payload["deployment_id"],
            facility_name=payload["facility_name"],
            timezone=payload["timezone"],
            location_metadata=LocationMetadata.from_dict(payload["location_metadata"]),
            operating_constraints=tuple(
                OperatingConstraint.from_dict(item)
                for item in payload["operating_constraints"]
            ),
            spatial_reference=SpatialReference.from_dict(payload["spatial_reference"]),
            equipment_assets=tuple(
                EquipmentAsset.from_dict(item) for item in payload["equipment_assets"]
            ),
            robot_assets=tuple(
                RobotAsset.from_dict(item) for item in payload["robot_assets"]
            ),
            sensor_bindings=tuple(
                SensorBinding.from_dict(item) for item in payload["sensor_bindings"]
            ),
            provenance=Provenance.from_dict(payload["provenance"]),
        )
