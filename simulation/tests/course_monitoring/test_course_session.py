"""Image -> evidence -> guarded shared staff -> later recapture, plus replay."""
from collections import Counter
from copy import deepcopy
import fcntl

import pytest

from scripts import course_session as runner
from scripts.course_camera import render_frame
from scripts.joint_learning import atomic_json, read_record


@pytest.fixture
def controlled():
    config = {**runner.DEFAULT_CONFIG, "days": 1, "demand_scale": 0, "advance_minutes": 40}
    compiled = runner.serialize_compiled(runner.compile_session({k: config[k] for k in
        ("seed", "days", "staff_count", "initial_stock", "demand_scale")}))
    cp = next(row for row in compiled["catalog"] if row["surface_type"] == "bunker")
    x,y = cp["x_m"],cp["y_m"]
    compiled["course_events"] = [{"checkpoint_id": cp["id"], "condition": "BUNKER_SURFACE",
        "start_minute": 480, "end_minute": 1080, "x_m": x, "y_m": y, "radius_m": 1}]
    compiled["camera_schedule"] = [dict(minute=m, cart_id="CART-01", checkpoint_id=cp["id"],
        cart_x_m=x, cart_y_m=y-14, position_accuracy_m=.1, camera_online=True,
        camera=dict(x_m=x,y_m=y-14,z_m=5,target_x_m=x,target_y_m=y)) for m in (480,510,550)]
    return config, compiled, cp


def test_actual_pixels_create_work_and_later_reinspection_verifies(tmp_path, controlled):
    config, compiled, cp = controlled
    session = runner.Session(tmp_path, config, compiled)
    for _ in range(25): session.advance()
    report = session.report("TEST")
    assert [case["status"] for case in report["cases"]] == ["AWAITING_VERIFICATION"]
    assert report["staff_jobs"][0]["status"] == "COMPLETED"
    for _ in range(15): session.advance()
    report = session.report("TEST")
    assert [case["status"] for case in report["cases"]] == ["VERIFIED"]
    assert [row["condition"] for row in report["observations"]] == ["BUNKER_SURFACE", "CLEAR"]
    assert report["observations"][0]["frame_id"] != report["observations"][1]["frame_id"]
    assert (tmp_path/report["frames"][0]["image"]).read_bytes().startswith(b"\x89PNG")
    assert sum(report["summary"]["ledger"].values()) == session.env.sim.ledger.total


def test_missing_detection_does_not_verify_wrong_region(tmp_path, controlled):
    config, compiled, cp = controlled
    # First image sees the defect; later usable images look outside its area.
    for row in compiled["camera_schedule"][1:]:
        row["camera"]["target_x_m"] += 40
    session = runner.Session(tmp_path, config, compiled)
    for _ in range(80): session.advance()
    assert [case["status"] for case in session.report("TEST")["cases"]] == ["AWAITING_VERIFICATION"]
    assert not any(row["condition"] == "CLEAR" for row in session.observations)
    assert any(row["kind"] == "REINSPECTION_INSUFFICIENT_COVERAGE" for row in session.alerts)


def test_clean_pixels_ignore_hidden_defects_and_unobserved_points_stay_unknown(tmp_path, controlled, monkeypatch):
    config, compiled, cp = controlled
    original = runner.render_frame
    def clean(scene, camera, seed, **kwargs):
        return original({**scene,"defects":[]},camera,seed,**kwargs)
    monkeypatch.setattr(runner,"render_frame",clean)
    session = runner.Session(tmp_path, config, compiled)
    session.advance()
    report = session.report("TEST")
    assert not report["cases"] and not report["observations"] and not report["staff_jobs"]
    assert sum(row["status"] == "UNOBSERVED" for row in report["coverage"]) == 53


@pytest.mark.parametrize("mode",["offline","blurred","occluded"])
def test_unusable_camera_cannot_create_condition(tmp_path, controlled, mode):
    config, compiled, cp = controlled
    compiled["camera_schedule"] = compiled["camera_schedule"][:1]
    row = compiled["camera_schedule"][0]
    if mode == "offline": row["camera_online"] = False
    elif mode == "blurred": row["camera"]["motion_blur_px"] = 20
    else: row["camera"]["lens_occlusion"] = .7
    session = runner.Session(tmp_path, config, compiled)
    session.advance()
    assert not session.observations
    assert all(job["task_kind"] == "REPHOTOGRAPH" for job in session.env.sim.staff_work_snapshots())


