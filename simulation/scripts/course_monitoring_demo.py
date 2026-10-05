"""Finite, synthetic whole-course observation/workflow rehearsal.

Run from simulation/: python -m scripts.course_monitoring_demo --out /tmp/course-day
The episode contains scripted scene changes, not a second facility runtime.
No generated observation claims to come from a real camera or detector.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from nxt_pilot_ops.grounds import GroundsWorkflow
from nxt_telemetry.course_condition import build_observation
from scripts.course_18_hole_fixture import build_fixture, checkpoint_catalog
from scripts.course_monitoring_report import render_markdown, render_report

SCHEMA = "nxt-course-monitoring-report/v0"
OPERATOR = "scripted-demo-operator"
RESOURCES = {
    "grounds-crew": {"id": "grounds-crew", "kind": "HUMAN", "capabilities": ["INSPECT", "REPAIR_DIVOT", "RAKE_BUNKER"]},
    "bunker-demo-robot": {"id": "bunker-demo-robot", "kind": "SIMULATED_ROBOT", "capabilities": ["INSPECT", "RAKE_BUNKER"]},
}
TASK_KIND = {"DIVOT": "REPAIR_DIVOT", "BUNKER_SURFACE": "RAKE_BUNKER", "STANDING_WATER": "INSPECT"}
TERMINAL = {"VERIFIED", "DISMISSED"}
DISCLAIMERS = [
    "SIMULATION：18 洞合成场景；没有接入真实摄像头、图像识别模型或机器人。",
    "覆盖率分母是 54 个检查点，不是球场面积；球车经过不代表整洞已检查。",
    "清晰且未见异常仅针对该检查点、本次检查的问题类别和拍摄时刻。",
    "场景预设问题出现及消失的时间，不证明真实维修效果、相机可见性或检测准确率。",
    "scripted-demo-operator 是演示脚本角色；合成沙坑机器人不代表亚云设备已有该能力。",
    "球车与目标坐标分别提供，均为模拟；检测分数未经校准，不是准确率。",
    "此页播放已保存的时间线；持续后台巡查、真实人员通知和实机调度尚未接入。",
]


def _parse_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("time requires explicit UTC ending in Z")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo != timezone.utc:
        raise ValueError("time requires UTC")
    return parsed


def _utc(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _episode(seed: int) -> tuple[dict, ...]:
    """Fixed scene intervals, deliberately independent of workflow completion."""
    shift = seed % 3
    return (
        {"checkpoint_id": f"cp-h{1 + shift:02d}-fairway", "kind": "DIVOT", "from_minute": 0, "until_minute": 125},
        {"checkpoint_id": f"cp-h{2 + shift:02d}-bunker", "kind": "BUNKER_SURFACE", "from_minute": 0, "until_minute": 265},
        {"checkpoint_id": "cp-h10-fairway", "kind": "DIVOT", "from_minute": 80, "until_minute": 410},
        {"checkpoint_id": "cp-h13-bunker", "kind": "BUNKER_SURFACE", "from_minute": 175, "until_minute": 375},
        {"checkpoint_id": "cp-h07-green", "kind": "STANDING_WATER", "from_minute": 100, "until_minute": 385},
        {"checkpoint_id": "cp-h18-bunker", "kind": "BUNKER_SURFACE", "from_minute": 360, "until_minute": 2000},
    )


def _condition(checkpoint: dict, minute: int, episode: tuple[dict, ...]) -> str:
    for item in episode:
        if item["checkpoint_id"] == checkpoint["id"] and item["from_minute"] <= minute < item["until_minute"]:
            return item["kind"]
    return "CLEAR"


def _project(snapshot: dict) -> tuple[list[dict], list[dict]]:
    """Add presentation aliases only; the original journal remains authoritative."""
    cases, tasks = deepcopy(snapshot["cases"]), deepcopy(snapshot["tasks"])
    if isinstance(cases, dict):
        cases = list(cases.values())
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    by_case = {case["case_id"]: case for case in cases}
    for case in cases:
        case.setdefault("kind", case.get("condition", case.get("condition_kind")))
    for task in tasks:
        case = by_case.get(task.get("case_id"), {})
        task.setdefault("checkpoint_id", case.get("checkpoint_id"))
        task.setdefault("hole_number", case.get("hole_number"))
        task.setdefault("kind", task.get("task_kind"))
        task.setdefault("assigned_to", task.get("resource", {}).get("id"))
    return cases, tasks


def _coverage(catalog: list[dict], observations: list[dict], cases: list[dict], now: datetime, freshness_minutes: int) -> list[dict]:
    latest = {item["checkpoint_id"]: item for item in observations}
    open_points = {case["checkpoint_id"] for case in cases if case["status"] not in TERMINAL}
    rows = []
    for cp in catalog:
        obs = latest.get(cp["id"])
        status = "UNOBSERVED"
        if obs:
            age = (now - _parse_utc(obs["captured_at_utc"])).total_seconds() / 60
            if age > freshness_minutes:
                status = "STALE"
            elif obs["quality"] != "USABLE":
                status = "UNUSABLE"
            elif cp["id"] in open_points or obs["condition"] != "CLEAR":
                status = "CANDIDATE"
            else:
                status = "OBSERVED_CLEAR"
        rows.append({"checkpoint_id": cp["id"], "status": status,
                     "last_observed_at_utc": obs["captured_at_utc"] if obs else None,
                     "inspected_condition": obs["inspected_condition"] if obs else None})
    return rows


def _validate_actions(actions: object, duration_minutes: int) -> list[dict]:
    if not isinstance(actions, list):
        raise ValueError("manager actions must be a JSON list")
    required = {"minute", "checkpoint_id", "operator", "action", "reason"}
    specific = {"confirm": set(), "dismiss": set(), "adjust": {"priority", "deadline_at_utc"}, "assign": {"resource_id"}}
    for action in actions:
        if not isinstance(action, dict) or not required <= set(action):
            raise ValueError("manager action requires minute, checkpoint_id, operator, action and reason")
        if not isinstance(action["action"], str) or action["action"] not in specific:
            raise ValueError("unsupported manager action")
        if set(action) != required | specific[action["action"]]:
            raise ValueError("manager action fields must match the selected action exactly")
        if type(action["minute"]) is not int or not 0 <= action["minute"] <= duration_minutes or action["minute"] % 5:
            raise ValueError("manager action minute must be a five-minute tick within the episode")
        if action["checkpoint_id"] not in {cp["id"] for cp in checkpoint_catalog()}:
            raise ValueError("unknown checkpoint_id")
        if any(not isinstance(action[key], str) or not action[key].strip() for key in ("operator", "reason")):
            raise ValueError("manager action needs actor and reason")
    return deepcopy(actions)


def run_demo(*, duration_minutes: int = 480, seed: int = 0,
             start_utc: str = "2026-09-16T00:00:00Z", freshness_minutes: int = 75,
             scripted_review: bool = True, manager_actions: list[dict] | None = None,
             realtime_speed: float | None = None) -> dict:
    """Return a deterministic saved-day bundle; optional pacing changes no facts."""
    if type(duration_minutes) is not int or not 5 <= duration_minutes <= 1440 or duration_minutes % 5:
        raise ValueError("duration_minutes must be 5..1440 in five-minute steps")
    if type(seed) is not int or type(freshness_minutes) is not int or freshness_minutes < 1:
        raise ValueError("seed must be an integer and freshness_minutes a positive integer")
    if realtime_speed is not None and (not isinstance(realtime_speed, (int, float)) or not 0 < realtime_speed <= 100000):
        raise ValueError("realtime_speed must be positive and finite")
    start = _parse_utc(start_utc)
    site, model = build_fixture()
    effective = model.effective_from
    if isinstance(effective, str):
        effective = _parse_utc(effective)
    if start < effective:
        raise ValueError("start_utc cannot precede the synthetic map effective time")
    catalog = checkpoint_catalog()
    by_id = {cp["id"]: cp for cp in catalog}
    workflow = GroundsWorkflow(site.site_id, site.deployment_id, model.content_digest)
    scene = _episode(seed)
    actions = _validate_actions([] if manager_actions is None else manager_actions, duration_minutes)
    observations, frames, action_receipts = [], [], []
    task_started: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for minute in range(0, duration_minutes + 1, 5):
        if minute and realtime_speed is not None:
            time.sleep(300 / realtime_speed)
        now = start + timedelta(minutes=minute)
        at = _utc(now)
        # Release resources at the declared completion tick, before new assignment.
        _, prior_tasks = _project(workflow.snapshot())
        for task in prior_tasks:
            if task["status"] == "IN_PROGRESS" and minute - task_started.get(task["task_id"], minute) >= 30:
                workflow.complete(task["task_id"], operator=OPERATOR, at=at)
        # Cart poses are explicit sampled stops; the report does not invent motion.
        carts, sample_points = [], []
        for cart_index in range(2):
            hole = ((minute // 10 + cart_index * 9) % 18) + 1
            kind = "fairway" if minute % 10 == 0 else "bunker"
            cp = by_id[f"cp-h{hole:02d}-{kind}"]
            cart_id = f"survey-cart-{cart_index + 1}"
            carts.append({"cart_id": cart_id, "x_m": cp["cart_x_m"], "y_m": cp["cart_y_m"]})
            sample_points.append((cp, cart_id))
        if minute % 60 == 0:
            # A third maintenance cart samples only three greens; others stay unknown.
            hole = (1, 7, 13)[(minute // 60) % 3]
            cp = by_id[f"cp-h{hole:02d}-green"]
            carts.append({"cart_id": "inspection-cart", "x_m": cp["cart_x_m"], "y_m": cp["cart_y_m"]})
            sample_points.append((cp, "inspection-cart"))
        pending_verification = []
        for cp, cart_id in sample_points:
            condition = _condition(cp, minute, scene)
            quality = "BLURRED" if (minute // 5 + cp["hole_number"] + seed) % 13 == 0 else "USABLE"
            inspected = {"fairway": "DIVOT", "bunker": "BUNKER_SURFACE", "green": "STANDING_WATER"}[cp["surface_type"]]
            frame_id = f"frame-{minute:04d}-{cp['id']}"
            obs = build_observation(
                site_id=site.site_id, deployment_id=site.deployment_id, map_revision=model.content_digest,
                frame_id=frame_id, checkpoint_id=cp["id"], hole_number=cp["hole_number"], feature_id=cp["feature_id"],
                cart_id=cart_id, camera_id=f"{cart_id}-side-camera", captured_at_utc=at,
                condition=condition, inspected_condition=inspected, quality=quality,
                cart_position={"x_m": cp["cart_x_m"], "y_m": cp["cart_y_m"], "accuracy_m": 3.0},
                target_position={"x_m": cp["x_m"], "y_m": cp["y_m"], "accuracy_m": 5.0},
                detection_score=0.8 if condition != "CLEAR" else 0.7,
                evidence_ref=f"synthetic:{frame_id}")
            observations.append(obs)
            receipt = workflow.observe(obs, at=at)
            if receipt.get("case_id"):
                first_seen.setdefault(receipt["case_id"], minute)
            cases, current_tasks = _project(workflow.snapshot())
            tasks_by_id = {task["task_id"]: task for task in current_tasks}
            if scripted_review and quality == "USABLE":
                for case in cases:
                    if case["checkpoint_id"] == cp["id"] and case["status"] == "AWAITING_VERIFICATION" and case["kind"] == inspected:
                        completed_at = tasks_by_id[case["latest_task_id"]]["completed_at_utc"]
                        if _parse_utc(obs["captured_at_utc"]) > _parse_utc(completed_at):
                            pending_verification.append((case["case_id"], obs, condition == "CLEAR"))
        # Optional explicit manager instructions run before demonstration automation.
        for action in (row for row in actions if row["minute"] == minute):
            cases, _ = _project(workflow.snapshot())
            matches = [case for case in cases if case["checkpoint_id"] == action["checkpoint_id"] and case["status"] not in TERMINAL]
            if len(matches) != 1:
                raise ValueError(f"manager action must resolve one active case: {action['checkpoint_id']} at {minute}")
            case = matches[0]
            common = {"operator": action["operator"], "reason": action["reason"], "at": at}
            if action["action"] in {"confirm", "dismiss"}:
                result = workflow.review(case["case_id"], action["action"], **common)
            elif action["action"] == "adjust":
                result = workflow.adjust(case["case_id"], priority=action["priority"], deadline_at_utc=action.get("deadline_at_utc"), **common)
            else:
                result = workflow.assign(case["case_id"], resource=RESOURCES[action["resource_id"]], task_kind=TASK_KIND[case["kind"]], **common)
            action_receipts.append({"action": action, "receipt": result})
        # A manager adjustment at this tick is recorded before scripted verification.
        for case_id, obs, passed in pending_verification:
            workflow.verify(case_id, obs, passed=passed, operator=OPERATOR,
                            reason="Scripted independent later-frame review; not a real operator", at=at)
        cases, _ = _project(workflow.snapshot())
        if scripted_review:
            for case in cases:
                case_id = case["case_id"]
                if case["status"] in {"CANDIDATE", "REOPENED"} and minute > first_seen.get(case_id, minute):
                    workflow.review(case_id, "confirm", operator=OPERATOR, reason="Synthetic demonstration review", at=at)
            cases, tasks = _project(workflow.snapshot())
            busy = {task.get("resource", {}).get("id") for task in tasks if task["status"] in {"ASSIGNED", "IN_PROGRESS"}}
            # Manual priority is respected. No inference or hidden learned policy.
            rank = {"URGENT": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
            ordered = sorted(cases, key=lambda c: (rank.get(c.get("priority", "NORMAL"), 2), c.get("deadline_at_utc") or "9999", c["case_id"]))
            for case in ordered:
                if case["status"] == "CONFIRMED":
                    resource_id = "bunker-demo-robot" if case["kind"] == "BUNKER_SURFACE" else "grounds-crew"
                    if resource_id not in busy:
                        workflow.assign(case["case_id"], resource=RESOURCES[resource_id], task_kind=TASK_KIND[case["kind"]],
                                        operator=OPERATOR, reason="Scripted capacity-one assignment; no physical command", at=at)
                        busy.add(resource_id)
        _, tasks = _project(workflow.snapshot())
        for task in tasks:
            if task["status"] == "ASSIGNED":
                workflow.start(task["task_id"], at=at)
                task_started[task["task_id"]] = minute
        cases, tasks = _project(workflow.snapshot())
        frames.append({"minute": minute, "at_utc": at, "carts": carts,
                       "coverage": _coverage(catalog, observations, cases, now, freshness_minutes),
                       "cases": cases, "tasks": tasks})
    final = frames[-1]
    counts = dict(Counter(row["status"] for row in final["coverage"]))
    report = {
        "schema": SCHEMA, "environment": "SIMULATION", "site_id": site.site_id,
        "deployment_id": site.deployment_id, "map_revision": model.content_digest,
        "coordinate_frame_id": model.frame.frame_id,
        "generated_at_utc": final["at_utc"], "duration_minutes": duration_minutes,
        "disclaimers": DISCLAIMERS, "checkpoints": [
            {"checkpoint_id": cp["id"], "hole_number": cp["hole_number"], "feature_id": cp["feature_id"],
             "x_m": cp["x_m"], "y_m": cp["y_m"], "kind": cp["surface_type"].upper()} for cp in catalog],
        "frames": frames, "cases": final["cases"], "tasks": final["tasks"],
        "summary": {"holes": 18, "checkpoint_count": len(catalog), "coverage": counts,
                    "observations": len(observations), "issues": len(final["cases"]), "tasks": len(final["tasks"]),
                    "verified": sum(case["status"] == "VERIFIED" for case in final["cases"]),
                    "freshness_minutes": freshness_minutes, "seed": seed},
    }
    state = workflow.to_dict()
    if GroundsWorkflow.from_dict(state).snapshot() != workflow.snapshot():
        raise ValueError("exported workflow failed deterministic replay")
    return {"report": report, "grounds_state": state, "observations": observations,
            "site": site.to_dict(), "course_model": model.to_dict(),
            "manager_action_receipts": action_receipts,
            "scenario": {"environment": "SIMULATION", "seed": seed, "start_utc": start_utc,
                         "duration_minutes": duration_minutes, "scripted_review": scripted_review,
                         "freshness_minutes": freshness_minutes, "manager_actions": actions,
                         "scene_intervals": list(scene)}}


def write_bundle(bundle: dict, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("report", "grounds_state", "site", "course_model", "scenario", "manager_action_receipts"):
        path = destination / f"{name}.json"
        path.write_text(json.dumps(bundle[name], ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (destination / "observations.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in bundle["observations"]), encoding="utf-8")
    (destination / "report.html").write_text(render_report(bundle["report"]), encoding="utf-8")
    (destination / "report.md").write_text(render_markdown(bundle["report"]), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration-minutes", type=int, default=480)
    parser.add_argument("--start-utc", default="2026-09-16T00:00:00Z")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--freshness-minutes", type=int, default=75)
    parser.add_argument("--manager-actions", type=Path)
    parser.add_argument("--no-scripted-review", action="store_true", help="retain candidates for explicit manager actions")
    parser.add_argument("--realtime-speed", type=float, help="1 = real time; finite run, no background service")
    args = parser.parse_args()
    actions = json.loads(args.manager_actions.read_text(encoding="utf-8")) if args.manager_actions else []
    bundle = run_demo(duration_minutes=args.duration_minutes, seed=args.seed, start_utc=args.start_utc,
                      freshness_minutes=args.freshness_minutes, scripted_review=not args.no_scripted_review,
                      manager_actions=actions, realtime_speed=args.realtime_speed)
    write_bundle(bundle, args.out)
    print(json.dumps(bundle["report"]["summary"], ensure_ascii=False, sort_keys=True))
    print(str(args.out / "report.html"))


if __name__ == "__main__":
    main()
