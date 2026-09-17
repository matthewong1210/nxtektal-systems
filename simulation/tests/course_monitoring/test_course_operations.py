"""Saved report reader never advances a session or projects hidden scenario truth."""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.course_18_hole_fixture import build_fixture, checkpoint_catalog
from scripts.course_session import DEFAULT_CONFIG
from scripts.course_operations import CourseOpsReader, CourseOpsError, MAX_JSON_BYTES
from scripts.joint_learning import atomic_json, digest, read_record, write_record


def fixture_series(root):
    config = deepcopy(DEFAULT_CONFIG)
    catalog = checkpoint_catalog()
    _, model = build_fixture()
    config["seed"] = 12
    series_config = {"schema": "nxt-course-session-series/v1", "environment": "SIMULATION", "session_config": config}
    child = root / "round-0000000000"
    (child / "media").mkdir(parents=True)
    image = io.BytesIO()
    Image.new("RGB", (64, 48), "green").save(image, format="PNG")
    pixels = image.getvalue()
    (child / "media/frame-000000.png").write_bytes(pixels)
    config.update(image_width=64, image_height=48)
    cp = catalog[0]
    frame = {"frame_id": "frame-000000", "cart_id": "CART-01", "checkpoint_id": cp["id"], "minute": 480,
             "image": "media/frame-000000.png", "image_sha256": hashlib.sha256(pixels).hexdigest(),
             "cart_position": {"x_m": 131, "y_m": 36, "accuracy_m": 3},
             "detection": {"quality": "USABLE", "score_calibration": "NOT_CALIBRATED", "detections": []},
             "surface_assumption": {"grass_firmness_index": .2}}
    report = {"schema": config["schema"], "environment": "SIMULATION", "status": "CHUNK_COMPLETE", "config": config,
              "summary": {"minute": 500, "inventory": 87654321, "ledger": {"secret": 42}, "robots": ["SECRET_ROBOT"]},
              "catalog": catalog, "frames": [frame], "observations": [], "cases": [], "tasks": [], "staff_jobs": [],
              "coverage": [], "cart_routes": [{"cart_id": "CART-16", "x_m": 999}], "weather": [{"hidden": "WEATHER_TRUTH"}],
              "compiled": "SECRET_COMPILED", "vision_evaluation": {"hidden": "SECRET_LABELS"}}
    state = {"schema": config["schema"], "config_digest": digest(config), "compiled_digest": "e" * 64,
             "engine": "f" * 64, "status": "CHUNK_COMPLETE", "step": 10, "minute": 500}
    parent = {"schema": series_config["schema"], "environment": "SIMULATION", "config_digest": digest(series_config),
              "engine": "a" * 64, "completed_rounds": 0, "status": "CHILD_CHUNK_COMPLETE", "current": {"index": 0, "seed": 12,
              "directory": child.name, "config_digest": digest(config), "status": "CHUNK_COMPLETE", "minute": 500}}
    atomic_json(root / "config.json", series_config)
    atomic_json(root / "control.json", {"paused": True})
    atomic_json(child / "config.json", config)
    atomic_json(child / "control.json", {"paused": False})
    write_record(root / "state.json", parent)
    write_record(child / "state.json", state)
    write_record(child / "report.json", report)
    return child, report, model.content_digest


@pytest.fixture
def saved(tmp_path):
    return tmp_path, *fixture_series(tmp_path)


def test_legacy_saved_projection_is_not_live_or_truth(saved):
    root, child, report, _ = saved
    before = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
    view = CourseOpsReader(root, now=lambda: "2026-09-18T01:00:00Z").snapshot()
    assert view["source"]["parent_paused"] is True
    assert view["source"]["child_paused"] is False
    assert view["source"]["published_at_utc"] is None and view["source"]["live"] is False
    assert view["generated_at_utc"] == "2026-09-18T01:00:00Z"
    assert view["clock"]["simulated_minute"] == 500
    assert view["range"]["status"] == "UNKNOWN" and view["range"]["inventory_fraction"] is None
    assert view["weather"] == {"status": "UNKNOWN"}
    assert len(view["course"]["checkpoints"]) == 54 and len(view["carts"]) == 1
    assert view["carts"][0]["x_m"] == 131 and view["carts"][0]["accuracy_m"] == 3
    assert view["course"]["checkpoints"][1]["coverage_status"] == "UNOBSERVED"
    body = json.dumps(view)
    for secret in ("87654321", "SECRET_", "WEATHER_TRUTH", "grass_firmness", "cart_routes", "vision_evaluation", "surface_assumption"):
        assert secret not in body
    assert before == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def test_media_is_declared_hash_checked_and_round_scoped(saved):
    root, child, _, _ = saved
    reader = CourseOpsReader(root)
    assert reader.media(child.name, "frame-000000") == (child / "media/frame-000000.png").read_bytes()
    for round_id, frame_id in [("round-0000000001", "frame-000000"), (child.name, "frame-999999"), ("../", "frame-000000"), (child.name, "../config")]:
        with pytest.raises(CourseOpsError): reader.media(round_id, frame_id)
    (child / "media/frame-000000.png").write_bytes(b"corrupt")
    with pytest.raises(CourseOpsError): reader.media(child.name, "frame-000000")


