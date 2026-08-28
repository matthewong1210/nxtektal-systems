"""Course-local coordinate frame contract.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.
"""

from __future__ import annotations

import math

import pytest

from nxt_course_world_model import (
    CourseCoordinateFrame,
    CourseWorldModelError,
    LOCAL_FRAME_AXES,
    LOCAL_FRAME_HANDEDNESS,
    LOCAL_FRAME_UNIT,
)

from tests.course_world_model.conftest import make_frame


class TestLocalFrameConvention:
    def test_the_local_convention_is_right_handed_enu_metres(self):
        assert LOCAL_FRAME_AXES == ("east", "north", "up")
        assert LOCAL_FRAME_HANDEDNESS == "right"
        assert LOCAL_FRAME_UNIT == "m"

    def test_a_valid_frame_carries_the_commissioned_crs_identity(self):
        frame = make_frame()
        assert frame.frame_id == "pilot-course-a.course-frame.v1"
        assert frame.crs_kind == "epsg"
        assert frame.crs_identifier == "EPSG:32651"
        assert frame.crs_axes == ("east", "north", "up")
        assert frame.origin_crs_x == 346000.0
        assert frame.origin_crs_z == 5.0
        assert frame.vertical_basis

    def test_local_cartesian_kind_is_also_accepted(self):
        frame = make_frame(
            crs_kind="local_cartesian", crs_identifier="site-local-v1"
        )
        assert frame.crs_kind == "local_cartesian"


class TestFrameValidation:
    def test_unknown_crs_kind_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_frame(crs_kind="geodetic-magic")

    def test_malformed_epsg_identifier_is_rejected(self):
        for identifier in ("EPSG:", "EPSG:0", "32651", "epsg:32651"):
            with pytest.raises(CourseWorldModelError):
                make_frame(crs_identifier=identifier)

    def test_blank_identity_fields_are_rejected(self):
        for field in ("frame_id", "crs_identifier", "vertical_basis"):
            with pytest.raises(CourseWorldModelError):
                make_frame(**{field: "   "})

    def test_surrounding_whitespace_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_frame(frame_id=" padded ")

    def test_non_metre_units_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_frame(crs_horizontal_unit="ft")
        with pytest.raises(CourseWorldModelError):
            make_frame(crs_vertical_unit="ft")

    def test_unsupported_crs_axes_are_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_frame(crs_axes=("north", "east", "up"))
        with pytest.raises(CourseWorldModelError):
            make_frame(crs_axes=("east", "north"))

    def test_xyz_crs_axes_are_accepted(self):
        frame = make_frame(crs_axes=("x", "y", "z"))
        assert frame.crs_axes == ("x", "y", "z")

    def test_non_finite_origin_is_rejected(self):
        for value in (math.nan, math.inf, -math.inf):
            with pytest.raises(CourseWorldModelError):
                make_frame(origin_crs_x=value)
            with pytest.raises(CourseWorldModelError):
                make_frame(origin_crs_z=value)

    def test_boolean_origin_is_rejected(self):
        with pytest.raises(CourseWorldModelError):
            make_frame(origin_crs_y=True)


class TestFrameSerialization:
    def test_round_trip_preserves_identity(self):
        frame = make_frame()
        assert CourseCoordinateFrame.from_dict(frame.to_dict()) == frame

    def test_serialized_payload_declares_the_local_convention(self):
        payload = make_frame().to_dict()
        assert payload["local_axes"] == ["east", "north", "up"]
        assert payload["local_unit"] == "m"
        assert payload["handedness"] == "right"

    def test_unknown_payload_keys_are_rejected(self):
        payload = make_frame().to_dict()
        payload["extra"] = 1
        with pytest.raises((CourseWorldModelError, ValueError, TypeError)):
            CourseCoordinateFrame.from_dict(payload)

    def test_missing_payload_keys_are_rejected(self):
        payload = make_frame().to_dict()
        del payload["vertical_basis"]
        with pytest.raises((CourseWorldModelError, ValueError, TypeError)):
            CourseCoordinateFrame.from_dict(payload)
