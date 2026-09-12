"""Versioned wire contracts for the simulated Edge <-> robot task exchange.

Three message families exist and no others:

* ``nxt.edge.robot-status/v1``  robot -> Edge, QoS 0, never retained;
* ``nxt.edge.task.request/v1``  Edge -> robot, QoS 1, never retained;
* ``nxt.edge.task.event/v1``    robot -> Edge, QoS 1, never retained.

Every field is required, unknown and duplicate keys fail closed, and
``environment.kind`` is a single-member vocabulary: ``SIMULATION``.  There
is no live/production value, no flag selects one, and a different kind is
a new protocol version that must pass the repository architecture gate.

Identity is content-derived.  ``task_id`` is the digest of every other
request field, so a byte-identical resend is the same task on both ends
and a request whose ``task_id`` does not equal its content is rejected at
sequence zero without touching any task history.

This module is stdlib-only and imports no other ``nxt_*`` package.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping

STATUS_SCHEMA = "nxt.edge.robot-status/v1"
REQUEST_SCHEMA = "nxt.edge.task.request/v1"
EVENT_SCHEMA = "nxt.edge.task.event/v1"
CONFIG_SCHEMA = "nxt-edge-task/config/v1"

ENVIRONMENT_KIND_SIMULATION = "SIMULATION"
TASK_TYPE_COLLECT_BALLS_ZONE = "COLLECT_BALLS_ZONE"
SUPPORTED_TASK_TYPES = frozenset({TASK_TYPE_COLLECT_BALLS_ZONE})

MAX_PAYLOAD_BYTES = 65_536
MAX_DETAIL_CHARS = 512
MAX_REASON_CHARS = 96
MAX_SEQUENCE = 2**63 - 1
MAX_DEPTH = 8

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_HEX24 = re.compile(r"^[0-9a-f]{24}$")


class ErrorCode(StrEnum):
    """Machine-readable rejection codes for wire and admission failures."""

    PAYLOAD_TOO_LARGE = "payload_too_large"
    INVALID_JSON = "invalid_json"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    INVALID_FIELD = "invalid_field"
    ENVIRONMENT_MISMATCH = "simulation_env_mismatch"
    SITE_MISMATCH = "site_mismatch"
    DEPLOYMENT_MISMATCH = "deployment_mismatch"
    TARGET_MISMATCH = "target_mismatch"
    UNKNOWN_ROBOT = "unknown_robot"
    UNKNOWN_ZONE = "unknown_zone"
    UNKNOWN_TASK = "unknown_task"
    TASK_ID_CONTENT_CONFLICT = "task_id_content_conflict"
    INVALID_TOPIC = "invalid_topic"
    INVALID_DELIVERY = "invalid_mqtt_delivery"
    ROBOT_HAS_ACTIVE_TASK = "robot_has_active_task"
    AUTHORIZATION_BLOCKED = "authorization_blocked"
    INVALID_CONFIG = "invalid_config"


class EdgeTaskError(ValueError):
    """A typed, bounded protocol or configuration failure."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        self.code = code
        self.detail = bounded_detail(detail)
        super().__init__(f"{code.value}: {self.detail}")


