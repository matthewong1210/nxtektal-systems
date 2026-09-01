"""Process-local V0 boot epochs and device-delivery deduplication."""

from __future__ import annotations

import json

import pytest

from scripts import edge_gateway_live_input_v0 as gateway
from scripts.edge_gateway_live_input_v0 import (
    DeviceSequenceTracker,
    GatewayError,
    GatewayErrorCode,
    LoadCellWireMessage,
    SequenceDisposition,
)
from scripts.pilot_course_a_edge_fixture import (
    CALIBRATION_ID_LOAD_CELL,
    DEPLOYMENT_ID,
    SENSOR_DISPENSER_COUNT,
    SITE_ID,
)


def _message(
    *,
    device_id: str = "loadcell-controller-01",
    boot_id: str = "boot-a",
    device_sequence: int = 10,
    raw_value: float | None = 288.5,
    diagnostic_code: str | None = None,
) -> LoadCellWireMessage:
    return LoadCellWireMessage.from_json(
        json.dumps(
            {
                "schema": "nxt.edge.load-cell.raw/v1",
                "site_id": SITE_ID,
                "deployment_id": DEPLOYMENT_ID,
                "gateway_id": "gw-pilot-a-01",
                "device_id": device_id,
                "sensor_id": SENSOR_DISPENSER_COUNT,
                "boot_id": boot_id,
                "device_sequence": device_sequence,
                "sampled_at_utc": "2026-08-28T03:15:04.120Z",
                "published_at_utc": "2026-08-28T03:15:04.220Z",
                "raw_value": raw_value,
                "raw_unit": "kg",
                "device_status": "ok",
                "calibration_id": CALIBRATION_ID_LOAD_CELL,
                "diagnostic_code": diagnostic_code,
            }
        )
    )


def _assert_error_code(excinfo: pytest.ExceptionInfo[GatewayError], code) -> None:
    assert excinfo.value.code is code


def test_first_delivery_and_monotonically_higher_sequence_are_accepted():
    tracker = DeviceSequenceTracker()

    assert tracker.accept(_message(device_sequence=10)) is SequenceDisposition.ACCEPTED
    assert tracker.accept(_message(device_sequence=12)) is SequenceDisposition.ACCEPTED


def test_identical_delivery_is_idempotently_classified_as_duplicate():
    tracker = DeviceSequenceTracker()
    first = _message()
    replay = _message()

    assert tracker.accept(first) is SequenceDisposition.ACCEPTED
    assert tracker.accept(replay) is SequenceDisposition.DUPLICATE


def test_an_identical_seen_replay_stays_duplicate_after_higher_sequences():
    tracker = DeviceSequenceTracker()

    assert tracker.accept(_message(device_sequence=10)) is SequenceDisposition.ACCEPTED
    assert tracker.accept(_message(device_sequence=11)) is SequenceDisposition.ACCEPTED
    assert tracker.accept(_message(device_sequence=10)) is SequenceDisposition.DUPLICATE


def test_conflicting_reuse_of_a_seen_sequence_fails_closed():
    tracker = DeviceSequenceTracker()
    tracker.accept(_message(device_sequence=10, raw_value=288.5))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(device_sequence=10, raw_value=100.0))
    _assert_error_code(excinfo, GatewayErrorCode.CONFLICTING_REPLAY)


def test_conflicting_seen_replay_takes_precedence_over_lower_sequence():
    tracker = DeviceSequenceTracker()
    tracker.accept(_message(device_sequence=10, raw_value=288.5))
    tracker.accept(_message(device_sequence=11, raw_value=289.0))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(device_sequence=10, raw_value=100.0))
    _assert_error_code(excinfo, GatewayErrorCode.CONFLICTING_REPLAY)


def test_an_unseen_lower_sequence_within_the_current_boot_is_rejected():
    tracker = DeviceSequenceTracker()
    tracker.accept(_message(device_sequence=10))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(device_sequence=9))
    _assert_error_code(excinfo, GatewayErrorCode.OUT_OF_ORDER_SEQUENCE)


def test_a_new_boot_id_starts_a_new_sequence_epoch():
    tracker = DeviceSequenceTracker()

    assert tracker.accept(
        _message(boot_id="boot-a", device_sequence=1842)
    ) is SequenceDisposition.ACCEPTED
    assert tracker.accept(
        _message(boot_id="boot-b", device_sequence=0)
    ) is SequenceDisposition.ACCEPTED
    assert tracker.accept(
        _message(boot_id="boot-b", device_sequence=1)
    ) is SequenceDisposition.ACCEPTED


def test_a_delivery_from_a_retired_boot_is_rejected_even_if_identical():
    tracker = DeviceSequenceTracker()
    old = _message(boot_id="boot-a", device_sequence=10)
    tracker.accept(old)
    tracker.accept(_message(boot_id="boot-b", device_sequence=0))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(old)
    _assert_error_code(excinfo, GatewayErrorCode.RETIRED_BOOT)


def test_boot_and_sequence_state_is_scoped_per_device():
    tracker = DeviceSequenceTracker()

    assert tracker.accept(
        _message(device_id="device-a", boot_id="boot-a", device_sequence=10)
    ) is SequenceDisposition.ACCEPTED
    assert tracker.accept(
        _message(device_id="device-b", boot_id="boot-a", device_sequence=1)
    ) is SequenceDisposition.ACCEPTED

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(
            _message(device_id="device-a", boot_id="boot-a", device_sequence=9)
        )
    _assert_error_code(excinfo, GatewayErrorCode.OUT_OF_ORDER_SEQUENCE)


def test_payload_identity_includes_nullable_diagnostic_fields():
    tracker = DeviceSequenceTracker()
    tracker.accept(_message(device_sequence=10, diagnostic_code=None))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(device_sequence=10, diagnostic_code="E-0042"))
    _assert_error_code(excinfo, GatewayErrorCode.CONFLICTING_REPLAY)


def test_replay_window_is_bounded_and_evicted_history_fails_closed(monkeypatch):
    monkeypatch.setattr(gateway, "MAX_SEQUENCE_REPLAY_WINDOW", 2)
    tracker = DeviceSequenceTracker()
    for sequence in (10, 11, 12):
        tracker.accept(_message(device_sequence=sequence))

    assert len(tracker._seen[("loadcell-controller-01", "boot-a")]) == 2
    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(device_sequence=10))
    _assert_error_code(excinfo, GatewayErrorCode.OUT_OF_ORDER_SEQUENCE)


def test_boot_history_capacity_fails_closed_without_retiring_the_active_boot(
    monkeypatch,
):
    monkeypatch.setattr(gateway, "MAX_RETIRED_BOOTS_PER_DEVICE", 2)
    tracker = DeviceSequenceTracker()
    for boot_id in ("boot-a", "boot-b", "boot-c"):
        tracker.accept(_message(boot_id=boot_id, device_sequence=0))

    with pytest.raises(GatewayError) as excinfo:
        tracker.accept(_message(boot_id="boot-d", device_sequence=0))
    _assert_error_code(excinfo, GatewayErrorCode.REPLAY_CAPACITY_EXCEEDED)
    assert tracker._active_boot["loadcell-controller-01"] == "boot-c"
