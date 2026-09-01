"""Commissioned-IANA-timezone mapping into the repository's civil site time."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import edge_gateway_live_input_v0 as gateway
from scripts.edge_gateway_live_input_v0 import (
    GatewayError,
    GatewayErrorCode,
    SiteClock,
)

SIMULATION_ROOT = Path(__file__).resolve().parents[2]


def _assert_error_code(excinfo: pytest.ExceptionInfo[GatewayError], code) -> None:
    assert excinfo.value.code is code


def test_utc_pair_maps_to_commissioned_local_day_and_civil_seconds():
    mapped = SiteClock("Asia/Shanghai").map_pair(
        "2026-08-28T03:15:04.120Z",
        "2026-08-28T03:15:04.220Z",
    )

    assert mapped.operating_day_id == "2026-08-28"
    assert mapped.sample_timestamp_s == pytest.approx(40_504.120)
    assert mapped.available_timestamp_s == pytest.approx(40_504.220)


def test_site_local_midnight_maps_to_zero_without_using_epoch_seconds():
    mapped = SiteClock("Asia/Shanghai").map_pair(
        "2026-08-27T16:00:00.000Z",
        "2026-08-27T16:00:00.250Z",
    )

    assert mapped.operating_day_id == "2026-08-28"
    assert mapped.sample_timestamp_s == 0.0
    assert mapped.available_timestamp_s == pytest.approx(0.250)


def test_a_pair_straddling_site_local_midnight_is_refused():
    with pytest.raises(GatewayError) as excinfo:
        SiteClock("Asia/Shanghai").map_pair(
            "2026-08-27T15:59:59.999Z",
            "2026-08-27T16:00:00.000Z",
        )
    _assert_error_code(excinfo, GatewayErrorCode.MIXED_OPERATING_DAY)


def _map_in_subprocess(host_timezone: str) -> dict:
    probe = """
import json
from scripts.edge_gateway_live_input_v0 import SiteClock

mapped = SiteClock("Asia/Shanghai").map_pair(
    "2026-08-28T03:15:04.120Z",
    "2026-08-28T03:15:04.220Z",
)
print(json.dumps({
    "operating_day_id": mapped.operating_day_id,
    "sample_timestamp_s": mapped.sample_timestamp_s,
    "available_timestamp_s": mapped.available_timestamp_s,
}, sort_keys=True))
"""
    environment = dict(os.environ)
    environment["TZ"] = host_timezone
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=SIMULATION_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_host_timezone_does_not_change_site_clock_output():
    assert _map_in_subprocess("UTC") == _map_in_subprocess("Pacific/Honolulu")


def test_spring_forward_skips_the_nonexistent_civil_hour():
    mapped = SiteClock("America/New_York").map_pair(
        "2026-03-08T06:59:59.500Z",
        "2026-03-08T07:00:00.500Z",
    )

    assert mapped.operating_day_id == "2026-03-08"
    assert mapped.sample_timestamp_s == pytest.approx(7_199.5)
    assert mapped.available_timestamp_s == pytest.approx(10_800.5)


@pytest.mark.parametrize(
    ("sampled", "published"),
    [
        # First 01:30 (EDT, fold=0).
        ("2026-11-01T05:30:00Z", "2026-11-01T05:30:01Z"),
        # Repeated 01:30 (EST, fold=1).
        ("2026-11-01T06:30:00Z", "2026-11-01T06:30:01Z"),
    ],
)
def test_both_folds_of_an_ambiguous_fall_back_time_are_refused(
    sampled, published
):
    with pytest.raises(GatewayError) as excinfo:
        SiteClock("America/New_York").map_pair(sampled, published)
    _assert_error_code(excinfo, GatewayErrorCode.AMBIGUOUS_LOCAL_TIME)


def test_an_unknown_iana_timezone_fails_closed():
    with pytest.raises(GatewayError) as excinfo:
        SiteClock("Mars/Olympus_Mons")
    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)


def test_site_clock_rechecks_timestamp_order_for_direct_callers():
    with pytest.raises(GatewayError) as excinfo:
        SiteClock("Asia/Shanghai").map_pair(
            "2026-08-28T03:15:04.220Z",
            "2026-08-28T03:15:04.120Z",
        )
    _assert_error_code(excinfo, GatewayErrorCode.TIMESTAMP_ORDER)


def test_processor_refuses_an_injected_clock_outside_commissioned_timezone():
    site = gateway.commissioned_site()
    config = gateway.load_gateway_config(
        gateway.SIM_ROOT
        / "configs"
        / "edge_gateway"
        / "pilot-course-a.example.yaml",
        site=site,
    )

    with pytest.raises(GatewayError) as excinfo:
        gateway.GatewayProcessor(config, site=site, clock=SiteClock("UTC"))

    _assert_error_code(excinfo, GatewayErrorCode.INVALID_CONFIG)
