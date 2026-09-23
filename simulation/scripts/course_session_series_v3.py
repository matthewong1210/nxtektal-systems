"""Bounded deterministic series of additive V3 SIMULATION sessions."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
from pathlib import Path

from scripts.course_session_v3 import DEFAULT_CONFIG as SESSION_DEFAULT_CONFIG
from scripts.course_session_v3 import engine_fingerprint as session_engine_fingerprint
from scripts.course_session_v3 import run as run_session
from scripts.course_session_v3 import validate_config as validate_session_config
from scripts.joint_learning import atomic_json, digest, read_json, read_record, write_record


SCHEMA = "nxt-course-session-series/v2"
DEFAULT_CONFIG = {
    "schema": SCHEMA,
    "environment": "SIMULATION",
    "series_id": SESSION_DEFAULT_CONFIG["series_id"],
    "max_rounds": 3,
    "session_config": deepcopy(SESSION_DEFAULT_CONFIG),
}


def validate_config(value):
    if type(value) is not dict or set(value) != set(DEFAULT_CONFIG):
        raise ValueError("V3 series config must contain exactly the versioned fields")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("V3 series requires its additive SIMULATION schema")
    if type(value["max_rounds"]) is not int or not 1 <= value["max_rounds"] <= 100:
        raise ValueError("invalid bounded max_rounds")
    child = validate_session_config(value["session_config"])
    if child["series_id"] != value["series_id"]:
        raise ValueError("child session series_id must equal the explicit parent series_id")
    return {**deepcopy(value), "session_config": child}


def engine_fingerprint():
    return digest({
        "session_engine": session_engine_fingerprint(),
        "series_source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    })


def _round_config(config, index):
    if type(index) is not int or not 0 <= index < config["max_rounds"]:
        raise ValueError("round index is outside the bounded series")
    value = deepcopy(config["session_config"])
    value["series_id"] = config["series_id"]
    value["round_index"] = index
    value["session_id"] = f"{config['series_id']}-session-{index:010d}"
    value["round_id"] = f"{config['series_id']}-round-{index:010d}"
    value["seed"] = (value["seed"] + index * 2654435761) % 2**32
    return validate_session_config(value)


def _round_name(index):
    return f"round-{index:010d}"


@contextmanager
def _lock(path):
    if path.is_symlink():
        raise ValueError("series lock path must not be a symlink")
    stream = path.open("a+b")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("another worker owns the V3 series") from None
        yield
    finally:
        stream.close()


def _state(config):
    return {
        "schema": SCHEMA,
        "environment": "SIMULATION",
        "series_id": config["series_id"],
        "config_digest": digest(config),
        "engine_digest": engine_fingerprint(),
        "status": "INITIALIZED",
        "current": None,
        "history": [],
        "completed_rounds": 0,
        "max_rounds": config["max_rounds"],
    }


def _validate_state(value, config):
    fields = {"schema", "environment", "series_id", "config_digest", "engine_digest",
              "status", "current", "history", "completed_rounds", "max_rounds"}
    if type(value) is not dict or set(value) != fields:
        raise ValueError("invalid V3 series state fields")
    if (value["schema"] != SCHEMA or value["environment"] != "SIMULATION"
            or value["series_id"] != config["series_id"]
            or value["config_digest"] != digest(config)
            or value["engine_digest"] != engine_fingerprint()
            or value["max_rounds"] != config["max_rounds"]):
        raise ValueError("V3 series identity/config/engine changed")
    if (type(value["completed_rounds"]) is not int
            or not 0 <= value["completed_rounds"] <= config["max_rounds"]
            or type(value["history"]) is not list
            or len(value["history"]) != value["completed_rounds"]):
        raise ValueError("invalid bounded V3 series history")
    if [row.get("round_index") for row in value["history"]] != list(range(value["completed_rounds"])):
        raise ValueError("V3 series history is not contiguous")
    for row in value["history"]:
        expected = _round_config(config, row["round_index"])
        if (set(row) != {"round_index", "session_id", "round_id", "directory", "status",
                        "child_config_digest", "child_state_digest"}
                or row["session_id"] != expected["session_id"]
                or row["round_id"] != expected["round_id"]
                or row["directory"] != _round_name(row["round_index"])
                or row["status"] != "SESSION_COMPLETE"
                or row["child_config_digest"] != digest(expected)):
            raise ValueError("completed V3 round identity changed")
    current = value["current"]
    if current is not None:
        if (type(current) is not dict
                or set(current) != {"round_index", "session_id", "round_id", "directory", "status"}
                or not 0 <= current["round_index"] < config["max_rounds"]):
            raise ValueError("invalid current V3 round")
        expected = _round_config(config, current["round_index"])
        if (current["session_id"] != expected["session_id"]
                or current["round_id"] != expected["round_id"]
                or current["directory"] != _round_name(current["round_index"])):
            raise ValueError("current V3 round identity changed")
    return value


def _control(root):
    value = read_json(root / "control.json")
    if type(value) is not dict or set(value) != {"paused"} or type(value["paused"]) is not bool:
        raise ValueError("invalid V3 series pause control")
    return value


def run(root, config=None):
    """Advance at most one deterministic child round chunk."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("V3 series root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    with _lock(root / ".series.lock"):
        state_path = root / "state.json"
        if state_path.exists():
            stored = validate_config(read_json(root / "config.json"))
            if config is not None and validate_config(config) != stored:
                raise ValueError("existing V3 series configuration is immutable")
            config = stored
            state = _validate_state(read_record(state_path), config)
        else:
            if any(path.name != ".series.lock" for path in root.iterdir()):
                raise ValueError("new V3 series requires an empty directory")
            config = validate_config(DEFAULT_CONFIG if config is None else config)
            atomic_json(root / "config.json", config)
            atomic_json(root / "control.json", {"paused": False})
            state = _state(config)
            write_record(state_path, state)
        if _control(root)["paused"]:
            return {**state, "status": "PAUSED"}
        if state["completed_rounds"] == config["max_rounds"]:
            state["status"] = "SERIES_COMPLETE"
            write_record(state_path, state)
            return deepcopy(state)
        current = state["current"]
        if current is None or current["status"] == "SESSION_COMPLETE":
            index = state["completed_rounds"]
            child_config = _round_config(config, index)
            current = {
                "round_index": index,
                "session_id": child_config["session_id"],
                "round_id": child_config["round_id"],
                "directory": _round_name(index),
                "status": "INITIALIZED",
            }
            state["current"] = current
            state["status"] = "ROUND_INITIALIZED"
            write_record(state_path, state)
        child_config = _round_config(config, current["round_index"])
        child_state = run_session(root / current["directory"], child_config)
        current["status"] = child_state["status"]
        if child_state["status"] == "SESSION_COMPLETE":
            entry = {
                "round_index": current["round_index"],
                "session_id": current["session_id"],
                "round_id": current["round_id"],
                "directory": current["directory"],
                "status": "SESSION_COMPLETE",
                "child_config_digest": digest(child_config),
                "child_state_digest": digest(child_state),
            }
            if state["completed_rounds"] == current["round_index"]:
                state["history"].append(entry)
                state["completed_rounds"] += 1
            elif state["history"][current["round_index"]] != entry:
                raise ValueError("completed V3 child changed before parent publication")
            state["status"] = ("SERIES_COMPLETE" if state["completed_rounds"] == config["max_rounds"]
                               else "ROUND_COMPLETE")
        else:
            state["status"] = "CHILD_" + child_state["status"]
        write_record(state_path, state)
        return deepcopy(state)


def status(root):
    root = Path(root)
    config = validate_config(read_json(root / "config.json"))
    state = _validate_state(read_record(root / "state.json"), config)
    return {**state, "status": "PAUSED"} if _control(root)["paused"] else state


def set_paused(root, paused):
    if type(paused) is not bool:
        raise ValueError("paused must be boolean")
    root = Path(root)
    with _lock(root / ".series.lock"):
        read_record(root / "state.json")
        atomic_json(root / "control.json", {"paused": paused})
    return status(root)
