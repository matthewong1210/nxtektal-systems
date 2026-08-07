"""Panel data logic is pure and testable without streamlit."""
from pathlib import Path

from nxt_range_demo.briefing_panel import briefing_for_time, load_briefings


def _write(tmp_path: Path) -> Path:
    lines = [
        '{"briefing":"b0","recommendations":[],"seq":0,"t_s":0.0}',
        '{"briefing":"b1","recommendations":[{"rule_id":"r"}],"seq":1,"t_s":60.0}',
        '{"briefing":"b2","recommendations":[],"seq":2,"t_s":120.0}',
    ]
    p = tmp_path / "briefings.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return p


def test_load_and_lookup_by_time(tmp_path: Path):
    briefings = load_briefings(_write(tmp_path))
    assert [b["seq"] for b in briefings] == [0, 1, 2]
    assert briefing_for_time(briefings, 0.0)["briefing"] == "b0"
    assert briefing_for_time(briefings, 59.9)["briefing"] == "b0"
    assert briefing_for_time(briefings, 60.0)["briefing"] == "b1"
    assert briefing_for_time(briefings, 999.0)["briefing"] == "b2"
    assert briefing_for_time(briefings, -1.0) is None
