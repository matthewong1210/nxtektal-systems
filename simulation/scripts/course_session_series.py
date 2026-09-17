"""Bounded SIMULATION rounds; one child chunk per call, no background daemon.

Each round is one continuous multi-day course_session. A subsequent round uses
a new immutable seed and resets all runtime state; rounds are independent
experiments, not one physical course's endless chronology or model training.
The scheduler is external. No command changes a child session's pause control.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
from html import escape
import json
from pathlib import Path, PurePosixPath
import shutil

from scripts.course_session import run as run_session
from scripts.course_session import validate_config as validate_session_config
from scripts.course_session import engine_fingerprint as session_engine_fingerprint
from scripts.course_session import DEFAULT_CONFIG as SESSION_DEFAULT_CONFIG
from scripts.joint_learning import atomic_json, digest, read_json, read_record, write_record

SCHEMA = "nxt-course-session-series/v1"
DEFAULT_CONFIG = {
    "schema": SCHEMA, "environment": "SIMULATION", "disk_limit_mb": 1536,
    "retain_completed_rounds": 3, "history_limit": 100,
    "session_config": deepcopy(SESSION_DEFAULT_CONFIG),
}
CHILD_STATUSES = {"INITIALIZED", "CHUNK_COMPLETE", "TIME_BUDGET", "DISK_LIMIT", "PAUSED", "SESSION_COMPLETE"}


def validate_config(value):
    if type(value) is not dict or set(value) != set(DEFAULT_CONFIG):
        raise ValueError("series config must contain exactly the versioned fields")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("series requires versioned SIMULATION configuration")
    for key, low, high in (("disk_limit_mb", 16, 16384), ("retain_completed_rounds", 1, 3),
                           ("history_limit", 3, 100)):
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"invalid series {key}")
    return {**deepcopy(value), "session_config": validate_session_config(value["session_config"])}


def engine_fingerprint():
    return digest({"session_engine": session_engine_fingerprint(),
                   "series_source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


def _round_config(config, index):
    if type(index) is not int or not 0 <= index < 2**32:
        raise ValueError("round index exhausted or invalid")
    value = deepcopy(config["session_config"])
    # Odd multiplier gives a uint32 bijection, without repeating a seed.
    value["seed"] = (value["seed"] + index * 2654435761) % 2**32
    return validate_session_config(value)


def _round_name(index):
    return f"round-{index:010d}"


def _control(root):
    value = read_json(root / "control.json")
    if type(value) is not dict or set(value) != {"paused"} or type(value["paused"]) is not bool:
        raise ValueError("invalid or missing series/child pause control")
    return value


@contextmanager
def _lock(path):
    if path.is_symlink():
        raise ValueError("lock path must not be a symlink")
    stream = path.open("a+b")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("another worker owns the series or child session") from None
        yield
    finally:
        stream.close()


def _save(root, state):
    write_record(root / "state.json", state)
    # The cursor is authoritative; this disposable navigation projection is
    # published afterwards and can always be regenerated on the next save.
    try:
        _publish_index(root, state)
    except OSError:
        pass


def _publish_index(root, state):
    current = state["current"]
    status_text = "等待开始"
    current_link = ""
    if current is not None:
        status_text = (f"第 {current['index'] + 1} 轮 · seed {current['seed']} · "
                       f"模拟分钟 {current['minute']} · {current['status']}")
        relative = current["directory"] + "/report.html"
        if (root / relative).is_file():
            current_link = f'<p><a class="current" href="{escape(relative, quote=True)}">打开当前轮报告 →</a></p>'
        else:
            current_link = "<p>当前轮报告将在第一个运行片段完成后生成。</p>"
    rows = []
    retained = [entry for entry in state["history"] if entry["detail_status"] == "RETAINED"][-3:]
    for entry in reversed(retained):
        relative = entry["directory"] + "/report.html"
        text = f"第 {entry['index'] + 1} 轮 · seed {entry['seed']}"
        link = f'<a href="{escape(relative, quote=True)}">{escape(text)}</a>' if (root / relative).is_file() else escape(text)
        rows.append(f"<li>{link} · 已完成</li>")
    history = "<ul>" + "".join(rows) + "</ul>" if rows else "<p>尚无已完成轮次。</p>"
    body = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NXTektal 连续模拟入口</title><style>
body{{font:16px/1.65 system-ui,sans-serif;max-width:900px;margin:48px auto;padding:0 24px;color:#183c32;background:#f5f8f5}}
main{{background:white;border:1px solid #dae5dd;border-radius:16px;padding:28px}}a{{color:#126b50}}h1{{line-height:1.25}}
.tag{{font-size:13px;letter-spacing:.08em;color:#647c6c}}.current{{font-weight:700;font-size:20px}}
</style><main><div class="tag">SIMULATION · 合成场景 · 非模型训练</div>
<h1>NXTektal 连续模拟</h1><p>{escape(status_text)}</p>{current_link}
<p>系列状态：{escape(state['status'])}；累计完成 {state['completed_rounds']} 轮。</p>
<h2>最近保留的完整报告</h2>{history}
<p>每轮内部跨日持续运行，库存、电量与未完成工作延续；不同轮次使用新种子并重置这些状态，属于独立实验。</p>
<p>后台每次唤醒推进一个有限片段。此页面是上次保存的导航快照，暂停与运行状态以状态文件为准。
只保留最近最多三轮完整证据；更早轮次按校验后的保留策略精简为摘要，不代表实际球场表现或模型训练。</p></main></html>"""
    pending = root / ".index.pending.html"
    pending.write_text(body, encoding="utf-8")
    pending.replace(root / "index.html")


