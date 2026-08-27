"""Pure projection behavior: state, evaluations, recommendations."""

from __future__ import annotations

from nxt_site_agent import no_state_projection, state_projection


def _envelope(**overrides):
    envelope = {
        "envelope_id": "fse:abc",
        "schema_version": "nxt-site-runtime-facility-snapshot-v1-test",
        "site_id": "pilot-course-a",
        "deployment_id": "pilot-a-site-agent-v0",
        "sequence_number": 1,
        "observation_timestamp_s": 66600.0,
        "facility_state": {
            "meta": {
                "t_s": 66600.0,
                "minute_of_day": 1110,
                "facility_open": True,
                "scenario_name": "svc",
            },
            "ball_flow": {"clean_available": 2400, "clean_sensed": 2400.0},
        },
        "assembly_report": {
            "missing_channels": [],
            "stale_channels": [],
            "consistency_issues": [],
            "overall_confidence": 1.0,
            "provenance_grade": "high",
        },
        "runtime_quality": {
            "assembly_confidence": 1.0,
            "upstream_confidence": 1.0,
            "effective_confidence": 1.0,
        },
        "source_references": [
            {
                "channel": "inventory.dispenser.count",
                "status": "ok",
                "confidence": 1.0,
                "sample_timestamp_s": 66595.0,
                "available_timestamp_s": 66600.0,
                "calibration_id": "CAL-LC-PILOTA-2026",
            },
            {
                "channel": "inventory.dispenser.sensed",
                "status": "ok",
                "confidence": 1.0,
                "sample_timestamp_s": 66595.0,
                "available_timestamp_s": 66600.0,
                "calibration_id": "CAL-LC-PILOTA-2026",
            },
        ],
    }
    envelope.update(overrides)
    return envelope


def test_no_state_projection_is_explicit():
    projection = no_state_projection("nothing published")
    assert projection["available"] is False
    assert projection["dispenser"] is None
    assert projection["reason"] == "nothing published"


def test_state_projection_surfaces_inventory_and_source_quality():
    projection = state_projection(_envelope(), scenario_now_s=66600.0)
    assert projection["available"] is True
    assert projection["dispenser"]["clean_available_balls"] == 2400
    assert projection["dispenser"]["count_source"]["status"] == "ok"
    assert projection["dispenser"]["reading_age_s"] == 5.0
    assert projection["envelope"]["sequence_number"] == 1
    assert projection["quality"]["runtime_quality"][
        "effective_confidence"
    ] == 1.0


def test_state_projection_reading_age_grows_with_scenario_time():
    projection = state_projection(_envelope(), scenario_now_s=70200.0)
    assert projection["dispenser"]["reading_age_s"] == 3605.0


def test_state_projection_missing_reference_never_fakes_age_or_value():
    envelope = _envelope(source_references=[])
    projection = state_projection(envelope, scenario_now_s=70200.0)
    assert projection["dispenser"]["count_source"] is None
    assert projection["dispenser"]["reading_age_s"] is None
    # the value shown is the published canonical value, not an invention
    assert projection["dispenser"]["clean_available_balls"] == 2400


def test_state_projection_without_scenario_time_has_no_age():
    projection = state_projection(_envelope(), scenario_now_s=None)
    assert projection["dispenser"]["reading_age_s"] is None
