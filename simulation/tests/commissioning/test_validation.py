"""Aggregate commissioning validation fails loudly at construction time."""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import CommissionedSite, CommissioningValidationError

from .conftest import clone_payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("site_id", ""),
        ("site_id", "site/escape"),
        ("deployment_id", " "),
        ("deployment_id", ".."),
        ("facility_name", ""),
        ("timezone", "Mars/Olympus_Mons"),
    ],
)
def test_required_identity_fields_are_validated(
    commissioned_site_payload, field, value
):
    payload = clone_payload(commissioned_site_payload)
    payload[field] = value
    with pytest.raises((CommissioningValidationError, ValueError), match=field):
        CommissionedSite.from_dict(payload)


def test_duplicate_equipment_id_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["equipment_assets"].append(copy.deepcopy(payload["equipment_assets"][0]))
    with pytest.raises(CommissioningValidationError, match="duplicate asset ID"):
        CommissionedSite.from_dict(payload)


def test_equipment_and_robot_share_one_asset_namespace(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    payload["robot_assets"][0]["robot_id"] = "washer-01"
    with pytest.raises(CommissioningValidationError, match="duplicate asset ID"):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("target", "capability"),
    [
        ("equipment", "robot_charging"),
        ("robot", "ball_washing"),
    ],
)
def test_type_incompatible_capability_is_rejected(
    commissioned_site_payload, target, capability
):
    payload = clone_payload(commissioned_site_payload)
    if target == "equipment":
        payload["equipment_assets"][0]["capabilities"][0]["capability"] = capability
    else:
        payload["robot_assets"][0]["capabilities"][0]["capability"] = capability
    with pytest.raises(CommissioningValidationError, match="incompatible"):
        CommissionedSite.from_dict(payload)


def test_duplicate_capability_declaration_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    capabilities = payload["robot_assets"][0]["capabilities"]
    capabilities.append(copy.deepcopy(capabilities[0]))
    with pytest.raises(CommissioningValidationError, match="duplicate capability"):
        CommissionedSite.from_dict(payload)


def test_duplicate_measured_value_name_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    capacity = payload["equipment_assets"][0]["capacity"]
    capacity.append(copy.deepcopy(capacity[0]))
    with pytest.raises(CommissioningValidationError, match="duplicate measured value"):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("path", "token"),
    [
        (("payload_limits",), "payload_limits"),
        (
            ("charging_requirements", "charging_station_ids"),
            "charging_station_ids",
        ),
        (
            ("charging_requirements", "electrical_requirements"),
            "electrical_requirements",
        ),
        (("safety_constraints",), "safety_constraints"),
    ],
)
def test_robot_safety_relevant_fields_have_no_silent_defaults(
    commissioned_site_payload, path, token
):
    payload = clone_payload(commissioned_site_payload)
    target = payload["robot_assets"][0]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = []
    with pytest.raises(CommissioningValidationError, match=token):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize(
    ("mutation", "token"),
    [
        (
            lambda payload: payload["equipment_assets"][0].update(
                {"location_reference": "geom-missing"}
            ),
            "location_reference",
        ),
        (
            lambda payload: payload["robot_assets"][0].update(
                {"operating_zones": ["zone-missing"]}
            ),
            "unknown zone",
        ),
        (
            lambda payload: payload["robot_assets"][0][
                "charging_requirements"
            ].update({"charging_station_ids": ["washer-01"]}),
            "non-charging",
        ),
        (
            lambda payload: payload["operating_constraints"][0].update(
                {"applies_to": ["unknown-target"]}
            ),
            "unknown target",
        ),
    ],
)
def test_cross_references_must_resolve(
    commissioned_site_payload, mutation, token
):
    payload = clone_payload(commissioned_site_payload)
    mutation(payload)
    with pytest.raises(CommissioningValidationError, match=token):
        CommissionedSite.from_dict(payload)


