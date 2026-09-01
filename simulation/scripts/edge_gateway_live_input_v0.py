"""Edge Gateway Live Input V0 deployment composition root.

This module is intentionally not a shipped ``nxt_*`` package.  It owns the
local MQTT/wire/process boundary and composes existing public contracts.  It
does not define a second Observation, FacilityState, assembler, policy, state
store, command surface, or physical execution path.

Two modes are deliberately distinct:

``LOAD_CELL_DIAGNOSTIC``
    MQTT load-cell evidence -> existing raw sample contracts -> existing Edge
    Observation Adapter Kit -> canonical Observation + EdgeAdapterReport.  No
    complete FacilityState is claimed.

``HYBRID_RUNTIME_REHEARSAL``
    One validated MQTT load-cell channel overlays the Pilot Course A fixture.
    Every remaining observation and upstream reference is explicitly
    simulation-labelled before the existing Site/Agent Runtime lifecycle runs.

The service exposes read-only health/readiness/status endpoints.  It has no
robot, actuator, ROS, navigation, register-write, e-stop mutation, cloud-sync,
OTA, SQLite, or LLM integration.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import re
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

from nxt_agent_runtime import (  # noqa: E402
    AgentRuntime,
    AgentRuntimeError,
    CycleKind,
    EvaluationJournal,
    JsonEvaluationCheckpointStore,
    JsonlSnapshotPublisher,
    SourceExhausted,
)
from nxt_commissioning import CommissionedSite  # noqa: E402
from nxt_edge_observation import (  # noqa: E402
    AdapterBindingSet,
    ConversionResult,
    EdgeAdapterReport,
    EdgeObservationAdapterKit,
    LoadCellSample,
    RawSampleBatch,
    RawSampleTiming,
)
from nxt_pilot_ops.ledger import JsonlEventLedger  # noqa: E402
from nxt_site_runtime.checkpoints import JsonCheckpointStore  # noqa: E402
from nxt_site_runtime.ports import SequencedObservationFrame  # noqa: E402
from nxt_telemetry.observations import (  # noqa: E402
    Observation,
    ObservationFrame,
    ObservationStatus,
    SourceType,
)

from scripts.pilot_course_a_edge_fixture import (  # noqa: E402
    PILOT_CYCLES,
    SENSOR_DISPENSER_COUNT,
    SENSOR_DISPENSER_SENSED,
    CycleSpec,
    adapter_kit,
    commissioned_site,
    facility_system_observations,
    raw_batch,
    site_config,
    upstream_inputs,
    upstream_reference,
)


CONFIG_SCHEMA = "nxt-edge-gateway/config/v0"
WIRE_SCHEMA = "nxt.edge.load-cell.raw/v1"
HEALTH_SCHEMA = "nxt-edge-gateway/health/v0"
STATUS_SCHEMA = "nxt-edge-gateway/status/v0"
HYBRID_DISPENSER_SENSOR_IDS = frozenset(
    {SENSOR_DISPENSER_COUNT, SENSOR_DISPENSER_SENSED}
)

# Deployment-boundary resource limits. They are deliberately script-owned:
# no core observation/runtime package learns about MQTT packet sizing or V0
# replay-memory policy.
MAX_WIRE_PAYLOAD_BYTES = 65_536
MAX_ERROR_DETAIL_CHARS = 1_024
MAX_SEQUENCE_REPLAY_WINDOW = 4_096
MAX_RETIRED_BOOTS_PER_DEVICE = 64
REDELIVERY_BACKOFF_S = 1.0
_TRUNCATION_MARKER = "...[detail truncated]"

DIAGNOSTIC_DISCLAIMER = (
    "LOAD-CELL DIAGNOSTIC ONLY — CANONICAL OBSERVATION EVIDENCE, NOT A "
    "COMPLETE FACILITY STATE OR LIVE CUSTOMER DEPLOYMENT"
)
HYBRID_DISCLAIMER = (
    "HYBRID RUNTIME REHEARSAL — MQTT LOAD-CELL CHANNEL ONLY; ALL OTHER "
    "INPUTS ARE SIMULATION — NOT LIVE CUSTOMER DATA"
)

_WIRE_FIELDS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "gateway_id",
        "device_id",
        "sensor_id",
        "boot_id",
        "device_sequence",
        "sampled_at_utc",
        "published_at_utc",
        "raw_value",
        "raw_unit",
        "device_status",
        "calibration_id",
        "diagnostic_code",
    }
)
_CONFIG_FIELDS = frozenset(
    {
        "schema",
        "mode",
        "site_id",
        "deployment_id",
        "gateway_id",
        "broker",
        "devices",
        "status",
        "evidence_dir",
        "fixture_cycle_index",
    }
)
_BROKER_FIELDS = frozenset(
    {"host", "port", "keepalive_s", "qos", "client_id"}
)
_DEVICE_FIELDS = frozenset({"device_id", "sensor_ids"})
_STATUS_FIELDS = frozenset({"host", "port"})
_UTC_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$"
)


class GatewayMode(StrEnum):
    LOAD_CELL_DIAGNOSTIC = "LOAD_CELL_DIAGNOSTIC"
    HYBRID_RUNTIME_REHEARSAL = "HYBRID_RUNTIME_REHEARSAL"


class ProcessingKind(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


class SequenceDisposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"


class GatewayErrorCode(StrEnum):
    MALFORMED_JSON = "malformed_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    SCHEMA_MISMATCH = "schema_mismatch"
    MISSING_FIELD = "missing_field"
    UNKNOWN_FIELD = "unknown_field"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_NUMBER = "invalid_number"
    INVALID_TIMESTAMP = "invalid_timestamp"
    TIMESTAMP_ORDER = "timestamp_order"
    INVALID_TOPIC = "invalid_topic"
    IDENTITY_MISMATCH = "identity_mismatch"
    MIXED_OPERATING_DAY = "mixed_operating_day"
    AMBIGUOUS_LOCAL_TIME = "ambiguous_local_time"
    CONFLICTING_REPLAY = "conflicting_replay"
    OUT_OF_ORDER_SEQUENCE = "out_of_order_sequence"
    RETIRED_BOOT = "retired_boot"
    INVALID_CONFIG = "invalid_config"
    OPERATING_DAY_ROLLOVER = "operating_day_rollover"
    SOURCE_PROTOCOL = "source_protocol"
    REPLAY_CAPACITY_EXCEEDED = "replay_capacity_exceeded"
    RUNTIME_RETRY_REQUIRED = "runtime_retry_required"
    INVALID_MQTT_DELIVERY = "invalid_mqtt_delivery"
    UNEXPECTED_PROCESSING_FAILURE = "unexpected_processing_failure"
    MQTT_UNAVAILABLE = "mqtt_unavailable"


class GatewayError(ValueError):
    """Typed fail-closed error at the deployment composition boundary."""

    def __init__(self, code: GatewayErrorCode, detail: str) -> None:
        if not isinstance(code, GatewayErrorCode):
            raise TypeError("code must be GatewayErrorCode")
        if type(detail) is not str or not detail:
            raise TypeError("detail must be a non-blank string")
        if len(detail) > MAX_ERROR_DETAIL_CHARS:
            detail = (
                detail[: MAX_ERROR_DETAIL_CHARS - len(_TRUNCATION_MARKER)]
                + _TRUNCATION_MARKER
            )
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")


def _config_error(detail: str) -> GatewayError:
    return GatewayError(GatewayErrorCode.INVALID_CONFIG, detail)


def _bounded_detail(detail: str) -> str:
    if type(detail) is not str:
        raise TypeError("detail must be a string")
    if len(detail) <= MAX_ERROR_DETAIL_CHARS:
        return detail
    return (
        detail[: MAX_ERROR_DETAIL_CHARS - len(_TRUNCATION_MARKER)]
        + _TRUNCATION_MARKER
    )


def _exact_keys(
    payload: Mapping[str, object], expected: frozenset[str], path: str
) -> None:
    if not isinstance(payload, Mapping):
        raise _config_error(f"{path} must be a mapping")
    if any(type(key) is not str for key in payload):
        raise _config_error(f"{path} keys must be strings")
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise _config_error(
            f"{path} keys must be exactly {sorted(expected)}; "
            f"missing={missing}, extra={extra}"
        )


def _text(value: object, path: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _config_error(f"{path} must be a non-blank trimmed string")
    if any(character in value for character in ("/", "+", "#")):
        raise _config_error(f"{path} contains a reserved routing character")
    return value


def _host(value: object, path: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise _config_error(f"{path} must be a non-blank trimmed string")
    return value


def _integer(
    value: object,
    path: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _config_error(f"{path} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        ceiling = "" if maximum is None else f" and <= {maximum}"
        raise _config_error(f"{path} must be >= {minimum}{ceiling}")
    return value


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    host: str
    port: int
    keepalive_s: int
    qos: int
    client_id: str


@dataclass(frozen=True, slots=True)
class DeviceRoute:
    device_id: str
    sensor_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatusEndpointConfig:
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class GatewayConfig:
    schema: str
    mode: GatewayMode
    site_id: str
    deployment_id: str
    gateway_id: str
    broker: BrokerConfig
    devices: tuple[DeviceRoute, ...]
    status: StatusEndpointConfig
    evidence_dir: Path
    fixture_cycle_index: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "GatewayConfig":
        if not isinstance(payload, Mapping):
            raise _config_error("gateway configuration must be a mapping")
        if "edge_gateway" in payload:
            if set(payload) != {"edge_gateway"}:
                raise _config_error(
                    "wrapped configuration must contain only edge_gateway"
                )
            inner = payload["edge_gateway"]
            if not isinstance(inner, Mapping):
                raise _config_error("edge_gateway must be a mapping")
        else:
            inner = payload
        _exact_keys(inner, _CONFIG_FIELDS, "edge_gateway")

        if inner["schema"] != CONFIG_SCHEMA:
            raise _config_error(
                f"unsupported schema {inner['schema']!r}; expected {CONFIG_SCHEMA!r}"
            )
        try:
            mode = GatewayMode(inner["mode"])
        except (TypeError, ValueError) as exc:
            raise _config_error(
                "mode must be LOAD_CELL_DIAGNOSTIC or "
                "HYBRID_RUNTIME_REHEARSAL"
            ) from exc

        broker_payload = inner["broker"]
        if not isinstance(broker_payload, Mapping):
            raise _config_error("edge_gateway.broker must be a mapping")
        _exact_keys(broker_payload, _BROKER_FIELDS, "edge_gateway.broker")
        qos = _integer(
            broker_payload["qos"], "edge_gateway.broker.qos", minimum=1, maximum=1
        )
        broker = BrokerConfig(
            host=_host(broker_payload["host"], "edge_gateway.broker.host"),
            port=_integer(
                broker_payload["port"],
                "edge_gateway.broker.port",
                minimum=1,
                maximum=65_535,
            ),
            keepalive_s=_integer(
                broker_payload["keepalive_s"],
                "edge_gateway.broker.keepalive_s",
                minimum=1,
                maximum=65_535,
            ),
            qos=qos,
            client_id=_text(
                broker_payload["client_id"], "edge_gateway.broker.client_id"
            ),
        )

        raw_devices = inner["devices"]
        if not isinstance(raw_devices, list) or not raw_devices:
            raise _config_error("edge_gateway.devices must be a non-empty list")
        devices: list[DeviceRoute] = []
        all_sensors: set[str] = set()
        for index, raw_device in enumerate(raw_devices):
            if not isinstance(raw_device, Mapping):
                raise _config_error(f"edge_gateway.devices[{index}] must be a mapping")
            _exact_keys(
                raw_device, _DEVICE_FIELDS, f"edge_gateway.devices[{index}]"
            )
            device_id = _text(
                raw_device["device_id"],
                f"edge_gateway.devices[{index}].device_id",
            )
            raw_sensors = raw_device["sensor_ids"]
            if not isinstance(raw_sensors, list) or not raw_sensors:
                raise _config_error(
                    f"edge_gateway.devices[{index}].sensor_ids must be a "
                    "non-empty list"
                )
            sensor_ids = tuple(
                _text(value, f"edge_gateway.devices[{index}].sensor_ids")
                for value in raw_sensors
            )
            if len(set(sensor_ids)) != len(sensor_ids):
                raise _config_error(
                    f"edge_gateway.devices[{index}].sensor_ids repeats an identity"
                )
            overlap = all_sensors & set(sensor_ids)
            if overlap:
                raise _config_error(
                    f"sensor identities are routed more than once: {sorted(overlap)}"
                )
            all_sensors.update(sensor_ids)
            devices.append(DeviceRoute(device_id=device_id, sensor_ids=sensor_ids))
        device_ids = [item.device_id for item in devices]
        if len(set(device_ids)) != len(device_ids):
            raise _config_error("edge_gateway.devices repeats a device_id")

        status_payload = inner["status"]
        if not isinstance(status_payload, Mapping):
            raise _config_error("edge_gateway.status must be a mapping")
        _exact_keys(status_payload, _STATUS_FIELDS, "edge_gateway.status")
        status = StatusEndpointConfig(
            host=_host(status_payload["host"], "edge_gateway.status.host"),
            port=_integer(
                status_payload["port"],
                "edge_gateway.status.port",
                minimum=0,
                maximum=65_535,
            ),
        )

        evidence_dir_value = inner["evidence_dir"]
        if (
            type(evidence_dir_value) is not str
            or not evidence_dir_value
            or evidence_dir_value != evidence_dir_value.strip()
        ):
            raise _config_error(
                "edge_gateway.evidence_dir must be a non-blank trimmed path"
            )
        return cls(
            schema=CONFIG_SCHEMA,
            mode=mode,
            site_id=_text(inner["site_id"], "edge_gateway.site_id"),
            deployment_id=_text(
                inner["deployment_id"], "edge_gateway.deployment_id"
            ),
            gateway_id=_text(inner["gateway_id"], "edge_gateway.gateway_id"),
            broker=broker,
            devices=tuple(devices),
            status=status,
            evidence_dir=Path(evidence_dir_value),
            fixture_cycle_index=_integer(
                inner["fixture_cycle_index"],
                "edge_gateway.fixture_cycle_index",
                minimum=0,
            ),
        )

    def route_for_device(self, device_id: str) -> DeviceRoute | None:
        return next(
            (item for item in self.devices if item.device_id == device_id), None
        )


class _DuplicateYamlKey(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise _DuplicateYamlKey(f"duplicate YAML key {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_gateway_config(
    path: str | Path, *, site: CommissionedSite | None = None
) -> GatewayConfig:
    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
        payload = yaml.load(text, Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, _DuplicateYamlKey) as exc:
        raise _config_error(f"cannot read {config_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise _config_error(f"{config_path} must decode to a mapping")
    if set(payload) != {"edge_gateway"}:
        raise _config_error(
            f"{config_path} must contain exactly one top-level edge_gateway mapping"
        )
    config = GatewayConfig.from_mapping(payload)
    if site is not None:
        validate_config_against_site(config, site)
    return config


def validate_config_against_site(
    config: GatewayConfig, site: CommissionedSite
) -> None:
    if not isinstance(config, GatewayConfig):
        raise _config_error("config must be a GatewayConfig")
    if not isinstance(site, CommissionedSite):
        raise _config_error("site must be a validated CommissionedSite")
    if (config.site_id, config.deployment_id) != (
        site.site_id,
        site.deployment_id,
    ):
        raise GatewayError(
            GatewayErrorCode.IDENTITY_MISMATCH,
            "gateway site/deployment identity does not match CommissionedSite",
        )
    bindings = {item.sensor_id: item for item in site.sensor_bindings}
    for route in config.devices:
        for sensor_id in route.sensor_ids:
            binding = bindings.get(sensor_id)
            if binding is None:
                raise GatewayError(
                    GatewayErrorCode.IDENTITY_MISMATCH,
                    f"sensor {sensor_id!r} is not commissioned",
                )
            if binding.sensor_type.value != "load_cell":
                raise GatewayError(
                    GatewayErrorCode.IDENTITY_MISMATCH,
                    f"sensor {sensor_id!r} is not a commissioned load cell",
                )
            if (
                config.mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL
                and sensor_id not in HYBRID_DISPENSER_SENSOR_IDS
            ):
                raise GatewayError(
                    GatewayErrorCode.IDENTITY_MISMATCH,
                    "hybrid rehearsal accepts only the commissioned Pilot "
                    f"Course A dispenser load-cell sensors; got {sensor_id!r}",
                )
    if not any(
        spec.cycle_index == config.fixture_cycle_index for spec in PILOT_CYCLES
    ):
        raise _config_error(
            f"fixture_cycle_index {config.fixture_cycle_index} is not declared"
        )


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonNumber(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _NonFiniteJsonNumber(f"non-finite JSON number {value}")


def _wire_text(value: object, field_name: str, *, optional: bool = False):
    if optional and value is None:
        return None
    if type(value) is not str or not value or value != value.strip():
        raise GatewayError(
            GatewayErrorCode.MALFORMED_JSON,
            f"{field_name} must be a non-blank trimmed string",
        )
    return value


def _parse_utc(value: object, field_name: str) -> datetime:
    if type(value) is not str or _UTC_PATTERN.fullmatch(value) is None:
        raise GatewayError(
            GatewayErrorCode.INVALID_TIMESTAMP,
            f"{field_name} must be an ISO-8601 UTC timestamp",
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GatewayError(
            GatewayErrorCode.INVALID_TIMESTAMP,
            f"{field_name} is not a real calendar timestamp",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise GatewayError(
            GatewayErrorCode.INVALID_TIMESTAMP,
            f"{field_name} must carry the UTC offset",
        )
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class LoadCellWireMessage:
    schema: str
    site_id: str
    deployment_id: str
    gateway_id: str
    device_id: str
    sensor_id: str
    boot_id: str
    device_sequence: int
    sampled_at_utc: str
    published_at_utc: str
    raw_value: float | None
    raw_unit: str
    device_status: str
    calibration_id: str | None
    diagnostic_code: str | None

    @classmethod
    def from_json(cls, raw: bytes | str) -> "LoadCellWireMessage":
        if isinstance(raw, bytes):
            if len(raw) > MAX_WIRE_PAYLOAD_BYTES:
                raise GatewayError(
                    GatewayErrorCode.PAYLOAD_TOO_LARGE,
                    f"wire payload exceeds {MAX_WIRE_PAYLOAD_BYTES} bytes",
                )
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GatewayError(
                    GatewayErrorCode.MALFORMED_JSON,
                    "payload must be UTF-8 JSON",
                ) from exc
        if type(raw) is not str:
            raise GatewayError(
                GatewayErrorCode.MALFORMED_JSON, "payload must be bytes or text"
            )
        if len(raw.encode("utf-8")) > MAX_WIRE_PAYLOAD_BYTES:
            raise GatewayError(
                GatewayErrorCode.PAYLOAD_TOO_LARGE,
                f"wire payload exceeds {MAX_WIRE_PAYLOAD_BYTES} bytes",
            )
        try:
            payload = json.loads(
                raw,
                object_pairs_hook=_unique_json_object,
                parse_float=Decimal,
                parse_constant=_reject_json_constant,
            )
        except _DuplicateJsonKey as exc:
            raise GatewayError(
                GatewayErrorCode.DUPLICATE_JSON_KEY,
                f"payload repeats key {exc.args[0]!r}",
            ) from exc
        except _NonFiniteJsonNumber as exc:
            raise GatewayError(
                GatewayErrorCode.INVALID_NUMBER,
                str(exc),
            ) from exc
        except (json.JSONDecodeError, ValueError, TypeError, RecursionError) as exc:
            raise GatewayError(
                GatewayErrorCode.MALFORMED_JSON, f"invalid JSON payload: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise GatewayError(
                GatewayErrorCode.MALFORMED_JSON,
                "wire payload root must be an object",
            )
        missing = sorted(_WIRE_FIELDS - set(payload))
        if missing:
            raise GatewayError(
                GatewayErrorCode.MISSING_FIELD, f"missing fields: {missing}"
            )
        unknown = sorted(set(payload) - _WIRE_FIELDS)
        if unknown:
            raise GatewayError(
                GatewayErrorCode.UNKNOWN_FIELD, f"unknown fields: {unknown}"
            )
        if payload["schema"] != WIRE_SCHEMA:
            raise GatewayError(
                GatewayErrorCode.SCHEMA_MISMATCH,
                f"unsupported schema {payload['schema']!r}",
            )
        sequence = payload["device_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise GatewayError(
                GatewayErrorCode.INVALID_NUMBER,
                "device_sequence must be a non-negative integer",
            )
        raw_value = payload["raw_value"]
        if raw_value is not None:
            if isinstance(raw_value, bool) or not isinstance(
                raw_value, (int, float, Decimal)
            ):
                raise GatewayError(
                    GatewayErrorCode.INVALID_NUMBER,
                    "raw_value must be a JSON number or null",
                )
            try:
                raw_value = float(raw_value)
            except (OverflowError, ValueError) as exc:
                raise GatewayError(
                    GatewayErrorCode.INVALID_NUMBER,
                    "raw_value cannot be represented as a finite float",
                ) from exc
            if not math.isfinite(raw_value):
                raise GatewayError(
                    GatewayErrorCode.INVALID_NUMBER, "raw_value must be finite"
                )
        sampled = _parse_utc(payload["sampled_at_utc"], "sampled_at_utc")
        published = _parse_utc(
            payload["published_at_utc"], "published_at_utc"
        )
        if published < sampled:
            raise GatewayError(
                GatewayErrorCode.TIMESTAMP_ORDER,
                "published_at_utc must be >= sampled_at_utc",
            )
        return cls(
            schema=WIRE_SCHEMA,
            site_id=_wire_text(payload["site_id"], "site_id"),
            deployment_id=_wire_text(payload["deployment_id"], "deployment_id"),
            gateway_id=_wire_text(payload["gateway_id"], "gateway_id"),
            device_id=_wire_text(payload["device_id"], "device_id"),
            sensor_id=_wire_text(payload["sensor_id"], "sensor_id"),
            boot_id=_wire_text(payload["boot_id"], "boot_id"),
            device_sequence=sequence,
            sampled_at_utc=payload["sampled_at_utc"],
            published_at_utc=payload["published_at_utc"],
            raw_value=raw_value,
            raw_unit=_wire_text(payload["raw_unit"], "raw_unit"),
            device_status=_wire_text(payload["device_status"], "device_status"),
            calibration_id=_wire_text(
                payload["calibration_id"], "calibration_id", optional=True
            ),
            diagnostic_code=_wire_text(
                payload["diagnostic_code"], "diagnostic_code", optional=True
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            field_name: getattr(self, field_name)
            for field_name in sorted(_WIRE_FIELDS)
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")


def expected_topic(config: GatewayConfig, device_id: str) -> str:
    if config.route_for_device(device_id) is None:
        raise GatewayError(
            GatewayErrorCode.IDENTITY_MISMATCH,
            f"device {device_id!r} is not configured",
        )
    return f"nxt/v1/sites/{config.site_id}/devices/{device_id}/load-cell"


def validate_topic_and_identity(
    topic: str,
    message: LoadCellWireMessage,
    config: GatewayConfig,
    site: CommissionedSite,
) -> None:
    validate_config_against_site(config, site)
    if type(topic) is not str:
        raise GatewayError(GatewayErrorCode.INVALID_TOPIC, "topic must be text")
    parts = topic.split("/")
    if (
        len(parts) != 7
        or parts[:3] != ["nxt", "v1", "sites"]
        or parts[4] != "devices"
        or parts[6] != "load-cell"
        or any(part in {"", "+", "#"} for part in parts)
    ):
        raise GatewayError(
            GatewayErrorCode.INVALID_TOPIC, f"invalid load-cell topic {topic!r}"
        )
    topic_site, topic_device = parts[3], parts[5]
    route = config.route_for_device(message.device_id)
    expected_identity = (
        config.site_id,
        config.deployment_id,
        config.gateway_id,
    )
    if (message.site_id, message.deployment_id, message.gateway_id) != (
        expected_identity
    ):
        raise GatewayError(
            GatewayErrorCode.IDENTITY_MISMATCH,
            "wire site/deployment/gateway identity does not match config",
        )
    if route is None or message.sensor_id not in route.sensor_ids:
        raise GatewayError(
            GatewayErrorCode.IDENTITY_MISMATCH,
            "wire device/sensor identity is not an allowed configured route",
        )
    if topic_site != config.site_id or topic_device != message.device_id:
        raise GatewayError(
            GatewayErrorCode.INVALID_TOPIC,
            "topic identity disagrees with config or wire message",
        )
    if topic != expected_topic(config, message.device_id):
        raise GatewayError(
            GatewayErrorCode.INVALID_TOPIC, "topic is not the exact configured topic"
        )


@dataclass(frozen=True, slots=True)
class MappedSiteTiming:
    operating_day_id: str
    sample_timestamp_s: float
    available_timestamp_s: float
    sampled_at_utc: str
    published_at_utc: str


class SiteClock:
    """Map explicit UTC wire evidence into commissioned civil site time."""

    def __init__(self, timezone_name: str) -> None:
        if type(timezone_name) is not str or not timezone_name.strip():
            raise _config_error("commissioned timezone must be a non-blank IANA name")
        try:
            self._zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise _config_error(f"unknown IANA timezone {timezone_name!r}") from exc
        self.timezone_name = timezone_name

    def _map_one(self, text: str, field_name: str) -> tuple[date, float]:
        utc_value = _parse_utc(text, field_name)
        local = utc_value.astimezone(self._zone)
        naive = local.replace(tzinfo=None)
        fold_zero = naive.replace(tzinfo=self._zone, fold=0)
        fold_one = naive.replace(tzinfo=self._zone, fold=1)
        if fold_zero.utcoffset() != fold_one.utcoffset():
            zero_roundtrip = (
                fold_zero.astimezone(timezone.utc)
                .astimezone(self._zone)
                .replace(tzinfo=None)
            )
            one_roundtrip = (
                fold_one.astimezone(timezone.utc)
                .astimezone(self._zone)
                .replace(tzinfo=None)
            )
            if zero_roundtrip == naive and one_roundtrip == naive:
                raise GatewayError(
                    GatewayErrorCode.AMBIGUOUS_LOCAL_TIME,
                    f"{field_name} maps into an ambiguous local fall-back fold",
                )
        seconds = (
            local.hour * 3600
            + local.minute * 60
            + local.second
            + local.microsecond / 1_000_000
        )
        return local.date(), seconds

    def map_pair(self, sampled_at_utc: str, published_at_utc: str) -> MappedSiteTiming:
        sampled_utc = _parse_utc(sampled_at_utc, "sampled_at_utc")
        published_utc = _parse_utc(published_at_utc, "published_at_utc")
        if published_utc < sampled_utc:
            raise GatewayError(
                GatewayErrorCode.TIMESTAMP_ORDER,
                "published_at_utc must be >= sampled_at_utc",
            )
        sample_day, sample_s = self._map_one(sampled_at_utc, "sampled_at_utc")
        available_day, available_s = self._map_one(
            published_at_utc, "published_at_utc"
        )
        if sample_day != available_day:
            raise GatewayError(
                GatewayErrorCode.MIXED_OPERATING_DAY,
                "one V0 delivery cannot cross the site's local calendar midnight",
            )
        return MappedSiteTiming(
            operating_day_id=sample_day.isoformat(),
            sample_timestamp_s=sample_s,
            available_timestamp_s=available_s,
            sampled_at_utc=sampled_at_utc,
            published_at_utc=published_at_utc,
        )

    def local_midnight(self, operating_day_id: str) -> datetime:
        try:
            day = date.fromisoformat(operating_day_id)
        except ValueError as exc:
            raise _config_error(
                f"invalid operating_day_id {operating_day_id!r}"
            ) from exc
        midnight = datetime(day.year, day.month, day.day, tzinfo=self._zone)
        # Some historical IANA zones shift at midnight. V0 refuses an invalid
        # or ambiguous anchor instead of silently changing Agent Runtime time.
        naive = midnight.replace(tzinfo=None)
        fold_zero = naive.replace(tzinfo=self._zone, fold=0)
        fold_one = naive.replace(tzinfo=self._zone, fold=1)
        if fold_zero.utcoffset() != fold_one.utcoffset():
            raise GatewayError(
                GatewayErrorCode.AMBIGUOUS_LOCAL_TIME,
                "the operating-day midnight is ambiguous in the commissioned zone",
            )
        return midnight


class DeviceSequenceTracker:
    """Process-local V0 replay and device boot-epoch tracker."""

    def __init__(self) -> None:
        self._active_boot: dict[str, str] = {}
        self._retired_boots: dict[str, set[str]] = {}
        self._highest: dict[tuple[str, str], int] = {}
        self._seen: dict[tuple[str, str], dict[int, bytes]] = {}

    def accept(self, message: LoadCellWireMessage) -> SequenceDisposition:
        device_id = message.device_id
        boot_id = message.boot_id
        active = self._active_boot.get(device_id)
        retired = self._retired_boots.setdefault(device_id, set())
        if active is not None and boot_id != active:
            if boot_id in retired:
                raise GatewayError(
                    GatewayErrorCode.RETIRED_BOOT,
                    f"boot {boot_id!r} for {device_id!r} is retired",
                )
            if len(retired) >= MAX_RETIRED_BOOTS_PER_DEVICE:
                raise GatewayError(
                    GatewayErrorCode.REPLAY_CAPACITY_EXCEEDED,
                    "retired boot history reached the V0 fail-closed limit for "
                    f"{device_id!r}",
                )
            retired.add(active)
            self._active_boot[device_id] = boot_id
            active = boot_id
        elif active is None:
            self._active_boot[device_id] = boot_id
            active = boot_id

        epoch = (device_id, boot_id)
        seen = self._seen.setdefault(epoch, {})
        digest = hashlib.sha256(message.canonical_bytes()).digest()
        previous = seen.get(message.device_sequence)
        if previous is not None:
            if previous == digest:
                return SequenceDisposition.DUPLICATE
            raise GatewayError(
                GatewayErrorCode.CONFLICTING_REPLAY,
                "the same device/boot/sequence was reused with different content",
            )
        highest = self._highest.get(epoch)
        if highest is not None and message.device_sequence < highest:
            raise GatewayError(
                GatewayErrorCode.OUT_OF_ORDER_SEQUENCE,
                f"device sequence {message.device_sequence} is below {highest}",
            )
        seen[message.device_sequence] = digest
        if len(seen) > MAX_SEQUENCE_REPLAY_WINDOW:
            del seen[min(seen)]
        self._highest[epoch] = message.device_sequence
        return SequenceDisposition.ACCEPTED


@dataclass(frozen=True, slots=True)
class ValidatedWireDelivery:
    topic: str
    message: LoadCellWireMessage
    timing: MappedSiteTiming
    sample: LoadCellSample
    channel: str
    content_digest: str

    @property
    def device_sequence(self) -> int:
        return self.message.device_sequence


def _narrow_load_cell_kit(
    site: CommissionedSite, sensor_ids: set[str]
) -> EdgeObservationAdapterKit:
    full = adapter_kit(site)
    bindings = []
    for sensor_id in sorted(sensor_ids):
        binding = full.bindings.by_sensor_id(sensor_id)
        if binding is None:
            raise GatewayError(
                GatewayErrorCode.IDENTITY_MISMATCH,
                f"sensor {sensor_id!r} has no commissioned adapter binding",
            )
        bindings.append(binding)
    profiles = tuple(
        profile
        for profile in full.load_cell_profiles
        if profile.sensor_id in sensor_ids
    )
    if len(profiles) != len(sensor_ids):
        available = {item.sensor_id for item in profiles}
        raise GatewayError(
            GatewayErrorCode.IDENTITY_MISMATCH,
            f"no load-cell profile for {sorted(sensor_ids - available)}",
        )
    return EdgeObservationAdapterKit(
        bindings=AdapterBindingSet(
            site_id=full.bindings.site_id,
            deployment_id=full.bindings.deployment_id,
            bindings=tuple(bindings),
        ),
        coordinate_frame=site.spatial_reference.coordinate_system.identifier,
        load_cell_profiles=profiles,
    )


def diagnose_load_cell_samples(
    site: CommissionedSite,
    samples: tuple[LoadCellSample, ...],
    *,
    frame_t_s: float,
    cycle_index: int,
) -> ConversionResult:
    """Run existing load-cell conversion over only the claimed bindings.

    Narrowing the binding set keeps a one-sensor diagnostic honest: silence
    from unrelated equipment is not asserted merely because that equipment is
    outside this diagnostic message. Duplicate claims for the target channel
    still reach the existing adapter and fail closed there.
    """

    if not isinstance(samples, tuple) or not samples:
        raise GatewayError(
            GatewayErrorCode.SOURCE_PROTOCOL,
            "diagnostic samples must be a non-empty tuple",
        )
    if any(not isinstance(item, LoadCellSample) for item in samples):
        raise GatewayError(
            GatewayErrorCode.SOURCE_PROTOCOL,
            "diagnostic samples must contain LoadCellSample values",
        )
    kit = _narrow_load_cell_kit(site, {item.sensor_id for item in samples})
    return kit.convert(
        RawSampleBatch(
            cycle_index=cycle_index,
            frame_t_s=frame_t_s,
            load_cells=samples,
        )
    )


@dataclass
class HybridObservationSource:
    """One-message-at-a-time existing ObservationSource composition.

    A staged frame is immutable and peeked until exactly one acknowledge or
    reject decision. Device delivery order is deliberately absent here: the
    independent contiguous Site Runtime position is ``next_sequence``.
    """

    site: CommissionedSite
    fixture_spec: CycleSpec = PILOT_CYCLES[0]
    _next_sequence: int = field(default=0, init=False, repr=False)
    _pending: SequencedObservationFrame | None = field(
        default=None, init=False, repr=False
    )
    _pending_report: EdgeAdapterReport | None = field(
        default=None, init=False, repr=False
    )
    _pending_delivery: ValidatedWireDelivery | None = field(
        default=None, init=False, repr=False
    )
    _operating_day_id: str | None = field(default=None, init=False, repr=False)
    acknowledged: list[int] = field(default_factory=list, init=False)
    rejected: list[tuple[int, str]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.site, CommissionedSite):
            raise TypeError("site must be a CommissionedSite")
        if not isinstance(self.fixture_spec, CycleSpec):
            raise TypeError("fixture_spec must be a CycleSpec")
        self._kit = adapter_kit(self.site)

    @property
    def next_sequence(self) -> int:
        return self._next_sequence

    @property
    def pending_report(self) -> EdgeAdapterReport | None:
        return self._pending_report

    @property
    def pending_delivery(self) -> ValidatedWireDelivery | None:
        return self._pending_delivery

    @property
    def operating_day_id(self) -> str | None:
        return self._operating_day_id

    def stage(self, delivery: ValidatedWireDelivery) -> SequencedObservationFrame:
        if not isinstance(delivery, ValidatedWireDelivery):
            raise TypeError("delivery must be a ValidatedWireDelivery")
        if self._pending is not None:
            raise GatewayError(
                GatewayErrorCode.SOURCE_PROTOCOL,
                "cannot stage a second delivery before acknowledge or reject",
            )
        if delivery.message.sensor_id not in HYBRID_DISPENSER_SENSOR_IDS:
            raise GatewayError(
                GatewayErrorCode.IDENTITY_MISMATCH,
                "hybrid rehearsal cannot overlay a non-dispenser sensor",
            )
        if self._operating_day_id is None:
            self._operating_day_id = delivery.timing.operating_day_id
        elif self._operating_day_id != delivery.timing.operating_day_id:
            raise GatewayError(
                GatewayErrorCode.OPERATING_DAY_ROLLOVER,
                "hybrid runtime V0 is bound to one local operating day",
            )

        spec = dataclasses.replace(
            self.fixture_spec,
            cycle_index=self._next_sequence,
            t_s=delivery.timing.available_timestamp_s,
        )
        fixture_batch = raw_batch(spec)
        replaced = False
        load_cells: list[LoadCellSample] = []
        for sample in fixture_batch.load_cells:
            if sample.sensor_id == delivery.message.sensor_id:
                load_cells.append(delivery.sample)
                replaced = True
            else:
                load_cells.append(sample)
        if not replaced:
            raise GatewayError(
                GatewayErrorCode.IDENTITY_MISMATCH,
                f"fixture has no load-cell sample for {delivery.message.sensor_id!r}",
            )
        batch = RawSampleBatch(
            cycle_index=self._next_sequence,
            frame_t_s=delivery.timing.available_timestamp_s,
            load_cells=tuple(load_cells),
            digital_io=fixture_batch.digital_io,
            robots=fixture_batch.robots,
        )
        converted = self._kit.convert(batch)
        observations: list[Observation] = []
        for observation in converted.observations:
            if observation.channel == delivery.channel:
                observations.append(observation)
            else:
                observations.append(
                    dataclasses.replace(
                        observation,
                        source_type=SourceType.SIMULATION,
                        source_id=f"synthetic.pilot-course-a.{observation.source_id}",
                    )
                )
        observations.extend(facility_system_observations(spec))
        observations.sort(key=lambda item: item.channel)
        frame = ObservationFrame(
            t_s=batch.frame_t_s,
            observations=tuple(observations),
        )
        self._pending = SequencedObservationFrame(
            sequence_number=self._next_sequence,
            frame=frame,
            upstream=upstream_inputs(spec),
            upstream_source_references=(upstream_reference(spec),),
        )
        self._pending_report = converted.report
        self._pending_delivery = delivery
        return self._pending

    def observe(self) -> SequencedObservationFrame:
        if self._pending is None:
            raise SourceExhausted("no validated MQTT delivery is staged")
        return self._pending

    def acknowledge(self, sequence_number: int) -> None:
        if self._pending is None or self._pending.sequence_number != sequence_number:
            raise GatewayError(
                GatewayErrorCode.SOURCE_PROTOCOL,
                "acknowledgement does not match the staged site sequence",
            )
        self.acknowledged.append(sequence_number)
        self._pending = None
        self._pending_report = None
        self._pending_delivery = None
        self._next_sequence += 1

    def reject(self, sequence_number: int, reason: str) -> None:
        if self._pending is None or self._pending.sequence_number != sequence_number:
            raise GatewayError(
                GatewayErrorCode.SOURCE_PROTOCOL,
                "rejection does not match the staged site sequence",
            )
        if type(reason) is not str or not reason:
            raise GatewayError(
                GatewayErrorCode.SOURCE_PROTOCOL,
                "rejection reason must be a non-blank string",
            )
        self.rejected.append((sequence_number, reason))
        self._pending = None
        self._pending_report = None
        self._pending_delivery = None
        # Rejection consumes the device message, not the publication position.


def _disclaimer(mode: GatewayMode) -> str:
    if mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL:
        return HYBRID_DISCLAIMER
    return DIAGNOSTIC_DISCLAIMER


class GatewayStatus:
    """Thread-safe noncanonical current diagnostics for read-only endpoints."""

    def __init__(self, *, mode: GatewayMode | str, site_id: str, deployment_id: str):
        try:
            self._mode = GatewayMode(mode)
        except (TypeError, ValueError) as exc:
            raise _config_error(f"unsupported status mode {mode!r}") from exc
        self._site_id = site_id
        self._deployment_id = deployment_id
        self._broker_connected = False
        self._sensor_seen = False
        self._adapter_healthy = False
        self._runtime_ready = False
        self._current: dict[str, object] = {}
        self._last_failure: dict[str, str] | None = None
        self._lock = threading.Lock()

    def set_broker_connected(self, connected: bool) -> None:
        if type(connected) is not bool:
            raise TypeError("connected must be a boolean")
        with self._lock:
            self._broker_connected = connected

    def record_sensor_result(
        self,
        *,
        adapter_healthy: bool,
        runtime_ready: bool | None,
        operating_day_id: str | None = None,
        detail: Mapping[str, object] | None = None,
    ) -> None:
        if type(adapter_healthy) is not bool:
            raise TypeError("adapter_healthy must be a boolean")
        if runtime_ready is not None and type(runtime_ready) is not bool:
            raise TypeError("runtime_ready must be a boolean or None")
        current = {} if detail is None else dict(detail)
        if operating_day_id is not None:
            current["operating_day_id"] = operating_day_id
        with self._lock:
            self._sensor_seen = True
            self._adapter_healthy = adapter_healthy
            self._runtime_ready = False if runtime_ready is None else runtime_ready
            self._current = current
            self._last_failure = None

    def record_failure(self, code: str, detail: str) -> None:
        if type(code) is not str or not code or type(detail) is not str:
            raise TypeError("failure code/detail must be strings")
        with self._lock:
            self._adapter_healthy = False
            if self._mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL:
                self._runtime_ready = False
            self._last_failure = {"code": code, "detail": _bounded_detail(detail)}

    def record_runtime_failure(self, code: str, detail: str) -> None:
        """Record runtime failure without falsifying prior adapter evidence."""

        if type(code) is not str or not code or type(detail) is not str:
            raise TypeError("failure code/detail must be strings")
        with self._lock:
            self._runtime_ready = False
            self._last_failure = {"code": code, "detail": _bounded_detail(detail)}

    def record_transport_failure(self, code: str, detail: str) -> None:
        """Record broker failure without falsifying adapter/runtime evidence."""

        if type(code) is not str or not code or type(detail) is not str:
            raise TypeError("failure code/detail must be strings")
        with self._lock:
            self._broker_connected = False
            self._last_failure = {"code": code, "detail": _bounded_detail(detail)}

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            runtime_required = self._mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL
            ready = (
                self._broker_connected
                and self._sensor_seen
                and self._adapter_healthy
                and (self._runtime_ready if runtime_required else True)
            )
            payload = {
                "schema": STATUS_SCHEMA,
                "mode": self._mode.value,
                "site_id": self._site_id,
                "deployment_id": self._deployment_id,
                "broker_connected": self._broker_connected,
                "sensor_seen": self._sensor_seen,
                "adapter_healthy": self._adapter_healthy,
                "runtime_ready": self._runtime_ready,
                "ready": ready,
                "current": dict(self._current),
                "last_failure": (
                    None if self._last_failure is None else dict(self._last_failure)
                ),
                "disclaimer": _disclaimer(self._mode),
            }
        # Ensure endpoint snapshots contain only JSON-ready diagnostic values.
        return json.loads(json.dumps(payload, allow_nan=False))


class GatewayStatusServer:
    """Small GET/HEAD-only local status server; never a control API."""

    def __init__(
        self,
        status: GatewayStatus,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not isinstance(status, GatewayStatus):
            raise TypeError("status must be GatewayStatus")
        self._status = status
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("status server is not running")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def start(self) -> "GatewayStatusServer":
        if self._server is not None:
            return self
        status = self._status

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002
                return None

            def _payload(self) -> tuple[int, dict[str, object]]:
                path = urlsplit(self.path).path
                snapshot = status.snapshot()
                if path == "/healthz":
                    return 200, {**snapshot, "schema": HEALTH_SCHEMA}
                if path == "/readyz":
                    return (200 if snapshot["ready"] else 503), snapshot
                if path == "/api/v0/status":
                    return 200, snapshot
                return 404, {"error": "not_found", "path": path}

            def _read(self, *, body: bool) -> None:
                code, payload = self._payload()
                encoded = json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                if body:
                    self.wfile.write(encoded)

            def do_GET(self) -> None:  # noqa: N802
                self._read(body=True)

            def do_HEAD(self) -> None:  # noqa: N802
                self._read(body=False)

            def _method_not_allowed(self) -> None:
                payload = b'{"error":"method_not_allowed"}'
                self.send_response(405)
                self.send_header("Allow", "GET, HEAD")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_POST = _method_not_allowed  # noqa: N815
            do_PUT = _method_not_allowed  # noqa: N815
            do_PATCH = _method_not_allowed  # noqa: N815
            do_DELETE = _method_not_allowed  # noqa: N815

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="edge-gateway-read-only-status",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "GatewayStatusServer":
        return self.start()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()


def _runtime_outcome_payload(outcome) -> dict[str, object] | None:
    if outcome is None:
        return None
    failure_payload = None
    if outcome.failure is not None:
        failure_payload = {
            "code": outcome.failure.code.value,
            "stage": outcome.failure.stage.value,
            "detail": outcome.failure.detail,
            "retryable": outcome.failure.retryable,
        }
    return {
        "kind": outcome.kind.value,
        "sequence_number": outcome.sequence_number,
        "envelope_id": outcome.envelope_id,
        "evaluation_id": outcome.evaluation_id,
        "acknowledged": outcome.acknowledged,
        "failure": failure_payload,
    }


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    kind: ProcessingKind
    mode: GatewayMode
    operating_day_id: str | None
    site_sequence: int | None
    observations: tuple[Observation, ...]
    adapter_report: EdgeAdapterReport | None
    complete_facility_state: bool
    disclaimer: str
    runtime_outcome: object | None = None
    failure: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        payload = {
            "kind": self.kind.value,
            "mode": self.mode.value,
            "operating_day_id": self.operating_day_id,
            "site_sequence": self.site_sequence,
            "observations": [item.to_dict() for item in self.observations],
            "adapter_report": (
                None if self.adapter_report is None else self.adapter_report.to_dict()
            ),
            "complete_facility_state": self.complete_facility_state,
            "disclaimer": self.disclaimer,
            "runtime_outcome": _runtime_outcome_payload(self.runtime_outcome),
            "failure": self.failure,
        }
        # Fail loudly if a new diagnostic field is not JSON-safe or is non-finite.
        json.dumps(payload, allow_nan=False)
        return payload


class GatewayProcessor:
    """Strict wire admission plus one of the two honest V0 compositions."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        site: CommissionedSite | None = None,
        clock: SiteClock | None = None,
        tracker: DeviceSequenceTracker | None = None,
        status: GatewayStatus | None = None,
    ) -> None:
        if not isinstance(config, GatewayConfig):
            raise TypeError("config must be GatewayConfig")
        self.config = config
        self.site = commissioned_site() if site is None else site
        validate_config_against_site(config, self.site)
        if clock is None:
            clock = SiteClock(self.site.timezone)
        elif not isinstance(clock, SiteClock):
            raise TypeError("clock must be a SiteClock")
        elif clock.timezone_name != self.site.timezone:
            raise _config_error(
                "injected SiteClock timezone must exactly match the "
                f"commissioned timezone {self.site.timezone!r}"
            )
        self.clock = clock
        self.tracker = DeviceSequenceTracker() if tracker is None else tracker
        self.status = (
            GatewayStatus(
                mode=config.mode,
                site_id=config.site_id,
                deployment_id=config.deployment_id,
            )
            if status is None
            else status
        )
        self._kit = adapter_kit(self.site)
        self._diagnostic_sequence = 0
        self._fixture_spec = next(
            spec
            for spec in PILOT_CYCLES
            if spec.cycle_index == config.fixture_cycle_index
        )
        self._hybrid_source = (
            HybridObservationSource(self.site, fixture_spec=self._fixture_spec)
            if config.mode is GatewayMode.HYBRID_RUNTIME_REHEARSAL
            else None
        )
        self._runtime: AgentRuntime | None = None
        self._runtime_day: str | None = None

    def set_broker_connected(self, connected: bool) -> None:
        self.status.set_broker_connected(connected)

    @property
    def has_pending_hybrid_delivery(self) -> bool:
        return (
            self._hybrid_source is not None
            and self._hybrid_source.pending_delivery is not None
        )

    def _prepare_with_disposition(
        self, topic: str, payload: bytes | str
    ) -> tuple[ValidatedWireDelivery, SequenceDisposition]:
        message = LoadCellWireMessage.from_json(payload)
        validate_topic_and_identity(topic, message, self.config, self.site)
        timing = self.clock.map_pair(
            message.sampled_at_utc, message.published_at_utc
        )
        sample = LoadCellSample(
            sensor_id=message.sensor_id,
            timing=RawSampleTiming(
                sample_timestamp_s=timing.sample_timestamp_s,
                available_timestamp_s=timing.available_timestamp_s,
            ),
            raw_value=message.raw_value,
            raw_unit=message.raw_unit,
            device_status=message.device_status,
            calibration_id=message.calibration_id,
            diagnostic_code=message.diagnostic_code,
        )
        binding = self._kit.bindings.by_sensor_id(message.sensor_id)
        if binding is None:
            raise GatewayError(
                GatewayErrorCode.IDENTITY_MISMATCH,
                f"no adapter binding for {message.sensor_id!r}",
            )
        content_digest = hashlib.sha256(message.canonical_bytes()).hexdigest()
        delivery = ValidatedWireDelivery(
            topic=topic,
            message=message,
            timing=timing,
            sample=sample,
            channel=binding.channel,
            content_digest=content_digest,
        )
        pending = (
            None
            if self._hybrid_source is None
            else self._hybrid_source.pending_delivery
        )
        if pending is not None:
            pending_key = (
                pending.message.device_id,
                pending.message.boot_id,
                pending.message.device_sequence,
            )
            message_key = (
                message.device_id,
                message.boot_id,
                message.device_sequence,
            )
            if message_key != pending_key:
                raise GatewayError(
                    GatewayErrorCode.SOURCE_PROTOCOL,
                    "a deferred hybrid frame must be redelivered before a new "
                    "device sequence is admitted",
                )
        disposition = self.tracker.accept(message)
        return delivery, disposition

    def prepare_message(
        self, topic: str, payload: bytes | str
    ) -> ValidatedWireDelivery | None:
        try:
            delivery, disposition = self._prepare_with_disposition(topic, payload)
        except GatewayError as exc:
            self.status.record_failure(exc.code.value, exc.detail)
            raise
        return (
            None
            if disposition is SequenceDisposition.DUPLICATE
            else delivery
        )

    def _make_runtime(self, operating_day_id: str) -> AgentRuntime:
        if self._hybrid_source is None:
            raise GatewayError(
                GatewayErrorCode.SOURCE_PROTOCOL,
                "diagnostic mode cannot construct Agent Runtime",
            )
        if self._runtime is not None:
            if self._runtime_day != operating_day_id:
                raise GatewayError(
                    GatewayErrorCode.OPERATING_DAY_ROLLOVER,
                    "runtime is already bound to a different operating day",
                )
            return self._runtime
        root = self.config.evidence_dir
        if not root.is_absolute():
            root = SIM_ROOT / root
        root = root / self.config.site_id / self.config.deployment_id
        root.mkdir(parents=True, exist_ok=True)
        self._runtime_day = operating_day_id
        self._runtime = AgentRuntime(
            site_id=self.config.site_id,
            deployment_id=self.config.deployment_id,
            site_config=site_config(self.site),
            observation_source=self._hybrid_source,
            publisher=JsonlSnapshotPublisher(root / "snapshots.jsonl"),
            ledger=JsonlEventLedger(root / "ledger.jsonl"),
            journal=EvaluationJournal(root / "evaluations.jsonl"),
            simulation_midnight=self.clock.local_midnight(operating_day_id),
            clean_sensed_valid=True,
            site_checkpoint_store=JsonCheckpointStore(root / "checkpoints" / "site"),
            evaluation_checkpoint_store=JsonEvaluationCheckpointStore(
                root / "checkpoints" / "evaluation"
            ),
        )
        return self._runtime

    def process_message(
        self, topic: str, payload: bytes | str
    ) -> ProcessingResult:
        try:
            delivery, disposition = self._prepare_with_disposition(topic, payload)
            redrive_pending = (
                self._hybrid_source is not None
                and self._hybrid_source.pending_delivery is not None
                and self._hybrid_source.pending_delivery.content_digest
                == delivery.content_digest
            )
            if (
                disposition is SequenceDisposition.DUPLICATE
                and not redrive_pending
            ):
                return ProcessingResult(
                    kind=ProcessingKind.DUPLICATE,
                    mode=self.config.mode,
                    operating_day_id=delivery.timing.operating_day_id,
                    site_sequence=None,
                    observations=(),
                    adapter_report=None,
                    complete_facility_state=False,
                    disclaimer=_disclaimer(self.config.mode),
                )
            if self.config.mode is GatewayMode.LOAD_CELL_DIAGNOSTIC:
                sequence = self._diagnostic_sequence
                converted = diagnose_load_cell_samples(
                    self.site,
                    (delivery.sample,),
                    frame_t_s=delivery.timing.available_timestamp_s,
                    cycle_index=sequence,
                )
                self._diagnostic_sequence += 1
                target = next(
                    item
                    for item in converted.observations
                    if item.channel == delivery.channel
                )
                healthy = target.status is ObservationStatus.OK
                result = ProcessingResult(
                    kind=ProcessingKind.ACCEPTED,
                    mode=self.config.mode,
                    operating_day_id=delivery.timing.operating_day_id,
                    site_sequence=sequence,
                    observations=converted.observations,
                    adapter_report=converted.report,
                    complete_facility_state=False,
                    disclaimer=DIAGNOSTIC_DISCLAIMER,
                )
                self.status.record_sensor_result(
                    adapter_healthy=healthy,
                    runtime_ready=None,
                    operating_day_id=delivery.timing.operating_day_id,
                    detail={
                        "canonical_observation_count": len(converted.observations),
                        "site_sequence": sequence,
                        "sensor_id": delivery.message.sensor_id,
                        "channel": delivery.channel,
                        "sampled_at_utc": delivery.message.sampled_at_utc,
                        "published_at_utc": delivery.message.published_at_utc,
                    },
                )
                return result

            assert self._hybrid_source is not None
            staged = (
                self._hybrid_source.observe()
                if redrive_pending
                else self._hybrid_source.stage(delivery)
            )
            sequence = staged.sequence_number
            report = self._hybrid_source.pending_report
            observations = staged.frame.observations
            runtime = self._make_runtime(delivery.timing.operating_day_id)
            outcome = runtime.run_once()
            ready = outcome.acknowledged and outcome.kind in {
                CycleKind.EVALUATED,
                CycleKind.REPLAY_SKIPPED,
            }
            target = staged.frame.by_channel()[delivery.channel]
            adapter_healthy = target.status is ObservationStatus.OK
            kind = ProcessingKind.ACCEPTED if ready else ProcessingKind.REJECTED
            result = ProcessingResult(
                kind=kind,
                mode=self.config.mode,
                operating_day_id=delivery.timing.operating_day_id,
                site_sequence=sequence,
                observations=observations,
                adapter_report=report,
                complete_facility_state=outcome.envelope_id is not None,
                disclaimer=HYBRID_DISCLAIMER,
                runtime_outcome=outcome,
            )
            self.status.record_sensor_result(
                adapter_healthy=adapter_healthy,
                runtime_ready=ready,
                operating_day_id=delivery.timing.operating_day_id,
                detail={
                    "site_sequence": sequence,
                    "sensor_id": delivery.message.sensor_id,
                    "channel": delivery.channel,
                    "sampled_at_utc": delivery.message.sampled_at_utc,
                    "published_at_utc": delivery.message.published_at_utc,
                    "runtime": _runtime_outcome_payload(outcome),
                },
            )
            return result
        except GatewayError as exc:
            self.status.record_failure(exc.code.value, exc.detail)
            raise