@pytest.mark.parametrize("fault", ["hash", "duplicate", "nan", "oversized", "mismatch", "future", "symlink", "map"])
def test_corrupt_or_inconsistent_evidence_fails_closed(saved, fault):
    root, child, report, _ = saved
    path = child / "report.json"
    if fault == "hash":
        path.write_text(json.dumps({"payload": report, "sha256": "0" * 64}))
    elif fault == "duplicate": path.write_text('{"payload":{},"payload":{},"sha256":"x"}')
    elif fault == "nan": path.write_text('{"payload":{"x":NaN},"sha256":"x"}')
    elif fault == "oversized":
        with path.open("wb") as f: f.truncate(MAX_JSON_BYTES + 1)
    elif fault == "mismatch":
        report["summary"]["minute"] += 1
        write_record(path, report)
    elif fault == "future":
        report["frames"][0]["minute"] = 9999
        write_record(path, report)
    elif fault == "symlink":
        other = root / "elsewhere.json"; other.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(other)
    elif fault == "map":
        report["observations"] = [{"map_revision": "sha256:" + "0" * 64}]
        write_record(path, report)
    with pytest.raises(CourseOpsError): CourseOpsReader(root).snapshot()


def test_declared_observed_range_is_whitelisted(saved):
    root, child, report, _ = saved
    report["observed_range"] = {"schema": "nxt-course-observed-range/v1", "minute": 500,
        "inventory_fraction": .25, "zones": [{"zone_id": "Z1", "balls": 42, "is_open": True, "secret": 1}],
        "robots": [{"robot_id": "R1", "activity": "IDLE", "health": "OK", "location": "station:H1",
            "battery_fraction": .9, "payload_balls": 3, "awaiting_human": False, "secret": 1}],
        "staff": {"capacity": 3, "busy": 1, "queued": 2, "secret": 1}, "future": "SECRET"}
    write_record(child / "report.json", report)
    view = CourseOpsReader(root).snapshot()
    assert view["range"]["status"] == "OBSERVED"
    assert view["range"]["zones"] == [{"zone_id": "Z1", "balls": 42, "is_open": True}]
    assert "secret" not in json.dumps(view["range"])


def test_changed_read_does_not_join_two_snapshots(saved, monkeypatch):
    root, child, report, _ = saved
    reader = CourseOpsReader(root)
    original = reader._read
    changed = False
    def read(relative, limit=MAX_JSON_BYTES):
        nonlocal changed
        result = original(relative, limit)
        if str(relative).endswith("report.json") and not changed:
            changed = True
            state = read_record(child / "state.json"); state["minute"] += 1
            write_record(child / "state.json", state)
        return result
    monkeypatch.setattr(reader, "_read", read)
    with pytest.raises(CourseOpsError): reader.snapshot()


def test_media_rejects_symlink_directory(saved):
    root, child, _, _ = saved
    (child / "media").rename(child / "elsewhere")
    (child / "media").symlink_to(child / "elsewhere", target_is_directory=True)
    with pytest.raises(CourseOpsError): CourseOpsReader(root).media(child.name, "frame-000000")


def test_new_report_exports_policy_visible_range_without_truth(tmp_path):
    from scripts import course_session as runner
    config = {**runner.DEFAULT_CONFIG, "days": 1}
    compiled = runner.serialize_compiled(runner.compile_session({k: config[k] for k in
        ("seed", "days", "staff_count", "initial_stock", "demand_scale")}))
    session = runner.Session(tmp_path, config, compiled)
    session.obs["dispenser_inventory_frac"][0] = .123
    session.info["robots"][0]["battery_frac"] = .876
    session.obs["robot_battery"][0] = .234
    report = session.report("INITIALIZED")
    observed = report["observed_range"]
    assert observed["inventory_fraction"] == pytest.approx(.123)
    assert observed["robots"][0]["battery_fraction"] == pytest.approx(.234)
    assert "ledger" not in json.dumps(observed) and "weather" not in json.dumps(observed)
    assert set(observed) == {"schema", "minute", "inventory_fraction", "zones", "robots", "staff"}


