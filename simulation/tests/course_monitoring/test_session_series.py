"""Series orchestration and evidence retention, with a durable child double."""
from copy import deepcopy
import fcntl
from pathlib import Path

import pytest

from scripts import course_session_series as series
from scripts.joint_learning import atomic_json, digest, read_json, read_record, write_record


@pytest.fixture
def child(monkeypatch):
    calls = []
    options = {"chunks": 2, "status": None}
    monkeypatch.setattr(series, "session_engine_fingerprint", lambda: "a" * 64)

    def run(directory, config):
        directory = Path(directory)
        calls.append({"directory": directory.name, "seed": config["seed"]})
        directory.mkdir(parents=True, exist_ok=True)
        if not (directory / "state.json").exists():
            atomic_json(directory / "config.json", config)
            atomic_json(directory / "control.json", {"paused": False})
            write_record(directory / "compiled.json", {"schema": "test-only", "seed": config["seed"]})
            step = 0
        else:
            step = read_record(directory / "state.json")["step"]
        step += 1
        result = {"schema": "nxt-whole-course-session/v2",
                  "status": options["status"] or ("SESSION_COMPLETE" if step >= options["chunks"] else "CHUNK_COMPLETE"),
                  "step": step, "minute": 480 + step * config["advance_minutes"],
                  "config_digest": digest(config), "engine": "a" * 64,
                  "compiled_digest": digest(read_record(directory / "compiled.json")),
                  "replay_digest": digest({"step": step})}
        write_record(directory / "report.json", {"environment": "SIMULATION", "status": result["status"],
                                                 "summary": {"minute": result["minute"], "metrics": {"balls_served": step * 100}},
                                                 "actual_shot_count": step * 100, "coverage": [],
                                                 "vision_evaluation": {"field_validated": False}})
        (directory / "image.png").write_bytes(b"synthetic-test-evidence")
        (directory / "report.html").write_text("<!doctype html><title>test-only report</title>")
        write_record(directory / "state.json", result)
        return result

    monkeypatch.setattr(series, "run_session", run)
    return calls, options


def config(**changes):
    value = deepcopy(series.DEFAULT_CONFIG)
    value.update(changes)
    return value


def test_one_chunk_per_call_new_seed_only_after_complete(tmp_path, child):
    calls, _ = child
    first = series.run(tmp_path)
    assert len(calls) == 1 and first["current"]["index"] == 0
    assert first["completed_rounds"] == 0
    second = series.run(tmp_path)
    assert len(calls) == 2 and second["status"] == "ROUND_COMPLETE"
    assert second["completed_rounds"] == 1 and second["current"]["index"] == 0
    third = series.run(tmp_path)
    assert len(calls) == 3 and third["current"]["index"] == 1
    assert calls[0]["seed"] == calls[1]["seed"] != calls[2]["seed"]
    assert "no inventory" in third["history"][0]["summary"]["round_reset"]
    assert read_json(tmp_path / calls[0]["directory"] / "config.json")["days"] == 3
    assert read_json(tmp_path / calls[0]["directory"] / "config.json")["advance_minutes"] == series.SESSION_DEFAULT_CONFIG["advance_minutes"]


def test_parent_and_child_pauses_have_priority_and_resume_never_unpauses_child(tmp_path, child):
    calls, _ = child
    state = series.run(tmp_path)
    child_dir = tmp_path / state["current"]["directory"]
    atomic_json(child_dir / "control.json", {"paused": True})
    series.set_paused(tmp_path, True)
    assert series.run(tmp_path)["status"] == "PAUSED"
    assert series.status(tmp_path)["status"] == "PAUSED"
    series.set_paused(tmp_path, False)
    assert series.run(tmp_path)["status"] == "CHILD_PAUSED"
    assert series.status(tmp_path)["status"] == "CHILD_PAUSED"
    assert read_json(child_dir / "control.json") == {"paused": True}
    assert len(calls) == 1
    atomic_json(child_dir / "control.json", {"paused": False})
    assert series.run(tmp_path)["status"] == "ROUND_COMPLETE"


def test_pause_of_completed_child_prevents_starting_another_round(tmp_path, child):
    calls, options = child
    options["chunks"] = 1
    state = series.run(tmp_path)
    atomic_json(tmp_path / state["current"]["directory"] / "control.json", {"paused": True})
    assert series.run(tmp_path)["status"] == "CHILD_PAUSED"
    assert len(calls) == 1


