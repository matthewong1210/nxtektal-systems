"""Strict manifest serialization and deserialization behavior."""

from __future__ import annotations

import json

import pytest

from nxt_commissioning import COMMISSIONING_SCHEMA, CommissionedSite

from .conftest import clone_payload


def test_manifest_roundtrip_is_exact_and_json_safe(commissioned_site):
    payload = commissioned_site.to_dict()
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
    restored = CommissionedSite.from_dict(json.loads(encoded))

    assert restored == commissioned_site
    assert restored.to_dict() == payload
    assert payload["schema"] == COMMISSIONING_SCHEMA


def test_serialization_uses_enum_strings_arrays_and_explicit_nulls(
    commissioned_site,
):
    payload = commissioned_site.to_dict()
    charger = next(
        asset for asset in payload["equipment_assets"] if asset["asset_id"] == "charger-01"
    )
    assert charger["asset_type"] == "charging_station"
    assert charger["manufacturer"] is None
    assert charger["model"] is None
    assert isinstance(payload["equipment_assets"], list)
    assert isinstance(
        payload["spatial_reference"]["geometry_references"][0]["points"], list
    )
    assert payload["sensor_bindings"][0]["calibration"]["status"] == "calibrated"


def test_serialization_is_deterministic(commissioned_site):
    first = json.dumps(
        commissioned_site.to_dict(), sort_keys=True, separators=(",", ":")
    )
    second = json.dumps(
        commissioned_site.to_dict(), sort_keys=True, separators=(",", ":")
    )
    assert first == second


@pytest.mark.parametrize(
    ("mutation", "token"),
    [
        (lambda payload: payload.update({"site_id_typo": "x"}), "site_id_typo"),
        (lambda payload: payload.pop("deployment_id"), "deployment_id"),
        (
            lambda payload: payload["location_metadata"].pop("locality"),
            "locality",
        ),
        (
            lambda payload: payload["sensor_bindings"][0]["calibration"].pop(
                "valid_until"
            ),
            "valid_until",
        ),
        (
            lambda payload: payload["provenance"].update({"confidence": 1.0}),
            "confidence",
        ),
    ],
)
def test_unknown_and_missing_keys_are_rejected(
    commissioned_site_payload, mutation, token
):
    payload = clone_payload(commissioned_site_payload)
    mutation(payload)
    with pytest.raises((TypeError, ValueError), match=token):
        CommissionedSite.from_dict(payload)


def test_unknown_manifest_schema_is_rejected(commissioned_site_payload):
    payload = clone_payload(commissioned_site_payload)
    payload["schema"] = "nxt-commissioning/manifest/v99"
    with pytest.raises(ValueError, match="schema"):
        CommissionedSite.from_dict(payload)


def test_nested_unordered_records_roundtrip_canonically(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    parameter = clone_payload(
        payload["operating_constraints"][0]["parameters"][0]
    )
    parameter.update({"name": "minimum_commanded_speed", "value": 0.1})
    payload["operating_constraints"][0]["parameters"].insert(0, parameter)
    site = CommissionedSite.from_dict(payload)

    assert CommissionedSite.from_dict(site.to_dict()) == site
    assert [
        item.name for item in site.operating_constraints[0].parameters
    ] == ["max_speed", "minimum_commanded_speed"]
