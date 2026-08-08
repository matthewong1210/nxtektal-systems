from __future__ import annotations

import json
from pathlib import Path

import pytest

from nxt_facility.build import build_facility_state
from nxt_range_ops.core.sim import RangeSimulation
from nxt_range_ops.scenarios.generators import make_scenario
from nxt_range_twin.stream import write_jsonl

from nxt_pilot_ops.adapters import NATIVE_STREAM_SCHEMA
from nxt_pilot_ops.cli import main


def _capture(tmp_path: Path) -> tuple[Path, Path]:
    simulation = RangeSimulation(make_scenario("normal_weekday"), seed=7)
    states_path = tmp_path / "facility_states.jsonl"
    meta_path = tmp_path / "stream.meta.json"
    write_jsonl(states_path, [build_facility_state(simulation).to_dict()])
    meta_path.write_text(
        json.dumps(
            {
                "schema": NATIVE_STREAM_SCHEMA,
                "site_id": "sim-baseline",
                "deployment_id": "offline-review",
                "episode_id": "normal_weekday-seed7",
                "scenario_name": "normal_weekday",
                "seed": 7,
                "policy": "inventory_threshold",
                "policy_version": "0",
                "control_interval_s": 60.0,
                "every_steps": 1,
                "n_records": 1,
                "simulator_version": "0.5.0",
                "git_commit": "abc1234",
                "disclaimer": "synthetic test output",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return states_path, meta_path


def test_cli_reviews_the_repository_native_capture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    states_path, meta_path = _capture(tmp_path)

    assert main(
        [
            str(states_path),
            "--meta",
            str(meta_path),
            "--simulation-midnight",
            "2026-08-08T00:00:00+00:00",
            "--first-record-sensed-state",
            "pre-sensor",
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "offline_shadow_review"
    assert output["source"]["disclaimer"] == "synthetic test output"
    assert output["source"]["schema"] == NATIVE_STREAM_SCHEMA
    assert len(output["evaluations"]) == 1
    trace = output["evaluations"][0]["trace"]
    assert trace["source_schema_version"] == NATIVE_STREAM_SCHEMA
    assert trace["source_sequence"] == 0
    assert trace["clean_sensed"] is None


@pytest.mark.parametrize(
    "value",
    ["2026-08-08T00:00:00", "2026-08-08T00:01:00+00:00"],
)
def test_cli_requires_a_timezone_aware_calendar_midnight(value: str) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "missing.jsonl",
                "--meta",
                "missing.meta.json",
                "--simulation-midnight",
                value,
                "--first-record-sensed-state",
                "pre-sensor",
            ]
        )
