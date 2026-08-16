"""Read commissioned sensor bindings from their existing projection.

The adapter layer consumes
``nxt_commissioning.project_telemetry_adapter_config(site)`` -- the
projection whose stated purpose is already *"static sensor bindings for
future physical telemetry adapters"*.  Consuming the disposable
projection rather than importing ``nxt_commissioning`` keeps this package
stdlib-pure, keeps the projection one-way, and keeps the manifest the
sole authority for site identity, channels, units, and calibration
identity.

Nothing here re-validates the commissioning vocabulary: a
``CommissionedSite`` cannot be constructed unless every binding's
channel, canonical unit, sensor type, and associated asset already
passed ``validate_commissioned_site``.  Re-checking those relationships
would be a second registry, which the architecture forbids.

The trust boundary that follows is deliberate and worth stating plainly.
This module validates that the projection is *structurally* what it
claims to be -- schema, identities, source address, units, source type,
calibration block, provenance blocks, and no duplicate sensor or channel
-- and fails closed otherwise.  It does **not** re-derive whether a
channel and a unit belong together.  A caller that fabricates a mapping
rather than passing a real projection can therefore build a binding whose
channel/unit pairing commissioning would have rejected; each adapter's
own unit guard refuses to convert it, so the outcome is an explicit
``MISSING`` observation with an ``unsupported_unit`` rejection rather
than a fabricated value.  ``associated_asset_id`` and ``sensor_type`` are
retained for diagnostics and never drive conversion.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .channels import channel_kind
from .contracts import EdgeAdapterError

TELEMETRY_ADAPTER_CONFIG_SCHEMA = "nxt-commissioning/telemetry-adapter-config/v0"

# Observation.calibration_id is a required non-blank string, while a
# commissioned binding whose sensor needs no calibration carries
# calibration_id=None.  This explicit sentinel records that fact without
# ever borrowing another sensor's calibration identity.
NOT_REQUIRED_CALIBRATION_ID = "calibration:not-required"

_SOURCE_TYPES = frozenset({"sensor", "external_system", "human"})
_REQUIRED_BINDING_KEYS = frozenset(
    {
        "sensor_id",
        "sensor_type",
        "source",
        "channel",
        "associated_asset_id",
        "units",
        "observation_source_type",
        "observation_source_id",
        "calibration",
        "provenance",
    }
)


def _text(payload: Mapping[str, object], key: str, path: str) -> str:
    value = payload.get(key)
    if type(value) is not str or not value or value != value.strip():
        raise EdgeAdapterError(f"{path}.{key} must be a non-blank trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class AdapterBinding:
    """One commissioned sensor-to-channel mapping, ready for conversion.

    ``source`` is the commissioned transport address (for example
    ``plc.range-01``).  V0 opens nothing, so nothing reads it during
    conversion; it is retained because it is the field a real reader will
    key on, and retaining it keeps the binding self-describing rather than
    silently dropping a commissioned fact.
    """

    sensor_id: str
    sensor_type: str
    source: str
    channel: str
    associated_asset_id: str
    canonical_unit: str
    source_type: str
    source_id: str
    calibration_id: str
    calibration_required: bool

    def __post_init__(self) -> None:
        # Fails closed when the projection names a channel this repository
        # cannot shape into a canonical value.
        channel_kind(self.channel)


@dataclass(frozen=True, slots=True)
class AdapterBindingSet:
    """Every binding for one commissioned deployment, indexed for lookup."""

    site_id: str
    deployment_id: str
    bindings: tuple[AdapterBinding, ...]

    @classmethod
    def from_projection(cls, payload: Mapping[str, object]) -> "AdapterBindingSet":
        """Build a binding set from a telemetry-adapter-config projection."""
        if not isinstance(payload, Mapping):
            raise EdgeAdapterError("telemetry adapter config must be a mapping")
        schema = payload.get("schema")
        if schema != TELEMETRY_ADAPTER_CONFIG_SCHEMA:
            raise EdgeAdapterError(
                f"unsupported telemetry adapter config schema {schema!r}; "
                f"expected {TELEMETRY_ADAPTER_CONFIG_SCHEMA!r}"
            )
        site_id = _text(payload, "site_id", "telemetry_adapter_config")
        deployment_id = _text(payload, "deployment_id", "telemetry_adapter_config")
        raw_bindings = payload.get("bindings")
        if not isinstance(raw_bindings, (list, tuple)) or not raw_bindings:
            raise EdgeAdapterError(
                "telemetry adapter config must declare at least one binding"
            )

        bindings: list[AdapterBinding] = []
        for index, raw in enumerate(raw_bindings):
            path = f"bindings[{index}]"
            if not isinstance(raw, Mapping):
                raise EdgeAdapterError(f"{path} must be a mapping")
            missing = sorted(_REQUIRED_BINDING_KEYS - set(raw))
            if missing:
                raise EdgeAdapterError(f"{path} is missing keys: {missing}")
            source_type = _text(raw, "observation_source_type", path)
            if source_type not in _SOURCE_TYPES:
                raise EdgeAdapterError(
                    f"{path}.observation_source_type {source_type!r} is not one of "
                    f"{sorted(_SOURCE_TYPES)}"
                )
            # Provenance never reaches an Observation -- the canonical
            # contract has no provenance field, and the manifest remains its
            # only home -- but a projection that carries a blank or malformed
            # provenance block is not the artefact this layer claims to
            # consume, so it is checked rather than silently accepted.
            provenance = raw.get("provenance")
            if not isinstance(provenance, Mapping) or not provenance:
                raise EdgeAdapterError(
                    f"{path}.provenance must be a non-empty mapping"
                )
            calibration = raw.get("calibration")
            if not isinstance(calibration, Mapping):
                raise EdgeAdapterError(f"{path}.calibration must be a mapping")
            calibration_provenance = calibration.get("provenance")
            if not isinstance(calibration_provenance, Mapping) or not (
                calibration_provenance
            ):
                raise EdgeAdapterError(
                    f"{path}.calibration.provenance must be a non-empty mapping"
                )
            status = calibration.get("status")
            if status not in {"calibrated", "not_required"}:
                raise EdgeAdapterError(
                    f"{path}.calibration.status {status!r} is not a known "
                    "commissioning calibration status"
                )
            calibration_required = status == "calibrated"
            if calibration_required:
                calibration_id = _text(
                    calibration, "calibration_id", f"{path}.calibration"
                )
            else:
                if calibration.get("calibration_id") is not None:
                    raise EdgeAdapterError(
                        f"{path}.calibration declares not_required with a "
                        "calibration identity"
                    )
                calibration_id = NOT_REQUIRED_CALIBRATION_ID
            bindings.append(
                AdapterBinding(
                    sensor_id=_text(raw, "sensor_id", path),
                    sensor_type=_text(raw, "sensor_type", path),
                    source=_text(raw, "source", path),
                    channel=_text(raw, "channel", path),
                    associated_asset_id=_text(raw, "associated_asset_id", path),
                    canonical_unit=_text(raw, "units", path),
                    source_type=source_type,
                    source_id=_text(raw, "observation_source_id", path),
                    calibration_id=calibration_id,
                    calibration_required=calibration_required,
                )
            )

        sensor_ids = [item.sensor_id for item in bindings]
        if len(set(sensor_ids)) != len(sensor_ids):
            raise EdgeAdapterError("telemetry adapter config repeats a sensor_id")
        channels = [item.channel for item in bindings]
        if len(set(channels)) != len(channels):
            raise EdgeAdapterError("telemetry adapter config repeats a channel")
        return cls(
            site_id=site_id,
            deployment_id=deployment_id,
            bindings=tuple(sorted(bindings, key=lambda item: item.sensor_id)),
        )

    def by_sensor_id(self, sensor_id: str) -> AdapterBinding | None:
        for binding in self.bindings:
            if binding.sensor_id == sensor_id:
                return binding
        return None

    def by_channel(self, channel: str) -> AdapterBinding | None:
        for binding in self.bindings:
            if binding.channel == channel:
                return binding
        return None

    @property
    def channels(self) -> tuple[str, ...]:
        return tuple(sorted(binding.channel for binding in self.bindings))


__all__ = [
    "NOT_REQUIRED_CALIBRATION_ID",
    "TELEMETRY_ADAPTER_CONFIG_SCHEMA",
    "AdapterBinding",
    "AdapterBindingSet",
]