class EventKind(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    PROGRESS = "PROGRESS"
    ASSISTANCE_REQUIRED = "ASSISTANCE_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"


TERMINAL_KINDS = frozenset(
    {EventKind.SUCCEEDED, EventKind.FAILED, EventKind.INCONCLUSIVE, EventKind.REJECTED}
)


class Availability(StrEnum):
    """Robot-declared availability.

    The literal values reuse the Shadow Ops ``RobotStatus`` vocabulary
    (verified by a parity test in the test suite; this package imports
    nothing from it) minus ``offline``: being offline is an Edge-derived
    liveness fact, never a robot declaration.
    """

    AVAILABLE = "available"
    BUSY = "busy"
    CHARGING = "charging"
    PAUSED = "paused"
    AWAITING_HUMAN = "awaiting_human"
    FAULTED = "faulted"
    ESTOPPED = "estopped"


class ReasonClass(StrEnum):
    """How a robot-reported reason code is classified on the Edge."""

    ROBOT_CONDITION = "robot_condition"
    PROTOCOL_ENTRY = "protocol_entry"
    UNKNOWN = "unknown"


# Closed reason vocabulary.  Unknown codes stay visible as ``unknown:<raw>``.
ROBOT_CONDITION_REASONS = frozenset(
    {
        "robot_faulted",
        "estop_latched",
        "energy_insufficient",
        "capability_unavailable",
        "cannot_continue",
        "needs_manual_recharge",
        "unsafe_return_unconfirmed",
        "not_started_after_restart",
        "interrupted_execution_unknown_outcome",
    }
)
PROTOCOL_ENTRY_REASONS = frozenset(
    {
        "robot_busy",
        "task_expired",
        "target_mismatch",
        "site_mismatch",
        "deployment_mismatch",
        "simulation_env_mismatch",
        "task_id_content_conflict",
        "unsupported_task_type",
        "unsupported_schema",
        "invalid_request",
        "unknown_zone",
    }
)


def reason_class(reason_code: str | None) -> ReasonClass | None:
    if reason_code is None:
        return None
    if reason_code in ROBOT_CONDITION_REASONS:
        return ReasonClass.ROBOT_CONDITION
    if reason_code in PROTOCOL_ENTRY_REASONS:
        return ReasonClass.PROTOCOL_ENTRY
    return ReasonClass.UNKNOWN


def normalize_reason(raw: object) -> str | None:
    """Keep known codes verbatim and preserve unknown codes as ``unknown:<raw>``."""

    if raw is None:
        return None
    if type(raw) is not str or not raw:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, "reason_code must be text or null")
    if raw in ROBOT_CONDITION_REASONS or raw in PROTOCOL_ENTRY_REASONS:
        return raw
    if raw.startswith("unknown:"):
        body = raw[len("unknown:"):]
    else:
        body = raw
    if not _REASON_TOKEN.fullmatch(body):
        raise EdgeTaskError(
            ErrorCode.INVALID_FIELD,
            "reason_code must be a bounded token of [A-Za-z0-9_.:-]",
        )
    return f"unknown:{body}"


# --------------------------------------------------------------------------
# Canonical serialization and identity
# --------------------------------------------------------------------------


def bounded_detail(detail: object) -> str:
    text = detail if type(detail) is str else repr(detail)
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) > MAX_DETAIL_CHARS:
        return text[: MAX_DETAIL_CHARS - 1] + "…"
    return text


