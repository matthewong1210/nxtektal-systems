"""Safe, deterministic one-way projections from commissioned facts."""

from __future__ import annotations

import dataclasses
import json

import pytest

from nxt_commissioning import (
    CommissionedSite,
    LegacySiteConfigContext,
    ProjectionError,
    canonical_projection_json,
    project_digital_twin_layout,
    project_legacy_site_config,
    project_site_config,
    project_telemetry_adapter_config,
)
from nxt_telemetry.observations import SiteConfig

from .conftest import clone_payload


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


def test_site_config_projection_maps_only_static_commissioned_facts(
    commissioned_site,
):
    projected = project_site_config(commissioned_site)
    assert projected["schema"] == "nxt-commissioning/site-config-projection/v0"
    assert projected["site_id"] == commissioned_site.site_id
    assert projected["deployment_id"] == commissioned_site.deployment_id
    assert projected["timezone"] == "Asia/Shanghai"
    assert projected["zone_ids"] == ["zone-east"]
    assert projected["robot_ids"] == ["robot-01"]
    assert [asset["asset_id"] for asset in projected["equipment"]] == [
        "charger-01",
        "washer-01",
    ]
    washer = next(
        item for item in projected["equipment"] if item["asset_id"] == "washer-01"
    )
    assert washer["capacity"][0]["value"] == 200
    assert projected["operating_constraints"][0]["safety_relevant"] is True


def test_legacy_site_config_projection_matches_constructor_exactly(
    commissioned_site,
):
    context = LegacySiteConfigContext(
        scenario_name="commissioned-compatibility-check",
        seed=7,
        total_balls=8000,
        staff_capacity=2,
        wet_ground_speed_multiplier=0.8,
        open_minute=360,
        close_minute=1320,
        forecast_bucket_minutes=30,
        provenance=commissioned_site.provenance,
    )
    payload = project_legacy_site_config(commissioned_site, context)
    config = SiteConfig(**payload)

    assert set(payload) == {field.name for field in dataclasses.fields(SiteConfig)}
    assert config.robot_ids == ("robot-01",)
    assert config.robot_payload_capacity == (("robot-01", 600),)
    assert config.station_ids == ()
    assert config.washer_throughput_bpm == 40.0
    assert config.washer_batch_size == 200
    assert config.charger_slots == 2
    assert project_legacy_site_config(commissioned_site, context) == payload


def test_digital_twin_projection_uses_surveyed_local_offsets(commissioned_site):
    projected = project_digital_twin_layout(commissioned_site)
    assert projected["schema"] == "nxt-commissioning/digital-twin-layout/v0"
    assert projected["coordinate_frame"]["source_crs"]["identifier"] == (
        "EPSG:32651"
    )
    assert projected["coordinate_frame"]["units"] == "m"
    geometries = {
        item["geometry_id"]: item for item in projected["geometry_references"]
    }
    washer_point = geometries["geom-washer-01"]["points"][0]
    assert {
        key: washer_point[key] for key in ("x_m", "y_m", "z_m")
    } == {
        "x_m": 2.0,
        "y_m": 3.0,
        "z_m": 0.0,
    }
    assert projected["zone_definitions"][0]["geometry_reference"] == (
        "geom-zone-east"
    )
    assert projected["robots"][0]["operating_zones"] == ["zone-east"]


def test_telemetry_projection_preserves_binding_and_calibration(commissioned_site):
    projected = project_telemetry_adapter_config(commissioned_site)
    assert projected["schema"] == (
        "nxt-commissioning/telemetry-adapter-config/v0"
    )
    bindings = {item["sensor_id"]: item for item in projected["bindings"]}
    load_cell = bindings["sensor-load-02"]
    assert load_cell["source"] == "plc.range-01"
    assert load_cell["channel"] == "wash.washer.wip"
    assert load_cell["associated_asset_id"] == "washer-01"
    assert load_cell["units"] == "balls"
    assert load_cell["observation_source_type"] == "sensor"
    assert load_cell["calibration"]["calibration_id"] == "CAL-LC-014-2026"
    assert load_cell["calibration"]["provenance"]["source_type"] == (
        "sensor_calibration"
    )


@pytest.mark.parametrize(
    "projector",
    [
        project_site_config,
        project_digital_twin_layout,
        project_telemetry_adapter_config,
    ],
)
def test_projection_is_repeatable_json_and_contains_no_runtime_state(
    commissioned_site, projector
):
    first = projector(commissioned_site)
    second = projector(commissioned_site)
    assert canonical_projection_json(first) == canonical_projection_json(second)
    json.dumps(first, sort_keys=True, allow_nan=False)

    forbidden = {
        "t_s",
        "minute_of_day",
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
    assert not (_nested_keys(first) & forbidden)


@pytest.mark.parametrize(
    "projector",
    [
        project_site_config,
        project_digital_twin_layout,
        project_telemetry_adapter_config,
    ],
)
def test_projection_determinism_is_independent_of_manifest_list_order(
    commissioned_site_payload, projector
):
    original = CommissionedSite.from_dict(commissioned_site_payload)
    reordered_payload = clone_payload(commissioned_site_payload)
    for key in (
        "operating_constraints",
        "equipment_assets",
        "robot_assets",
        "sensor_bindings",
    ):
        reordered_payload[key].reverse()
    reordered_payload["spatial_reference"]["geometry_references"].reverse()
    reordered_payload["spatial_reference"]["zone_definitions"].reverse()
    reordered = CommissionedSite.from_dict(reordered_payload)

    assert canonical_projection_json(projector(original)) == (
        canonical_projection_json(projector(reordered))
    )


def test_projected_payload_is_detached_from_manifest(commissioned_site):
    projected = project_site_config(commissioned_site)
    projected["equipment"][0]["capacity"][0]["value"] = 999
    projected["zone_ids"].clear()

    fresh = project_site_config(commissioned_site)
    assert all(
        value["value"] != 999
        for asset in fresh["equipment"]
        for value in asset["capacity"]
    )
    assert fresh["zone_ids"] == ["zone-east"]


def test_canonical_projection_json_has_stable_compact_key_order():
    assert canonical_projection_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_twin_projection_rejects_units_it_cannot_transform(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    coordinate_system = payload["spatial_reference"]["coordinate_system"]
    coordinate_system.update(
        {
            "kind": "local_cartesian",
            "identifier": "LOCAL:survey-grid",
            "horizontal_unit": "ft",
            "vertical_unit": "ft",
            "axes": ["x", "y", "z"],
        }
    )
    site = CommissionedSite.from_dict(payload)
    with pytest.raises(ProjectionError, match="metres"):
        project_digital_twin_layout(site)


def test_all_projection_boundaries_preserve_root_provenance(commissioned_site):
    expected = commissioned_site.provenance.to_dict()
    assert project_site_config(commissioned_site)["provenance"] == expected
    assert project_digital_twin_layout(commissioned_site)["provenance"] == expected
    assert project_telemetry_adapter_config(commissioned_site)["provenance"] == (
        expected
    )
