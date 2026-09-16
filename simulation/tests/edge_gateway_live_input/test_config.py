"""Strict deployment-config and shipped-example coverage for Edge Gateway V0."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.edge_gateway_live_input_v0 import (
    CONFIG_SCHEMA,
    GatewayConfig,
    load_gateway_config,
)
from scripts.pilot_course_a_edge_fixture import (
    DEPLOYMENT_ID,
    SENSOR_WASHER_WIP,
    SITE_ID,
    commissioned_site,
)


SIMULATION_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = (
    SIMULATION_ROOT
    / "configs"
    / "edge_gateway"
    / "pilot-course-a.example.yaml"
)


def _example_document() -> dict:
    payload = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _inner(document: dict) -> dict:
    assert set(document) == {"edge_gateway"}
    inner = document["edge_gateway"]
    assert isinstance(inner, dict)
    return inner


def _mode_value(config: GatewayConfig) -> str:
    return getattr(config.mode, "value", config.mode)


def test_shipped_example_is_the_strict_versioned_pilot_configuration():
    document = _example_document()
    config = GatewayConfig.from_mapping(document)

    assert config.schema == CONFIG_SCHEMA == "nxt-edge-gateway/config/v0"
    assert _mode_value(config) == "HYBRID_RUNTIME_REHEARSAL"
    assert config.site_id == SITE_ID
    assert config.deployment_id == DEPLOYMENT_ID
    assert config.gateway_id
    assert config.broker.host
    assert 1 <= config.broker.port <= 65_535
    assert config.broker.keepalive_s > 0
    assert config.broker.qos == 1
    assert config.broker.client_id
    assert len(config.devices) == 1
    assert config.devices[0].device_id
    assert config.devices[0].sensor_ids
    assert config.status.host
    assert 1 <= config.status.port <= 65_535
    assert str(config.evidence_dir)
    assert config.fixture_cycle_index >= 0


def test_file_loader_matches_the_shipped_document_contract():
    document = _example_document()
    wrapped = GatewayConfig.from_mapping(document)
    loaded = load_gateway_config(EXAMPLE)
    assert wrapped == loaded


def test_file_loader_requires_the_single_versioned_top_level_wrapper(tmp_path):
    flat_path = tmp_path / "flat.yaml"
    flat_path.write_text(
        yaml.safe_dump(_inner(_example_document()), sort_keys=False),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level edge_gateway"):
        load_gateway_config(flat_path)


def test_loader_validates_config_identity_and_sensors_against_commissioning(
    tmp_path
):
    site = commissioned_site()
    assert load_gateway_config(EXAMPLE, site=site).site_id == site.site_id

    wrong_deployment = copy.deepcopy(_example_document())
    _inner(wrong_deployment)["deployment_id"] = "another-deployment"
    wrong_deployment_path = tmp_path / "wrong-deployment.yaml"
    wrong_deployment_path.write_text(
        yaml.safe_dump(wrong_deployment, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_gateway_config(wrong_deployment_path, site=site)

    unknown_sensor = copy.deepcopy(_example_document())
    _inner(unknown_sensor)["devices"][0]["sensor_ids"] = [
        "sensor-not-commissioned"
    ]
    unknown_sensor_path = tmp_path / "unknown-sensor.yaml"
    unknown_sensor_path.write_text(
        yaml.safe_dump(unknown_sensor, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_gateway_config(unknown_sensor_path, site=site)


def test_hybrid_config_refuses_a_commissioned_non_dispenser_load_cell(tmp_path):
    document = copy.deepcopy(_example_document())
    _inner(document)["devices"][0]["sensor_ids"] = [SENSOR_WASHER_WIP]
    path = tmp_path / "washer-hybrid.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="dispenser load-cell sensors"):
        load_gateway_config(path, site=commissioned_site())

    _inner(document)["mode"] = "LOAD_CELL_DIAGNOSTIC"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    assert load_gateway_config(path, site=commissioned_site()).mode.value == (
        "LOAD_CELL_DIAGNOSTIC"
    )


def test_repository_config_validator_includes_the_gateway_example():
    result = subprocess.run(
        [sys.executable, "-B", "scripts/validate_configs.py"],
        cwd=SIMULATION_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "edge_gateway/pilot-course-a.example.yaml" in result.stdout
