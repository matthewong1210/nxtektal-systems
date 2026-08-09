"""Important commissioned values retain inseparable provenance."""

from __future__ import annotations

import pytest

from nxt_commissioning import CommissionedSite, ProvenanceSource

from .conftest import clone_payload


def test_provenance_survives_full_manifest_roundtrip(commissioned_site):
    restored = CommissionedSite.from_dict(commissioned_site.to_dict())
    equipment = {asset.asset_id: asset for asset in restored.equipment_assets}
    sensors = {binding.sensor_id: binding for binding in restored.sensor_bindings}
    robot = restored.robot_assets[0]

    assert (
        restored.spatial_reference.facility_origin.provenance.source_type
        is ProvenanceSource.SURVEY_DATA
    )
    assert (
        equipment["washer-01"].capacity[0].provenance.source_type
        is ProvenanceSource.MANUFACTURER_SPECIFICATION
    )
    assert equipment["washer-01"].capacity[0].provenance.notes == (
        "Supplier datasheet revision C."
    )
    assert (
        robot.payload_limits[0].provenance.source_type
        is ProvenanceSource.MANUFACTURER_SPECIFICATION
    )
    assert (
        sensors["sensor-load-02"].calibration.provenance.source_type
        is ProvenanceSource.SENSOR_CALIBRATION
    )
    assert sensors["sensor-load-02"].calibration.calibration_id == (
        "CAL-LC-014-2026"
    )


def test_measured_value_without_provenance_is_rejected(
    commissioned_site_payload,
):
    payload = clone_payload(commissioned_site_payload)
    del payload["equipment_assets"][0]["capacity"][0]["provenance"]
    with pytest.raises(ValueError, match="provenance"):
        CommissionedSite.from_dict(payload)


@pytest.mark.parametrize("captured_at", ["2026-07-20", "2026-07-20T01:00:00"])
def test_provenance_requires_timezone_aware_capture_time(
    commissioned_site_payload, captured_at
):
    payload = clone_payload(commissioned_site_payload)
    payload["provenance"]["captured_at"] = captured_at
    with pytest.raises(ValueError, match="captured_at"):
        CommissionedSite.from_dict(payload)
