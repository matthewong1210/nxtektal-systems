"""Manager-briefing side panel: scrub-synced narration of the same state.

Reads the demo-tier briefings.jsonl sidecar (precomputed at capture time by
scripts/facility_twin_capture.py — the decision layer runs at capture, never
in the viewer). Matches by sim time t_s, never frame index.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path
from typing import Optional


def load_briefings(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    return sorted(records, key=lambda r: r["t_s"])


def briefing_for_time(briefings: list[dict], t_s: float) -> Optional[dict]:
    times = [r["t_s"] for r in briefings]
    idx = bisect.bisect_right(times, t_s) - 1
    return briefings[idx] if idx >= 0 else None


def render_panel(st, briefings: list[dict], t_s: float) -> None:
    record = briefing_for_time(briefings, t_s)
    if record is None:
        st.caption("No briefing yet at this time.")
        return
    st.subheader("Manager briefing (deterministic)")
    st.text(record["briefing"])
    for rec in record["recommendations"]:
        st.markdown(f"- **{rec.get('urgency', '?')}** · {rec.get('rule_id', '?')}: "
                    f"{rec.get('action', rec.get('rationale', ''))}")