def _hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _files(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("child detail path is missing or is a symlink")
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("symlinks are forbidden in session evidence")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = {"size": path.stat().st_size, "sha256": _hash_file(path)}
    return files


def _disk_bytes(root):
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("symlinks are forbidden in series evidence")
        if path.is_file():
            total += path.stat().st_size
    return total


def _validate_manifest(entry, config):
    fields = {"index", "seed", "directory", "config_digest", "completion_status", "child_state_digest",
              "report_digest", "tree_digest", "summary", "summary_digest", "detail_status"}
    if type(entry) is not dict or set(entry) not in (fields, fields | {"files"}):
        raise ValueError("invalid completed-round manifest fields")
    expected = _round_config(config, entry["index"])
    if (entry["seed"] != expected["seed"] or entry["directory"] != _round_name(entry["index"])
            or entry["config_digest"] != digest(expected)):
        raise ValueError("round identity/configuration binding changed")
    if entry["detail_status"] not in {"RETAINED", "RETIRING", "COMPACTED"}:
        raise ValueError("invalid completed-round detail status")
    if entry["detail_status"] == "COMPACTED" and "files" in entry:
        raise ValueError("compacted manifest must retain summaries only")
    if entry["summary_digest"] != digest(entry["summary"]) or entry["completion_status"] != "SESSION_COMPLETE":
        raise ValueError("completed round summary identity changed")
    if entry["detail_status"] != "COMPACTED":
        if type(entry.get("files")) is not dict or entry["tree_digest"] != digest(entry["files"]):
            raise ValueError("completed-round detail manifest changed")
        for name, metadata in entry["files"].items():
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or ".." in path.parts or path.as_posix() != name
                    or "\\" in name or "\x00" in name or type(metadata) is not dict
                    or set(metadata) != {"size", "sha256"} or type(metadata["size"]) is not int
                    or metadata["size"] < 0 or type(metadata["sha256"]) is not str or len(metadata["sha256"]) != 64):
                raise ValueError("unsafe detail file manifest")


