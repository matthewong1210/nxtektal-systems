"""Contract tests for nxt_memory.records (pure, no simulator)."""

from __future__ import annotations

import ast
import dataclasses
import json
from pathlib import Path

import pytest

from nxt_memory.records import (
    DISCLAIMER,
    SCHEMA_VERSION,
    HumanVerdict,
    Verdict,
    iter_windows,
    load_meta,
    outcome_section,
    validate_window,
)

from .conftest import make_meta, make_window, metrics

MEMORY_PKG = Path(__file__).resolve().parents[2] / "nxt_memory"


def test_meta_carries_site_identity_and_schema_version():
    meta = make_meta(site_id="site-A", deployment_id="pilot-01")
    payload = meta.to_dict()
    assert payload["site_id"] == "site-A"
    assert payload["deployment_id"] == "pilot-01"
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["seed"] == 7
    json.dumps(payload)


def test_window_is_frozen_with_derived_record_id():
    window = make_window(seq=3)
    assert window.record_id == "unit-seed7:w000003"
    with pytest.raises(dataclasses.FrozenInstanceError):
        window.seq = 4
    json.dumps(window.to_dict(), sort_keys=True)


def test_outcome_section_deltas_and_impact():
    before = metrics()
    after = metrics(
        stockout_minutes=3.0,
        open_minutes_elapsed=90.0,
        energy_wh=180.0,
        human_interventions_requested=2,
        human_interventions_completed=1,
        estops=1,
        hard_failures=1,
        unsafe_rejections=4,
        balls_processed=80,
    )
    out = outcome_section(metrics_before=before, metrics_after=after)
    assert out["metrics_before"] == before
    assert out["metrics_after"] == after
    assert out["deltas"]["stockout_minutes"] == pytest.approx(3.0)
    assert out["deltas"]["balls_processed"] == 50
    impact = out["impact"]
    # availability mirrors OpsMetrics.service_availability: 1 - stockout/open
    assert impact["availability_change"] == pytest.approx((1 - 3.0 / 90.0) - 1.0)
    assert impact["stockout_minutes"] == pytest.approx(3.0)
    assert impact["labor"] == {
        "human_interventions_requested": 2,
        "human_interventions_completed": 1,
    }
    assert impact["energy_wh"] == pytest.approx(60.0)
    assert impact["safety"] == {
        "estops": 1,
        "hard_failures": 1,
        "unsafe_rejections": 4,
    }
    # the schema has no verdict on quality — only measurements
    assert "success" not in json.dumps(out)


def test_validate_window_accepts_wellformed():
    validate_window(make_window())


def test_validate_window_rejects_bad_rec_index_and_echo():
    bad_index = make_window(
        verdicts=(
            HumanVerdict(rec_index=9, rule_id="battery_reserve", verdict=Verdict.ACCEPTED, decided_by="op"),
        )
    )
    with pytest.raises(ValueError, match="rec_index"):
        validate_window(bad_index)
    bad_echo = make_window(
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="idle_capacity", verdict=Verdict.REJECTED, decided_by="op"),
        )
    )
    with pytest.raises(ValueError, match="rule_id"):
        validate_window(bad_echo)


def test_validate_window_rejects_duplicate_verdicts_for_one_recommendation():
    # Two verdicts on the same rec_index would make the audit queries
    # disagree about one immutable record (last-wins vs any-accepted).
    window = make_window(
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="battery_reserve", verdict=Verdict.ACCEPTED, decided_by="op"),
            HumanVerdict(rec_index=0, rule_id="battery_reserve", verdict=Verdict.REJECTED, decided_by="op"),
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_window(window)


def test_validate_window_rejects_unknown_verdict_strings():
    # Drivers can assemble sections from raw dicts; a typo'd verdict must
    # die at write time, not crash the queries months later.
    window = make_window()
    tampered = json.loads(json.dumps(window.decision))
    tampered["human"]["verdicts"][0]["verdict"] = "acepted"
    with pytest.raises(ValueError, match="verdict"):
        validate_window(dataclasses.replace(window, decision=tampered))


def test_validate_window_pins_outcome_and_impact_key_sets():
    # The schema must never grow scores/success/ratings: exact allowlists.
    window = make_window()
    scored = json.loads(json.dumps(window.outcome))
    scored["score"] = 1.0
    with pytest.raises(ValueError, match="outcome"):
        validate_window(dataclasses.replace(window, outcome=scored))
    rated = json.loads(json.dumps(window.outcome))
    rated["impact"]["rating"] = "good"
    with pytest.raises(ValueError, match="impact"):
        validate_window(dataclasses.replace(window, outcome=rated))
    assert set(window.outcome) == {"metrics_before", "metrics_after", "deltas", "impact"}
    assert set(window.outcome["impact"]) == {
        "availability_change",
        "stockout_minutes",
        "labor",
        "energy_wh",
        "safety",
    }


def test_validate_window_enforces_section_separation():
    window = make_window()
    tampered_decision = dict(window.decision)
    tampered_decision["events"] = []
    with pytest.raises(ValueError, match="section"):
        validate_window(dataclasses.replace(window, decision=tampered_decision))
    tampered_execution = dict(window.execution)
    tampered_execution["recommendations"] = []
    with pytest.raises(ValueError, match="section"):
        validate_window(dataclasses.replace(window, execution=tampered_execution))


def test_iter_windows_and_load_meta_round_trip(tmp_path):
    episode_dir = tmp_path / "ep"
    episode_dir.mkdir()
    windows = [make_window(seq=i) for i in range(3)]
    with (episode_dir / "windows.jsonl").open("w") as fh:
        for w in windows:
            fh.write(json.dumps(w.to_dict(), sort_keys=True) + "\n")
    meta_payload = dict(make_meta().to_dict(), n_windows=3, disclaimer=DISCLAIMER)
    (episode_dir / "episode.meta.json").write_text(
        json.dumps(meta_payload, indent=2, sort_keys=True)
    )
    loaded = list(iter_windows(episode_dir))
    assert [w["seq"] for w in loaded] == [0, 1, 2]
    assert loaded[0] == windows[0].to_dict()
    meta = load_meta(episode_dir)
    assert meta["site_id"] == "site-test"
    assert meta["n_windows"] == 3
    assert "does NOT represent" in meta["disclaimer"]


def test_no_wall_clock_or_uuid_imports_in_memory_package():
    banned = {"time", "datetime", "uuid", "random", "secrets"}
    for path in MEMORY_PKG.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            roots = set()
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots = {node.module.split(".")[0]}
            assert not (roots & banned), f"{path.name} imports banned: {roots & banned}"
