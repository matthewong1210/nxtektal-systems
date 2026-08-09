"""The manifest is immutable static configuration, never runtime state."""

from __future__ import annotations

import dataclasses

import pytest


def _nested_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {
            child
            for nested in value.values()
            for child in _nested_keys(nested)
        }
    if isinstance(value, list):
        return {child for nested in value for child in _nested_keys(nested)}
    return set()


def test_commissioned_site_and_nested_records_are_frozen(commissioned_site):
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.site_id = "another-site"
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.location_metadata.country_code = "US"
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.spatial_reference.facility_origin.x = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.equipment_assets[0].asset_id = "changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.robot_assets[0].payload_limits[0].value = 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        commissioned_site.sensor_bindings[0].calibration.method = "changed"


def test_all_nested_collections_are_immutable_tuples(commissioned_site):
    assert isinstance(commissioned_site.operating_constraints, tuple)
    assert isinstance(commissioned_site.equipment_assets, tuple)
    assert isinstance(commissioned_site.robot_assets, tuple)
    assert isinstance(commissioned_site.sensor_bindings, tuple)
    assert isinstance(commissioned_site.location_metadata.address_lines, tuple)
    assert isinstance(
        commissioned_site.spatial_reference.geometry_references, tuple
    )
    assert isinstance(commissioned_site.spatial_reference.zone_definitions, tuple)
    assert isinstance(
        commissioned_site.spatial_reference.geometry_references[0].points, tuple
    )
    assert isinstance(commissioned_site.robot_assets[0].operating_zones, tuple)
    assert isinstance(
        commissioned_site.robot_assets[0]
        .charging_requirements.electrical_requirements,
        tuple,
    )


def test_serialized_containers_are_detached_from_contract(commissioned_site):
    payload = commissioned_site.to_dict()
    payload["location_metadata"]["address_lines"].append("mutated")
    payload["equipment_assets"][0]["capacity"][0]["value"] = 999
    payload["spatial_reference"]["geometry_references"].clear()

    fresh = commissioned_site.to_dict()
    assert fresh["location_metadata"]["address_lines"] == [
        "88 NXT Drive",
        "Pudong New Area",
    ]
    assert all(
        value["value"] != 999
        for asset in fresh["equipment_assets"]
        for value in asset["capacity"]
    )
    assert fresh["spatial_reference"]["geometry_references"]


def test_manifest_has_no_runtime_state_fields(commissioned_site):
    forbidden = {
        "t_s",
        "minute_of_day",
        "current_robot_battery",
        "current_inventory",
        "current_tasks",
        "current_demand",
        "battery_frac",
        "payload_balls",
        "assigned_zone",
        "queue_length",
        "wip",
        "forecast_balls_per_minute",
    }
    assert not (_nested_keys(commissioned_site.to_dict()) & forbidden)


def test_address_lines_reject_scalar_string(commissioned_site):
    with pytest.raises(TypeError, match="address_lines"):
        dataclasses.replace(
            commissioned_site.location_metadata,
            address_lines="not-an-address-array",
        )