def _validate_state(state, config):
    keys = {"schema", "environment", "config_digest", "engine", "status", "current", "history",
            "completed_rounds", "archived_rounds", "history_anchor", "disk_bytes"}
    if type(state) is not dict or set(state) != keys or state["schema"] != SCHEMA or state["environment"] != "SIMULATION":
        raise ValueError("invalid series state schema")
    if state["config_digest"] != digest(config):
        raise ValueError("series configuration identity changed")
    for key in ("completed_rounds", "archived_rounds", "disk_bytes"):
        if type(state[key]) is not int or state[key] < 0:
            raise ValueError("invalid series counters")
    if type(state["history"]) is not list or len(state["history"]) > config["history_limit"] + 1:
        raise ValueError("invalid bounded series history")
    if state["completed_rounds"] != state["archived_rounds"] + len(state["history"]):
        raise ValueError("series history counters disagree")
    expected_indices = list(range(state["archived_rounds"], state["completed_rounds"]))
    if [entry["index"] for entry in state["history"]] != expected_indices:
        raise ValueError("series history is not a contiguous completion sequence")
    for entry in state["history"]:
        _validate_manifest(entry, config)
    current = state["current"]
    if current is not None:
        expected = _round_config(config, current["index"])
        if (set(current) != {"index", "seed", "directory", "config_digest", "status", "minute"}
                or current["seed"] != expected["seed"] or current["directory"] != _round_name(current["index"])
                or current["config_digest"] != digest(expected) or current["status"] not in CHILD_STATUSES
                or type(current["minute"]) not in (int, float) or not 0 <= current["minute"] <= 10080):
            raise ValueError("invalid current round identity")
        correct_index = state["completed_rounds"] - (current["status"] == "SESSION_COMPLETE")
        if current["index"] != correct_index:
            raise ValueError("current round disagrees with completion history")
    elif state["completed_rounds"]:
        raise ValueError("completed history requires a current round identity")
    return state


def _load(root):
    config = validate_config(read_json(root / "config.json"))
    state = _validate_state(read_record(root / "state.json"), config)
    return config, state


def _child_state(directory, config):
    if directory.is_symlink():
        raise ValueError("child session directory must not be a symlink")
    stored = validate_session_config(read_json(directory / "config.json"))
    state = read_record(directory / "state.json")
    if (stored != config or state.get("config_digest") != digest(config)
            or state.get("engine") != session_engine_fingerprint() or state.get("status") not in CHILD_STATUSES):
        raise ValueError("child session identity, engine or status mismatch")
    _control(directory)
    return state


def _finish(root, state, config, child_state):
    current = state["current"]
    directory = root / current["directory"]
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("completed child directory missing or unsafe")
    with _lock(directory / ".session.lock"):
        verified = _child_state(directory, _round_config(config, current["index"]))
        if verified != child_state or verified["status"] != "SESSION_COMPLETE":
            raise ValueError("child completion changed before parent publication")
        report = read_record(directory / "report.json")
        if report.get("status") != "SESSION_COMPLETE" or report.get("environment") != "SIMULATION":
            raise ValueError("completed child report is missing or incomplete")
        compiled = read_record(directory / "compiled.json")
        if child_state.get("compiled_digest") != digest(compiled):
            raise ValueError("completed child compiled inputs changed")
        summary = {"runtime": deepcopy(report.get("summary", {})),
                   "actual_shot_count": report.get("actual_shot_count"),
                   "vision_evaluation": deepcopy(report.get("vision_evaluation", {})),
                   "checkpoint_coverage": deepcopy(report.get("coverage", [])),
                   "round_reset": "Independent seed/experiment; no inventory, battery or work carries between rounds"}
        files = _files(directory)
        entry = {"index": current["index"], "seed": current["seed"], "directory": current["directory"],
                 "config_digest": current["config_digest"], "completion_status": "SESSION_COMPLETE",
                 "child_state_digest": digest(child_state), "report_digest": digest(report),
                 "tree_digest": digest(files), "summary": summary, "summary_digest": digest(summary),
                 "detail_status": "RETAINED", "files": files}
        _validate_manifest(entry, config)
        state["history"].append(entry)
        state["completed_rounds"] += 1
        current["status"] = "SESSION_COMPLETE"
        current["minute"] = child_state["minute"]
        state["status"] = "ROUND_COMPLETE"
        _save(root, state)


