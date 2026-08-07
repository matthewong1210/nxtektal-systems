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
    """Load the briefings sidecar, sorted by sim time t_s.

    The sidecar is written by scripts/facility_twin_capture.py but read here
    as untrusted user input (its path comes from a free-text sidebar field),
    so each line is validated defensively: lines that parse as valid JSON
    but are not a JSON object, or are an object missing a numeric "t_s", are
    silently skipped rather than raising. This panel is presentation-tier —
    a partial render beats a crash on a hand-edited or truncated sidecar.
    """
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            t_s = record.get("t_s")
            if not isinstance(t_s, (int, float)) or isinstance(t_s, bool):
                continue
            records.append(record)
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
    st.text(record.get("briefing", "(briefing missing)"))
    for rec in record.get("recommendations", []):
        st.markdown(f"- **{rec.get('urgency', '?')}** · {rec.get('rule_id', '?')}: "
                    f"{rec.get('action', rec.get('rationale', ''))}")
