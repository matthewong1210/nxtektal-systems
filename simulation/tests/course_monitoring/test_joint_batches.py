"""Crash, immutable split, pause, cache-integrity and offline-report boundaries."""
from copy import deepcopy
import fcntl
import json
from types import SimpleNamespace

import pytest

from scripts import joint_learning as runner
from scripts.joint_learning_report import render_report


@pytest.fixture
def harness(monkeypatch):
    from nxt_range_ops.evaluation import joint_learning as evaluation
    from nxt_range_ops.policies.joint_dispatch import candidate_catalog
    catalog = candidate_catalog()
    finalist = catalog[1]["candidate_id"]
    calls, phases = [], []

    def build(seed, regime, assumptions):
        scenario = SimpleNamespace(hours=SimpleNamespace(open_minute=480), model_dump=lambda **kwargs: {"seed": seed, "regime": regime})
        return {"seed": seed, "regime": regime, "scenario": scenario, "joint_inputs": {"demand_by_minute": [1, 2, 3]}}

    def evaluate(episode, candidate):
        calls.append((episode["episode_id"], candidate["candidate_id"]))
        return {"schema": "nxt-joint-evaluation/v0", "environment": "SIMULATION", "candidate": candidate,
                "episode_id": episode["episode_id"], "candidate_id": candidate["candidate_id"],
                "seed": episode["seed"], "regime": episode["regime"], "split": episode["split"],
                "input_digest": evaluation.episode_input_digest(episode), "demand_digest": evaluation.episode_demand_digest(episode),
                "summary": {"success": True, "demand_fill_rate": 0.9, "stockout_minutes": 10,
                            "inspection_completion_rate": 0.8}, "score": 1, "timeline": []}

    def choose(rows, incumbent_id):
        assert all(row["split"] == "train" for row in rows)
        phases.append("train")
        return {"candidate_id": finalist, "reason": "Fixture selection only"}

    def validate(rows, finalist_id, incumbent_id):
        assert all(row["split"] == "validation" for row in rows)
        phases.append("validation")
        return {"accepted": True, "candidate_id": finalist_id, "reason": "Fixture validation only"}

    monkeypatch.setattr(evaluation, "choose_training_finalist", choose)
    monkeypatch.setattr(evaluation, "validation_gate", validate)
    monkeypatch.setattr(runner, "engine_fingerprint", lambda: "frozen-test-engine")
    config = {**runner.DEFAULT_CONFIG, "train_per_regime": 1}
    return config, build, evaluate, calls, phases


def test_completed_batch_is_frozen_and_test_never_selects(tmp_path, harness):
    config, build, evaluate, calls, phases = harness
    state = runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    assert state["status"] == "BATCH_COMPLETE" and state["completed_batches"] == 1
    assert len(calls) == len(set(calls))
    assert phases == ["train", "validation"]
    manifest = runner.read_record(tmp_path / "batches/batch-000000/manifest.json")
    splits = {name: {row["seed"] for row in manifest["episodes"] if row["split"] == name} for name in ("train", "validation", "test")}
    assert not splits["train"] & splits["validation"]
    assert not splits["train"] & splits["test"]
    assert not splits["validation"] & splits["test"]
    assert "保留测试" in (tmp_path / "report.html").read_text()
    assert (tmp_path / "batches/batch-000000/selection.json").exists()
    assert (tmp_path / "batches/batch-000000/validation.json").exists()


def test_seed_namespace_never_reuses_train_validation_or_test_across_batches():
    seeds = [runner._seed(17, batch, split, regime, index)
             for batch in range(10) for split in ("train", "validation", "test")
             for regime in runner.REGIMES for index in range(10)]
    assert len(set(seeds)) == len(seeds)
    assert all(0 <= seed < 2**32 for seed in seeds)


def test_missing_pause_control_cannot_silently_restart_experiment(tmp_path, harness):
    config, build, evaluate, calls, _ = harness
    config["max_episodes_per_run"] = 1
    runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    (tmp_path / "control.json").unlink()
    with pytest.raises(runner.BatchError, match="control.json is missing"):
        runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert len(calls) == 1


@pytest.mark.parametrize("key,value", [("seed", 1), ("regime", "wrong"), ("environment", "LIVE"), ("candidate", {}), ("input_digest", "wrong")])
def test_result_must_match_entire_manifest_identity(tmp_path, harness, key, value):
    config, build, evaluate, calls, _ = harness
    def wrong(episode, candidate):
        result = evaluate(episode, candidate)
        result[key] = value
        return result
    state = runner.run_batch(tmp_path, config=config, builder=build, evaluator=wrong)
    assert state["status"] == "FAILED" and "identity mismatch" in state["last_error"]
    assert not list((tmp_path / "batches/batch-000000/episodes").glob("*.json"))


def test_count_recovers_crash_after_episode_commit_before_state(tmp_path, harness, monkeypatch):
    config, build, evaluate, calls, _ = harness
    config["max_episodes_per_run"] = 1
    original = runner._state_write
    class Crash(BaseException):
        pass
    def crash(directory, state):
        if state["total_completed_episodes"] == 1:
            raise Crash()
        original(directory, state)
    monkeypatch.setattr(runner, "_state_write", crash)
    with pytest.raises(Crash):
        runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    monkeypatch.setattr(runner, "_state_write", original)
    state = runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert state["total_completed_episodes"] == 2 and len(calls) == 2


