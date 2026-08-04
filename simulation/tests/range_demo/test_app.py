"""Headless smoke tests for the Streamlit app via streamlit.testing.

These run the real app script against the synthetic bundle and assert the
contract points: it renders without exceptions, the SIMULATION RESULTS
labelling is present, and debug state stays hidden by default.
"""

from __future__ import annotations

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")

APP_PATH = "nxt_range_demo/app.py"


@pytest.fixture
def app(bundle_dir, monkeypatch):
    monkeypatch.setenv("NXT_DEMO_BUNDLE", str(bundle_dir))
    test = streamlit_testing.AppTest.from_file(APP_PATH, default_timeout=30)
    return test


def _all_text(at) -> str:
    chunks = []
    for kind in ("markdown", "caption", "subheader", "header", "title"):
        chunks.extend(str(el.value) for el in getattr(at, kind, []))
    return "\n".join(chunks)


def test_app_runs_without_exception(app):
    at = app.run()
    assert not at.exception


def test_app_labels_everything_as_simulation_results(app):
    at = app.run()
    text = _all_text(at)
    assert "SIMULATION RESULTS" in text
    assert "placeholder" in text.lower()


def test_app_does_not_expose_debug_state_by_default(app, bundle_dir):
    import json

    episode_path = bundle_dir / "episode.json"
    episode = json.loads(episode_path.read_text(encoding="utf-8"))
    for frame in episode["frames"]:
        frame["debug"] = {"robot_positions": {"R1": {"x_m": 1.0, "y_m": 2.0}}}
    episode["meta"]["includes_debug_state"] = True
    episode_path.write_text(json.dumps(episode), encoding="utf-8")

    at = app.run()
    assert not at.exception
    assert "robot_positions" not in _all_text(at)
    # The debug toggle must exist but default to off.
    debug_toggles = [t for t in at.sidebar.toggle if "debug" in str(t.label).lower()]
    assert debug_toggles and debug_toggles[0].value is False


def test_app_shows_final_summary_kpis(app):
    at = app.run()
    text = _all_text(at)
    assert "Final summary" in text or any(
        "Final summary" in str(t.label) for t in at.tabs
    )
