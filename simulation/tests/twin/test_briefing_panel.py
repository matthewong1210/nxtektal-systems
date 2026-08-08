"""Panel data logic is pure and testable without streamlit."""
from pathlib import Path

from nxt_range_demo.briefing_panel import (
    briefing_for_time,
    load_briefings,
    render_panel,
)


def _write(tmp_path: Path) -> Path:
    lines = [
        '{"briefing":"b0","recommendations":[],"seq":0,"t_s":0.0}',
        '{"briefing":"b1","recommendations":[{"rule_id":"r"}],"seq":1,"t_s":60.0}',
        '{"briefing":"b2","recommendations":[],"seq":2,"t_s":120.0}',
    ]
    p = tmp_path / "briefings.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def _write_malformed(tmp_path: Path) -> Path:
    lines = [
        '{"briefing":"ok","recommendations":[],"seq":0,"t_s":0.0}',
        "42",
        '["not", "a", "dict"]',
        '{"seq":"no-t_s","note":"missing t_s key"}',
        '{"t_s":30.0,"seq":"no-briefing"}',
        '{"t_s":45.0,"seq":"null-recs","briefing":"b-null","recommendations":null}',
    ]
    p = tmp_path / "briefings.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


class _FakeStreamlit:
    """Minimal st stub that records calls instead of rendering."""

    def __init__(self):
        self.calls = []

    def caption(self, *args, **kwargs):
        self.calls.append(("caption", args, kwargs))

    def subheader(self, *args, **kwargs):
        self.calls.append(("subheader", args, kwargs))

    def text(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))

    def markdown(self, *args, **kwargs):
        self.calls.append(("markdown", args, kwargs))


def test_load_and_lookup_by_time(tmp_path: Path):
    briefings = load_briefings(_write(tmp_path))
    assert [b["seq"] for b in briefings] == [0, 1, 2]
    assert briefing_for_time(briefings, 0.0)["briefing"] == "b0"
    assert briefing_for_time(briefings, 59.9)["briefing"] == "b0"
    assert briefing_for_time(briefings, 60.0)["briefing"] == "b1"
    assert briefing_for_time(briefings, 999.0)["briefing"] == "b2"
    assert briefing_for_time(briefings, -1.0) is None


def test_load_briefings_skips_non_dict_and_no_t_s_lines(tmp_path: Path):
    briefings = load_briefings(_write_malformed(tmp_path))
    # Only the dict records that carry a numeric t_s survive, sorted.
    assert [b.get("seq") for b in briefings] == [0, "no-briefing", "null-recs"]
    assert [b["t_s"] for b in briefings] == [0.0, 30.0, 45.0]


def test_render_panel_survives_missing_briefing_fields(tmp_path: Path):
    briefings = load_briefings(_write_malformed(tmp_path))
    fake_st = _FakeStreamlit()
    # The record at t_s=30.0 has no "briefing"/"recommendations" keys — this
    # must not raise.
    render_panel(fake_st, briefings, 30.0)
    assert any(call[0] == "text" for call in fake_st.calls)
    # The record at t_s=45.0 has an explicit "recommendations": null (valid
    # JSON, invalid contract shape) — this must not raise either.
    fake_st.calls.clear()
    render_panel(fake_st, briefings, 45.0)
    assert any(call[0] == "text" for call in fake_st.calls)