def canonical_json(value: Any) -> str:
    """Byte-stable JSON: sorted keys, no whitespace, no NaN, UTF-8 text."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def derive_task_id(request_fields: Mapping[str, Any]) -> str:
    """``task_"" + digest of every request field except ``task_id``."""

    seed = {key: value for key, value in request_fields.items() if key != "task_id"}
    return "task_" + stable_digest(seed)[:24]


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, "datetime values must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: object, field_name: str = "timestamp") -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{field_name} must be UTC ISO-8601 text ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{field_name} is not ISO-8601: {exc}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Strict JSON decoding
# --------------------------------------------------------------------------


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise EdgeTaskError(ErrorCode.INVALID_JSON, f"non-finite JSON number {value!r}")


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, "JSON nesting exceeds the V0 depth limit")
    if isinstance(value, dict):
        for item in value.values():
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def decode_object(raw: bytes | bytearray | memoryview | str) -> dict[str, Any]:
    """Strictly decode one JSON object: bounded size, no duplicate keys, no NaN."""

    if isinstance(raw, str):
        data = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray, memoryview)):
        data = bytes(raw)
    else:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, "payload must be bytes or text")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise EdgeTaskError(
            ErrorCode.PAYLOAD_TOO_LARGE, f"payload of {len(data)} bytes exceeds {MAX_PAYLOAD_BYTES}"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, f"payload is not UTF-8: {exc.reason}") from exc
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except _DuplicateKey as exc:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, f"duplicate JSON key {exc.args[0]!r}") from exc
    except RecursionError as exc:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, "JSON nesting too deep") from exc
    except json.JSONDecodeError as exc:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, f"invalid JSON: {exc.msg}") from exc
    if type(value) is not dict:
        raise EdgeTaskError(ErrorCode.INVALID_JSON, "payload root must be a JSON object")
    _check_depth(value)
    return value


def exact_keys(payload: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if not isinstance(payload, Mapping):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{label} must be an object")
    keys = frozenset(payload)
    if keys != expected:
        missing = sorted(expected - keys)
        unknown = sorted(keys - expected)
        raise EdgeTaskError(
            ErrorCode.INVALID_FIELD, f"{label} keys mismatch: missing={missing} unknown={unknown}"
        )


def _identifier(value: object, name: str) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a bounded identifier")
    return value


def _optional_identifier(value: object, name: str) -> str | None:
    return None if value is None else _identifier(value, name)


def _bounded_int(value: object, name: str, *, minimum: int = 0, maximum: int = MAX_SEQUENCE) -> int:
    if isinstance(value, bool) or type(value) is not int or value < minimum or value > maximum:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _optional_bool(value: object, name: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a boolean or null")
    return value


def _optional_fraction(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a finite fraction in [0, 1]")
    return number


def _optional_finite(value: object, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a number or null")
    number = float(value)
    if not math.isfinite(number):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be finite")
    return number


def _environment(payload: object) -> dict[str, str]:
    exact_keys(payload, frozenset({"kind", "simulation_env_id"}), "environment")
    kind = payload["kind"]
    if kind != ENVIRONMENT_KIND_SIMULATION:
        raise EdgeTaskError(
            ErrorCode.ENVIRONMENT_MISMATCH,
            "environment.kind must be SIMULATION; no other kind exists in this protocol version",
        )
    return {
        "kind": ENVIRONMENT_KIND_SIMULATION,
        "simulation_env_id": _identifier(payload["simulation_env_id"], "environment.simulation_env_id"),
    }


# --------------------------------------------------------------------------
# Topics
# --------------------------------------------------------------------------


def status_topic(site_id: str, robot_id: str) -> str:
    return f"nxt/v1/sites/{site_id}/robots/{robot_id}/status"


def request_topic(site_id: str, robot_id: str) -> str:
    return f"nxt/v1/sites/{site_id}/robots/{robot_id}/task/request"


def event_topic(site_id: str, robot_id: str) -> str:
    return f"nxt/v1/sites/{site_id}/robots/{robot_id}/task/event"


def topic_parts(topic: object) -> tuple[str, str, str]:
    """Return ``(site_id, robot_id, family)`` where family is status|request|event.

    Only the three exact V1 shapes are accepted; wildcards, extra levels,
    and any other version fail closed.
    """

    if type(topic) is not str:
        raise EdgeTaskError(ErrorCode.INVALID_TOPIC, "topic must be text")
    parts = topic.split("/")
    if (
        len(parts) >= 7
        and parts[0] == "nxt"
        and parts[1] == "v1"
        and parts[2] == "sites"
        and parts[4] == "robots"
        and all(part for part in parts)
    ):
        site_id = parts[3]
        robot_id = parts[5]
        if not _IDENTIFIER.fullmatch(site_id) or not _IDENTIFIER.fullmatch(robot_id):
            raise EdgeTaskError(ErrorCode.INVALID_TOPIC, "topic site/robot segments must be bounded identifiers")
        rest = parts[6:]
        if rest == ["status"]:
            return site_id, robot_id, "status"
        if rest == ["task", "request"]:
            return site_id, robot_id, "request"
        if rest == ["task", "event"]:
            return site_id, robot_id, "event"
    raise EdgeTaskError(ErrorCode.INVALID_TOPIC, f"unsupported topic {topic!r}")


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

_STATUS_KEYS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "environment",
        "robot_id",
        "boot_id",
        "boot_sequence",
        "status_sequence",
        "reported_at_utc",
        "availability",
        "current_task",
        "capabilities",
        "energy",
        "safety",
        "fault_code",
        "location",
    }
)
_CURRENT_TASK_KEYS = frozenset({"task_id", "phase", "last_event_sequence"})
_CAPABILITY_KEYS = frozenset({"task_types"})
_ENERGY_KEYS = frozenset({"level_fraction", "can_continue", "needs_manual_recharge"})
_SAFETY_KEYS = frozenset({"estop_latched", "awaiting_human", "safe_return_confirmed"})
_LOCATION_KEYS = frozenset({"zone_id", "x_m", "y_m", "coordinate_frame"})


@dataclass(frozen=True, slots=True)
class RobotStatusMessage:
    site_id: str
    deployment_id: str
    simulation_env_id: str
    robot_id: str
    boot_id: str
    boot_sequence: int
    status_sequence: int
    reported_at_utc: str
    availability: Availability
    current_task: dict[str, Any] | None
    task_types: tuple[str, ...]
    level_fraction: float | None
    can_continue: bool | None
    needs_manual_recharge: bool | None
    estop_latched: bool | None
    awaiting_human: bool | None
    safe_return_confirmed: bool | None
    fault_code: str | None
    zone_id: str | None
    x_m: float | None
    y_m: float | None
    coordinate_frame: str | None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RobotStatusMessage":
        exact_keys(payload, _STATUS_KEYS, "robot-status")
        if payload["schema"] != STATUS_SCHEMA:
            raise EdgeTaskError(ErrorCode.UNSUPPORTED_SCHEMA, f"expected {STATUS_SCHEMA}")
        env = _environment(payload["environment"])
        current = payload["current_task"]
        if current is not None:
            exact_keys(current, _CURRENT_TASK_KEYS, "current_task")
            current = {
                "task_id": _task_identifier(current["task_id"]),
                "phase": _identifier(current["phase"], "current_task.phase"),
                "last_event_sequence": _bounded_int(
                    current["last_event_sequence"], "current_task.last_event_sequence"
                ),
            }
        exact_keys(payload["capabilities"], _CAPABILITY_KEYS, "capabilities")
        task_types = payload["capabilities"]["task_types"]
        if type(task_types) is not list or len(task_types) > 16:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "capabilities.task_types must be a short list")
        task_types_tuple = tuple(_identifier(item, "capabilities.task_types[]") for item in task_types)
        if len(set(task_types_tuple)) != len(task_types_tuple):
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "capabilities.task_types must be unique")
        exact_keys(payload["energy"], _ENERGY_KEYS, "energy")
        exact_keys(payload["safety"], _SAFETY_KEYS, "safety")
        exact_keys(payload["location"], _LOCATION_KEYS, "location")
        try:
            availability = Availability(payload["availability"])
        except ValueError as exc:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "availability is outside the closed vocabulary") from exc
        parse_utc(payload["reported_at_utc"], "reported_at_utc")
        return cls(
            site_id=_identifier(payload["site_id"], "site_id"),
            deployment_id=_identifier(payload["deployment_id"], "deployment_id"),
            simulation_env_id=env["simulation_env_id"],
            robot_id=_identifier(payload["robot_id"], "robot_id"),
            boot_id=_identifier(payload["boot_id"], "boot_id"),
            boot_sequence=_bounded_int(payload["boot_sequence"], "boot_sequence", minimum=1),
            status_sequence=_bounded_int(payload["status_sequence"], "status_sequence"),
            reported_at_utc=payload["reported_at_utc"],
            availability=availability,
            current_task=current,
            task_types=task_types_tuple,
            level_fraction=_optional_fraction(payload["energy"]["level_fraction"], "energy.level_fraction"),
            can_continue=_optional_bool(payload["energy"]["can_continue"], "energy.can_continue"),
            needs_manual_recharge=_optional_bool(
                payload["energy"]["needs_manual_recharge"], "energy.needs_manual_recharge"
            ),
            estop_latched=_optional_bool(payload["safety"]["estop_latched"], "safety.estop_latched"),
            awaiting_human=_optional_bool(payload["safety"]["awaiting_human"], "safety.awaiting_human"),
            safe_return_confirmed=_optional_bool(
                payload["safety"]["safe_return_confirmed"], "safety.safe_return_confirmed"
            ),
            fault_code=_optional_reason_text(payload["fault_code"], "fault_code"),
            zone_id=_optional_identifier(payload["location"]["zone_id"], "location.zone_id"),
            x_m=_optional_finite(payload["location"]["x_m"], "location.x_m"),
            y_m=_optional_finite(payload["location"]["y_m"], "location.y_m"),
            coordinate_frame=_optional_identifier(
                payload["location"]["coordinate_frame"], "location.coordinate_frame"
            ),
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "RobotStatusMessage":
        return cls.from_dict(decode_object(raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STATUS_SCHEMA,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": self.simulation_env_id},
            "robot_id": self.robot_id,
            "boot_id": self.boot_id,
            "boot_sequence": self.boot_sequence,
            "status_sequence": self.status_sequence,
            "reported_at_utc": self.reported_at_utc,
            "availability": self.availability.value,
            "current_task": None if self.current_task is None else dict(self.current_task),
            "capabilities": {"task_types": list(self.task_types)},
            "energy": {
                "level_fraction": self.level_fraction,
                "can_continue": self.can_continue,
                "needs_manual_recharge": self.needs_manual_recharge,
            },
            "safety": {
                "estop_latched": self.estop_latched,
                "awaiting_human": self.awaiting_human,
                "safe_return_confirmed": self.safe_return_confirmed,
            },
            "fault_code": self.fault_code,
            "location": {
                "zone_id": self.zone_id,
                "x_m": self.x_m,
                "y_m": self.y_m,
                "coordinate_frame": self.coordinate_frame,
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    def content_key(self) -> dict[str, Any]:
        """Everything except the per-message counters; drives change detection."""

        payload = self.to_dict()
        payload.pop("status_sequence")
        payload.pop("reported_at_utc")
        return payload


def _optional_reason_text(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not _REASON_TOKEN.fullmatch(value):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"{name} must be a bounded token or null")
    return value


def _task_identifier(value: object) -> str:
    if type(value) is not str or not value.startswith("task_") or not _HEX24.fullmatch(value[5:]):
        raise EdgeTaskError(ErrorCode.INVALID_FIELD, "task_id must be task_<24 hex>")
    return value


_REQUEST_KEYS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "environment",
        "task_id",
        "target_robot_id",
        "task_type",
        "parameters",
        "issued_at_utc",
        "expires_at_utc",
        "progress_window_s",
        "issued_by",
    }
)
_PARAMETER_KEYS = frozenset({"zone_id"})
_ISSUED_BY = re.compile(r"^SIMULATION_TEST_ENTRY:[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class TaskRequest:
    site_id: str
    deployment_id: str
    simulation_env_id: str
    task_id: str
    target_robot_id: str
    task_type: str
    zone_id: str
    issued_at_utc: str
    expires_at_utc: str
    progress_window_s: int
    issued_by: str

    @staticmethod
    def build(
        *,
        site_id: str,
        deployment_id: str,
        simulation_env_id: str,
        target_robot_id: str,
        task_type: str,
        zone_id: str,
        issued_at_utc: str,
        expires_at_utc: str,
        progress_window_s: int,
        issued_by: str,
    ) -> "TaskRequest":
        fields = {
            "schema": REQUEST_SCHEMA,
            "site_id": site_id,
            "deployment_id": deployment_id,
            "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": simulation_env_id},
            "target_robot_id": target_robot_id,
            "task_type": task_type,
            "parameters": {"zone_id": zone_id},
            "issued_at_utc": issued_at_utc,
            "expires_at_utc": expires_at_utc,
            "progress_window_s": progress_window_s,
            "issued_by": issued_by,
        }
        fields["task_id"] = derive_task_id(fields)
        return TaskRequest.from_dict(fields)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskRequest":
        exact_keys(payload, _REQUEST_KEYS, "task.request")
        if payload["schema"] != REQUEST_SCHEMA:
            raise EdgeTaskError(ErrorCode.UNSUPPORTED_SCHEMA, f"expected {REQUEST_SCHEMA}")
        env = _environment(payload["environment"])
        exact_keys(payload["parameters"], _PARAMETER_KEYS, "parameters")
        issued_at = parse_utc(payload["issued_at_utc"], "issued_at_utc")
        expires_at = parse_utc(payload["expires_at_utc"], "expires_at_utc")
        if expires_at <= issued_at:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "expires_at_utc must follow issued_at_utc")
        task_type = _identifier(payload["task_type"], "task_type")
        issued_by = payload["issued_by"]
        if type(issued_by) is not str or not _ISSUED_BY.fullmatch(issued_by):
            raise EdgeTaskError(
                ErrorCode.INVALID_FIELD,
                "issued_by must be SIMULATION_TEST_ENTRY:<operator>; no other issuer exists",
            )
        task_id = _task_identifier(payload["task_id"])
        expected = derive_task_id(payload)
        if task_id != expected:
            raise EdgeTaskError(
                ErrorCode.TASK_ID_CONTENT_CONFLICT, "task_id does not equal the digest of its content"
            )
        return cls(
            site_id=_identifier(payload["site_id"], "site_id"),
            deployment_id=_identifier(payload["deployment_id"], "deployment_id"),
            simulation_env_id=env["simulation_env_id"],
            task_id=task_id,
            target_robot_id=_identifier(payload["target_robot_id"], "target_robot_id"),
            task_type=task_type,
            zone_id=_identifier(payload["parameters"]["zone_id"], "parameters.zone_id"),
            issued_at_utc=payload["issued_at_utc"],
            expires_at_utc=payload["expires_at_utc"],
            progress_window_s=_bounded_int(payload["progress_window_s"], "progress_window_s", minimum=1, maximum=86_400),
            issued_by=issued_by,
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "TaskRequest":
        return cls.from_dict(decode_object(raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REQUEST_SCHEMA,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": self.simulation_env_id},
            "task_id": self.task_id,
            "target_robot_id": self.target_robot_id,
            "task_type": self.task_type,
            "parameters": {"zone_id": self.zone_id},
            "issued_at_utc": self.issued_at_utc,
            "expires_at_utc": self.expires_at_utc,
            "progress_window_s": self.progress_window_s,
            "issued_by": self.issued_by,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    def content_digest(self) -> str:
        return stable_digest(self.to_dict())

    @property
    def expires_at(self) -> datetime:
        return parse_utc(self.expires_at_utc)

    @property
    def issued_at(self) -> datetime:
        return parse_utc(self.issued_at_utc)


_EVENT_KEYS = frozenset(
    {
        "schema",
        "site_id",
        "deployment_id",
        "environment",
        "task_id",
        "robot_id",
        "boot_id",
        "boot_sequence",
        "event_sequence",
        "kind",
        "reason_code",
        "detail",
        "reported_at_utc",
        "progress",
    }
)
_PROGRESS_KEYS = frozenset({"phase"})


@dataclass(frozen=True, slots=True)
class TaskEvent:
    site_id: str
    deployment_id: str
    simulation_env_id: str
    task_id: str
    robot_id: str
    boot_id: str
    boot_sequence: int
    event_sequence: int
    kind: EventKind
    reason_code: str | None
    detail: str
    reported_at_utc: str
    phase: str | None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskEvent":
        exact_keys(payload, _EVENT_KEYS, "task.event")
        if payload["schema"] != EVENT_SCHEMA:
            raise EdgeTaskError(ErrorCode.UNSUPPORTED_SCHEMA, f"expected {EVENT_SCHEMA}")
        env = _environment(payload["environment"])
        try:
            kind = EventKind(payload["kind"])
        except ValueError as exc:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "kind is outside the closed vocabulary") from exc
        progress = payload["progress"]
        phase: str | None = None
        if progress is not None:
            exact_keys(progress, _PROGRESS_KEYS, "progress")
            phase = _identifier(progress["phase"], "progress.phase")
        detail = payload["detail"]
        if type(detail) is not str or len(detail) > MAX_DETAIL_CHARS:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, f"detail must be text of at most {MAX_DETAIL_CHARS} chars")
        event_sequence = _bounded_int(payload["event_sequence"], "event_sequence")
        reason = normalize_reason(payload["reason_code"])
        if reason != payload["reason_code"]:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "reason_code must be a known code or unknown:<token>")
        if event_sequence == 0 and kind is not EventKind.REJECTED:
            raise EdgeTaskError(ErrorCode.INVALID_FIELD, "event_sequence 0 is reserved for request-level REJECTED")
        parse_utc(payload["reported_at_utc"], "reported_at_utc")
        return cls(
            site_id=_identifier(payload["site_id"], "site_id"),
            deployment_id=_identifier(payload["deployment_id"], "deployment_id"),
            simulation_env_id=env["simulation_env_id"],
            task_id=_task_identifier(payload["task_id"]),
            robot_id=_identifier(payload["robot_id"], "robot_id"),
            boot_id=_identifier(payload["boot_id"], "boot_id"),
            boot_sequence=_bounded_int(payload["boot_sequence"], "boot_sequence", minimum=1),
            event_sequence=event_sequence,
            kind=kind,
            reason_code=reason,
            detail=detail,
            reported_at_utc=payload["reported_at_utc"],
            phase=phase,
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "TaskEvent":
        return cls.from_dict(decode_object(raw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EVENT_SCHEMA,
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "environment": {"kind": ENVIRONMENT_KIND_SIMULATION, "simulation_env_id": self.simulation_env_id},
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "boot_id": self.boot_id,
            "boot_sequence": self.boot_sequence,
            "event_sequence": self.event_sequence,
            "kind": self.kind.value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "reported_at_utc": self.reported_at_utc,
            "progress": None if self.phase is None else {"phase": self.phase},
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.to_dict())

    def content_digest(self) -> str:
        return stable_digest(self.to_dict())

    @property
    def order_key(self) -> tuple[int, int]:
        return (self.boot_sequence, self.event_sequence)

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS and self.event_sequence >= 1

    @property
    def is_request_level_rejection(self) -> bool:
        return self.event_sequence == 0


# --------------------------------------------------------------------------
# Deployment configuration (strict JSON, no live-hardware switch)
# --------------------------------------------------------------------------

_CONFIG_KEYS = frozenset({"schema", "site_id", "deployment_id", "simulation_env_id", "broker", "edge", "robots"})
_BROKER_KEYS = frozenset({"host", "port", "keepalive_s"})
_EDGE_KEYS = frozenset(
    {
        "client_id",
        "evidence_dir",
        "stale_after_s",
        "offline_after_s",
        "restart_grace_s",
        "min_republish_interval_s",
        "max_republish_attempts",
        "default_progress_window_s",
        "tick_interval_s",
    }
)
_ROBOT_KEYS = frozenset({"robot_id", "role", "task_types", "heartbeat_interval_s", "evidence_dir", "client_id"})
_ROLES = frozenset({"picker", "carrier"})


@dataclass(frozen=True, slots=True)
class RobotConfig:
    robot_id: str
    role: str
    task_types: tuple[str, ...]
    heartbeat_interval_s: float
    evidence_dir: str
    client_id: str


@dataclass(frozen=True, slots=True)
class EdgeTaskConfig:
    site_id: str
    deployment_id: str
    simulation_env_id: str
    broker_host: str
    broker_port: int
    keepalive_s: int
    edge_client_id: str
    edge_evidence_dir: str
    stale_after_s: float
    offline_after_s: float
    restart_grace_s: float
    min_republish_interval_s: float
    max_republish_attempts: int
    default_progress_window_s: int
    tick_interval_s: float
    robots: tuple[RobotConfig, ...]
    config_digest: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EdgeTaskConfig":
        try:
            exact_keys(payload, _CONFIG_KEYS, "config")
            if payload["schema"] != CONFIG_SCHEMA:
                raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"config schema must be {CONFIG_SCHEMA}")
            exact_keys(payload["broker"], _BROKER_KEYS, "config.broker")
            exact_keys(payload["edge"], _EDGE_KEYS, "config.edge")
            robots_raw = payload["robots"]
            if type(robots_raw) is not list or not robots_raw or len(robots_raw) > 8:
                raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "config.robots must list 1..8 robots")
            robots: list[RobotConfig] = []
            for item in robots_raw:
                exact_keys(item, _ROBOT_KEYS, "config.robots[]")
                role = item["role"]
                if role not in _ROLES:
                    raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "config.robots[].role must be picker or carrier")
                task_types = item["task_types"]
                if type(task_types) is not list:
                    raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "config.robots[].task_types must be a list")
                for task_type in task_types:
                    if task_type not in SUPPORTED_TASK_TYPES:
                        raise EdgeTaskError(
                            ErrorCode.INVALID_CONFIG, f"unsupported task_type {task_type!r} in config"
                        )
                robots.append(
                    RobotConfig(
                        robot_id=_identifier(item["robot_id"], "config.robots[].robot_id"),
                        role=role,
                        task_types=tuple(task_types),
                        heartbeat_interval_s=_positive_number(item["heartbeat_interval_s"], "heartbeat_interval_s"),
                        evidence_dir=_text(item["evidence_dir"], "config.robots[].evidence_dir"),
                        client_id=_identifier(item["client_id"], "config.robots[].client_id"),
                    )
                )
            robot_ids = [robot.robot_id for robot in robots]
            client_ids = [robot.client_id for robot in robots] + [payload["edge"]["client_id"]]
            if len(set(robot_ids)) != len(robot_ids):
                raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "config.robots robot_id values must be unique")
            if len(set(client_ids)) != len(client_ids):
                raise EdgeTaskError(ErrorCode.INVALID_CONFIG, "MQTT client ids must be unique across processes")
            edge = payload["edge"]
            broker = payload["broker"]
            return cls(
                site_id=_identifier(payload["site_id"], "config.site_id"),
                deployment_id=_identifier(payload["deployment_id"], "config.deployment_id"),
                simulation_env_id=_identifier(payload["simulation_env_id"], "config.simulation_env_id"),
                broker_host=_text(broker["host"], "config.broker.host"),
                broker_port=_bounded_int(broker["port"], "config.broker.port", minimum=1, maximum=65_535),
                keepalive_s=_bounded_int(broker["keepalive_s"], "config.broker.keepalive_s", minimum=1, maximum=3600),
                edge_client_id=_identifier(edge["client_id"], "config.edge.client_id"),
                edge_evidence_dir=_text(edge["evidence_dir"], "config.edge.evidence_dir"),
                stale_after_s=_positive_number(edge["stale_after_s"], "stale_after_s"),
                offline_after_s=_positive_number(edge["offline_after_s"], "offline_after_s"),
                restart_grace_s=_positive_number(edge["restart_grace_s"], "restart_grace_s"),
                min_republish_interval_s=_positive_number(edge["min_republish_interval_s"], "min_republish_interval_s"),
                max_republish_attempts=_bounded_int(edge["max_republish_attempts"], "max_republish_attempts", minimum=1, maximum=64),
                default_progress_window_s=_bounded_int(edge["default_progress_window_s"], "default_progress_window_s", minimum=1, maximum=86_400),
                tick_interval_s=_positive_number(edge["tick_interval_s"], "tick_interval_s"),
                robots=tuple(robots),
                config_digest=stable_digest(payload),
            )
        except EdgeTaskError as exc:
            if exc.code is ErrorCode.INVALID_CONFIG:
                raise
            raise EdgeTaskError(ErrorCode.INVALID_CONFIG, exc.detail) from exc

    @classmethod
    def from_json(cls, raw: bytes | str) -> "EdgeTaskConfig":
        return cls.from_dict(decode_object(raw))

    def robot(self, robot_id: str) -> RobotConfig:
        for robot in self.robots:
            if robot.robot_id == robot_id:
                return robot
        raise EdgeTaskError(ErrorCode.UNKNOWN_ROBOT, f"robot {robot_id!r} is not configured")

    @property
    def robot_ids(self) -> tuple[str, ...]:
        return tuple(robot.robot_id for robot in self.robots)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"{name} must be non-blank trimmed text")
    return value


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise EdgeTaskError(ErrorCode.INVALID_CONFIG, f"{name} must be positive and finite")
    return number


@dataclass(frozen=True, slots=True)
class AdmissionFacts:
    """Plain commissioned facts handed in by a composition root.

    The package never imports the commissioning package; the composition
    root projects the validated manifest into these identifiers and the
    manifest digest recorded as provenance on ``task_created``.
    """

    site_id: str
    deployment_id: str
    robot_ids: frozenset[str]
    zone_ids: frozenset[str]
    manifest_digest: str


__all__ = [
    "CONFIG_SCHEMA",
    "ENVIRONMENT_KIND_SIMULATION",
    "EVENT_SCHEMA",
    "MAX_PAYLOAD_BYTES",
    "PROTOCOL_ENTRY_REASONS",
    "REQUEST_SCHEMA",
    "ROBOT_CONDITION_REASONS",
    "STATUS_SCHEMA",
    "SUPPORTED_TASK_TYPES",
    "TASK_TYPE_COLLECT_BALLS_ZONE",
    "TERMINAL_KINDS",
    "AdmissionFacts",
    "Availability",
    "EdgeTaskConfig",
    "EdgeTaskError",
    "ErrorCode",
    "EventKind",
    "ReasonClass",
    "RobotConfig",
    "RobotStatusMessage",
    "TaskEvent",
    "TaskRequest",
    "bounded_detail",
    "canonical_bytes",
    "canonical_json",
    "decode_object",
    "derive_task_id",
    "event_topic",
    "exact_keys",
    "normalize_reason",
    "parse_utc",
    "reason_class",
    "request_topic",
    "stable_digest",
    "status_topic",
    "topic_parts",
    "utc_text",
]