def test_saved_prefix_replay_and_tampered_png(tmp_path, controlled):
    config, compiled, cp = controlled
    first = runner.Session(tmp_path, config, compiled)
    for _ in range(40): first.advance()
    second = runner.Session(tmp_path, config, compiled)
    for _ in range(40): second.advance()
    assert second.replay_digest() == first.replay_digest()
    image = tmp_path/first.frames[0]["image"]
    image.write_bytes(image.read_bytes()+b"tampered")
    with pytest.raises(ValueError,match="identity"):
        runner.Session(tmp_path,config,compiled).advance()


def test_served_shots_match_ledger_event_locations(tmp_path):
    config = {**runner.DEFAULT_CONFIG,"days":1,"initial_stock":20}
    compiled = runner.serialize_compiled(runner.compile_session({k:config[k] for k in
        ("seed","days","staff_count","initial_stock","demand_scale")}))
    compiled["camera_schedule"] = []
    session = runner.Session(tmp_path,config,compiled)
    for _ in range(100): session.advance()
    counts = Counter()
    for event in session.env.sim.events.to_dicts():
        if event["kind"] == "demand_served": counts.update(event["payload"]["by_zone"])
    assert Counter(row["zone_id"] for row in session.actual_shots) == counts
    assert len(session.actual_shots) == session.env.sim.metrics.demand_balls_served
    assert len(session.actual_shots) < sum(row["requested_balls"] for row in compiled["traffic"])


def test_small_realistic_divot_does_not_crash_renderer():
    result = render_frame(dict(x_m=0,y_m=0,surface_type="FAIRWAY",defects=[
        dict(condition="DIVOT",x_m=0,y_m=0,radius_m=.04)]),
        dict(x_m=0,y_m=-14,z_m=2,target_x_m=0,target_y_m=0),42)
    assert result["png_bytes"].startswith(b"\x89PNG")


def test_cursor_continuation_pause_and_publication_retry(tmp_path, controlled, monkeypatch):
    from scripts import course_session_report
    config, compiled, cp = controlled
    # Scenario compilation and engine hashing are separate tested seams.
    compiled["scenario"] = runner.RangeOpsScenario.model_validate(compiled["scenario"])
    monkeypatch.setattr(runner,"compile_session",lambda config:deepcopy(compiled))
    monkeypatch.setattr(runner,"engine_fingerprint",lambda:"test-engine")
    initial = runner.run(tmp_path,config)
    assert initial["minute"] == 520
    atomic_json(tmp_path/"control.json",{"paused":True})
    assert runner.run(tmp_path)["status"] == "PAUSED"
    assert read_record(tmp_path/"state.json")["step"] == initial["step"]
    atomic_json(tmp_path/"control.json",{"paused":False})
    render = course_session_report.render_report
    monkeypatch.setattr(course_session_report,"render_report",lambda report: (_ for _ in ()).throw(OSError("disk write failed")))
    with pytest.raises(OSError): runner.run(tmp_path)
    assert read_record(tmp_path/"state.json")["step"] == initial["step"]
    monkeypatch.setattr(course_session_report,"render_report",render)
    resumed = runner.run(tmp_path)
    assert resumed["minute"] == 560
    assert (tmp_path/"report.html").is_file()
    with pytest.raises(ValueError,match="immutable"):
        runner.run(tmp_path,{**config,"staff_count":1})
    with (tmp_path/".session.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(ValueError,match="another worker"):
            runner.run(tmp_path)


def test_scene_completion_does_not_drain_standing_water(controlled):
    config,compiled,cp = controlled
    compiled["course_events"][0]["condition"] = "STANDING_WATER"
    jobs=[dict(checkpoint_id=cp["id"],task_kind="INSPECT",status="COMPLETED",completed_at_s=500*60)]
    scene,_=runner.scene_at(compiled,cp,510,jobs)
    assert scene["defects"][0]["condition"] == "STANDING_WATER"
