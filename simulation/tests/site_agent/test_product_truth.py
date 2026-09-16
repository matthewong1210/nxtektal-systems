"""Product-truth guards for the CTO sensor integration document.

The physical prototype is a VL53L4CX Time-of-Flight ranging sensor,
not a load cell. These tests pin the integration document to that
truth so the seam description cannot silently drift back to a
mass-shaped claim. They read only this repository — no CI dependency
on the external sensor repository exists or may be added.
"""

from __future__ import annotations

from pathlib import Path

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
DOCUMENT = SIMULATION_ROOT / "docs" / "cto_hopper_sensor_integration.md"

REQUIRED_TRUTHS = (
    "VL53L4CX",
    "Time-of-Flight",
    "distance_mm",
    "not directly compatible with LoadCellSample",
    "separate architecture review",
    "no physical device connected",
)

# Claims the correction removed; their return would misdescribe the
# actual prototype as a mass sensor again.
FORBIDDEN_CLAIMS = (
    "gross mass",
    "translated into, the existing raw-sample contract",
    "only the *source composition* changes",
)


def _text() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def test_integration_document_states_the_tof_prototype_truth():
    text = _text()
    normalized = " ".join(text.split())
    for truth in REQUIRED_TRUTHS:
        assert " ".join(truth.split()) in normalized, (
            f"cto_hopper_sensor_integration.md must state: {truth!r}"
        )


def test_integration_document_does_not_reclaim_load_cell_compatibility():
    text = _text()
    for claim in FORBIDDEN_CLAIMS:
        assert claim not in text, (
            "cto_hopper_sensor_integration.md reintroduced the "
            f"incorrect claim: {claim!r}"
        )


def test_integration_document_separates_the_three_layers():
    text = _text()
    for marker in (
        "Firmware / wire message",
        "Future transport reader",
        "Future Site OS ranging adapter",
        "RangeStatus",
        "objects_found",
        "monotonic message sequence",
        "durable cursor",
        "calibration identity",
    ):
        assert marker in text, (
            f"cto_hopper_sensor_integration.md must describe: {marker!r}"
        )


def test_integration_document_leaves_the_representation_decision_open():
    normalized = " ".join(_text().split())
    assert "estimated ball count" in normalized
    assert "level/threshold observation" in normalized
    assert "another bounded representation" in normalized
    # the mapping must not be presented as trivial or linear
    assert (
        "do not assume distance-to-ball-count conversion is linear"
        in normalized
    )
