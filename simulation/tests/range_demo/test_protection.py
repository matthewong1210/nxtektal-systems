"""The demo viewer is presentation-only: running it must leave the
simulator, benchmark, and exporter source trees byte-identical."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PROTECTED = (
    SIMULATION_ROOT / "nxt_range_ops",
    SIMULATION_ROOT / "nxt_range_agent",
    SIMULATION_ROOT / "nxt_range_viewer",
)


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def test_app_run_does_not_modify_protected_packages(bundle_dir, monkeypatch):
    before = {str(root): _tree_digest(root) for root in PROTECTED}

    monkeypatch.setenv("NXT_DEMO_BUNDLE", str(bundle_dir))
    at = streamlit_testing.AppTest.from_file(
        "nxt_range_demo/app.py", default_timeout=30
    ).run()
    assert not at.exception

    after = {str(root): _tree_digest(root) for root in PROTECTED}
    assert after == before
