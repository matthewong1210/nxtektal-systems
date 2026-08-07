"""Append-only episode recorder for operational memory.

Write-side discipline:
- accepts plain dicts / contract objects only — never simulator objects,
  so this module stays stdlib-pure and cannot perturb a running episode;
- one sorted-keys JSON line per window, flushed on every call (a crash
  leaves a valid prefix);
- append-only: no record is ever rewritten; an existing episode directory
  is a hard error, never silently overwritten;
- validates integrity at write time (seq monotonic, episode identity,
  verdict echo-checks, section separation) so the store is auditable by
  construction.

Human decisions arrive inside the windows as recorded inputs from the
driving harness; this layer never generates them and never reads the
store back into the live loop.
"""

from __future__ import annotations

import json
from pathlib import Path

from .records import (
    DISCLAIMER,
    EpisodeMeta,
    MemoryWindow,
    WindowStatus,
    validate_window,
)


class EpisodeMemoryRecorder:
    """Records one episode's MemoryWindows under
    ``<base_dir>/<site_id>/<deployment_id>/<episode_id>/``."""

    def __init__(self, base_dir: str | Path, meta: EpisodeMeta):
        self.meta = meta
        self.episode_dir = (
            Path(base_dir) / meta.site_id / meta.deployment_id / meta.episode_id
        )
        self._windows_path = self.episode_dir / "windows.jsonl"
        if (self.episode_dir / "episode.meta.json").exists():
            raise FileExistsError(
                f"episode store already finalized (append-only, no overwrite): "
                f"{self.episode_dir}"
            )
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        # mode "x" makes the no-overwrite guarantee atomic at the OS level
        self._fh = self._windows_path.open("x", encoding="utf-8")
        self._next_seq = 0
        self._next_cursor = 0
        self._truncated_seen = False

    def record_window(self, window: MemoryWindow) -> None:
        if window.episode_id != self.meta.episode_id:
            raise ValueError(
                f"window episode_id {window.episode_id!r} does not match "
                f"recorder episode_id {self.meta.episode_id!r}"
            )
        if self._truncated_seen:
            raise ValueError(
                "a truncated window is final: no windows may follow it"
            )
        if window.seq != self._next_seq:
            raise ValueError(
                f"non-monotonic seq {window.seq}; expected {self._next_seq}"
            )
        cursor_start = window.execution.get("event_cursor_start")
        cursor_end = window.execution.get("event_cursor_end")
        n_events = len(window.execution.get("events", []))
        if cursor_start != self._next_cursor:
            raise ValueError(
                f"event cursor gap: window starts at {cursor_start}, "
                f"expected {self._next_cursor} — execution truth must be "
                "contiguous across windows"
            )
        if cursor_end != cursor_start + n_events:
            raise ValueError(
                f"event cursor mismatch: {n_events} events but cursor span "
                f"[{cursor_start}, {cursor_end})"
            )
        validate_window(window)
        self._fh.write(json.dumps(window.to_dict(), sort_keys=True) + "\n")
        self._fh.flush()
        self._next_seq += 1
        self._next_cursor = cursor_end
        if window.status is WindowStatus.TRUNCATED:
            self._truncated_seen = True

    def finalize(self) -> dict[str, Path]:
        self._fh.close()
        meta_payload = dict(self.meta.to_dict())
        meta_payload["n_windows"] = self._next_seq
        meta_payload["disclaimer"] = DISCLAIMER
        meta_path = self.episode_dir / "episode.meta.json"
        meta_path.write_text(json.dumps(meta_payload, indent=2, sort_keys=True))
        return {"windows": self._windows_path, "meta": meta_path}