def test_exclusive_series_and_retirement_child_locks(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    first = series.run(tmp_path, config(retain_completed_rounds=1))
    with (tmp_path / ".series.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another worker"):
            series.run(tmp_path)
    old = tmp_path / first["current"]["directory"]
    with (old / ".session.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="another worker"):
            series.run(tmp_path)
    assert old.exists()


def test_immutable_config_engine_and_missing_controls_fail_closed(tmp_path, child, monkeypatch):
    calls, _ = child
    state = series.run(tmp_path)
    with pytest.raises(ValueError, match="immutable"):
        series.run(tmp_path, config(disk_limit_mb=2048))
    monkeypatch.setattr(series, "session_engine_fingerprint", lambda: "b" * 64)
    with pytest.raises(ValueError, match="code/dependencies"):
        series.run(tmp_path)
    # Root pause is observed even if the installed engine has changed.
    series.set_paused(tmp_path, True)
    assert series.run(tmp_path)["status"] == "PAUSED"
    series.set_paused(tmp_path, False)
    monkeypatch.setattr(series, "session_engine_fingerprint", lambda: "a" * 64)
    (tmp_path / state["current"]["directory"] / "control.json").unlink()
    with pytest.raises(ValueError, match="control"):
        series.run(tmp_path)
    assert len(calls) == 1


def test_invalid_parent_control_is_not_recreated(tmp_path, child):
    series.run(tmp_path)
    (tmp_path / "control.json").unlink()
    with pytest.raises(ValueError, match="control"):
        series.run(tmp_path)
    with pytest.raises(ValueError, match="control"):
        series.set_paused(tmp_path, False)
    assert not (tmp_path / "control.json").exists()


def test_pause_during_child_call_publishes_cursor_but_defers_retention(tmp_path, child, monkeypatch):
    calls, options = child
    options["chunks"] = 1
    series.run(tmp_path, config(retain_completed_rounds=1))
    original = series.run_session

    def pausing(directory, config):
        result = original(directory, config)
        series.set_paused(tmp_path, True)
        return result

    monkeypatch.setattr(series, "run_session", pausing)
    state = series.run(tmp_path)
    assert state["status"] == "PAUSED" and state["completed_rounds"] == 2
    assert len(calls) == 2 and len(list(tmp_path.glob("round-*"))) == 2
    assert series.run(tmp_path)["status"] == "PAUSED" and len(calls) == 2


def test_partial_initialization_and_seed_tampering_fail_closed(tmp_path, child):
    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    atomic_json(incomplete / "config.json", series.DEFAULT_CONFIG)
    with pytest.raises(ValueError, match="empty directory"):
        series.run(incomplete)
    proper = tmp_path / "proper"
    state = series.run(proper)
    child_dir = proper / state["current"]["directory"]
    original = read_json(child_dir / "config.json")
    original["seed"] += 1
    atomic_json(child_dir / "config.json", original)
    with pytest.raises(ValueError, match="identity"):
        series.run(proper)


def test_retention_preserves_three_completed_rounds_and_verified_summaries(tmp_path, child):
    calls, options = child
    options["chunks"] = 1
    for _ in range(5):
        state = series.run(tmp_path)
    assert len(calls) == 5 and state["completed_rounds"] == 5
    assert len(list(tmp_path.glob("round-*"))) == 3
    assert [entry["detail_status"] for entry in state["history"]] == ["COMPACTED"] * 2 + ["RETAINED"] * 3
    for entry in state["history"]:
        assert digest(entry["summary"]) == entry["summary_digest"]
        assert entry["completion_status"] == "SESSION_COMPLETE"
        assert ("files" in entry) == (entry["detail_status"] == "RETAINED")
    assert all(entry["seed"] == series._round_config(series.DEFAULT_CONFIG, entry["index"])["seed"]
               for entry in state["history"])


def test_stable_index_links_current_and_retained_reports_after_retirement(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    for _ in range(4):
        state = series.run(tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert state["current"]["directory"] + "/report.html" in page
    assert series._round_name(0) + "/report.html" not in page
    for index in (1, 2, 3):
        assert series._round_name(index) + "/report.html" in page
    assert "非模型训练" in page and "不同轮次使用新种子并重置" in page
    assert str(state["current"]["minute"]) in page
    assert not (tmp_path / ".index.pending.html").exists()


def test_index_failure_does_not_rollback_authoritative_cursor(tmp_path, child, monkeypatch):
    _, options = child
    options["chunks"] = 1
    original = series._publish_index
    monkeypatch.setattr(series, "_publish_index", lambda *args: (_ for _ in ()).throw(OSError("projection unavailable")))
    result = series.run(tmp_path)
    assert read_record(tmp_path / "state.json")["completed_rounds"] == result["completed_rounds"] == 1
    monkeypatch.setattr(series, "_publish_index", original)
    series.run(tmp_path)
    assert (tmp_path / "index.html").exists()


def test_completed_details_are_never_deleted_when_changed(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    first = series.run(tmp_path, config(retain_completed_rounds=1))
    old = tmp_path / first["current"]["directory"]
    (old / "image.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="evidence changed"):
        series.run(tmp_path)
    assert old.exists() and (old / "image.png").read_bytes() == b"changed"


def test_parent_crash_after_child_complete_is_reconciled_without_second_child_call(tmp_path, child, monkeypatch):
    calls, options = child
    options["chunks"] = 1
    original = series._finish
    monkeypatch.setattr(series, "_finish", lambda *args: (_ for _ in ()).throw(OSError("publication crash")))
    with pytest.raises(OSError, match="publication crash"):
        series.run(tmp_path)
    assert len(calls) == 1
    monkeypatch.setattr(series, "_finish", original)
    state = series.run(tmp_path)
    assert state["completed_rounds"] == 1 and len(calls) == 1
    assert series.run(tmp_path)["completed_rounds"] == 2 and len(calls) == 2


def test_retirement_crash_resumes_only_approved_remaining_files(tmp_path, child, monkeypatch):
    _, options = child
    options["chunks"] = 1
    first = series.run(tmp_path, config(retain_completed_rounds=1))
    old = tmp_path / first["current"]["directory"]
    original = series.shutil.rmtree

    def crash(directory):
        (directory / "image.png").unlink()
        raise OSError("partial deletion")

    monkeypatch.setattr(series.shutil, "rmtree", crash)
    with pytest.raises(OSError, match="partial deletion"):
        series.run(tmp_path)
    state = read_record(tmp_path / "state.json")
    assert state["history"][0]["detail_status"] == "RETIRING"
    monkeypatch.setattr(series.shutil, "rmtree", original)
    state = series.run(tmp_path)
    assert not old.exists() and state["history"][0]["detail_status"] == "COMPACTED"


def test_retirement_recovery_refuses_new_unapproved_files(tmp_path, child, monkeypatch):
    _, options = child
    options["chunks"] = 1
    first = series.run(tmp_path, config(retain_completed_rounds=1))
    old = tmp_path / first["current"]["directory"]
    original = series.shutil.rmtree
    monkeypatch.setattr(series.shutil, "rmtree", lambda *args: (_ for _ in ()).throw(OSError("delete crash")))
    with pytest.raises(OSError):
        series.run(tmp_path)
    (old / "not-approved.txt").write_text("preserve me")
    monkeypatch.setattr(series.shutil, "rmtree", original)
    with pytest.raises(ValueError, match="unapproved"):
        series.run(tmp_path)
    assert (old / "not-approved.txt").read_text() == "preserve me"


def test_history_is_bounded_and_old_compacted_entries_fold_into_anchor(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    for _ in range(6):
        state = series.run(tmp_path, config(retain_completed_rounds=1, history_limit=3))
    assert state["completed_rounds"] == 6 and state["archived_rounds"] == 3
    assert [entry["index"] for entry in state["history"]] == [3, 4, 5]
    assert len(state["history_anchor"]) == 64
    assert len(list(tmp_path.glob("round-*"))) == 1


def test_parent_disk_budget_stops_without_running_child_again(tmp_path, child):
    calls, _ = child
    series.run(tmp_path, config(disk_limit_mb=16))
    with (tmp_path / "external-budget-fixture.bin").open("wb") as stream:
        stream.truncate(17 * 1024**2)
    state = series.run(tmp_path)
    assert state["status"] == "DISK_LIMIT" and len(calls) == 1


def test_disk_pressure_can_compact_more_than_normal_retention(tmp_path, child, monkeypatch):
    _, options = child
    options["chunks"] = 1
    series.run(tmp_path)
    series.run(tmp_path)
    original = series._disk_bytes
    monkeypatch.setattr(series, "_disk_bytes", lambda root: 2000 * 1024**2 if len(list(root.glob("round-*"))) > 1 else original(root))
    state = series.run(tmp_path)
    assert len(list(tmp_path.glob("round-*"))) == 1
    assert state["completed_rounds"] == 3 and state["status"] == "ROUND_COMPLETE"


@pytest.mark.parametrize("status", ["TIME_BUDGET", "DISK_LIMIT", "PAUSED"])
def test_nonterminal_child_result_never_rotates_seed(tmp_path, child, status):
    calls, options = child
    options["status"] = status
    for _ in range(2):
        state = series.run(tmp_path)
        assert state["completed_rounds"] == 0 and state["current"]["index"] == 0
    assert calls[0]["seed"] == calls[1]["seed"]


def test_symlinks_cannot_redirect_detail_deletion(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    first = series.run(tmp_path, config(retain_completed_rounds=1))
    old = tmp_path / first["current"]["directory"]
    target = tmp_path / "keep.txt"
    target.write_text("keep")
    (old / "link.txt").symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        series.run(tmp_path)
    assert target.read_text() == "keep"


def test_invalid_manifest_cannot_escape_round_directory(tmp_path, child):
    _, options = child
    options["chunks"] = 1
    series.run(tmp_path)
    state = read_record(tmp_path / "state.json")
    entry = state["history"][0]
    entry["files"]["../outside"] = {"size": 1, "sha256": "a" * 64}
    entry["tree_digest"] = digest(entry["files"])
    write_record(tmp_path / "state.json", state)
    with pytest.raises(ValueError, match="unsafe detail"):
        series.run(tmp_path)


@pytest.mark.parametrize("changes", [
    {"unknown": 1}, {"environment": "PHYSICAL"}, {"retain_completed_rounds": 0},
    {"retain_completed_rounds": 4}, {"history_limit": 101}, {"disk_limit_mb": True},
    {"session_config": {}},
])
def test_invalid_configuration_is_rejected(changes):
    with pytest.raises(ValueError):
        series.validate_config(config(**changes))