def bound_observation(report, revision):
    from nxt_telemetry.course_condition import build_observation
    frame = report["frames"][0]
    cp = report["catalog"][0]
    return build_observation(site_id="synthetic-course-18", deployment_id="synthetic-course-18-monitoring-v0",
        map_revision=revision, frame_id=frame["frame_id"], checkpoint_id=cp["id"], hole_number=cp["hole_number"],
        feature_id=cp["feature_id"], cart_id=frame["cart_id"], camera_id=frame["cart_id"] + "-camera",
        captured_at_utc="2026-09-17T08:00:00Z", condition="DIVOT", inspected_condition="DIVOT", quality="USABLE",
        cart_position=frame["cart_position"], target_position={"x_m": 130, "y_m": 90, "accuracy_m": 5},
        detection_score=.5, evidence_ref="synthetic:" + frame["image_sha256"])


@pytest.mark.parametrize("field,value", [("checkpoint_id", "cp-h01-bunker"), ("cart_id", "CART-16"),
    ("captured_at_utc", "2026-09-17T09:00:00Z"), ("evidence_ref", "synthetic:" + "0" * 64)])
def test_observation_cannot_relabel_another_frame(saved, field, value):
    from nxt_telemetry.course_condition import build_observation
    root, child, report, revision = saved
    observation = bound_observation(report, revision)
    observation[field] = value
    for key in ("schema", "environment", "source_kind", "score_calibration", "observation_id"):
        observation.pop(key)
    report["observations"] = [build_observation(**observation)]
    write_record(child / "report.json", report)
    with pytest.raises(CourseOpsError): CourseOpsReader(root).snapshot()


def test_bound_observation_and_media_identity(saved):
    root, child, report, revision = saved
    report["observations"] = [bound_observation(report, revision)]
    write_record(child / "report.json", report)
    reader = CourseOpsReader(root)
    snapshot = reader.snapshot()
    assert len(snapshot["observations"]) == 1 and snapshot["course"]["map_revision"] == revision
    assert snapshot["source"]["series_id"] and snapshot["source"]["session_id"]
    frame = snapshot["frames"][0]
    assert frame["image_url"].endswith("?sha256=" + frame["image_sha256"])
    assert reader.media(child.name, frame["frame_id"], frame["image_sha256"])
    with pytest.raises(CourseOpsError) as err: reader.media(child.name, frame["frame_id"], "0" * 64)
    assert err.value.code == "course_ops_not_found"


def test_contract_examples_and_reader_match_json_schema(saved):
    from jsonschema import Draft202012Validator, FormatChecker
    root, _, _, _ = saved
    folder = Path(__file__).resolve().parents[2] / "docs/contracts/course-ops-v1"
    schema = json.loads((folder / "snapshot.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    validator.validate(CourseOpsReader(root).snapshot())
    for path in (folder / "examples").glob("*.json"):
        validator.validate(json.loads(path.read_text())["data"])


@pytest.mark.parametrize("fault", ["future_job", "future_case", "future_task", "map_points"])
def test_future_work_and_changed_map_points_fail_closed(saved, fault):
    root, child, report, revision = saved
    obs = bound_observation(report, revision)
    report["observations"] = [obs]
    case = {"case_id": "case1", "checkpoint_id": obs["checkpoint_id"], "condition_kind": "DIVOT", "priority": "NORMAL",
            "status": "CONFIRMED", "opened_at_utc": "2026-09-17T08:00:00Z", "updated_at_utc": "2026-09-17T08:00:00Z",
            "latest_task_id": None, "observation_ids": [obs["observation_id"]]}
    report["cases"] = [case]
    if fault == "future_case": case["updated_at_utc"] = "2026-09-17T09:00:00Z"
    elif fault == "future_task":
        report["tasks"] = [{"task_id": "task1", "case_id": "case1", "task_kind": "INSPECT", "status": "ASSIGNED",
            "resource": {"id": "slot1", "kind": "HUMAN"}, "assigned_at_utc": "2026-09-17T09:00:00Z",
            "started_at_utc": None, "completed_at_utc": None}]
    elif fault == "future_job":
        report["staff_jobs"] = [{"job_id": "job1", "checkpoint_id": obs["checkpoint_id"], "task_kind": "INSPECT",
            "status": "PENDING", "available_minute": 900, "captured_minute": 480, "observation_id": obs["observation_id"],
            "evidence_ref": obs["evidence_ref"], "assigned_at_s": None, "started_at_s": None, "completed_at_s": None}]
    elif fault == "map_points": report["catalog"][0]["x_m"] += 1
    write_record(child / "report.json", report)
    with pytest.raises(CourseOpsError): CourseOpsReader(root).snapshot()
