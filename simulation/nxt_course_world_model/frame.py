"""The course-local coordinate frame and its commissioned CRS binding.

The course frame is a right-handed local ENU frame in metres:

* X points east, Y points north, Z points up;
* the origin is the commissioned facility origin, expressed here in
  the commissioned coordinate reference system's own coordinates;
* the vertical basis is an explicit declaration (V0 models declare
  local height above the commissioned facility origin elevation, not
  an orthometric datum).

The frame duplicates no commissioned truth: it records the identity of
the commissioned ``CoordinateReferenceSystem`` (kind, identifier,
units, axes) plus the commissioned origin coordinates so a consumer
can verify alignment against the validated site.  Device-frame
transforms into this frame are a future contract and are deliberately
absent here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from nxt_commissioning import CoordinateSystemKind

from .errors import CourseWorldModelError
from .geometry import require_finite_number

LOCAL_FRAME_AXES = ("east", "north", "up")
LOCAL_FRAME_HANDEDNESS = "right"
LOCAL_FRAME_UNIT = "m"

_EPSG_IDENTIFIER = re.compile(r"^EPSG:[1-9][0-9]*$")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SUPPORTED_CRS_AXES = (("east", "north", "up"), ("x", "y", "z"))

_FRAME_KEYS = frozenset(
    {
        "frame_id",
        "crs_kind",
        "crs_identifier",
        "crs_horizontal_unit",
        "crs_vertical_unit",
        "crs_axes",
        "origin_crs_x",
        "origin_crs_y",
        "origin_crs_z",
        "vertical_basis",
        "local_axes",
        "local_unit",
        "handedness",
    }
)


def _require_identifier(value: object, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise CourseWorldModelError(
            f"{field_name} must be a non-blank trimmed string"
        )
    if not _IDENTIFIER_PATTERN.fullmatch(value):
        raise CourseWorldModelError(
            f"{field_name} {value!r} is not a valid identifier"
        )
    return value


def _require_non_blank(value: object, field_name: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise CourseWorldModelError(
            f"{field_name} must be a non-blank trimmed string"
        )
    return value


@dataclass(frozen=True, slots=True)
class CourseCoordinateFrame:
    """One immutable course-local frame declaration."""

    frame_id: str
    crs_kind: str
    crs_identifier: str
    crs_horizontal_unit: str
    crs_vertical_unit: str
    crs_axes: tuple[str, ...]
    origin_crs_x: float
    origin_crs_y: float
    origin_crs_z: float
    vertical_basis: str

    def __post_init__(self) -> None:
        _require_identifier(self.frame_id, "frame_id")
        supported_kinds = tuple(
            kind.value for kind in CoordinateSystemKind
        )
        if self.crs_kind not in supported_kinds:
            raise CourseWorldModelError(
                f"crs_kind must be one of {sorted(supported_kinds)}; "
                f"got {self.crs_kind!r}"
            )
        _require_identifier(self.crs_identifier, "crs_identifier")
        if (
            self.crs_kind == CoordinateSystemKind.EPSG.value
            and _EPSG_IDENTIFIER.fullmatch(self.crs_identifier) is None
        ):
            raise CourseWorldModelError(
                "an EPSG coordinate reference requires an identifier in "
                f"'EPSG:<code>' format; got {self.crs_identifier!r}"
            )
        for field_name, unit in (
            ("crs_horizontal_unit", self.crs_horizontal_unit),
            ("crs_vertical_unit", self.crs_vertical_unit),
        ):
            _require_non_blank(unit, field_name)
            if unit != LOCAL_FRAME_UNIT:
                raise CourseWorldModelError(
                    f"{field_name} must be {LOCAL_FRAME_UNIT!r} so course "
                    f"coordinates stay metric; got {unit!r}"
                )
        if not isinstance(self.crs_axes, tuple):
            raise CourseWorldModelError("crs_axes must be a tuple")
        if tuple(self.crs_axes) not in _SUPPORTED_CRS_AXES:
            raise CourseWorldModelError(
                "crs_axes must be exactly ('east', 'north', 'up') or "
                f"('x', 'y', 'z'); got {self.crs_axes!r}"
            )
        for field_name, value in (
            ("origin_crs_x", self.origin_crs_x),
            ("origin_crs_y", self.origin_crs_y),
            ("origin_crs_z", self.origin_crs_z),
        ):
            require_finite_number(value, field_name)
        _require_non_blank(self.vertical_basis, "vertical_basis")

    def to_dict(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "crs_kind": self.crs_kind,
            "crs_identifier": self.crs_identifier,
            "crs_horizontal_unit": self.crs_horizontal_unit,
            "crs_vertical_unit": self.crs_vertical_unit,
            "crs_axes": list(self.crs_axes),
            "origin_crs_x": self.origin_crs_x,
            "origin_crs_y": self.origin_crs_y,
            "origin_crs_z": self.origin_crs_z,
            "vertical_basis": self.vertical_basis,
            "local_axes": list(LOCAL_FRAME_AXES),
            "local_unit": LOCAL_FRAME_UNIT,
            "handedness": LOCAL_FRAME_HANDEDNESS,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> "CourseCoordinateFrame":
        if not isinstance(data, Mapping):
            raise CourseWorldModelError("frame payload must be a mapping")
        actual = set(data)
        if actual != _FRAME_KEYS:
            missing = sorted(_FRAME_KEYS - actual)
            extra = sorted(actual - _FRAME_KEYS)
            raise CourseWorldModelError(
                f"frame payload keys must be exactly {sorted(_FRAME_KEYS)}; "
                f"missing={missing}, extra={extra}"
            )
        if data["local_axes"] != list(LOCAL_FRAME_AXES):
            raise CourseWorldModelError(
                "frame payload local_axes must declare "
                f"{list(LOCAL_FRAME_AXES)!r}"
            )
        if data["local_unit"] != LOCAL_FRAME_UNIT:
            raise CourseWorldModelError(
                f"frame payload local_unit must be {LOCAL_FRAME_UNIT!r}"
            )
        if data["handedness"] != LOCAL_FRAME_HANDEDNESS:
            raise CourseWorldModelError(
                "frame payload handedness must be "
                f"{LOCAL_FRAME_HANDEDNESS!r}"
            )
        raw_axes = data["crs_axes"]
        if not isinstance(raw_axes, list):
            raise CourseWorldModelError("crs_axes must be a list")
        return cls(
            frame_id=data["frame_id"],  # type: ignore[arg-type]
            crs_kind=data["crs_kind"],  # type: ignore[arg-type]
            crs_identifier=data["crs_identifier"],  # type: ignore[arg-type]
            crs_horizontal_unit=data[  # type: ignore[arg-type]
                "crs_horizontal_unit"
            ],
            crs_vertical_unit=data[  # type: ignore[arg-type]
                "crs_vertical_unit"
            ],
            crs_axes=tuple(raw_axes),
            origin_crs_x=data["origin_crs_x"],  # type: ignore[arg-type]
            origin_crs_y=data["origin_crs_y"],  # type: ignore[arg-type]
            origin_crs_z=data["origin_crs_z"],  # type: ignore[arg-type]
            vertical_basis=data["vertical_basis"],  # type: ignore[arg-type]
        )


__all__ = [
    "CourseCoordinateFrame",
    "LOCAL_FRAME_AXES",
    "LOCAL_FRAME_HANDEDNESS",
    "LOCAL_FRAME_UNIT",
]
