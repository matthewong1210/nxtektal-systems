"""Bounded continuation of one multi-day SIMULATION, with actual rendered RGB.

The runtime owns all ball/fleet/shared-staff execution. This composition root
owns immutable input compilation, image evidence, explicit simulated operator
workflow, prefix replay and publication. It never calls apply_directive.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import time

from nxt_range_ops.config.models import RangeOpsScenario
from nxt_range_ops.env.range_ops_env import RangeOpsEnv
from nxt_range_ops.policies.joint_dispatch import JointDispatchPolicy, candidate_catalog, policy_inputs
from nxt_pilot_ops.grounds import GroundsWorkflow
from nxt_telemetry.course_condition import build_observation
from scripts.course_18_hole_fixture import build_fixture
from scripts.course_camera import render_frame, detect_frame, project_detection, RENDERER_VERSION, DETECTOR_VERSION
from scripts.course_session_scenario import compile_session
from scripts.joint_learning import atomic_json, canonical, digest, read_json, read_record, write_record

SCHEMA = "nxt-whole-course-session/v2"
DEFAULT_CONFIG = {
    "schema": SCHEMA, "environment": "SIMULATION", "seed": 17092026,
    "days": 3, "staff_count": 3, "initial_stock": 4000, "demand_scale": 1.3,
    "advance_minutes": 720, "wall_time_seconds": 240, "disk_limit_mb": 512,
    "image_width": 384, "image_height": 240, "start_date": "2026-09-17",
    "assumptions": {},
}
OPERATOR = "SIMULATED-OPERATOR"
WORK = {"DIVOT": "REPAIR_DIVOT", "BUNKER_SURFACE": "RAKE_BUNKER",
        "STANDING_WATER": "INSPECT", "DEBRIS": "CLEAR_DEBRIS"}
REPAIRS = {"REPAIR_DIVOT", "RAKE_BUNKER", "CLEAR_DEBRIS"}
NATIVE = {"DIVOT", "BUNKER_SURFACE", "STANDING_WATER"}


def reinspection_target(frame, observations, kind):
    """Require the earlier detected region and its uncertainty to be visible.

    This is a synthetic baseline's minimum-scale gate, not proof of real-world
    absence. It uses recorded detections and reported calibration, never labels.
    An unobserved or too-small target remains awaiting independent inspection.
    """
    calibration = frame["calibration"]
    def pixel(x, y):
        delta = [x-calibration["position_m"][0], y-calibration["position_m"][1], -calibration["position_m"][2]]
        forward = sum(a*b for a,b in zip(delta, calibration["forward"]))
        if forward <= 0:
            return None
        horizontal = sum(a*b for a,b in zip(delta, calibration["right"]))
        vertical = sum(a*b for a,b in zip(delta, calibration["up"]))
        return (calibration["width"]/2 + calibration["focal_px"]*horizontal/forward,
                calibration["height"]/2 - calibration["focal_px"]*vertical/forward)
    positives = [row for row in observations if row["condition"] == kind and row.get("target_position")]
    if not positives or frame["detection"]["quality"] != "USABLE":
        return None
    for row in positives:
        target = row["target_position"]
        x, y = target["x_m"], target["y_m"]
        uncertainty = target["accuracy_m"] + frame["cart_position"]["accuracy_m"]
        bounds = [pixel(x+dx*uncertainty, y+dy*uncertainty) for dx,dy in ((-1,-1),(-1,1),(1,-1),(1,1))]
        if any(p is None or not (4 <= p[0] <= calibration["width"]-4 and 4 <= p[1] <= calibration["height"]-4) for p in bounds):
            return None
        # Published scenario minimum sizes, not hidden size of this defect.
        radius = {"DIVOT": .04, "BUNKER_SURFACE": .25, "STANDING_WATER": 1.0}[kind]
        points = [pixel(x+dx*radius, y+dy*radius) for dx,dy in ((-1,-1),(-1,1),(1,-1),(1,1))]
        if any(p is None for p in points):
            return None
        width = max(p[0] for p in points)-min(p[0] for p in points)
        height = max(p[1] for p in points)-min(p[1] for p in points)
        if width < 4 or height < 2 or width*height < 16:
            return None
    return {key: positives[-1]["target_position"][key] for key in ("x_m", "y_m")}


def validate_config(value):
    if type(value) is not dict or set(value) != set(DEFAULT_CONFIG):
        raise ValueError("config must contain exactly the documented fields")
    if value["schema"] != SCHEMA or value["environment"] != "SIMULATION":
        raise ValueError("only versioned SIMULATION config is accepted")
    if type(value["assumptions"]) is not dict:
        raise ValueError("assumptions must be an object; compiler validates its whitelist")
    limits = {"seed": (0, 2**32-1), "days": (1, 7), "staff_count": (1, 12),
              "initial_stock": (0, 8000), "advance_minutes": (1, 10080),
              "wall_time_seconds": (1, 1800), "disk_limit_mb": (16, 4096),
              "image_width": (64, 1024), "image_height": (48, 768)}
    for key, (low, high) in limits.items():
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise ValueError(f"invalid {key}")
    if type(value["demand_scale"]) not in (int, float) or not math.isfinite(value["demand_scale"]) or not 0 <= value["demand_scale"] <= 5:
        raise ValueError("invalid demand_scale")
    date = datetime.strptime(value["start_date"], "%Y-%m-%d")
    if date.strftime("%Y-%m-%d") != value["start_date"] or date < datetime(2026, 9, 16):
        raise ValueError("start_date precedes synthetic map or is not ISO date")
    return deepcopy(value)


def engine_fingerprint():
    root = Path(__file__).resolve().parents[1]
    paths = sorted([p for folder in root.glob("nxt_*") for p in folder.rglob("*.py")]
                   + [root / "scripts" / name for name in (
                       "course_session.py", "course_session_scenario.py", "course_camera.py",
                       "course_session_report.py", "course_18_hole_fixture.py", "joint_learning.py")])
    content = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    content["dependencies"] = {name: importlib.metadata.version(name) for name in ("numpy", "Pillow", "simpy", "gymnasium", "pydantic")}
    return digest(content)


def utc_at(start_date, minute):
    return (datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            + timedelta(minutes=minute)).isoformat(timespec="seconds").replace("+00:00", "Z")


def serialize_compiled(compiled):
    result = dict(compiled)
    result["scenario"] = compiled["scenario"].model_dump(mode="json")
    # Round-trip detaches the large immutable inputs and rejects non-JSON values.
    return json.loads(canonical(result))


def _event_start(event):
    return event.get("start_minute", event.get("from_minute", 0))


def scene_at(compiled, checkpoint, minute, completed_jobs):
    """Pure scene projection, with explicitly assumed maintenance response.

    Completed work can remove a modeled surface mark; inspection does not
    drain water. The system still needs a later usable image for verification.
    """
    rows = [row for row in compiled.get("surface_snapshots", [])
            if row["checkpoint_id"] == checkpoint["id"] and row["minute"] <= minute]
    surface = max(rows, key=lambda row: row["minute"]) if rows else {}
    defects = []
    for event in compiled["course_events"]:
        if event.get("checkpoint_id") != checkpoint["id"] or event.get("condition") not in WORK:
            continue
        start = _event_start(event)
        if not start <= minute < event.get("end_minute", event.get("until_minute", 10**9)):
            continue
        task_kind = WORK[event["condition"]]
        cleared = task_kind in REPAIRS and any(
            job["checkpoint_id"] == checkpoint["id"] and job.get("task_kind") == task_kind
            and job["status"] == "COMPLETED" and start <= job["completed_at_s"] / 60 <= minute
            for job in completed_jobs)
        if cleared:
            continue
        defects.append({"condition": event["condition"],
                        "x_m": event.get("x_m", checkpoint["x_m"]),
                        "y_m": event.get("y_m", checkpoint["y_m"]),
                        "radius_m": event.get("radius_m", 0.25)})
    return {"x_m": checkpoint["x_m"], "y_m": checkpoint["y_m"],
            "surface_type": checkpoint["surface_type"].upper(), "patch_radius_m": 12,
            "wetness": surface.get("wetness", surface.get("moisture_index", 0)),
            "sunlight": surface.get("sunlight", 0.8), "cloud_cover": surface.get("cloud_cover", 0),
            "defects": defects}, surface


class Session:
    """One live runtime; replay uses the same class and the same saved images."""

    def __init__(self, root, config, compiled):
        self.root, self.config, self.compiled = Path(root), config, compiled
        scenario = RangeOpsScenario.model_validate(compiled["scenario"])
        self.env = RangeOpsEnv(scenario, session_inputs=compiled["session_inputs"])
        self.obs, self.info = self.env.reset(seed=config["seed"])
        self.policy = JointDispatchPolicy(scenario, self.env.catalog, candidate_catalog()[1], seed=config["seed"])
        self.policy.reset()
        self.step_count = 0
        self.site, model = build_fixture()
        self.map_revision = model.content_digest
        self.catalog = {row["id"]: row for row in compiled["catalog"]}
        # Partition independent point histories; runtime remains the sole staff
        # capacity owner. Workflow records mirror actual admitted staff work.
        self.workflows = {key: GroundsWorkflow(self.site.site_id, self.site.deployment_id, self.map_revision)
                          for key in self.catalog}
        self.job_links, self.used_slots, self.job_stages = {}, set(), {}
        self.frames, self.observations, self.timeline, self.visual_scores = [], [], [], []
        self.alerts = []
        self.schedule = defaultdict(list)
        for index, row in enumerate(compiled["camera_schedule"]):
            self.schedule[int(row["minute"])].append((index, row))
        self._captured_minutes = set()
        self._event_cursor = 0
        self.actual_shots = []
        self._latest_frame = {}

    @property
    def minute(self):
        return self.env.sim.now / 60

    def _slot(self, cp, kind):
        return next((slot for slot in self.compiled["session_inputs"]["staff_job_slots"]
                     if slot["checkpoint_id"] == cp and slot["task_kind"] == kind
                     and slot["job_id"].startswith(f"d{int(self.minute // 1440)}-")
                     and slot["job_id"] not in self.used_slots), None)

    def _admit(self, cp, kind, observation_id, evidence_ref, workflow_case=None):
        # Multiple carts seeing one open point/type do not request extra workers.
        jobs = self.env.sim.staff_work_snapshots()
        if any(job["checkpoint_id"] == cp and job.get("task_kind") == kind
               and job["status"] in {"PENDING", "ASSIGNED", "IN_PROGRESS"} for job in jobs):
            return
        slot = self._slot(cp, kind)
        if slot is None:
            return
        deadline = min(int(self.env.sim.session_end_s / 60), int(self.minute) + 120)
        self.env.admit_observation_job(slot["job_id"], observation_id=observation_id,
                                      evidence_ref=evidence_ref, captured_minute=int(self.minute),
                                      deadline_minute=deadline)
        self.used_slots.add(slot["job_id"])
        if workflow_case is not None:
            self.job_links[slot["job_id"]] = {"checkpoint_id": cp, "case_id": workflow_case, "task_id": None}

    def _sync_jobs(self):
        for job in self.env.sim.staff_work_snapshots():
            link = self.job_links.get(job["job_id"])
            if not link or self.job_stages.get(job["job_id"]) == job["status"]:
                continue
            workflow = self.workflows[link["checkpoint_id"]]
            if job["started_at_s"] is not None and link["task_id"] is None:
                at = utc_at(self.config["start_date"], job["started_at_s"] / 60)
                receipt = workflow.assign(link["case_id"],
                    resource={"id": "runtime-job-" + job["job_id"], "kind": "HUMAN", "capabilities": [job["task_kind"]]},
                    task_kind=job["task_kind"], operator=OPERATOR,
                    reason="Mirror actual shared-pool simulation admission; identity is a work slot, not a person", at=at)
                # Resolve from the owner rather than depending on receipt layout.
                tasks = workflow.snapshot()["tasks"]
                task = next(row for row in tasks if row["case_id"] == link["case_id"] and row["status"] == "ASSIGNED")
                link["task_id"] = task["task_id"]
                workflow.start(task["task_id"], at=at)
            if job["status"] == "COMPLETED" and link["task_id"]:
                workflow.complete(link["task_id"], operator=OPERATOR,
                                  at=utc_at(self.config["start_date"], job["completed_at_s"] / 60))
            self.job_stages[job["job_id"]] = job["status"]

    def _image(self, index, row, cp):
        frame_id = f"frame-{index:06d}"
        path = self.root / "frames" / (frame_id + ".json")
        media = self.root / "media" / (frame_id + ".png")
        scene, surface = scene_at(self.compiled, cp, row["minute"], self.env.sim.staff_work_snapshots())
        camera = row["camera"]
        source_digest = digest({"scene": scene, "camera": camera, "index": index})
        if path.exists():
            frame = read_record(path)
            if frame["source_digest"] != source_digest or frame["image_sha256"] != hashlib.sha256(media.read_bytes()).hexdigest():
                raise ValueError("saved image/source identity differs during replay")
            return frame
        rendered = render_frame(scene, camera, (self.config["seed"] + index * 7919) % 2**32,
                                width=self.config["image_width"], height=self.config["image_height"])
        pixels = rendered["png_bytes"]
        detected = detect_frame(pixels)  # Only pixels cross this boundary.
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(pixels)
        calibration = deepcopy(rendered["calibration"])
        # Localization uses the reported GPS position, not the renderer truth.
        calibration["position_m"][0] += row["cart_x_m"] - camera["x_m"]
        calibration["position_m"][1] += row["cart_y_m"] - camera["y_m"]
        frame = {"frame_id": frame_id, "minute": row["minute"], "cart_id": row["cart_id"],
                 "checkpoint_id": cp["id"], "image": "media/" + media.name,
                 "image_sha256": hashlib.sha256(pixels).hexdigest(), "source_digest": source_digest,
                 "detection": detected, "calibration": calibration, "surface_assumption": surface,
                 "cart_position": {"x_m": row["cart_x_m"], "y_m": row["cart_y_m"], "accuracy_m": row.get("position_accuracy_m", 3)},
                 "renderer_version": RENDERER_VERSION, "detector_version": DETECTOR_VERSION}
        # Reference labels remain separate; neither detector nor policy reads them.
        write_record(self.root / "reference" / (frame_id + ".json"),
                     {"frame_id": frame_id, "image_sha256": frame["image_sha256"], "labels": rendered["labels"]})
        write_record(path, frame)
        return frame

    def _record_frame(self, frame, cp):
        self.frames.append(frame)
        self._latest_frame[cp["id"]] = frame
        detection = frame["detection"]
        evidence = "synthetic:" + frame["image_sha256"]
        at = utc_at(self.config["start_date"], frame["minute"])
        if detection["quality"] != "USABLE":
            self._admit(cp["id"], "REPHOTOGRAPH", frame["frame_id"], evidence)
            return
        workflow = self.workflows[cp["id"]]
        snapshot = workflow.snapshot()
        kinds = {case["condition_kind"] for case in snapshot["cases"] if case["status"] not in {"VERIFIED", "DISMISSED"}}
        kinds |= {item["condition"] for item in detection["detections"] if item["condition"] in NATIVE}
        for item in detection["detections"]:
            if item["condition"] == "DEBRIS":
                point = project_detection(item["bbox"], frame["calibration"])
                if point and math.hypot(point["x_m"]-cp["x_m"], point["y_m"]-cp["y_m"]) <= 20:
                    self.alerts.append({"kind": "DEBRIS_DETECTION", "checkpoint_id": cp["id"], "minute": frame["minute"], "evidence_ref": evidence})
                    self._admit(cp["id"], "CLEAR_DEBRIS", frame["frame_id"] + ":debris", evidence)
        for kind in sorted(kinds):
            matches = [item for item in detection["detections"] if item["condition"] == kind]
            best = max(matches, key=lambda item: item["score"]) if matches else None
            if best:
                target = project_detection(best["bbox"], frame["calibration"])
            else:
                prior_case = next((case for case in snapshot["cases"] if case["condition_kind"] == kind and case["status"] == "AWAITING_VERIFICATION"), None)
                if prior_case is None:
                    continue
                prior = [row for row in snapshot["observations"] if row["observation_id"] in prior_case["observation_ids"]]
                target = reinspection_target(frame, prior, kind)
                if target is None:
                    self.alerts.append({"kind": "REINSPECTION_INSUFFICIENT_COVERAGE", "checkpoint_id": cp["id"], "minute": frame["minute"], "condition": kind, "evidence_ref": evidence})
                    continue
            if target is None or math.hypot(target["x_m"]-cp["x_m"], target["y_m"]-cp["y_m"]) > 20:
                continue
            observation = build_observation(site_id=self.site.site_id, deployment_id=self.site.deployment_id,
                map_revision=self.map_revision, frame_id=frame["frame_id"], checkpoint_id=cp["id"],
                hole_number=cp["hole_number"], feature_id=cp["feature_id"], cart_id=frame["cart_id"],
                camera_id=frame["cart_id"] + "-camera", captured_at_utc=at,
                condition=kind if best else "CLEAR", inspected_condition=kind, quality="USABLE",
                cart_position=frame["cart_position"], target_position={**target, "accuracy_m": max(5.0, frame["cart_position"]["accuracy_m"]+2.0)},
                detection_score=best["score"] if best else 0.5, evidence_ref=evidence)
            self.observations.append(observation)
            workflow.observe(observation, at=at)
            cases = workflow.snapshot()["cases"]
            case = next((case for case in cases if case["condition_kind"] == kind and case["status"] not in {"VERIFIED", "DISMISSED"}), None)
            if not case:
                continue
            if case["status"] == "AWAITING_VERIFICATION":
                tasks = workflow.snapshot()["tasks"]
                task = next(t for t in tasks if t["task_id"] == case["latest_task_id"])
                if at > task["completed_at_utc"]:
                    workflow.verify(case["case_id"], observation, passed=best is None, operator=OPERATOR,
                                    reason="Independent later synthetic camera frame; not real repair evidence", at=at)
                    case = next(c for c in workflow.snapshot()["cases"] if c["case_id"] == case["case_id"])
            if case["status"] in {"CANDIDATE", "REOPENED"}:
                workflow.review(case["case_id"], "confirm", operator=OPERATOR,
                                reason="Explicit simulated operator reviews pixel-baseline candidate", at=at)
            if best and case["status"] in {"CANDIDATE", "REOPENED", "CONFIRMED"}:
                self._admit(cp["id"], WORK[kind], observation["observation_id"], evidence, case["case_id"])

    def _capture(self):
        minute = int(self.minute)
        if self.minute != minute or minute in self._captured_minutes:
            return
        self._captured_minutes.add(minute)
        for index, row in self.schedule.get(minute, []):
            cp = self.catalog[row["checkpoint_id"]]
            if not row.get("camera_online", True):
                self.alerts.append({"kind": "CAMERA_OFFLINE", "cart_id": row["cart_id"], "minute": minute,
                                    "checkpoint_id": cp["id"]})
                continue
            self._record_frame(self._image(index, row, cp), cp)
        self.obs, self.info = self.env.refresh_observation_info()

    def advance(self):
        self._sync_jobs()
        self._capture()
        obs, info = policy_inputs(self.obs, self.info)
        action = int(self.policy.act(obs, info))
        self.obs, _, terminated, truncated, self.info = self.env.step(action)
        self.step_count += 1
        for event in self.env.sim.events.since(self._event_cursor):
            if event.kind.value == "demand_served":
                index = int(event.t_s / 60) - self.env.scenario.hours.open_minute
                count = int(event.payload.get("served", event.payload.get("count", 0)))
                if not count:
                    count = sum(event.payload.get("by_zone", {}).values())
                self.actual_shots.extend(self.compiled["shots"][index][:count])
        self._event_cursor = len(self.env.sim.events)
        if int(self.minute) % 10 == 0 and self.minute == int(self.minute):
            self.timeline.append(self.summary())
        return terminated, truncated

    def summary(self):
        sim = self.env.sim
        return {"minute": self.minute, "day": int(self.minute // 1440)+1,
                "facility_open": sim.facility_open, "inventory": sim.dispenser_count(),
                "metrics": sim.metrics.to_dict(), "jobs": sim.joint_metrics,
                "ledger": sim.ledger.counts(), "session_progress": sim.session_progress,
                "staff": list(sim.staff_summary()), "robots": [row.to_dict() for row in sim.robot_snapshots()],
                "zones": [row.to_dict() for row in sim.zone_snapshots()],
                "frames": len(self.frames), "observations": len(self.observations)}

    def replay_digest(self):
        self._sync_jobs()
        return digest({"runtime": self.env.sim.state_summary(), "events": self.env.sim.events.to_dicts(),
                       "jobs": self.env.sim.staff_work_snapshots(),
                       "workflows": {key: value.events for key, value in self.workflows.items()},
                       "frame_hashes": [frame["image_sha256"] for frame in self.frames]})

    def report(self, status):
        self._sync_jobs()
        cases, tasks = [], []
        for workflow in self.workflows.values():
            snapshot = workflow.snapshot()
            cases.extend(snapshot["cases"])
            tasks.extend(snapshot["tasks"])
        visible_labels = predicted = hits = 0
        for frame in self.frames:
            reference = read_record(self.root / "reference" / (frame["frame_id"] + ".json"))
            if reference["image_sha256"] != frame["image_sha256"]:
                raise ValueError("reference labels belong to a different image")
            truth = {r["condition"] for r in reference["labels"] if r.get("visible_pixels", 0) >= 12}
            found = {r["condition"] for r in frame["detection"]["detections"]}
            visible_labels += len(truth)
            predicted += len(found)
            hits += len(truth & found)
        coverage = []
        for cp in self.catalog.values():
            frame = self._latest_frame.get(cp["id"])
            state = "UNOBSERVED" if frame is None else "STALE" if self.minute-frame["minute"] > 90 else frame["detection"]["quality"]
            coverage.append({"checkpoint_id": cp["id"], "status": state,
                             "last_minute": frame["minute"] if frame else None})
        return {"schema": SCHEMA, "environment": "SIMULATION", "status": status,
                "summary": self.summary(), "config": self.config, "catalog": list(self.catalog.values()),
                "assumptions": self.compiled.get("assumptions", {}),
                "timeline": self.timeline, "frames": self.frames, "observations": self.observations,
                "cases": cases, "tasks": tasks, "staff_jobs": self.env.sim.staff_work_snapshots(),
                "weather": [row for row in self.compiled["weather"] if row["minute"] <= self.minute],
                "traffic": [row for row in self.compiled["traffic"] if row["minute"] <= self.minute],
                "cart_routes": [row for row in self.compiled["cart_routes"] if row["minute"] <= self.minute],
                "operational_events": [row for row in self.compiled.get("operational_events", []) if _event_start(row) <= self.minute],
                "alerts": self.alerts, "coverage": coverage,
                "actual_shot_count": len(self.actual_shots), "actual_shots_sample": self.actual_shots[::max(1, len(self.actual_shots)//3000)],
                "events": self.env.sim.events.to_dicts(),
                "vision_evaluation": {"scope": "SYNTHETIC_FRAME_CLASS_PRESENCE_ONLY", "field_validated": False,
                    "visible_reference_classes": visible_labels, "predicted_classes": predicted, "matching_classes": hits,
                    "precision": hits/predicted if predicted else None,
                    "recall": hits/visible_labels if visible_labels else None}}


def run(root, config=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / ".session.lock").open("a+b")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        raise ValueError("another worker owns this session") from None
    with lock:
        started = time.monotonic()
        state_path = root / "state.json"
        if state_path.exists():
            state = read_record(state_path)
            stored = validate_config(read_json(root / "config.json"))
            if config is not None and validate_config(config) != stored:
                raise ValueError("existing session configuration is immutable")
            config = stored
            if state["config_digest"] != digest(config) or state["engine"] != engine_fingerprint():
                raise ValueError("session code/dependencies/config changed; preserve evidence and create a new session")
            control = read_json(root / "control.json")
            if type(control) is not dict or set(control) != {"paused"} or type(control["paused"]) is not bool:
                raise ValueError("invalid pause control")
            if control["paused"]:
                return {**state, "status": "PAUSED"}
            compiled = read_record(root / "compiled.json")
            if digest(compiled) != state["compiled_digest"]:
                raise ValueError("compiled scenario identity changed")
        else:
            if any(p.name != ".session.lock" for p in root.iterdir()):
                raise ValueError("new session needs an empty directory; partial evidence is not a fresh start")
            config = validate_config(DEFAULT_CONFIG if config is None else config)
            compiled = serialize_compiled(compile_session({k: config[k] for k in ("seed", "days", "staff_count", "initial_stock", "demand_scale", "assumptions")}))
            atomic_json(root / "config.json", config)
            atomic_json(root / "control.json", {"paused": False})
            write_record(root / "compiled.json", compiled)
            state = {"schema": SCHEMA, "status": "INITIALIZED", "step": 0,
                     "config_digest": digest(config), "compiled_digest": digest(compiled),
                     "engine": engine_fingerprint(), "replay_digest": None}
            write_record(state_path, state)
        session = Session(root, config, compiled)
        for _ in range(state["step"]):
            if time.monotonic()-started > config["wall_time_seconds"]:
                raise ValueError("prefix replay exceeded run budget; existing cursor preserved")
            session.advance()
        if state["replay_digest"] is not None and state["replay_digest"] != session.replay_digest():
            raise ValueError("runtime/evidence prefix replay differs; refusing continuation")
        target = min(session.env.sim.session_end_s / 60, session.minute + config["advance_minutes"])
        status = "SESSION_COMPLETE" if session.env.sim.facility_closed else "CHUNK_COMPLETE"
        while session.minute < target:
            control = read_json(root / "control.json")
            if type(control) is not dict or set(control) != {"paused"} or type(control["paused"]) is not bool:
                raise ValueError("invalid pause control")
            if control["paused"]:
                status = "PAUSED"
                break
            if time.monotonic()-started > config["wall_time_seconds"]:
                status = "TIME_BUDGET"
                break
            if session.step_count % 20 == 0 and sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) > config["disk_limit_mb"]*1024**2:
                status = "DISK_LIMIT"
                break
            terminated, truncated = session.advance()
            if truncated:
                raise ValueError("runtime truncated before session end")
            if terminated:
                status = "SESSION_COMPLETE"
                break
        state.update(status=status, step=session.step_count, minute=session.minute,
                     replay_digest=session.replay_digest())
        report = session.report(status)
        write_record(root / "report.json", report)
        from scripts.course_session_report import render_report
        pending_report = root / ".report.pending.html"
        pending_report.write_text(render_report(report), encoding="utf-8")
        pending_report.replace(root / "report.html")
        # Publish the cursor last. A failed publication can replay the same
        # image prefix and retry; it cannot strand a completed cursor forever.
        write_record(state_path, state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "status", "pause", "resume"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.state_dir, read_json(args.config) if args.config else None)
    elif args.command == "status":
        result = {**read_record(args.state_dir / "state.json"), "control": read_json(args.state_dir / "control.json")}
    else:
        read_record(args.state_dir / "state.json")
        atomic_json(args.state_dir / "control.json", {"paused": args.command == "pause"})
        result = read_json(args.state_dir / "control.json")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