def _retire(root, state, config, entry):
    """Persist deletion approval before touching verified, completed details.

    A crash may leave a subset of files. The RETIRING manifest authorizes only
    those previously hashed paths; changed/new files fail, never disappear.
    """
    _validate_manifest(entry, config)
    directory = root / entry["directory"]
    if directory.is_symlink():
        raise ValueError("retirement directory must not be a symlink")
    if not directory.exists():
        if entry["detail_status"] != "RETIRING":
            raise ValueError("retained completed details disappeared without retirement intent")
    else:
        with _lock(directory / ".session.lock"):
            current_files = _files(directory)
            if entry["detail_status"] == "RETAINED":
                child = read_record(directory / "state.json")
                report = read_record(directory / "report.json")
                if (child.get("status") != "SESSION_COMPLETE" or digest(child) != entry["child_state_digest"]
                        or digest(report) != entry["report_digest"] or current_files != entry["files"]):
                    raise ValueError("completed detail evidence changed; refusing deletion")
                entry["detail_status"] = "RETIRING"
                _save(root, state)
            if any(entry["files"].get(name) != metadata for name, metadata in current_files.items()):
                raise ValueError("retiring evidence contains changed or unapproved files")
            shutil.rmtree(directory)
    entry["detail_status"] = "COMPACTED"
    entry.pop("files")
    _save(root, state)


def _retain(root, state, config):
    for entry in state["history"]:
        if entry["detail_status"] == "RETIRING":
            _retire(root, state, config, entry)
    kept = [entry for entry in state["history"] if entry["detail_status"] == "RETAINED"]
    while len(kept) > config["retain_completed_rounds"]:
        _retire(root, state, config, kept.pop(0))
    # The configured retention is a maximum, not permission to exceed disk.
    # Under pressure, older completed rounds can be compacted earlier.
    while _disk_bytes(root) > config["disk_limit_mb"] * 1024**2:
        candidate = next((entry for entry in kept if entry["index"] != state["current"]["index"]), None)
        if candidate is None:
            break
        _retire(root, state, config, candidate)
        kept.remove(candidate)
    while len(state["history"]) > config["history_limit"]:
        old = state["history"][0]
        if old["detail_status"] != "COMPACTED":
            raise ValueError("cannot archive history while detailed evidence remains")
        state["history_anchor"] = digest({"previous": state["history_anchor"], "completed_round": old})
        state["history"].pop(0)
        state["archived_rounds"] += 1
    state["disk_bytes"] = _disk_bytes(root)
    _save(root, state)


