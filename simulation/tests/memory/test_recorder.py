"""Recorder tests: append-only discipline, integrity, byte-reproducibility."""

from __future__ import annotations

import json

import pytest

from nxt_memory.recorder import EpisodeMemoryRecorder
from nxt_memory.records import (
    HumanVerdict,
    Verdict,
    WindowStatus,
    iter_windows,
    load_meta,
)

from .conftest import make_meta, make_window


def record_episode(base_dir, n_windows: int = 3, truncate_last: bool = False):
    recorder = EpisodeMemoryRecorder(base_dir, make_meta())
    for seq in range(n_windows):
        status = (
            WindowStatus.TRUNCATED
            if truncate_last and seq == n_windows - 1
            else WindowStatus.COMPLETE
        )
        recorder.record_window(make_window(seq=seq, status=status))
    return recorder.finalize()


def test_store_layout_nests_by_site_and_deployment(tmp_path):
    paths = record_episode(tmp_path)
    episode_dir = tmp_path / "site-test" / "deploy-test" / "unit-seed7"
    assert (episode_dir / "windows.jsonl").exists()
    assert (episode_dir / "episode.meta.json").exists()
    assert paths["windows"] == episode_dir / "windows.jsonl"
    meta = load_meta(episode_dir)
    assert meta["n_windows"] == 3
    assert "does NOT represent" in meta["disclaimer"]
    assert [w["seq"] for w in iter_windows(episode_dir)] == [0, 1, 2]


def test_byte_reproducible_across_runs(tmp_path):
    record_episode(tmp_path / "a")
    record_episode(tmp_path / "b")
    rel = "site-test/deploy-test/unit-seed7"
    for name in ("windows.jsonl", "episode.meta.json"):
        assert (tmp_path / "a" / rel / name).read_bytes() == (
            tmp_path / "b" / rel / name
        ).read_bytes()


def test_windows_flushed_per_call_not_buffered(tmp_path):
    recorder = EpisodeMemoryRecorder(tmp_path, make_meta())
    recorder.record_window(make_window(seq=0))
    # readable before finalize: a crash must leave a valid prefix
    episode_dir = tmp_path / "site-test" / "deploy-test" / "unit-seed7"
    lines = (episode_dir / "windows.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    json.loads(lines[0])


def test_seq_must_be_monotonic_from_zero(tmp_path):
    recorder = EpisodeMemoryRecorder(tmp_path, make_meta())
    recorder.record_window(make_window(seq=0))
    with pytest.raises(ValueError, match="seq"):
        recorder.record_window(make_window(seq=2))
    with pytest.raises(ValueError, match="seq"):
        recorder.record_window(make_window(seq=0))


def test_episode_id_mismatch_rejected(tmp_path):
    recorder = EpisodeMemoryRecorder(tmp_path, make_meta())
    with pytest.raises(ValueError, match="episode_id"):
        recorder.record_window(make_window(seq=0, episode_id="other-ep"))


def test_integrity_violations_rejected_at_write_time(tmp_path):
    recorder = EpisodeMemoryRecorder(tmp_path, make_meta())
    bad = make_window(
        seq=0,
        verdicts=(
            HumanVerdict(rec_index=0, rule_id="wrong_rule", verdict=Verdict.ACCEPTED, decided_by="op"),
        ),
    )
    with pytest.raises(ValueError, match="rule_id"):
        recorder.record_window(bad)


def test_truncated_window_must_be_last(tmp_path):
    recorder = EpisodeMemoryRecorder(tmp_path, make_meta())
    recorder.record_window(make_window(seq=0, status=WindowStatus.TRUNCATED))
    with pytest.raises(ValueError, match="truncated"):
        recorder.record_window(make_window(seq=1))


def test_no_silent_overwrite_of_existing_episode(tmp_path):
    record_episode(tmp_path)
    with pytest.raises(FileExistsError):
        EpisodeMemoryRecorder(tmp_path, make_meta())
