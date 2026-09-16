"""Frozen synthetic course-condition evidence, separate from range telemetry.

This pure contract reads no device, file, clock, map, policy, or runtime. It
does not add a commissioned camera channel or assemble FacilityState. A cart
position and a separately supplied target position are distinct estimates;
neither is inferred from the other. Scores are synthetic and not calibrated.
``frame_id`` identifies the independent captured image, not the map coordinate
frame. ``map_revision`` binds both position estimates to the referenced map's
course-local coordinate frame; image identity must change for a new capture.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Any

SCHEMA = "nxt-course-condition-observation/v0"
CONDITIONS = frozenset({"DIVOT", "BUNKER_SURFACE", "STANDING_WATER", "CLEAR"})
QUALITIES = frozenset({"USABLE", "BLURRED", "OCCLUDED", "POSE_UNCERTAIN"})
_MARKERS = {
    "schema": SCHEMA,
    "environment": "SIMULATION",
    "source_kind": "SYNTHETIC_FIXTURE",
    "score_calibration": "NOT_CALIBRATED",
}
_TIMESTAMP = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z",
    re.ASCII,
)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be a nonempty trimmed string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, not bool")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return 0.0 if result == 0.0 else result


def _utc(value: object) -> str:
    """Validate UTC calendar text without importing or reading a clock."""
    text = _text(value, "captured_at_utc")
    match = _TIMESTAMP.fullmatch(text)
    if match is None:
        raise ValueError("captured_at_utc requires YYYY-MM-DDTHH:MM:SS[.ffffff]Z")
    year, month, day, hour, minute, second = map(int, match.groups()[:6])
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    lengths = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if not (1 <= year <= 9999 and 1 <= month <= 12):
        raise ValueError("captured_at_utc has an invalid calendar date")
    if not (1 <= day <= lengths[month - 1] and hour < 24 and minute < 60 and second < 60):
        raise ValueError("captured_at_utc has an invalid date or time")
    fraction = (match.group(7) or "").rstrip("0")
    return text[:19] + ("." + fraction if fraction else "") + "Z"


@dataclass(frozen=True, slots=True)
class PositionEstimate:
    """Course-local XY metres and an explicitly synthetic accuracy radius."""

    x_m: float
    y_m: float
    accuracy_m: float

    def __post_init__(self) -> None:
        for field in ("x_m", "y_m", "accuracy_m"):
            object.__setattr__(self, field, _number(getattr(self, field), field))
        if self.accuracy_m <= 0:
            raise ValueError("accuracy_m must be positive; perfect accuracy is not asserted")

    @classmethod
    def from_dict(cls, value: object) -> PositionEstimate:
        if not isinstance(value, Mapping) or set(value) != {"x_m", "y_m", "accuracy_m"}:
            raise ValueError("position requires exactly x_m, y_m, accuracy_m")
        return cls(**value)


@dataclass(frozen=True, slots=True)
class CourseConditionObservation:
    """Evidence only: no issue confirmation, work order, or execution authority."""

    site_id: str
    deployment_id: str
    map_revision: str
    frame_id: str
    checkpoint_id: str
    hole_number: int
    feature_id: str
    cart_id: str
    camera_id: str
    captured_at_utc: str
    condition: str
    inspected_condition: str
    quality: str
    cart_position: PositionEstimate | None
    target_position: PositionEstimate | None
    detection_score: float
    evidence_ref: str

    def __post_init__(self) -> None:
        for name in (
            "site_id", "deployment_id", "map_revision", "frame_id", "checkpoint_id",
            "feature_id", "cart_id", "camera_id", "condition", "inspected_condition", "quality", "evidence_ref",
        ):
            _text(getattr(self, name), name)
        if type(self.hole_number) is not int or not 1 <= self.hole_number <= 18:
            raise ValueError("hole_number must be an integer from 1 through 18")
        if self.condition not in CONDITIONS or self.quality not in QUALITIES:
            raise ValueError("unsupported condition or observation quality")
        if self.inspected_condition not in CONDITIONS - {"CLEAR"}:
            raise ValueError("inspected_condition must specify the condition being checked")
        if self.condition != "CLEAR" and self.condition != self.inspected_condition:
            raise ValueError("a detected condition must match the condition being checked")
        if not self.evidence_ref.startswith("synthetic:") or not self.evidence_ref[10:]:
            raise ValueError("evidence_ref must identify explicit synthetic: evidence")
        if any(char.isspace() for char in self.evidence_ref):
            raise ValueError("evidence_ref must not contain whitespace")
        for name in ("cart_position", "target_position"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PositionEstimate):
                raise ValueError(f"{name} must be PositionEstimate or None")
        if self.quality == "USABLE" and (self.cart_position is None or self.target_position is None):
            raise ValueError("USABLE observations require distinct supplied cart and target estimates")
        score = _number(self.detection_score, "detection_score")
        if not 0 <= score <= 1:
            raise ValueError("detection_score must be between 0 and 1; it is not a probability")
        object.__setattr__(self, "detection_score", score)
        object.__setattr__(self, "captured_at_utc", _utc(self.captured_at_utc))

    def _body(self) -> dict[str, Any]:
        return {**_MARKERS, **asdict(self)}

    @property
    def observation_id(self) -> str:
        canonical = json.dumps(self._body(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return "observation_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {**self._body(), "observation_id": self.observation_id}

    @classmethod
    def from_dict(cls, body: object) -> CourseConditionObservation:
        keys = set(cls.__dataclass_fields__) | set(_MARKERS) | {"observation_id"}
        if not isinstance(body, Mapping) or set(body) != keys:
            raise ValueError("observation fields must match the versioned contract exactly")
        if any(body[key] != value for key, value in _MARKERS.items()):
            raise ValueError("only explicit SIMULATION synthetic, uncalibrated evidence is supported")
        values = {name: body[name] for name in cls.__dataclass_fields__}
        for name in ("cart_position", "target_position"):
            if values[name] is not None:
                values[name] = PositionEstimate.from_dict(values[name])
        result = cls(**values)
        if body["observation_id"] != result.observation_id:
            raise ValueError("observation content does not match its stable identity")
        return result


def build_observation(**values: Any) -> dict[str, Any]:
    """Build detached plain data through the frozen validated contract."""
    normalized = dict(values)
    for name in ("cart_position", "target_position"):
        value = normalized.get(name)
        if value is not None and not isinstance(value, PositionEstimate):
            normalized[name] = PositionEstimate.from_dict(value)
    return CourseConditionObservation(**normalized).to_dict()


def validate_observation(body: object) -> dict[str, Any]:
    """Validate exact schema and stable identity, returning a detached body."""
    return CourseConditionObservation.from_dict(body).to_dict()
