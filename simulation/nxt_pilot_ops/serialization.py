"""Canonical serialization used by deterministic IDs and the ledger."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must be timezone-aware")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def to_primitive(value: Any) -> Any:
    """Convert supported values into an unambiguous JSON-compatible tree."""

    if dataclasses.is_dataclass(value):
        return {
            item.name: to_primitive(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        for key in value:
            if type(key) is not str:
                raise TypeError("deterministic mappings require string keys")
        return {key: to_primitive(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [to_primitive(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [to_primitive(item) for item in value]
        return sorted(converted, key=canonical_json)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not serializable")
        return 0.0 if value == 0.0 else value
    if value is None or type(value) in (str, int, bool):
        return value
    raise TypeError(f"unsupported deterministic serialization type: {type(value)!r}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
