"""Binding ingestion from the existing commissioning projection."""

from __future__ import annotations

import copy

import pytest

from nxt_commissioning import (
    TELEMETRY_ADAPTER_CONFIG_SCHEMA,
    project_telemetry_adapter_config,
)
from nxt_edge_observation import (
    NOT_REQUIRED_CALIBRATION_ID,
    AdapterBindingSet,
    EdgeAdapterError,
)
from nxt_edge_observation.bindings import (
    TELEMETRY_ADAPTER_CONFIG_SCHEMA as ADAPTER_SIDE_SCHEMA,
)

from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    SENSOR_DISPENSER_COUNT,
    SENSOR_STATION_OPEN,
)


@pytest.fixture
def projection(site):
    return project_telemetry_adapter_config(site)


def test_the_adapter_tracks_the_commissioning_projection_schema():
    assert ADAPTER_SIDE_SCHEMA == TELEMETRY_ADAPTER_CONFIG_SCHEMA


def test_bindings_are_read_straight_from_the_projection(projection):
    bindings = AdapterBindingSet.from_projection(projection)
    assert bindings.site_id == projection["site_id"]
    assert bindings.deployment_id == projection["deployment_id"]
    assert len(bindings.bindings) == len(projection["bindings"])
    assert bindings.channels == tuple(sorted(bindings.channels))


def test_a_calibrated_binding_keeps_its_commissioned_identity(projection):
    bindings = AdapterBindingSet.from_projection(projection)
    binding = bindings.by_sensor_id(SENSOR_DISPENSER_COUNT)
    assert binding.calibration_required is True
    assert binding.calibration_id == CALIBRATION_ID_LOAD_CELL
    assert binding.canonical_unit == "balls"
    assert binding.source_type == "sensor"
    assert binding.source_id == SENSOR_DISPENSER_COUNT


def test_a_not_required_binding_uses_an_explicit_sentinel(projection):
    bindings = AdapterBindingSet.from_projection(projection)
    binding = bindings.by_sensor_id(SENSOR_STATION_OPEN)
    assert binding.calibration_required is False
    assert binding.calibration_id == NOT_REQUIRED_CALIBRATION_ID
    # It never borrows another sensor's calibration identity.
    assert binding.calibration_id != CALIBRATION_ID_LOAD_CELL


def test_lookup_by_channel_and_sensor_agree(projection):
    bindings = AdapterBindingSet.from_projection(projection)
    for binding in bindings.bindings:
        assert bindings.by_channel(binding.channel) is binding
        assert bindings.by_sensor_id(binding.sensor_id) is binding
    assert bindings.by_channel("robot.R99.activity") is None
    assert bindings.by_sensor_id("nope") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"schema": "some-other/schema/v1"}),
        lambda p: p.pop("schema"),
        lambda p: p.update({"site_id": " "}),
        lambda p: p.update({"deployment_id": ""}),
        lambda p: p.update({"bindings": []}),
        lambda p: p.update({"bindings": "not-a-list"}),
    ],
)
def test_a_malformed_projection_fails_closed(projection, mutate):
    payload = copy.deepcopy(projection)
    mutate(payload)
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(payload)


def test_a_binding_missing_keys_fails_closed(projection):
    payload = copy.deepcopy(projection)
    payload["bindings"][0].pop("units")
    with pytest.raises(EdgeAdapterError) as excinfo:
        AdapterBindingSet.from_projection(payload)
    assert "units" in str(excinfo.value)


def test_duplicate_sensor_ids_and_channels_fail_closed(projection):
    duplicate_sensor = copy.deepcopy(projection)
    duplicate_sensor["bindings"].append(
        copy.deepcopy(duplicate_sensor["bindings"][0])
    )
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(duplicate_sensor)

    duplicate_channel = copy.deepcopy(projection)
    clone = copy.deepcopy(duplicate_channel["bindings"][0])
    clone["sensor_id"] = "sensor-clone-01"
    clone["observation_source_id"] = "sensor-clone-01"
    duplicate_channel["bindings"].append(clone)
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(duplicate_channel)


def test_an_unshapeable_channel_fails_closed(projection):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["channel"] = "washer.WASH1.running"
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(payload)


def test_an_unknown_observation_source_type_fails_closed(projection):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["observation_source_type"] = "telepathy"
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(payload)


