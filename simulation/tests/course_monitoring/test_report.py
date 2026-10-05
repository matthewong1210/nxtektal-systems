"""Read-only report projection, escaping, missingness, and offline guarantees."""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "course_monitoring_report.py"
spec = importlib.util.spec_from_file_location("course_monitoring_report", SCRIPT)
assert spec and spec.loader
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def report():
    checkpoints = [
        {"checkpoint_id": f"H{hole:02d}-{kind}", "hole_number": hole,
         "feature_id": f"feature-{hole}-{kind}", "kind": kind,
         "x_m": hole * 20, "y_m": offset * 10}
        for hole in range(1, 19)
        for offset, kind in enumerate(("FAIRWAY", "BUNKER", "GREEN"))
    ]
    return {
        "schema": renderer.SCHEMA, "environment": "SIMULATION",
        "site_id": "course-demo", "deployment_id": "run-01", "map_revision": "map-synthetic-v0",
        "generated_at_utc": "2026-09-17T03:00:00Z", "duration_minutes": 60,
        "disclaimers": ["输入为合成记录。"], "checkpoints": checkpoints,
        "frames": [{"minute": 0, "at_utc": "2026-09-17T02:00:00Z", "carts": [], "coverage": [], "cases": [], "tasks": []},
                   {"minute": 60, "at_utc": "2026-09-17T03:00:00Z", "carts": [{"cart_id": "cart-a", "x_m": 22, "y_m": 8}],
                    "coverage": [{"checkpoint_id": "H01-FAIRWAY", "status": "OBSERVED_CLEAR", "last_observed_at_utc": "2026-09-17T02:59:00Z"},
                                 {"checkpoint_id": "H01-BUNKER", "status": "STALE", "last_observed_at_utc": "2026-09-17T02:10:00Z"}],
                    "cases": [{"case_id": "case-1", "checkpoint_id": "H01-BUNKER", "hole_number": 1, "kind": "UNRAKED", "status": "AWAITING_VERIFICATION", "confirmed_by": "staff-1", "task_id": "task-1"}],
                    "tasks": [{"task_id": "task-1", "case_id": "case-1", "hole_number": 1, "kind": "RAKE_BUNKER", "status": "COMPLETED", "resource_kind": "HUMAN", "assigned_to": "crew-a", "completed_at_utc": "2026-09-17T02:50:00Z"}]}],
        "summary": {"coverage": {}, "observations": 2, "issues": 1, "tasks": 1},
    }


def test_18_holes_54_point_denominator_and_unknown_are_explicit():
    html = renderer.render_report(report())
    assert "18 个球洞 · 54 个预设观察点" in html
    assert 'id="count-UNOBSERVED">52<' in html
    assert 'id="count-STALE">1<' in html
    assert 'id="count-OBSERVED_CLEAR">1<' in html
    assert "不是球场面积" in html
    assert all(f">第 {hole:02d} 洞</option>" in html for hole in range(1, 19))
    assert "没有真实图像、真实视觉识别" in html
    assert "不是实测地图、行驶路线或导航指令" in html


def test_timestamps_utc_and_review_distinct_from_completion():
    html = renderer.render_report(report())
    markdown = renderer.render_markdown(report())
    for output in (html, markdown):
        assert "2026-09-17T03:00:00Z" in output
        assert "2026-09-17T02:59:00Z" in output
        assert "2026-09-17T02:50:00Z" in output
        assert "等待复查" in output
        assert "未提供" in output
    assert "作业完成不等于复查通过" in html
    assert "复查时间 UTC" in markdown


def test_empty_report_does_not_invent_holes_coverage_or_findings():
    source = report()
    source.update(checkpoints=[], frames=[], cases=[], tasks=[], summary={})
    html = renderer.render_report(source)
    markdown = renderer.render_markdown(source)
    assert "0 个球洞 · 0 个预设观察点" in html
    assert "没有问题证据" in html
    assert "没有维护任务记录" in html
    assert "没有回放帧" in html
    assert "没有观察点资料；无法判断覆盖情况" in markdown
    assert 'aria-pressed="false" disabled' in html