def test_multiple_manifest_defects_are_reported_together(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    payload["site_id"] = ""
    payload["timezone"] = "not/a-timezone"
    payload["robot_assets"][0]["operating_zones"] = ["missing-zone"]

    with pytest.raises(CommissioningValidationError) as caught:
        CommissionedSite.from_dict(payload)
    paths = {issue.path for issue in caught.value.issues}
    assert "site_id" in paths
    assert "timezone" in paths
    assert any(path.endswith("operating_zones[0]") for path in paths)


def test_equipment_type_requires_its_defining_capability(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    capability = payload["equipment_assets"][1]["capabilities"][0]
    capability["capability"] = "emergency_stop"
    capability["parameters"] = []
    with pytest.raises(CommissioningValidationError, match="defining capability"):
        CommissionedSite.from_dict(payload)


def test_capability_requires_semantic_parameter(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["equipment_assets"][0]["capabilities"][0]["parameters"] = []
    with pytest.raises(CommissioningValidationError, match="requires exactly one"):
        CommissionedSite.from_dict(payload)


def test_payload_limit_rejects_unrelated_measurement(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    limit = payload["robot_assets"][0]["payload_limits"][0]
    limit.update({"name": "max_temperature", "unit": "C"})
    with pytest.raises(CommissioningValidationError, match="payload capacity"):
        CommissionedSite.from_dict(payload)


def test_charging_requirements_reject_nonelectrical_measurement(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    requirement = payload["robot_assets"][0]["charging_requirements"][
        "electrical_requirements"
    ][0]
    requirement.update({"name": "ball_count", "unit": "balls"})
    with pytest.raises(CommissioningValidationError, match="electrical"):
        CommissionedSite.from_dict(payload)


def test_robot_safety_limit_requires_semantic_unit(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["robot_assets"][0]["safety_constraints"][0]["limit"]["unit"] = "balls"
    with pytest.raises(CommissioningValidationError, match="safety measurement"):
        CommissionedSite.from_dict(payload)


def test_site_safety_parameter_requires_semantic_unit(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["operating_constraints"][0]["parameters"][0]["unit"] = "bananas"
    with pytest.raises(CommissioningValidationError, match="safety measurement"):
        CommissionedSite.from_dict(payload)


def test_robot_safety_limit_must_be_non_negative(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["robot_assets"][0]["safety_constraints"][0]["limit"]["value"] = -5
    with pytest.raises(CommissioningValidationError, match="non-negative"):
        CommissionedSite.from_dict(payload)


def test_site_maximum_speed_must_be_positive(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["operating_constraints"][0]["parameters"][0]["value"] = -1
    with pytest.raises(CommissioningValidationError, match="greater than zero"):
        CommissionedSite.from_dict(payload)


def test_emergency_stop_distance_must_be_strictly_positive(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    limit = payload["robot_assets"][0]["safety_constraints"][0]["limit"]
    limit.update({"name": "emergency_stop_distance", "value": 0, "unit": "m"})
    with pytest.raises(CommissioningValidationError, match="greater than zero"):
        CommissionedSite.from_dict(payload)


def test_zero_maximum_slope_is_an_explicit_valid_limit(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    payload["robot_assets"][0]["safety_constraints"][0]["limit"]["value"] = 0
    site = CommissionedSite.from_dict(payload)
    assert site.robot_assets[0].safety_constraints[0].limit.value == 0


@pytest.mark.parametrize(
    ("lower_name", "lower_value", "upper_name", "upper_value", "unit"),
    [
        ("minimum_commanded_speed", 2, "max_speed", 1, "m/s"),
        ("min_temperature", 50, "max_temperature", -20, "C"),
    ],
)
def test_contradictory_site_safety_bounds_are_rejected(
    commissioned_site_payload,
    lower_name,
    lower_value,
    upper_name,
    upper_value,
    unit,
):
    payload = clone_payload(commissioned_site_payload)
    template = payload["operating_constraints"][0]["parameters"][0]
    lower = copy.deepcopy(template)
    lower.update({"name": lower_name, "value": lower_value, "unit": unit})
    upper = copy.deepcopy(template)
    upper.update({"name": upper_name, "value": upper_value, "unit": unit})
    payload["operating_constraints"][0]["parameters"] = [lower, upper]
    with pytest.raises(CommissioningValidationError, match="must not exceed"):
        CommissionedSite.from_dict(payload)
