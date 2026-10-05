"""Read-only, bounded projection of verified saved course-session evidence.

No simulator/policy/runtime imports, writes, continuation, or command surface.
The report's hidden scenario/evaluation fields are deliberately never projected.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import zlib

from nxt_telemetry.course_condition import CourseConditionObservation

SCHEMA = "nxt-course-ops/v1"
SESSION_SCHEMA = "nxt-whole-course-session/v2"
SERIES_SCHEMA = "nxt-course-session-series/v1"
OBSERVED_RANGE_SCHEMA = "nxt-course-observed-range/v1"
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 4 * 1024 * 1024
CATALOG_DIGEST = "ed58bcd29c05cbd91f3ecb7322bf623ff26b0ba6d715d1f49dc35275a615395a"
MAP_REVISION = "sha256:058b2d16ad60dae347b5fb1f39d39e3880f5017313ce348df2dab3084ec5837f"
ROUND = re.compile(r"round-\d{10}\Z")
FRAME = re.compile(r"frame-\d{6}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
QUALITIES = {"USABLE", "BLURRED", "OCCLUDED"}


class CourseOpsError(ValueError):
    def __init__(self, detail="Saved course evidence is unavailable or inconsistent.", *, code="course_ops_unavailable"):
        self.code = code
        super().__init__(detail)


def _require(condition):
    if not condition:
        raise CourseOpsError()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value)
        value[key] = item
    return value


def _json(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CourseOpsError()))
        _canonical(value)  # Reject overflowing exponents as well as explicit NaN.
        return value
    except (ValueError, UnicodeError, RecursionError, TypeError, OverflowError):
        raise CourseOpsError() from None


def _record(raw):
    value = _json(raw)
    _require(type(value) is dict and set(value) == {"payload", "sha256"})
    _require(type(value["payload"]) is dict and value["sha256"] == _digest(value["payload"]))
    return value["payload"]


def _obj(value):
    _require(type(value) is dict)
    return value


def _rows(value, maximum=20000):
    _require(type(value) is list and len(value) <= maximum)
    return [_obj(row) for row in value]


def _text(value):
    _require(type(value) is str and 0 < len(value) <= 1000)
    return value


def _number(value, low=0, high=10**12):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high)
    return value


def _nullable_number(value):
    return None if value is None else _number(value)


def _boolean(value):
    _require(type(value) is bool)
    return value


def _time(value):
    _text(value)
    _require(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z", value) is not None)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CourseOpsError() from None
    return value


def _nullable_text(value):
    return None if value is None else _text(value)


def _unknown_range():
    return {"status": "UNKNOWN", "observed_minute": None, "inventory_fraction": None,
            "zones": [], "robots": [], "staff": None}


def _range(report, minute):
    if "observed_range" not in report:
        return _unknown_range()
    source = _obj(report["observed_range"])
    _require(source["schema"] == OBSERVED_RANGE_SCHEMA and source["minute"] == minute)
    staff = source["staff"]
    if staff is not None:
        staff = {key: _number(_obj(staff)[key], high=10000) for key in ("capacity", "busy", "queued")}
        _require(all(type(value) is int for value in staff.values()) and staff["busy"] <= staff["capacity"])
    robots = []
    for row in _rows(source["robots"], 100):
        robots.append({**{key: _text(row[key]) for key in ("robot_id", "activity", "health", "location")},
                       "battery_fraction": _number(row["battery_fraction"], high=1),
                       "payload_balls": _number(row["payload_balls"]), "awaiting_human": _boolean(row["awaiting_human"])})
    zones = [{"zone_id": _text(row["zone_id"]), "balls": _nullable_number(row["balls"]),
              "is_open": None if row["is_open"] is None else _boolean(row["is_open"])} for row in _rows(source["zones"], 100)]
    inventory = source["inventory_fraction"]
    return {"status": "OBSERVED", "observed_minute": minute,
            "inventory_fraction": None if inventory is None else _number(inventory, high=2),
            "zones": zones, "robots": robots, "staff": staff}


class CourseOpsReader:
    """The caller supplies the sole allowed series root; clients cannot select paths."""
    def __init__(self, series_root, *, now=None):
        self.root = Path(series_root).absolute()
        self.now = now or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"))

    def _read(self, relative, limit=MAX_JSON_BYTES):
        parts = Path(relative).parts
        _require(parts and not Path(relative).is_absolute() and all(p not in (".", "..") for p in parts))
        descriptors = []
        try:
            # Walk each component by descriptor: symlinks and directory swaps
            # cannot turn a declared relative path into arbitrary-file access.
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            descriptors.append(fd)
            for component in parts[:-1]:
                fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            descriptors.append(file_fd)
            info = os.fstat(file_fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_size <= limit)
            chunks, total = [], 0
            while True:
                chunk = os.read(file_fd, min(65536, limit + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk); total += len(chunk)
                _require(total <= limit)
            after = os.fstat(file_fd)
            _require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                     (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
            return b"".join(chunks)
        except OSError:
            raise CourseOpsError() from None
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    def _stable(self, reads):
        for relative, content in reads.items():
            _require(self._read(relative) == content)

    def _load(self):
        reads = {}
        def load(relative, record=False):
            content = self._read(relative)
            reads[relative] = content
            return _record(content) if record else _json(content)
        try:
            parent = load("state.json", True)
            config = load("config.json")
            control = load("control.json")
            _require(parent["schema"] == SERIES_SCHEMA and parent["environment"] == "SIMULATION")
            _require(config["schema"] == SERIES_SCHEMA and config["environment"] == "SIMULATION")
            _require(parent["config_digest"] == _digest(config))
            _require(type(parent["engine"]) is str and HASH.fullmatch(parent["engine"]))
            _require(set(control) == {"paused"}); _boolean(control["paused"])
            current = _obj(parent["current"])
            index = current["index"]
            _require(type(index) is int and 0 <= index < 2**32)
            _require(type(parent["completed_rounds"]) is int and parent["completed_rounds"] >= 0)
            _require(index == parent["completed_rounds"] - (current["status"] == "SESSION_COMPLETE"))
            round_id = current["directory"]
            _require(type(round_id) is str and ROUND.fullmatch(round_id) and round_id == f"round-{index:010d}")
            child = load(round_id + "/state.json", True)
            child_config = load(round_id + "/config.json")
            child_control = load(round_id + "/control.json")
            report = load(round_id + "/report.json", True)
            _require(set(child_control) == {"paused"}); _boolean(child_control["paused"])
            _require(child["schema"] == report["schema"] == child_config["schema"] == SESSION_SCHEMA)
            _require(type(child["engine"]) is str and HASH.fullmatch(child["engine"]))
            _require(report["environment"] == child_config["environment"] == "SIMULATION")
            expected_config = dict(config["session_config"])
            expected_config["seed"] = (expected_config["seed"] + index * 2654435761) % 2**32
            _require(child_config == expected_config == report["config"])
            _require(current["seed"] == child_config["seed"])
            _require(child["config_digest"] == current["config_digest"] == _digest(child_config))
            minute = _number(child["minute"], high=7 * 1440)
            _require(report["summary"]["minute"] == minute == current["minute"])
            _require(report["status"] == child["status"] == current["status"])
            _require(child["status"] in {"INITIALIZED", "CHUNK_COMPLETE", "TIME_BUDGET", "DISK_LIMIT", "PAUSED", "SESSION_COMPLETE"})
            self._stable(reads)
            return parent, control, child_control, report, reads
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise CourseOpsError() from None

    def snapshot(self):
        try:
            parent, control, child_control, report, reads = self._load()
            current = parent["current"]
            round_id = current["directory"]
            minute = report["summary"]["minute"]
            catalog = _rows(report["catalog"], 54)
            _require(len(catalog) == 54)
            # This endpoint's identity is the existing fixed synthetic map;
            # verify its declared points instead of silently accepting a drift.
            _require(_digest([{key: row[key] for key in ("id", "hole_number", "feature_id", "surface_type", "x_m", "y_m")}
                              for row in sorted(catalog, key=lambda row: (row["hole_number"], ("fairway", "bunker", "green").index(row["surface_type"])))]) == CATALOG_DIGEST)
            checkpoints, ids = [], set()
            for row in catalog:
                hole = row["hole_number"]
                _require(type(hole) is int and 1 <= hole <= 18)
                surface = row["surface_type"]
                _require(surface in ("fairway", "bunker", "green"))
                cp = row["id"]
                _require(cp == f"cp-h{hole:02d}-{surface}" and cp not in ids)
                _require(row["feature_id"] == f"hole-{hole:02d}-{surface}")
                ids.add(cp)
                checkpoints.append({"checkpoint_id": cp, "hole_number": hole, "feature_id": row["feature_id"],
                    "surface_type": surface, "x_m": _number(row["x_m"], -10000, 10000),
                    "y_m": _number(row["y_m"], -10000, 10000), "coverage_status": "UNOBSERVED", "last_observed_minute": None})
            frames, frame_ids, latest, carts = [], set(), {}, {}
            raw_frames = {}
            width = _number(report["config"]["image_width"], 1, 1024)
            height = _number(report["config"]["image_height"], 1, 768)
            for row in _rows(report["frames"], 10000):
                frame_id = row["frame_id"]
                _require(type(frame_id) is str and FRAME.fullmatch(frame_id) and frame_id not in frame_ids)
                frame_ids.add(frame_id)
                raw_frames[frame_id] = row
                _require(row["checkpoint_id"] in ids and row["image"] == f"media/{frame_id}.png")
                _require(type(row["image_sha256"]) is str and HASH.fullmatch(row["image_sha256"]))
                captured = _number(row["minute"], high=minute)
                cart_id = row["cart_id"]
                _require(cart_id in {f"CART-{n:02d}" for n in range(1, 17)})
                position = _obj(row["cart_position"])
                cart = {"cart_id": cart_id, "frame_id": frame_id, "minute": captured,
                        "x_m": _number(position["x_m"], -10000, 10000), "y_m": _number(position["y_m"], -10000, 10000),
                        "accuracy_m": _number(position["accuracy_m"], high=1000)}
                if cart_id not in carts or captured >= carts[cart_id]["minute"]: carts[cart_id] = cart
                detection = _obj(row["detection"])
                _require(detection["quality"] in QUALITIES and detection["score_calibration"] == "NOT_CALIBRATED")
                candidates = []
                for item in _rows(detection["detections"], 100):
                    bbox = item["bbox"]
                    _require(type(bbox) is list and len(bbox) == 4)
                    x1, y1, x2, y2 = [_number(v, high=2048) for v in bbox]
                    _require(x1 < x2 <= width and y1 < y2 <= height)
                    candidates.append({"condition": _text(item["condition"]), "score": _number(item["score"], high=1), "bbox": bbox})
                frame = {"frame_id": frame_id, "cart_id": cart_id, "checkpoint_id": row["checkpoint_id"], "minute": captured,
                         "image_url": f"/api/v1/course-ops/media/{round_id}/{frame_id}.png?sha256={row['image_sha256']}", "image_sha256": row["image_sha256"],
                         "width": width, "height": height, "quality": detection["quality"], "score_calibration": "NOT_CALIBRATED",
                         "detections": candidates}
                frames.append(frame)
                cp = row["checkpoint_id"]
                if cp not in latest or captured >= latest[cp]["minute"]: latest[cp] = frame
            for cp in checkpoints:
                frame = latest.get(cp["checkpoint_id"])
                if frame:
                    cp.update(last_observed_minute=frame["minute"],
                              coverage_status="STALE" if minute - frame["minute"] > 90 else frame["quality"])
            observations = []
            observation_ids = set()
            checkpoint_by_id = {row["checkpoint_id"]: row for row in checkpoints}
            start = datetime.strptime(report["config"]["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            map_revision = None
            for row in _rows(report["observations"]):
                CourseConditionObservation.from_dict(row)
                _require(row["observation_id"] not in observation_ids)
                observation_ids.add(row["observation_id"])
                _require(row["site_id"] == "synthetic-course-18" and row["deployment_id"] == "synthetic-course-18-monitoring-v0")
                _require(row["map_revision"] == MAP_REVISION and row["environment"] == "SIMULATION")
                _require(row["checkpoint_id"] in ids and row["frame_id"] in frame_ids)
                _require(row["score_calibration"] == "NOT_CALIBRATED")
                frame = raw_frames[row["frame_id"]]
                cp = checkpoint_by_id[row["checkpoint_id"]]
                expected_time = (start + timedelta(minutes=frame["minute"])).isoformat(timespec="seconds").replace("+00:00", "Z")
                _require(row["checkpoint_id"] == frame["checkpoint_id"] and row["cart_id"] == frame["cart_id"])
                _require(row["hole_number"] == cp["hole_number"] and row["feature_id"] == cp["feature_id"])
                _require(row["camera_id"] == frame["cart_id"] + "-camera" and row["captured_at_utc"] == expected_time)
                _require(row["evidence_ref"] == "synthetic:" + frame["image_sha256"])
                _require(row["cart_position"] == frame["cart_position"] and row["quality"] == frame["detection"]["quality"])
                map_revision = row["map_revision"]
                observations.append({**{key: _text(row[key]) for key in ("observation_id", "checkpoint_id", "frame_id", "condition", "quality")},
                    "captured_at_utc": _time(row["captured_at_utc"]), "detection_score": None if row["detection_score"] is None else _number(row["detection_score"], high=1)})
            simulated_time = start + timedelta(minutes=minute)
            def past(value):
                _time(value)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                _require(start <= parsed <= simulated_time)
                return parsed
            observations_by_id = {row["observation_id"]: row for row in observations}
            cases = []
            for row in _rows(report["cases"]):
                _require(row["checkpoint_id"] in ids)
                _require(past(row["opened_at_utc"]) <= past(row["updated_at_utc"]))
                _require(type(row["observation_ids"]) is list and len(row["observation_ids"]) <= 20000)
                _require(all(key in observations_by_id and observations_by_id[key]["checkpoint_id"] == row["checkpoint_id"]
                             for key in row["observation_ids"]))
                cases.append({**{key: _text(row[key]) for key in ("case_id", "checkpoint_id", "condition_kind", "status", "priority")},
                              "opened_at_utc": _time(row["opened_at_utc"]), "updated_at_utc": _time(row["updated_at_utc"]),
                              "latest_task_id": _nullable_text(row["latest_task_id"])})
            case_ids = {row["case_id"] for row in cases}
            _require(len(case_ids) == len(cases))
            tasks = []
            for row in _rows(report["tasks"]):
                _require(row["case_id"] in case_ids)
                assigned = past(row["assigned_at_utc"])
                started = past(row["started_at_utc"]) if row["started_at_utc"] is not None else None
                completed = past(row["completed_at_utc"]) if row["completed_at_utc"] is not None else None
                _require(started is None or assigned <= started)
                _require(completed is None or (started is not None and started <= completed))
                tasks.append({**{key: _text(row[key]) for key in ("task_id", "case_id", "task_kind", "status")},
                    "resource_id": _text(row["resource"]["id"]), "resource_kind": _text(row["resource"]["kind"]),
                    **{key: None if row[key] is None else _time(row[key]) for key in ("assigned_at_utc", "started_at_utc", "completed_at_utc")}})
            tasks_by_id = {row["task_id"]: row for row in tasks}
            _require(len(tasks_by_id) == len(tasks))
            _require(all(row["latest_task_id"] is None or (row["latest_task_id"] in tasks_by_id
                         and tasks_by_id[row["latest_task_id"]]["case_id"] == row["case_id"]) for row in cases))
            jobs = []
            for row in _rows(report["staff_jobs"]):
                _require(row["checkpoint_id"] in ids)
                available = _number(row["available_minute"], high=minute)
                _number(row["captured_minute"], high=minute)
                _require(row["observation_id"] in observation_ids or row["observation_id"] in frame_ids
                         or (row["observation_id"].endswith(":debris") and row["observation_id"][:-7] in frame_ids))
                linked = observations_by_id.get(row["observation_id"])
                linked_frame = raw_frames[linked["frame_id"] if linked else row["observation_id"].removesuffix(":debris")]
                _require(row["checkpoint_id"] == linked_frame["checkpoint_id"])
                _require(row["evidence_ref"] == "synthetic:" + linked_frame["image_sha256"])
                for key in ("assigned_at_s", "started_at_s", "completed_at_s"):
                    if row[key] is not None: _number(row[key], low=available * 60, high=minute * 60)
                _require(row["completed_at_s"] is None or (row["started_at_s"] is not None and row["started_at_s"] <= row["completed_at_s"]))
                jobs.append({**{key: _text(row[key]) for key in ("job_id", "checkpoint_id", "task_kind", "status")},
                             "started_at_s": _nullable_number(row["started_at_s"]), "completed_at_s": _nullable_number(row["completed_at_s"])})
            simulated_at = (datetime.strptime(report["config"]["start_date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                            + timedelta(minutes=minute)).isoformat(timespec="seconds").replace("+00:00", "Z")
            child = _record(reads[round_id + "/state.json"])
            result = {"schema": SCHEMA, "environment": "SIMULATION", "generated_at_utc": _time(self.now()),
                "source": {"kind": "SAVED_SIMULATION_REPORT",
                    "series_id": _digest({"config_digest": parent["config_digest"], "engine": parent["engine"]}),
                    "session_id": _digest({"config_digest": child["config_digest"], "engine": child["engine"]}),
                    "engine_digest": child["engine"], "round_id": round_id, "index": current["index"], "seed": current["seed"],
                    "status": _text(parent["status"]), "parent_paused": control["paused"], "child_paused": child_control["paused"],
                    "published_at_utc": None, "report_sha256": _digest(report), "live": False},
                "clock": {"simulated_minute": minute, "simulated_at_utc": simulated_at},
                "course": {"site_id": "synthetic-course-18", "deployment_id": "synthetic-course-18-monitoring-v0",
                    "map_revision": map_revision, "geometry": None, "hole_count": 18, "cart_count": 16, "checkpoints": checkpoints},
                "carts": sorted(carts.values(), key=lambda row: row["cart_id"]), "frames": frames,
                "observations": observations, "cases": cases, "tasks": tasks, "staff_jobs": jobs,
                "range": _range(report, minute), "weather": {"status": "UNKNOWN"}}
            self._stable(reads)
            return result
        except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
            raise CourseOpsError() from None

    def media(self, round_id, frame_id, expected_sha=None):
        if not (type(round_id) is str and ROUND.fullmatch(round_id) and type(frame_id) is str and FRAME.fullmatch(frame_id)):
            raise CourseOpsError("Saved image not found.", code="course_ops_not_found")
        parent, _, _, report, reads = self._load()
        if round_id != parent["current"]["directory"]:
            raise CourseOpsError("Saved image belongs to a different round.", code="course_ops_not_found")
        rows = [row for row in _rows(report["frames"], 10000) if row.get("frame_id") == frame_id]
        if len(rows) != 1:
            raise CourseOpsError("Saved image not found.", code="course_ops_not_found")
        row = rows[0]
        _number(row.get("minute"), high=report["summary"]["minute"])
        if expected_sha is not None and (type(expected_sha) is not str or not HASH.fullmatch(expected_sha) or expected_sha != row.get("image_sha256")):
            raise CourseOpsError("Saved image identity no longer matches.", code="course_ops_not_found")
        _require(row.get("image") == f"media/{frame_id}.png")
        content = self._read(f"{round_id}/media/{frame_id}.png", MAX_IMAGE_BYTES)
        _require(hashlib.sha256(content).hexdigest() == row.get("image_sha256"))
        _require(content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 33)
        offset, saw_data, saw_end = 8, False, False
        while offset + 12 <= len(content):
            size = struct.unpack(">I", content[offset:offset + 4])[0]
            kind = content[offset + 4:offset + 8]
            end = offset + size + 12
            _require(end <= len(content))
            _require(zlib.crc32(content[offset + 4:end - 4]) == struct.unpack(">I", content[end - 4:end])[0])
            if offset == 8:
                _require(kind == b"IHDR" and size == 13)
                width, height = struct.unpack(">II", content[offset + 8:offset + 16])
                _require(0 < width <= 1024 and 0 < height <= 768 and width == report["config"]["image_width"] and height == report["config"]["image_height"])
            saw_data |= kind == b"IDAT"
            if kind == b"IEND":
                _require(size == 0 and end == len(content)); saw_end = True
            offset = end
        _require(saw_data and saw_end and offset == len(content))
        self._stable(reads)
        return content