def test_an_inconsistent_calibration_block_fails_closed(projection):
    payload = copy.deepcopy(projection)
    for binding in payload["bindings"]:
        if binding["calibration"]["status"] == "not_required":
            binding["calibration"]["calibration_id"] = "CAL-SNEAKY-1"
            break
    else:  # pragma: no cover - the fixture always has one
        pytest.fail("fixture has no not_required binding")
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(payload)


def test_an_unknown_calibration_status_fails_closed(projection):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["calibration"]["status"] = "probably_fine"
    with pytest.raises(EdgeAdapterError):
        AdapterBindingSet.from_projection(payload)


# ---------------------------------------------------------------------------
# Projection evidence integrity: required fields must be validated, not merely
# present. Provenance never reaches an Observation, but a projection carrying a
# blank or malformed provenance block is not the artefact this layer consumes.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [None, "oops", {}, [], 12345, ""],
)
def test_a_malformed_binding_provenance_is_refused(projection, value):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["provenance"] = value
    with pytest.raises(EdgeAdapterError) as excinfo:
        AdapterBindingSet.from_projection(payload)
    assert "provenance" in str(excinfo.value)


@pytest.mark.parametrize("value", [None, "oops", {}, 12345])
def test_a_malformed_calibration_provenance_is_refused(projection, value):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["calibration"]["provenance"] = value
    with pytest.raises(EdgeAdapterError) as excinfo:
        AdapterBindingSet.from_projection(payload)
    assert "provenance" in str(excinfo.value)


@pytest.mark.parametrize("value", [None, "", "   ", 12345, " plc.range-01 "])
def test_a_malformed_source_address_is_refused(projection, value):
    payload = copy.deepcopy(projection)
    payload["bindings"][0]["source"] = value
    with pytest.raises(EdgeAdapterError) as excinfo:
        AdapterBindingSet.from_projection(payload)
    assert "source" in str(excinfo.value)


def test_the_commissioned_source_address_is_retained(projection):
    bindings = AdapterBindingSet.from_projection(projection)
    binding = bindings.by_sensor_id(SENSOR_DISPENSER_COUNT)
    declared = {
        item["sensor_id"]: item["source"] for item in projection["bindings"]
    }
    assert binding.source == declared[SENSOR_DISPENSER_COUNT]
    assert binding.source.strip() == binding.source
    for item in bindings.bindings:
        assert item.source, item.sensor_id


def test_provenance_is_validated_but_never_carried_into_a_binding(projection):
    """The manifest stays provenance's only home; Observation has no field."""
    bindings = AdapterBindingSet.from_projection(projection)
    for binding in bindings.bindings:
        assert not hasattr(binding, "provenance")


def test_a_fabricated_unit_is_still_caught_at_conversion(projection, kit):
    """The documented trust boundary, proven rather than assumed.

    `from_projection` does not re-implement the commissioning vocabulary --
    that would be a second registry. A fabricated unit therefore builds a
    binding, but each adapter's own unit guard refuses to convert it.
    """
    from nxt_edge_observation import EdgeObservationAdapterKit
    from scripts.pilot_course_a_edge_fixture import COORDINATE_FRAME

    from .conftest import batch, load_cell_sample

    payload = copy.deepcopy(projection)
    for item in payload["bindings"]:
        if item["sensor_id"] == SENSOR_DISPENSER_COUNT:
            item["units"] = "1"
    fabricated = EdgeObservationAdapterKit(
        bindings=AdapterBindingSet.from_projection(payload),
        coordinate_frame=COORDINATE_FRAME,
        load_cell_profiles=tuple(kit._load_cells.values()),
        digital_device_profiles=tuple(kit._digital_devices.values()),
        digital_input_profiles=tuple(kit._digital_inputs.values()),
        robot_profiles=tuple(kit._robots.values()),
    )
    result = fabricated.convert(batch(load_cells=(load_cell_sample(),)))
    observation = {
        item.channel: item for item in result.observations
    }["inventory.dispenser.count"]
    assert observation.status.value == "missing"
    assert observation.value is None
    assert "unsupported_unit" in {
        item.code.value
        for item in result.report.rejected
        if item.sensor_id == SENSOR_DISPENSER_COUNT
    }
