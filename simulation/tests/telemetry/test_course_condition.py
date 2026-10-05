"""Synthetic observation contracts stay honest about quality and identity."""

from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from nxt_telemetry.course_condition import (
    CourseConditionObservation, PositionEstimate, build_observation, validate_observation,
)


def fields(**changes):
    payload = {
        "site_id": "synthetic-course-18", "deployment_id": "synthetic-course-18-monitoring-v0",
        "map_revision": "sha256:example", "frame_id": "synthetic-course-18.enu.v1",
        "checkpoint_id": "cp-h01-fairway", "hole_number": 1,
        "feature_id": "hole-01-fairway", "cart_id": "synthetic-cart-1", "camera_id": "synthetic-camera-1",
        "captured_at_utc": "2026-09-16T08:00:00Z", "condition": "DIVOT", "inspected_condition": "DIVOT",
        "quality": "USABLE", "cart_position": {"x_m": 130, "y_m": 35, "accuracy_m": 0.5},
        "target_position": {"x_m": 130, "y_m": 90, "accuracy_m": 2},
        "detection_score": 0.8, "evidence_ref": "synthetic:frame-001",
    }
    return {**payload, **changes}


def test_explicit_synthetic_markers_and_stable_duplicate_identity():
    first = build_observation(**fields())
    duplicate = build_observation(**fields(captured_at_utc="2026-09-16T08:00:00.000Z"))
    assert first == duplicate
    assert first["environment"] == "SIMULATION"
    assert first["source_kind"] == "SYNTHETIC_FIXTURE"
    assert first["score_calibration"] == "NOT_CALIBRATED"
    assert first["cart_position"] != first["target_position"]
    body = {key: value for key, value in first.items() if key != "observation_id"}
    expected = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:32]
    assert first["observation_id"] == "observation_" + expected
    assert validate_observation(first) == first


def test_source_and_map_changes_are_new_observations():
    original = build_observation(**fields())["observation_id"]
    for change in ({"map_revision": "sha256:new"}, {"evidence_ref": "synthetic:frame-002"},
                   {"captured_at_utc": "2026-09-16T08:00:01Z"}, {"camera_id": "synthetic-camera-2"}):
        assert build_observation(**fields(**change))["observation_id"] != original


def test_frozen_contract_has_no_mutable_nested_position_and_builder_detaches_inputs():
    inputs = fields()
    body = build_observation(**inputs)
    record = CourseConditionObservation.from_dict(body)
    with pytest.raises(FrozenInstanceError):
        record.quality = "BLURRED"
    with pytest.raises(FrozenInstanceError):
        record.target_position.x_m = 1
    inputs["target_position"]["x_m"] = 999
    body["target_position"]["x_m"] = 888
    assert record.target_position.x_m == 130.0
    assert record.to_dict()["target_position"]["x_m"] == 130.0


@pytest.mark.parametrize("quality", ["BLURRED", "OCCLUDED", "POSE_UNCERTAIN"])
def test_unusable_missing_pose_is_preserved_not_replaced_with_cart_location(quality):
    body = build_observation(**fields(quality=quality, target_position=None, cart_position=None))
    assert body["target_position"] is None and body["cart_position"] is None
    assert validate_observation(body)["quality"] == quality


@pytest.mark.parametrize("position", ["cart_position", "target_position"])
def test_usable_requires_both_independent_positions(position):
    with pytest.raises(ValueError, match="USABLE"):
        build_observation(**fields(**{position: None}))


def test_clear_is_explicitly_scoped_to_condition_checked():
    body = build_observation(**fields(condition="CLEAR", inspected_condition="DIVOT"))
    assert body["condition"] == "CLEAR" and body["inspected_condition"] == "DIVOT"
    with pytest.raises(ValueError, match="match"):
        build_observation(**fields(inspected_condition="BUNKER_SURFACE"))
    with pytest.raises(ValueError, match="inspected_condition"):
        build_observation(**fields(condition="CLEAR", inspected_condition="CLEAR"))


@pytest.mark.parametrize("changes", [
    {"condition": "UNKNOWN"}, {"quality": "GOOD"}, {"hole_number": True}, {"hole_number": 0},
    {"hole_number": 19}, {"hole_number": 1.0}, {"detection_score": True},
    {"detection_score": -0.01}, {"detection_score": 1.01}, {"detection_score": float("nan")},
    {"detection_score": float("inf")}, {"evidence_ref": "https://camera.example/frame"},
    {"evidence_ref": "synthetic:"}, {"evidence_ref": "synthetic:has space"},
    {"site_id": " site"}, {"site_id": ""}, {"checkpoint_id": "cp\nline"},
    {"target_position": {"x_m": 1, "y_m": 2}},
    {"target_position": {"x_m": True, "y_m": 2, "accuracy_m": 1}},
    {"target_position": {"x_m": float("inf"), "y_m": 2, "accuracy_m": 1}},
    {"target_position": {"x_m": 1, "y_m": 2, "accuracy_m": 0}},
    {"target_position": {"x_m": 1, "y_m": 2, "accuracy_m": -1}},
])
def test_invalid_evidence_is_rejected(changes):
    with pytest.raises(ValueError):
        build_observation(**fields(**changes))


@pytest.mark.parametrize("stamp", [
    "2026-09-16T08:00:00", "2026-09-16T08:00:00+08:00", "2026-02-29T08:00:00Z",
    "0000-01-01T00:00:00Z", "2026-13-01T00:00:00Z", "2026-04-31T00:00:00Z",
    "2026-09-16T24:00:00Z", "2026-09-16T08:60:00Z", "2026-09-16T08:00:60Z",
])
def test_invalid_or_timezone_ambiguous_dates_are_rejected(stamp):
    with pytest.raises(ValueError):
        build_observation(**fields(captured_at_utc=stamp))


def test_leap_date_and_fractional_seconds_are_normalized_without_clock():
    body = build_observation(**fields(captured_at_utc="2028-02-29T08:00:00.120000Z"))
    assert body["captured_at_utc"] == "2028-02-29T08:00:00.12Z"


@pytest.mark.parametrize("field,value", [
    ("observation_id", "observation_forged"), ("quality", "BLURRED"),
    ("environment", "LIVE"), ("source_kind", "CAMERA"), ("score_calibration", "CALIBRATED"),
])
def test_identity_or_provenance_tampering_is_rejected(field, value):
    body = build_observation(**fields())
    body[field] = value
    with pytest.raises(ValueError):
        validate_observation(body)


def test_exact_contract_preserves_missingness_instead_of_defaulting():
    body = build_observation(**fields())
    del body["quality"]
    with pytest.raises(ValueError, match="exactly"):
        validate_observation(body)
    body = build_observation(**fields())
    body["confirmed"] = True
    with pytest.raises(ValueError, match="exactly"):
        validate_observation(body)
    with pytest.raises(TypeError):
        build_observation(**{key: value for key, value in fields().items() if key != "target_position"})


def test_direct_position_input_has_same_canonical_identity():
    normal = build_observation(**fields())
    direct = build_observation(**fields(target_position=PositionEstimate(130.0, 90.0, 2.0)))
    assert direct == normal
