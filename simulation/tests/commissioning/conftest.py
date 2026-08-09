"""Compact, fully-provenanced commissioned-facility fixtures."""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import CommissionedSite


def provenance(
    source_type: str,
    source_id: str,
    *,
    captured_at: str = "2026-07-15T02:30:00Z",
    captured_by: str = "commissioning@nxt.example",
    evidence_uri: str | None = None,
    notes: str | None = None,
) -> dict:
    return {
        "source_type": source_type,
        "source_id": source_id,
        "captured_at": captured_at,
        "captured_by": captured_by,
        "evidence_uri": evidence_uri or f"urn:nxt:evidence:{source_id}",
        "notes": notes or f"Evidence for {source_id}",
    }


def measured(name: str, value: int | float, unit: str, source: dict) -> dict:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "provenance": copy.deepcopy(source),
    }


def point(x: float, y: float, z: float, survey: dict) -> dict:
    return {
        "x": x,
        "y": y,
        "z": z,
        "provenance": copy.deepcopy(survey),
    }


def clone_payload(payload: dict) -> dict:
    """Clone an input manifest without relying on dataclass internals."""

    return copy.deepcopy(payload)


@pytest.fixture
def commissioned_site_payload() -> dict:
    survey = provenance(
        "survey_data",
        "survey-shanghai-2026-07",
        notes="Total-station survey in EPSG:32651.",
    )
    manufacturer = provenance(
        "manufacturer_specification",
        "aquawash-w400-rev-c",
        captured_at="2026-06-01T00:00:00Z",
        captured_by="supplier-data@nxt.example",
        notes="Supplier datasheet revision C.",
    )
    manual = provenance(
        "manual_measurement",
        "field-sheet-fm-003",
        captured_at="2026-07-16T03:00:00Z",
        notes="Commissioning engineer field measurement.",
    )
    operator = provenance(
        "operator_input",
        "work-order-cw-2026-001",
        captured_at="2026-08-08T09:00:00+08:00",
        captured_by="site-operator@nxt.example",
        notes="Confirmed during commissioning walk-through.",
    )
    calibration = provenance(
        "sensor_calibration",
        "calibration-batch-2026-07",
        captured_at="2026-07-20T01:00:00Z",
        captured_by="calibration-lab@nxt.example",
        notes="Traceable commissioning calibration certificates.",
    )

    origin = (346000.0, 3456000.0, 5.0)
    zone_points = [
        point(origin[0] + 10, origin[1] + 10, origin[2], survey),
        point(origin[0] + 30, origin[1] + 10, origin[2], survey),
        point(origin[0] + 30, origin[1] + 25, origin[2], survey),
        point(origin[0] + 10, origin[1] + 25, origin[2], survey),
    ]

    calibrated_load_cell = {
        "status": "calibrated",
        "calibration_id": "CAL-LC-014-2026",
        "calibrated_at": "2026-07-20T01:00:00Z",
        "valid_until": "2027-07-20T01:00:00Z",
        "method": "traceable reference load",
        "provenance": copy.deepcopy(calibration),
    }
    calibrated_battery_monitor = {
        "status": "calibrated",
        "calibration_id": "CAL-BAT-010-2026",
        "calibrated_at": "2026-07-21T01:00:00Z",
        "valid_until": "2027-07-21T01:00:00Z",
        "method": "traceable voltage reference",
        "provenance": copy.deepcopy(calibration),
    }

    return {
        "schema": "nxt-commissioning/manifest/v0",
        "site_id": "cn-shanghai-range-01",
        "deployment_id": "commissioning-2026-08",
        "facility_name": "NXT Shanghai Range",
        "timezone": "Asia/Shanghai",
        "location_metadata": {
            "address_lines": ["88 NXT Drive", "Pudong New Area"],
            "locality": "Shanghai",
            "region": "Shanghai",
            "postal_code": "201210",
            "country_code": "CN",
            "site_reference": "east-gate",
            "provenance": copy.deepcopy(operator),
        },
        "operating_constraints": [
            {
                "constraint_id": "site-robot-speed-limit",
                "category": "safety",
                "description": "Maximum autonomous robot speed on site.",
                "applies_to": ["site"],
                "parameters": [measured("max_speed", 1.5, "m/s", manual)],
                "safety_relevant": True,
                "provenance": copy.deepcopy(operator),
            }
        ],
        "spatial_reference": {
            "coordinate_system": {
                "kind": "epsg",
                "identifier": "EPSG:32651",
                "horizontal_unit": "m",
                "vertical_unit": "m",
                "axes": ["east", "north", "up"],
                "provenance": copy.deepcopy(survey),
            },
            "facility_origin": point(*origin, survey),
            "geometry_references": [
                {
                    "geometry_id": "geom-zone-east",
                    "geometry_type": "polygon",
                    "points": zone_points,
                    "source_uri": "urn:nxt:geometry:zone-east",
                    "provenance": copy.deepcopy(survey),
                },
                {
                    "geometry_id": "geom-washer-01",
                    "geometry_type": "point",
                    "points": [point(origin[0] + 2, origin[1] + 3, origin[2], survey)],
                    "source_uri": "urn:nxt:geometry:washer-01",
                    "provenance": copy.deepcopy(survey),
                },
                {
                    "geometry_id": "geom-charger-01",
                    "geometry_type": "point",
                    "points": [point(origin[0] + 5, origin[1] + 3, origin[2], survey)],
                    "source_uri": "urn:nxt:geometry:charger-01",
                    "provenance": copy.deepcopy(survey),
                },
            ],
            "zone_definitions": [
                {
                    "zone_id": "zone-east",
                    "name": "East collection zone",
                    "zone_type": "collection",
                    "geometry_reference": "geom-zone-east",
                    "safety_classification": "robot-operating-area",
                    "provenance": copy.deepcopy(survey),
                }
            ],
        },
        # Reverse lexical order is intentional; aggregate/projection ordering
        # must be deterministic rather than an accident of authoring order.
        "equipment_assets": [
            {
                "asset_id": "washer-01",
                "asset_type": "washer",
                "manufacturer": "AquaWash",
                "model": "W400",
                "capabilities": [
                    {
                        "capability": "ball_washing",
                        "parameters": [
                            measured("rated_throughput", 40, "balls/min", manufacturer)
                        ],
                        "provenance": copy.deepcopy(manufacturer),
                    }
                ],
                "capacity": [measured("batch_capacity", 200, "balls", manufacturer)],
                "location_reference": "geom-washer-01",
                "provenance": copy.deepcopy(manufacturer),
            },
            {
                "asset_id": "charger-01",
                "asset_type": "charging_station",
                "manufacturer": None,
                "model": None,
                "capabilities": [
                    {
                        "capability": "robot_charging",
                        "parameters": [measured("nominal_voltage", 48, "V", manual)],
                        "provenance": copy.deepcopy(manual),
                    }
                ],
                "capacity": [measured("slot_count", 2, "count", manual)],
                "location_reference": "geom-charger-01",
                "provenance": copy.deepcopy(manual),
            },
        ],
        "robot_assets": [
            {
                "robot_id": "robot-01",
                "capabilities": [
                    {
                        "capability": "material_transport",
                        "parameters": [],
                        "provenance": copy.deepcopy(manufacturer),
                    },
                    {
                        "capability": "autonomous_navigation",
                        "parameters": [measured("max_speed", 1.5, "m/s", manufacturer)],
                        "provenance": copy.deepcopy(manufacturer),
                    },
                    {
                        "capability": "docking",
                        "parameters": [],
                        "provenance": copy.deepcopy(manufacturer),
                    },
                    {
                        "capability": "emergency_stop",
                        "parameters": [],
                        "provenance": copy.deepcopy(manufacturer),
                    },
                ],
                "payload_limits": [measured("max_payload", 600, "balls", manufacturer)],
                "operating_zones": ["zone-east"],
                "charging_requirements": {
                    "charging_station_ids": ["charger-01"],
                    "connector_type": "nxt-dock-v1",
                    "electrical_requirements": [
                        measured("nominal_voltage", 48, "V", manufacturer),
                        measured("max_current", 20, "A", manufacturer),
                    ],
                    "provenance": copy.deepcopy(manufacturer),
                },
                "safety_constraints": [
                    {
                        "constraint_id": "robot-max-slope",
                        "description": "Maximum permitted ground slope.",
                        "limit": measured("max_slope", 5, "deg", manufacturer),
                        "provenance": copy.deepcopy(manufacturer),
                    }
                ],
                "provenance": copy.deepcopy(manufacturer),
            }
        ],
        "sensor_bindings": [
            {
                "sensor_id": "sensor-load-02",
                "sensor_type": "load_cell",
                "source": "plc.range-01",
                "channel": "wash.washer.wip",
                "associated_asset_id": "washer-01",
                "units": "balls",
                "calibration": calibrated_load_cell,
                "provenance": copy.deepcopy(calibration),
            },
            {
                "sensor_id": "sensor-battery-01",
                "sensor_type": "battery_monitor",
                "source": "fleet.robot-01",
                "channel": "robot.robot-01.battery_frac",
                "associated_asset_id": "robot-01",
                "units": "1",
                "calibration": calibrated_battery_monitor,
                "provenance": copy.deepcopy(calibration),
            },
        ],
        "provenance": provenance(
            "imported_record",
            "commissioning-manifest-cw-2026-001",
            captured_at="2026-08-08T10:00:00+08:00",
            notes="Approved commissioning manifest.",
        ),
    }


@pytest.fixture
def commissioned_site(commissioned_site_payload: dict) -> CommissionedSite:
    return CommissionedSite.from_dict(commissioned_site_payload)
