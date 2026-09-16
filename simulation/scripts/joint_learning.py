"""Durable, bounded offline course/range simulation learning batches.

No network, model API, physical control, or operating-system scheduler. A thread
heartbeat can call `run` repeatedly; each call resumes at episode boundaries.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import sys

CONFIG_SCHEMA = "nxt-joint-learning-config/v1"
STATE_SCHEMA = "nxt-joint-learning-state/v1"
REGIMES = ("dry", "morning_rain", "afternoon_rain", "rain_then_surge", "surprise_surge")
DEFAULT_CONFIG = {
    "schema": CONFIG_SCHEMA, "environment": "SIMULATION", "master_seed": 17092026,
    "train_per_regime": 2, "validation_per_regime": 1, "test_per_regime": 1,
    "assumptions": {}, "max_episodes_per_run": 100, "wall_time_seconds": 240,
    "disk_limit_mb": 512, "retain_completed_batches": 12, "max_attempts": 2,
}


class BatchError(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BatchError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(BatchError(f"invalid JSON constant {value}")))
    except (OSError, ValueError) as exc:
        raise BatchError(f"cannot read verified JSON {path.name}: {exc}") from exc


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_record(path: Path, payload: dict) -> None:
    atomic_json(path, {"payload": payload, "sha256": digest(payload)})


def read_record(path: Path) -> dict:
    value = read_json(path)
    if not isinstance(value, dict) or set(value) != {"payload", "sha256"} or not isinstance(value["payload"], dict):
        raise BatchError(f"bad evidence envelope: {path.name}")
    if value["sha256"] != digest(value["payload"]):
        raise BatchError(f"evidence digest mismatch: {path.name}")
    return value["payload"]


def validate_config(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_CONFIG):
        raise BatchError("configuration must use exactly the documented v1 fields")
    if value["schema"] != CONFIG_SCHEMA or value["environment"] != "SIMULATION":
        raise BatchError("only the v1 SIMULATION configuration is supported")
    ranges = {"master_seed": (0, 2**31 - 1), "train_per_regime": (1, 10),
              "validation_per_regime": (1, 10), "test_per_regime": (1, 10),
              "max_episodes_per_run": (1, 500), "wall_time_seconds": (1, 1800),
              "disk_limit_mb": (16, 4096), "retain_completed_batches": (1, 100), "max_attempts": (1, 3)}
    for key, (lower, upper) in ranges.items():
        if type(value[key]) is not int or not lower <= value[key] <= upper:
            raise BatchError(f"{key} must be an integer in [{lower}, {upper}]")
    if not isinstance(value["assumptions"], dict):
        raise BatchError("assumptions must be an explicit parameter object")
    canonical(value)
    return json.loads(canonical(value))


def engine_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    paths = [path for package in root.glob("nxt_*") if package.is_dir() for path in package.rglob("*.py")]
    paths += [root / "scripts" / name for name in ("joint_scenarios.py", "joint_learning.py", "joint_learning_report.py", "course_18_hole_fixture.py")]
    paths += [root / "uv.lock"]
    payload = [(str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(paths)]
    versions = {name: importlib.metadata.version(name) for name in ("numpy", "simpy", "gymnasium", "pydantic")}
    return digest({"sources": payload, "python": sys.version, "dependencies": versions})


@contextmanager
def exclusive_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".batch.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BatchError("another batch worker holds the lock") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _control(directory: Path) -> dict:
    value = read_json(directory / "control.json")
    if not isinstance(value, dict) or set(value) != {"paused"} or type(value["paused"]) is not bool:
        raise BatchError("control.json requires exactly one boolean paused field")
    return value


def _seed(master: int, batch: int, split: str, regime: str, index: int) -> int:
    # Reserve ten indices per regime/split, then use a 32-bit bijection. This
    # matches the simulator's seed contract and prevents reuse across batches.
    ordinal = batch * 150 + ("train", "validation", "test").index(split) * 50 + REGIMES.index(regime) * 10 + index
    if not 0 <= ordinal < 2**32 or not 0 <= index < 10:
        raise BatchError("experiment seed namespace exhausted")
    value = ordinal ^ master
    value = ((value ^ (value >> 16)) * 0x7FEB352D) & 0xFFFFFFFF
    value = ((value ^ (value >> 15)) * 0x846CA68B) & 0xFFFFFFFF
    return value ^ (value >> 16)


def make_manifest(config: dict, state: dict, candidates: list[dict], builder) -> dict:
    from nxt_range_ops.evaluation.joint_learning import episode_input_digest, episode_demand_digest
    descriptors, used_seeds = [], set()
    for split in ("train", "validation", "test"):
        for regime in REGIMES:
            for index in range(config[f"{split}_per_regime"]):
                seed = _seed(config["master_seed"], state["next_batch"], split, regime, index)
                if seed in used_seeds:
                    raise BatchError("seed split collision")
                used_seeds.add(seed)
                descriptor = {"seed": seed, "split": split, "regime": regime,
                              "assumptions": config["assumptions"]}
                descriptor["episode_id"] = "episode_" + digest(descriptor)[:24]
                descriptors.append(descriptor)
    ids = [candidate["candidate_id"] for candidate in candidates]
    if len(set(ids)) != len(ids) or state["incumbent"]["candidate_id"] not in ids or "baseline" not in ids:
        raise BatchError("candidate catalog must have unique IDs, fixed baseline and incumbent")
    compiled = {}
    for descriptor in descriptors:
        episode = builder(descriptor["seed"], descriptor["regime"], descriptor["assumptions"])
        compiled[descriptor["episode_id"]] = {
            "input_digest": episode_input_digest(episode), "demand_digest": episode_demand_digest(episode),
            "scenario": episode["scenario"].model_dump(mode="json"), "joint_inputs": episode["joint_inputs"]}
    return {"schema": "nxt-joint-batch-manifest/v1", "environment": "SIMULATION",
            "engine_id": state["engine_id"], "config_digest": digest(config),
            "batch_index": state["next_batch"], "incumbent": state["incumbent"],
            "candidates": candidates, "episodes": descriptors, "compiled_inputs": compiled}


def _batch_dir(directory: Path, index: int) -> Path:
    return directory / "batches" / f"batch-{index:06d}"


def _state_write(directory: Path, state: dict) -> None:
    state["updated_at_utc"] = utc_now()
    write_record(directory / "state.json", state)


def initialize(directory: Path, config: dict | None = None) -> dict:
    from nxt_range_ops.policies.joint_dispatch import candidate_catalog
    config_path = directory / "config.json"
    if not config_path.exists():
        atomic_json(config_path, validate_config(config or DEFAULT_CONFIG))
    actual = validate_config(read_json(config_path))
    if config is not None and digest(actual) != digest(validate_config(config)):
        raise BatchError("existing experiment configuration is immutable; use a new directory")
    engine = engine_fingerprint()
    if not (directory / "control.json").exists() and (directory / "state.json").exists():
        raise BatchError("existing experiment control.json is missing; refusing to assume it is unpaused")
    if not (directory / "control.json").exists():
        atomic_json(directory / "control.json", {"paused": False})
    if (directory / "state.json").exists():
        state = read_record(directory / "state.json")
        if state.get("schema") != STATE_SCHEMA or state.get("environment") != "SIMULATION":
            raise BatchError("unsupported experiment state")
        if state.get("engine_id") != engine or state.get("config_digest") != digest(actual):
            raise BatchError("code/dependencies or configuration changed; preserve this experiment and start a new directory")
        return state
    baseline = next(row for row in candidate_catalog() if row["candidate_id"] == "baseline")
    state = {"schema": STATE_SCHEMA, "environment": "SIMULATION", "engine_id": engine,
             "config_digest": digest(actual), "next_batch": 0, "completed_batches": 0,
             "total_completed_episodes": 0, "incumbent": baseline, "status": "READY",
             "last_completed": None, "active_progress": None, "last_error": None,
             "created_at_utc": utc_now(), "updated_at_utc": utc_now()}
    _state_write(directory, state)
    return state


def _disk_bytes(directory: Path) -> int:
    return sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())


def _retain(directory: Path, completed_index: int, count: int) -> None:
    # Delete only old episode detail within explicitly owned completed batches.
    # Keep all manifests and compact summaries so experiment history is durable.
    cutoff = completed_index - count + 1
    for path in (directory / "batches").glob("batch-*"):
        try:
            index = int(path.name.removeprefix("batch-"))
        except ValueError:
            continue
        if index < cutoff and (path / "complete.json").exists() and (path / "episodes").is_dir():
            read_record(path / "complete.json")
            shutil.rmtree(path / "episodes")


def _publish(directory: Path, state: dict) -> None:
    from scripts.joint_learning_report import render_report, render_markdown
    payload = {"state": state, "config": read_json(directory / "config.json"),
               "control": _control(directory), "last_completed": state.get("last_completed")}
    write_record(directory / "latest.json", payload)
    for filename, body in (("report.html", render_report(payload)), ("report.md", render_markdown(payload))):
        target = directory / filename
        temp = directory / f".{filename}.pending"
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, target)


def _result_identity(result: object, descriptor: dict, candidate: dict, compiled: dict) -> None:
    expected = {"schema": "nxt-joint-evaluation/v0", "environment": "SIMULATION",
                "episode_id": descriptor["episode_id"], "seed": descriptor["seed"],
                "regime": descriptor["regime"], "split": descriptor["split"],
                "candidate_id": candidate["candidate_id"], "candidate": candidate,
                "input_digest": compiled["input_digest"]}
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
        raise BatchError("evaluator result identity mismatch with frozen manifest")
    # Failed/truncated evaluations may have only partial demand history and are
    # preserved for rejection; only a claimed successful day must match it all.
    if result.get("summary", {}).get("success") is True and result.get("demand_digest") != compiled["demand_digest"]:
        raise BatchError("successful evaluation demand differs from frozen scenario")


def _reconcile_episode_count(directory: Path, state: dict, batch_path: Path, manifest: dict) -> None:
    completed = sum(read_record(path)["episode_count"] for path in (directory / "batches").glob("batch-*/complete.json"))
    count = 0
    by_id = {row["episode_id"]: row for row in manifest["episodes"]}
    candidates = {row["candidate_id"]: row for row in manifest["candidates"]}
    for path in (batch_path / "episodes").glob("*.json"):
        value = read_record(path)
        binding = value.get("binding", {})
        descriptor = binding.get("episode", {})
        candidate = binding.get("candidate", {})
        if (value.get("status") != "SUCCESS" or binding.get("manifest_digest") != digest(manifest)
                or descriptor != by_id.get(descriptor.get("episode_id"))
                or candidate != candidates.get(candidate.get("candidate_id"))
                or path.stem != digest(binding)):
            raise BatchError("cached episode does not belong to the frozen manifest")
        _result_identity(value.get("result"), descriptor, candidate, manifest["compiled_inputs"][descriptor["episode_id"]])
        count += 1
    state["total_completed_episodes"] = completed + count


def run_batch(directory: Path, *, config: dict | None = None, evaluator=None, builder=None) -> dict:
    """Run or resume at most one batch, with a finite wall/episode/disk budget."""
    from nxt_range_ops.evaluation.joint_learning import evaluate_episode, choose_training_finalist, validation_gate
    from nxt_range_ops.policies.joint_dispatch import candidate_catalog
    from scripts.joint_scenarios import build_joint_episode
    evaluator = evaluator or evaluate_episode
    builder = builder or build_joint_episode
    started, executed = time.monotonic(), 0
    with exclusive_lock(directory):
        state = initialize(directory, config)
        settings = validate_config(read_json(directory / "config.json"))
        if state["completed_batches"]:
            _retain(directory, state["next_batch"] - 1, settings["retain_completed_batches"])
        if _control(directory)["paused"]:
            state["status"] = "PAUSED"
            _state_write(directory, state)
            _publish(directory, state)
            return state
        batch_path = _batch_dir(directory, state["next_batch"])
        batch_path.mkdir(parents=True, exist_ok=True)
        manifest_path = batch_path / "manifest.json"
        expected = make_manifest(settings, state, candidate_catalog(), builder)
        if manifest_path.exists():
            manifest = read_record(manifest_path)
            if manifest != expected:
                raise BatchError("frozen manifest diverges from code, split or candidate configuration")
        else:
            manifest = expected
            write_record(manifest_path, manifest)
        manifest_digest = digest(manifest)

        # A crash after complete.json but before state.json commits once on resume.
        if (batch_path / "complete.json").exists():
            complete = read_record(batch_path / "complete.json")
            if complete.get("manifest_digest") != manifest_digest:
                raise BatchError("completed batch refers to another manifest")
            return _finish(directory, state, complete, settings)

        _reconcile_episode_count(directory, state, batch_path, manifest)
        state.update(status="RUNNING", last_error=None)
        _state_write(directory, state)
        candidates = {row["candidate_id"]: row for row in manifest["candidates"]}
        by_split = {name: [row for row in manifest["episodes"] if row["split"] == name] for name in ("train", "validation", "test")}

        def suspend(reason: str) -> dict:
            state["status"] = reason
            _state_write(directory, state)
            _publish(directory, state)
            return state

        def one(descriptor: dict, candidate_id: str) -> dict | None:
            nonlocal executed
            candidate = candidates[candidate_id]
            binding = {"manifest_digest": manifest_digest, "episode": descriptor, "candidate": candidate}
            job_id = digest(binding)
            path = batch_path / "episodes" / f"{job_id}.json"
            if path.exists():
                saved = read_record(path)
                if saved.get("binding") != binding or saved.get("status") != "SUCCESS":
                    raise BatchError("episode cache binding mismatch")
                result = saved.get("result")
                _result_identity(result, descriptor, candidate, manifest["compiled_inputs"][descriptor["episode_id"]])
                return result
            if _control(directory)["paused"]:
                suspend("PAUSED")
                return None
            if executed >= settings["max_episodes_per_run"] or time.monotonic() - started >= settings["wall_time_seconds"]:
                suspend("WAITING_NEXT_RUN")
                return None
            if _disk_bytes(directory) >= settings["disk_limit_mb"] * 1024 * 1024:
                state["last_error"] = "Experiment disk limit reached; no evidence was silently deleted"
                suspend("DISK_LIMIT")
                return None
            failure_path = batch_path / "failures" / f"{job_id}.json"
            attempts = read_record(failure_path)["attempts"] if failure_path.exists() else 0
            if attempts >= settings["max_attempts"]:
                state["last_error"] = f"Retry limit reached for {job_id}"
                suspend("FAILED")
                return None
            state["active_progress"] = {"batch_index": state["next_batch"], "split": descriptor["split"],
                                        "episode_id": descriptor["episode_id"], "candidate_id": candidate_id,
                                        "executed_this_run": executed, "regime": descriptor["regime"]}
            _state_write(directory, state)
            try:
                episode = builder(descriptor["seed"], descriptor["regime"], descriptor["assumptions"])
                episode.update(episode_id=descriptor["episode_id"], split=descriptor["split"])
                result = evaluator(episode, candidate)
                _result_identity(result, descriptor, candidate, manifest["compiled_inputs"][descriptor["episode_id"]])
                write_record(path, {"binding": binding, "status": "SUCCESS", "result": result})
                executed += 1
                state["total_completed_episodes"] += 1
                _state_write(directory, state)
                return result
            except Exception as exc:
                write_record(failure_path, {"binding": binding, "attempts": attempts + 1,
                                            "error_type": type(exc).__name__, "error": str(exc), "at_utc": utc_now()})
                state["last_error"] = f"{type(exc).__name__}: {exc}"
                suspend("FAILED")
                return None

        rows = []
        for descriptor in by_split["train"]:
            for candidate_id in candidates:
                result = one(descriptor, candidate_id)
                if result is None:
                    return state
                rows.append(result)
        selection = choose_training_finalist(rows, incumbent_id=state["incumbent"]["candidate_id"])
        finalist_id = selection["candidate_id"]
        if finalist_id not in candidates:
            raise BatchError("selector returned an unknown candidate")
        selection_payload = {"manifest_digest": manifest_digest, "selection": selection,
                             "training_result_digests": [digest(row) for row in rows]}
        selection_path = batch_path / "selection.json"
        if selection_path.exists() and read_record(selection_path) != selection_payload:
            raise BatchError("frozen training selection changed on replay")
        write_record(selection_path, selection_payload)
        compare_ids = list(dict.fromkeys(["baseline", state["incumbent"]["candidate_id"], finalist_id]))
        valid = []
        for descriptor in by_split["validation"]:
            for candidate_id in compare_ids:
                result = one(descriptor, candidate_id)
                if result is None:
                    return state
                valid.append(result)
        gate = validation_gate(valid, finalist_id, incumbent_id=state["incumbent"]["candidate_id"])
        if type(gate.get("accepted")) is not bool:
            raise BatchError("validation must explicitly accept or reject the frozen finalist")
        gate_payload = {"manifest_digest": manifest_digest, "gate": gate,
                        "validation_result_digests": [digest(row) for row in valid]}
        gate_path = batch_path / "validation.json"
        if gate_path.exists() and read_record(gate_path) != gate_payload:
            raise BatchError("frozen validation decision changed on replay")
        write_record(gate_path, gate_payload)
        test = []
        for descriptor in by_split["test"]:
            for candidate_id in compare_ids:
                result = one(descriptor, candidate_id)
                if result is None:
                    return state
                test.append(result)
        # Test never influences candidate choice or admission.
        winner = finalist_id if gate["accepted"] else state["incumbent"]["candidate_id"]
        complete = {"schema": "nxt-joint-batch-result/v1", "environment": "SIMULATION",
                    "manifest_digest": manifest_digest, "batch_index": state["next_batch"],
                    "selection": selection, "validation": gate, "previous_incumbent": state["incumbent"],
                    "incumbent": candidates[winner], "episode_count": len(rows) + len(valid) + len(test),
                    "train": _compact(rows), "validation_results": _compact(valid), "test": _compact(test),
                    "completed_at_utc": utc_now(), "retained_example": test[-1] if test else None}
        write_record(batch_path / "complete.json", complete)
        return _finish(directory, state, complete, settings)


def _compact(rows: list[dict]) -> list[dict]:
    return [{key: value for key, value in row.items() if key not in {"timeline", "course_frames"}} for row in rows]


def _finish(directory: Path, state: dict, complete: dict, settings: dict) -> dict:
    if complete["batch_index"] != state["next_batch"]:
        raise BatchError("batch sequence mismatch")
    state.update(incumbent=complete["incumbent"], next_batch=state["next_batch"] + 1,
                 completed_batches=state["completed_batches"] + 1, last_completed=complete,
                 status="BATCH_COMPLETE", active_progress=None, last_error=None)
    # Recompute counters from compact committed evidence; no double counting after a crash.
    completed = [read_record(path)["episode_count"] for path in (directory / "batches").glob("batch-*/complete.json")]
    state["total_completed_episodes"] = sum(completed)
    _state_write(directory, state)
    _retain(directory, complete["batch_index"], settings["retain_completed_batches"])
    _publish(directory, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "pause", "resume"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="configuration for a new experiment directory")
    args = parser.parse_args()
    directory = args.state_dir.resolve()
    if args.command in {"pause", "resume"}:
        # Separate control file can pause a currently locked running worker.
        if not (directory / "state.json").exists():
            raise BatchError("initialize an experiment before changing its control")
        atomic_json(directory / "control.json", {"paused": args.command == "pause"})
        print(json.dumps({"paused": args.command == "pause"}))
        return
    if args.command == "status":
        state = read_record(directory / "state.json")
    else:
        config = validate_config(read_json(args.config)) if args.config else None
        state = run_batch(directory, config=config)
    print(json.dumps({key: state[key] for key in ("status", "next_batch", "completed_batches", "total_completed_episodes", "incumbent", "last_error", "updated_at_utc")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