def test_carts_do_not_create_coverage_without_observation_evidence():
    source = report()
    source["frames"][-1]["coverage"] = []
    source["frames"][-1]["carts"] = [{"cart_id": "cart-a", "x_m": p["x_m"], "y_m": p["y_m"]} for p in source["checkpoints"]]
    html = renderer.render_report(source)
    assert 'id="count-UNOBSERVED">54<' in html
    assert 'id="count-OBSERVED_CLEAR">0<' in html


def test_scripts_html_and_attribute_injections_are_inert_and_json_roundtrips():
    attack = '</script><img src=x onerror="alert(1)"><script>alert(2)</script>&\u2028\u2029'
    source = report()
    source["site_id"] = attack
    source["disclaimers"] = [attack]
    source["frames"][-1]["cases"][0]["detail"] = attack
    html = renderer.render_report(source)
    assert attack not in html
    assert "&lt;/script&gt;&lt;img" in html
    payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)
    assert payload
    assert "<" not in payload[1] and "&" not in payload[1]
    assert "\u2028" not in payload[1] and "\u2029" not in payload[1]
    assert json.loads(payload[1]) == source
    assert html.count("<script") == 3
    assert "innerHTML" not in html
    assert "textContent" in html


def test_markdown_does_not_enable_html_links_or_split_rows():
    source = report()
    source["frames"][-1]["cases"][0]["detail"] = '<img src=x onerror=bad> [visit](javascript:evil) | bad\nnext'
    output = renderer.render_markdown(source)
    assert "<img" not in output
    assert r"\[visit\]" in output
    assert r"\| bad next" in output


def test_rendering_is_deterministic_and_leaves_input_unchanged():
    source = report()
    original = copy.deepcopy(source)
    assert renderer.render_report(source) == renderer.render_report(source)
    assert renderer.render_markdown(source) == renderer.render_markdown(source)
    assert source == original


def test_report_has_only_offline_readonly_controls_and_csp():
    html = renderer.render_report(report())
    assert "script-src &#x27;sha256-" in html
    assert "connect-src &#x27;none&#x27;" in html
    for forbidden in ("fetch(", "XMLHttpRequest", "WebSocket", "localStorage", "sessionStorage", "<iframe", '<img ', 'method="POST"', 'src="http', 'href="http'):
        assert forbidden not in html
    assert 'id="timeline" type="range"' in html
    assert 'id="hole-filter"' in html and 'id="status-filter"' in html
    assert "setInterval" in html
    assert "不确认问题、不派工" in html


@pytest.mark.parametrize("patch", [{"schema": "foreign/v1"}, {"environment": "LIVE"}, {"frames": {}}, {"checkpoints": {}}])
def test_refuses_unsupported_report_boundaries(patch):
    source = report()
    source.update(patch)
    with pytest.raises(ValueError):
        renderer.render_report(source)
    with pytest.raises(ValueError):
        renderer.render_markdown(source)


def test_rejects_nonfinite_coordinates_instead_of_invalid_embedded_json():
    source = report()
    source["checkpoints"][0]["x_m"] = float("nan")
    with pytest.raises(ValueError):
        renderer.render_report(source)


def test_grounds_workflow_projection_fields_and_joined_task_location():
    source = report()
    source["frames"][-1]["cases"][0].update(condition_kind="DIVOT", latest_task_id="task-2", review_reason="scripted inspection")
    source["frames"][-1]["tasks"] = [{"task_id": "task-2", "case_id": "case-1", "resource": {"id": "script-crew", "kind": "HUMAN"}, "task_kind": "REPAIR_DIVOT", "status": "ASSIGNED", "assigned_at_utc": "2026-09-17T02:40:00Z"}]
    output = renderer.render_markdown(source)
    assert "疑似打痕" in output
    assert "script-crew" in output
    assert "脚本人员" in output
    assert "scripted inspection" in output
    assert "| task-2 | case-1 | 1 |" in output
    assert "2026-09-17T02:40:00Z" in output
    assert "不是现场人员或真实设备" in output