def test_pause_and_resume_never_reexecutes_completed_episode(tmp_path, harness):
    config, build, evaluate, calls, _ = harness
    config["max_episodes_per_run"] = 1
    first = runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    assert first["status"] == "WAITING_NEXT_RUN" and len(calls) == 1
    runner.atomic_json(tmp_path / "control.json", {"paused": True})
    paused = runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert paused["status"] == "PAUSED" and len(calls) == 1
    runner.atomic_json(tmp_path / "control.json", {"paused": False})
    resumed = runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert resumed["status"] == "WAITING_NEXT_RUN" and len(calls) == 2
    assert len(set(calls)) == 2


def test_episode_record_tamper_fails_loudly(tmp_path, harness):
    config, build, evaluate, calls, _ = harness
    config["max_episodes_per_run"] = 1
    runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    path = next((tmp_path / "batches/batch-000000/episodes").glob("*.json"))
    record = json.loads(path.read_text())
    record["payload"]["result"]["score"] = 999
    path.write_text(json.dumps(record))
    with pytest.raises(runner.BatchError, match="digest mismatch"):
        runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert len(calls) == 1


def test_changed_code_and_config_cannot_mix_results(tmp_path, harness, monkeypatch):
    config, build, evaluate, _, _ = harness
    config["max_episodes_per_run"] = 1
    runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    changed = deepcopy(config)
    changed["master_seed"] += 1
    with pytest.raises(runner.BatchError, match="immutable"):
        runner.run_batch(tmp_path, config=changed, builder=build, evaluator=evaluate)
    monkeypatch.setattr(runner, "engine_fingerprint", lambda: "changed-engine")
    with pytest.raises(runner.BatchError, match="code/dependencies"):
        runner.run_batch(tmp_path, builder=build, evaluator=evaluate)


def test_lock_refuses_concurrent_worker(tmp_path, harness):
    config, build, evaluate, calls, _ = harness
    with (tmp_path / ".batch.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(runner.BatchError, match="another batch"):
            runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    assert not calls


def test_failure_is_recorded_and_retry_is_bounded(tmp_path, harness):
    config, build, _, calls, _ = harness
    def fail(episode, candidate):
        calls.append(episode["episode_id"])
        raise RuntimeError("injected interruption")
    for _ in range(3):
        state = runner.run_batch(tmp_path, config=config, builder=build, evaluator=fail)
        assert state["status"] == "FAILED"
    assert len(calls) == 2
    assert state["last_error"].startswith("Retry limit")
    assert not (tmp_path / "batches/batch-000000/complete.json").exists()


def test_complete_record_recovers_crash_before_state_commit(tmp_path, harness, monkeypatch):
    config, build, evaluate, calls, _ = harness
    original = runner._finish
    class Crash(BaseException):
        pass
    monkeypatch.setattr(runner, "_finish", lambda *args: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    completed_calls = len(calls)
    monkeypatch.setattr(runner, "_finish", original)
    recovered = runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert recovered["completed_batches"] == 1
    assert recovered["total_completed_episodes"] == completed_calls
    assert len(calls) == completed_calls


def test_disk_limit_stops_before_new_episode(tmp_path, harness, monkeypatch):
    config, build, evaluate, calls, _ = harness
    monkeypatch.setattr(runner, "_disk_bytes", lambda path: 10**12)
    result = runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    assert result["status"] == "DISK_LIMIT" and not calls


def test_duplicate_keys_and_nonfinite_config_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"paused":false,"paused":true}')
    with pytest.raises(runner.BatchError, match="duplicate JSON"):
        runner.read_json(path)
    for bad in (float("nan"), True, 0):
        with pytest.raises(runner.BatchError):
            runner.validate_config({**runner.DEFAULT_CONFIG, "wall_time_seconds": bad})


def test_report_is_readonly_escaped_and_simulation_only(tmp_path, harness):
    config, build, evaluate, _, _ = harness
    runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    payload = runner.read_record(tmp_path / "latest.json")
    payload["state"]["last_error"] = '</td><script>alert(1)</script>'
    rendered = render_report(payload)
    assert '<script>' not in rendered
    assert '&lt;script&gt;' in rendered
    for surface in ("fetch(", "localStorage", "<form", "<iframe", "http://", "https://"):
        assert surface not in rendered
    payload["state"]["environment"] = "LIVE"
    with pytest.raises(ValueError):
        render_report(payload)


def test_report_exposes_failed_denominator_and_excludes_partial_metric():
    from scripts.joint_learning_report import _groups
    rows = [{"candidate_id": "baseline", "score": 10, "summary": {"success": True, "demand_fill_rate": 0.8}},
            {"candidate_id": "baseline", "score": None, "summary": {"success": False, "demand_fill_rate": 0.1}}]
    group = _groups(rows)[0]
    assert group["episodes"] == 2 and group["completed"] == 1 and group["failed"] == 1
    assert group["fill"] == 80 and group["score"] == 10


def test_retention_recovers_crash_after_state_commit(tmp_path, harness, monkeypatch):
    config, build, evaluate, _, _ = harness
    config["retain_completed_batches"] = 1
    runner.run_batch(tmp_path, config=config, builder=build, evaluator=evaluate)
    original = runner._retain
    class Crash(BaseException):
        pass
    def retain(directory, completed_index, count):
        if completed_index == 1:
            raise Crash()
        return original(directory, completed_index, count)
    monkeypatch.setattr(runner, "_retain", retain)
    with pytest.raises(Crash):
        runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert (tmp_path / "batches/batch-000000/episodes").is_dir()
    monkeypatch.setattr(runner, "_retain", original)
    runner.atomic_json(tmp_path / "control.json", {"paused": True})
    state = runner.run_batch(tmp_path, builder=build, evaluator=evaluate)
    assert state["completed_batches"] == 2
    assert not (tmp_path / "batches/batch-000000/episodes").exists()
    assert (tmp_path / "batches/batch-000000/complete.json").exists()