def _mqtt_module():
    """Load the sole optional transport dependency at the script boundary."""

    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:
        raise GatewayError(
            GatewayErrorCode.MQTT_UNAVAILABLE,
            "install the edge-gateway optional dependency to use MQTT",
        ) from exc
    return mqtt


def _json_line(payload: Mapping[str, object]) -> None:
    print(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        flush=True,
    )


def run_gateway(
    config: GatewayConfig,
    *,
    site: CommissionedSite | None = None,
    max_messages: int | None = None,
) -> int:
    """Run the local MQTT composition until interrupted or a test limit fires."""

    if max_messages is not None and (
        isinstance(max_messages, bool)
        or not isinstance(max_messages, int)
        or max_messages < 1
    ):
        raise _config_error("max_messages must be a positive integer or None")
    mqtt = _mqtt_module()
    processor = GatewayProcessor(config, site=site)
    status_server = GatewayStatusServer(
        processor.status,
        host=config.status.host,
        port=config.status.port,
    )
    received_count = 0
    bounded_complete = False
    bounded_success = False
    terminal_failure: Exception | None = None
    redelivery_requested = False
    redelivery_failure: GatewayError | None = None
    subscription_mid: int | None = None
    subscription_established = False
    subscriptions: list[tuple[str, int]] = []

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=config.broker.client_id,
        protocol=mqtt.MQTTv311,
        clean_session=False,
    )
    client.manual_ack_set(True)

    def terminate(error: Exception) -> None:
        nonlocal terminal_failure
        if terminal_failure is None:
            terminal_failure = error
        client.disconnect()

    def request_redelivery(error: GatewayError) -> None:
        nonlocal redelivery_failure, redelivery_requested
        redelivery_requested = True
        redelivery_failure = error
        client.disconnect()

    def acknowledge(message) -> bool:
        try:
            result = client.ack(message.mid, message.qos)
        except Exception as exc:  # pragma: no cover - defensive Paho boundary
            detail = f"{type(exc).__name__}: {exc}"
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"manual MQTT acknowledgement failed: {detail}",
            )
            processor.status.record_transport_failure(
                "mqtt_ack_failed", error.detail
            )
            request_redelivery(error)
            return False
        if result != mqtt.MQTT_ERR_SUCCESS:
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"manual MQTT acknowledgement returned {result}",
            )
            processor.status.record_transport_failure(
                "mqtt_ack_failed", error.detail
            )
            request_redelivery(error)
            return False
        return True

    def emit_rejection(topic: str, code: str, detail: str) -> None:
        _json_line(
            {
                "event": "message_rejected",
                "kind": ProcessingKind.REJECTED.value,
                "mode": config.mode.value,
                "topic": topic,
                "failure": {"code": code, "detail": _bounded_detail(detail)},
                "disclaimer": _disclaimer(config.mode),
            }
        )

    def on_connect(client, userdata, flags, reason_code, properties):
        nonlocal subscription_mid, subscriptions
        del userdata, properties
        if getattr(reason_code, "is_failure", reason_code != 0):
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"broker refused connection: {reason_code}",
            )
            processor.status.record_transport_failure(
                "mqtt_connect_failed", error.detail
            )
            terminate(error)
            return
        session_present = bool(getattr(flags, "session_present", False))
        if subscription_established and session_present:
            # A SUBACK was already observed by this process and the broker
            # confirms that exact persistent session resumed.  Queued QoS 1
            # PUBLISH packets may arrive before any redundant re-SUBACK.
            processor.set_broker_connected(True)
            _json_line(
                {
                    "event": "mqtt_session_resumed",
                    "mode": config.mode.value,
                    "subscriptions": [topic for topic, _qos in subscriptions],
                    "disclaimer": _disclaimer(config.mode),
                }
            )
            return
        subscriptions = [
            (expected_topic(config, route.device_id), config.broker.qos)
            for route in config.devices
        ]
        result, subscription_mid = client.subscribe(subscriptions)
        if result != mqtt.MQTT_ERR_SUCCESS:
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"MQTT subscribe returned {result}",
            )
            processor.status.record_transport_failure(
                "mqtt_subscribe_failed", error.detail
            )
            subscription_mid = None
            terminate(error)

    def on_subscribe(client, userdata, mid, reason_code_list, properties):
        nonlocal subscription_established
        del userdata, properties
        if subscription_mid is None or mid != subscription_mid:
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"unexpected subscription acknowledgement {mid}",
            )
            processor.status.record_transport_failure(
                "mqtt_subscribe_failed",
                error.detail,
            )
            terminate(error)
            return
        if not reason_code_list or any(
            getattr(reason_code, "is_failure", False)
            for reason_code in reason_code_list
        ):
            error = GatewayError(
                GatewayErrorCode.MQTT_UNAVAILABLE,
                f"broker rejected subscription: {reason_code_list}",
            )
            processor.status.record_transport_failure(
                "mqtt_subscribe_failed",
                error.detail,
            )
            terminate(error)
            return
        subscription_established = True
        processor.set_broker_connected(True)
        _json_line(
            {
                "event": "mqtt_connected",
                "mode": config.mode.value,
                "subscriptions": [topic for topic, _qos in subscriptions],
                "disclaimer": _disclaimer(config.mode),
            }
        )

    def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
        del client, userdata, disconnect_flags, properties
        processor.set_broker_connected(False)
        if getattr(reason_code, "is_failure", reason_code != 0):
            processor.status.record_transport_failure(
                "mqtt_disconnected", f"broker disconnect: {reason_code}"
            )

    def on_message(client, userdata, message):
        nonlocal bounded_complete, bounded_success, received_count
        del userdata
        received_count += 1
        # A bounded smoke succeeds only if its terminal callback leaves the
        # gateway ready; an earlier success cannot mask a later rejection.
        bounded_success = False
        try:
            if getattr(message, "qos", None) != config.broker.qos:
                raise GatewayError(
                    GatewayErrorCode.INVALID_MQTT_DELIVERY,
                    "inbound MQTT delivery must use configured QoS 1",
                )
            if getattr(message, "retain", None) is not False:
                raise GatewayError(
                    GatewayErrorCode.INVALID_MQTT_DELIVERY,
                    "retained MQTT load-cell deliveries are forbidden in V0",
                )
            result = processor.process_message(message.topic, message.payload)
            _json_line({"event": "message_result", **result.to_dict()})
            if processor.has_pending_hybrid_delivery:
                error = GatewayError(
                    GatewayErrorCode.RUNTIME_RETRY_REQUIRED,
                    "hybrid runtime left the immutable source frame "
                    "unacknowledged; keep the MQTT delivery pending for "
                    "persistent-session redelivery",
                )
                processor.status.record_runtime_failure(
                    error.code.value, error.detail
                )
                # Preserve the exact in-memory source/site cursor while a
                # graceful persistent-session reconnect asks the broker to
                # redeliver the unacknowledged QoS 1 packet.
                request_redelivery(error)
            elif acknowledge(message):
                bounded_success = processor.status.snapshot()["ready"] is True
        except AgentRuntimeError as exc:
            processor.status.record_runtime_failure(exc.incident_code, exc.detail)
            emit_rejection(message.topic, exc.incident_code, exc.detail)
            # Do not PUBACK a fail-closed runtime incident.  The persistent
            # broker session retains QoS 1 delivery for a repaired restart.
            terminate(exc)
        except GatewayError as exc:
            processor.status.record_failure(exc.code.value, exc.detail)
            emit_rejection(message.topic, exc.code.value, exc.detail)
            # Strict wire/identity/replay rejection is terminal for this
            # delivery, so acknowledge it to avoid a poison-message loop.
            if getattr(message, "qos", None) == config.broker.qos:
                acknowledge(message)
        except Exception as exc:  # pragma: no cover - last-resort loop isolation
            detail = f"{type(exc).__name__}: {exc}"
            error = GatewayError(
                GatewayErrorCode.UNEXPECTED_PROCESSING_FAILURE, detail
            )
            processor.status.record_failure(error.code.value, error.detail)
            emit_rejection(message.topic, error.code.value, error.detail)
            # Unknown processing failures are retryable by default.  Keep the
            # delivery unacknowledged and stop for supervised restart.
            terminate(error)
        finally:
            if max_messages is not None and received_count >= max_messages:
                bounded_complete = True
                client.disconnect()

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    with status_server:
        try:
            first_connection = True
            while True:
                if first_connection:
                    connect_result = client.connect(
                        config.broker.host,
                        config.broker.port,
                        keepalive=config.broker.keepalive_s,
                    )
                    first_connection = False
                else:
                    connect_result = client.reconnect()
                if connect_result != mqtt.MQTT_ERR_SUCCESS:
                    raise GatewayError(
                        GatewayErrorCode.MQTT_UNAVAILABLE,
                        f"MQTT connect returned {connect_result}",
                    )
                result = client.loop_forever(retry_first_connection=False)
                if terminal_failure is not None:
                    raise terminal_failure
                if redelivery_requested:
                    if max_messages is not None and bounded_complete:
                        assert redelivery_failure is not None
                        raise redelivery_failure
                    redelivery_requested = False
                    redelivery_failure = None
                    subscription_mid = None
                    # Bound retry pressure without making transport time part
                    # of any canonical observation or runtime identifier.
                    threading.Event().wait(REDELIVERY_BACKOFF_S)
                    continue
                if result != mqtt.MQTT_ERR_SUCCESS and not bounded_complete:
                    raise GatewayError(
                        GatewayErrorCode.MQTT_UNAVAILABLE,
                        f"MQTT network loop returned {result}",
                    )
                if max_messages is not None and not bounded_success:
                    raise GatewayError(
                        GatewayErrorCode.SOURCE_PROTOCOL,
                        "bounded smoke ended without a ready accepted delivery",
                    )
                if max_messages is None:
                    raise GatewayError(
                        GatewayErrorCode.MQTT_UNAVAILABLE,
                        "MQTT network loop ended unexpectedly",
                    )
                break
        finally:
            processor.set_broker_connected(False)
            client.disconnect()
    return received_count


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local Edge Gateway Live Input V0 composition root."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="strict nxt-edge-gateway/config/v0 YAML file",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate commissioned identities and exit without importing MQTT",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=None,
        help="optional positive received-message limit for a bounded smoke test",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    site = commissioned_site()
    try:
        config = load_gateway_config(args.config, site=site)
        if args.max_messages is not None and args.max_messages < 1:
            raise _config_error("--max-messages must be a positive integer")
        if args.check_config:
            _json_line(
                {
                    "event": "config_valid",
                    "schema": config.schema,
                    "mode": config.mode.value,
                    "site_id": config.site_id,
                    "deployment_id": config.deployment_id,
                    "disclaimer": _disclaimer(config.mode),
                }
            )
            return 0
        run_gateway(config, site=site, max_messages=args.max_messages)
        return 0
    except KeyboardInterrupt:
        return 130
    except AgentRuntimeError as exc:
        _json_line(
            {
                "event": "gateway_failed",
                "failure": {
                    "code": exc.incident_code,
                    "detail": _bounded_detail(exc.detail),
                },
            }
        )
        return 2
    except GatewayError as exc:
        _json_line(
            {
                "event": "gateway_failed",
                "failure": {"code": exc.code.value, "detail": exc.detail},
            }
        )
        return 2
    except OSError as exc:
        _json_line(
            {
                "event": "gateway_failed",
                "failure": {"code": "mqtt_io_error", "detail": str(exc)},
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