def run(root, config=None):
    """Advance at most one child chunk; completing a round ends this call."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError("series root must not be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    with _lock(root / ".series.lock"):
        if (root / "state.json").exists():
            stored, state = _load(root)
            if config is not None and validate_config(config) != stored:
                raise ValueError("existing series configuration is immutable")
            config = stored
        else:
            if any(path.name != ".series.lock" for path in root.iterdir()):
                raise ValueError("new series requires an empty directory; partial evidence is not a fresh start")
            config = validate_config(DEFAULT_CONFIG if config is None else config)
            atomic_json(root / "config.json", config)
            atomic_json(root / "control.json", {"paused": False})
            state = {"schema": SCHEMA, "environment": "SIMULATION", "config_digest": digest(config),
                     "engine": engine_fingerprint(), "status": "INITIALIZED", "current": None,
                     "history": [], "completed_rounds": 0, "archived_rounds": 0,
                     "history_anchor": digest({"series": SCHEMA, "config": digest(config)}), "disk_bytes": 0}
            _save(root, state)
        if _control(root)["paused"]:
            return {**state, "status": "PAUSED"}
        if state["engine"] != engine_fingerprint():
            raise ValueError("series code/dependencies changed; preserve evidence and create a new series")
        current = state["current"]
        if current is not None:
            directory = root / current["directory"]
            if (directory / "state.json").exists():
                child = _child_state(directory, _round_config(config, current["index"]))
                if _control(directory)["paused"]:
                    return {**state, "status": "CHILD_PAUSED"}
                if child["status"] == "SESSION_COMPLETE" and current["status"] != "SESSION_COMPLETE":
                    # Child cursor was published before a parent crash. Record
                    # it exactly once and do not start another round this call.
                    _finish(root, state, config, child)
                    if _control(root)["paused"]:
                        return {**state, "status": "PAUSED"}
                    _retain(root, state, config)
                    return state
                if current["status"] == "SESSION_COMPLETE" and child["status"] != "SESSION_COMPLETE":
                    raise ValueError("completed child cursor regressed")
            elif current["status"] != "INITIALIZED":
                raise ValueError("current child evidence is missing")
        _retain(root, state, config)
        if state["disk_bytes"] >= config["disk_limit_mb"] * 1024**2:
            state["status"] = "DISK_LIMIT"
            _save(root, state)
            return state
        if current is None or current["status"] == "SESSION_COMPLETE":
            index = state["completed_rounds"]
            child_config = _round_config(config, index)
            state["current"] = {"index": index, "seed": child_config["seed"], "directory": _round_name(index),
                                "config_digest": digest(child_config), "status": "INITIALIZED", "minute": 480}
            current = state["current"]
            state["status"] = "ROUND_INITIALIZED"
            _save(root, state)
        child_config = _round_config(config, current["index"])
        # Re-read root pause immediately before handing control to the child.
        if _control(root)["paused"]:
            return {**state, "status": "PAUSED"}
        directory = root / current["directory"]
        result = run_session(directory, child_config)
        child = _child_state(directory, child_config)
        if result.get("status") == "PAUSED" and _control(directory)["paused"]:
            return {**state, "status": "CHILD_PAUSED"}
        if result != child:
            raise ValueError("child return value disagrees with durable child state")
        if child["status"] == "SESSION_COMPLETE":
            _finish(root, state, config, child)
        else:
            current["status"] = child["status"]
            current["minute"] = child["minute"]
            state["status"] = "CHILD_" + child["status"]
            _save(root, state)
        if _control(root)["paused"]:
            return {**state, "status": "PAUSED"}
        _retain(root, state, config)
        if state["disk_bytes"] >= config["disk_limit_mb"] * 1024**2:
            state["status"] = "DISK_LIMIT"
            _save(root, state)
        if _control(root)["paused"]:
            return {**state, "status": "PAUSED"}
        return state


def status(root):
    root = Path(root)
    _, state = _load(root)
    control = _control(root)
    result = {**state, "control": control}
    if control["paused"]:
        result["status"] = "PAUSED"
    current = state["current"]
    if current is not None and (root / current["directory"] / "control.json").exists():
        result["child_control"] = _control(root / current["directory"])
        if result["child_control"]["paused"] and not control["paused"]:
            result["status"] = "CHILD_PAUSED"
    return result


def set_paused(root, paused):
    if type(paused) is not bool:
        raise ValueError("paused must be a boolean")
    root = Path(root)
    _load(root)
    _control(root)  # Missing/tampered controls never silently become defaults.
    atomic_json(root / "control.json", {"paused": paused})
    return {"paused": paused}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "pause", "resume"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.state_dir, read_json(args.config) if args.config else None)
    elif args.command == "status":
        result = status(args.state_dir)
    else:
        result = set_paused(args.state_dir, args.command == "pause")
    # Do not flood scheduler logs with detail hash inventories or full history.
    if "history" in result:
        result = {key: value for key, value in result.items() if key != "history"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
