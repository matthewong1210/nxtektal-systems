"""Planning confirmation drives actual V3 ledger collection and unload."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

import pytest

from nxt_edge_task.cases import TASK_CREATED
from nxt_edge_task.contracts import parse_utc
from scripts import course_session_v3


WALL = datetime(2035, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class WallClock:
    def __init__(self, value=WALL):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def demo_api():
    from scripts import course_collection_execution_demo

    return course_collection_execution_demo


def _run(root, demo_api):
    runtime = demo_api.CourseCollectionExecutionDemo(
        root, initialize=True, wall_clock=WallClock()
    )
    try:
        result = runtime.run(advance=2)
        duplicate = runtime.ensure_execution()
        return result, duplicate, runtime.evidence()
    finally:
        runtime.close()


def test_fresh_root_planning_to_actual_collection_and_unload_is_reproducible(
    tmp_path, demo_api
):
    first, duplicate, first_evidence = _run(tmp_path / "first", demo_api)
    second, _, second_evidence = _run(tmp_path / "second", demo_api)

    snapshot = first["collection_executions"]
    execution = snapshot["executions"][0]
    planning = first["planning"]
    trace = first_evidence["assignment"]
    replay = first_evidence["replay_plan"]
    created = [
        row for row in first_evidence["edge_records"]
        if row["record_kind"] == TASK_CREATED
    ]

    assert len(created) == 1
    warmup = replay[0]["prepared"]["decision"]
    assert warmup["sim_t_s"] == 28800
    assert warmup["original_action"]["name"] == "Wait"
    assert warmup["selected_action"] == warmup["original_action"]
    assert warmup["selection"] == "ORIGINAL_POLICY_UNCHANGED"
    assert [tick["session"]["now_sim_t_s"] for tick in first["ticks"]] == [
        30000,
        30600,
    ]
    assert all(tick["delivered"] for tick in first["ticks"])
    assert snapshot["now_sim_t_s"] == 30600
    assert duplicate == first["request_receipt"]
    assert len(snapshot["requests"]) == len(snapshot["receipts"]) == 1
    assert execution["state"] == "SUCCEEDED"
    assert execution["reason"] == "UNLOADED_ALL_COLLECTED_BALLS"
    assert execution["policy_id"] == "JointDispatchPolicy-v1"
    assert execution["max_execution_s"] == 1200
    assert execution["started_sim_t_s"] == 29400
    assert execution["execution_deadline_sim_t_s"] == 30600
    assert 30000 < execution["terminal_sim_t_s"] <= 30600
    assert execution["runtime_evidence"]["collection_exit_reason"] == "ROBOT_PAYLOAD_FULL"
    assert execution["runtime_evidence"]["conservation_passed"] is True
    assert execution["runtime_evidence"]["payload_parity_passed"] is True
    assert execution["edge_evidence"]["effective_state"] == "SUCCEEDED"
    assert execution["edge_evidence"]["result_verification"] == "VERIFIED"
    assert execution["success_display_allowed"] is True
    assert execution["device_protection"] == {
        "protected": False,
        "reasons": [],
        "authorization_blocked": False,
    }

    raw = execution["raw_quantity"]
    unloaded = execution["unload_quantity"]
    assert raw["source"] == unloaded["source"] == "RANGE_SIMULATION_BALL_LEDGER"
    assert raw["status"] == unloaded["status"] == "COMPLETE"
    assert raw["balls"] == unloaded["balls"] == 600
    assert raw["balls"] > 0
    assert raw["assignment_id"] == unloaded["assignment_id"] == trace["assignment_id"]
    assert raw["event_digest"] == unloaded["event_digest"] == trace["event_digest"]
    assert raw["source_event_ids"] == [
        event["event_id"] for event in trace["events"]
        if event["kind"] == "RAW_COLLECTED_TO_ROBOT"
    ]
    assert unloaded["source_event_ids"] == [
        event["event_id"] for event in trace["events"]
        if event["kind"] == "UNLOADED_TO_STATION"
    ]
    raw_moves = [event for event in trace["events"] if event["kind"] == "RAW_COLLECTED_TO_ROBOT"]
    unload_moves = [event for event in trace["events"] if event["kind"] == "UNLOADED_TO_STATION"]
    assert sum(event["balls"] for event in raw_moves) == 600
    assert sum(event["balls"] for event in unload_moves) == 600
    assert {event["source_location"] for event in raw_moves} == {"zone:NEAR_LEFT"}
    assert {event["destination_location"] for event in raw_moves} == {"robot:R1"}
    assert {event["source_location"] for event in unload_moves} == {"robot:R1"}
    assert {event["destination_location"] for event in unload_moves} == {"station:H1"}

    assert [(row["sim_t_s"], row["original_action"]["name"],
             row["selected_action"]["name"], row["selection"])
            for row in execution["actions"]] == [
        (29400, "Wait", "AssignCollection", "WAIT_SLOT"),
        (30000, "SendToHandoff", "SendToHandoff", "ORIGINAL_POLICY_CONVERGED"),
    ]
    assert execution["actions"][0]["selected_action"] == {
        "name": "AssignCollection", "index": 8,
        "robot_id": "R1", "target_id": "NEAR_LEFT",
    }
    assert execution["actions"][1]["original_action"] == {
        "name": "SendToHandoff", "index": 28,
        "robot_id": "R1", "target_id": None,
    }
    assert execution["actions"][1]["selected_action"] == execution["actions"][1]["original_action"]

    assert planning["outcomes"] == []
    assert planning["latest_input"]["inventory_clean_balls"]["value"] == 600
    assert all("washed" not in event["kind"].lower() and "supplied" not in event["kind"].lower()
               for event in trace["events"])
    assert first_evidence["planning_record_kinds"].count("planning_confirmation_recorded") == 1
    assert first_evidence["planning_record_kinds"].count("planning_outcome_recorded") == 0
    assert first_evidence["edge_task_state"] == "SUCCEEDED"
    assert len(execution["edge_evidence"]["event_ids"]) >= 6
    assert "mock_robot_task_device" not in inspect.getsource(demo_api).lower()

    stable = lambda value: {
        "request_id": value["request_receipt"]["request_id"],
        "execution_id": value["collection_executions"]["executions"][0]["execution_id"],
        "assignment_id": value["collection_executions"]["executions"][0]["assignment_id"],
        "replay_digest": value["collection_executions"]["replay_digest"],
        "event_digest": value["collection_executions"]["executions"][0]["runtime_evidence"]["event_digest"],
        "edge_event_ids": value["collection_executions"]["executions"][0]["edge_evidence"]["event_ids"],
    }
    assert stable(first) == stable(second)
    assert first_evidence["assignment"] == second_evidence["assignment"]


def test_schedule_form_uses_simulation_date_while_service_read_uses_wall_health(
    tmp_path, demo_api
):
    wall = WallClock()
    runtime = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=wall
    )
    try:
        runtime.start()
        status = runtime.runtime_status()
        assert status["simulation_time_utc"] == "2026-09-16T08:10:00Z"
        created = runtime.create_schedule(
            {
                "robot_id": "picker-01",
                "zone_id": "Z1",
                "due_at_utc": "2026-09-16T08:11:00Z",
                "expires_at_utc": "2026-09-16T08:12:00Z",
                "operator": "simulation-form-test",
            }
        )
        assert created["status"] == "created"
        read = runtime.task_operations_snapshot()
        assert parse_utc(read["server_time_utc"]) == WALL
        assert read["scheduler"] == {"state": "RUNNING", "detail": None}
        assert read["runtime"]["session_state"] == "ACTIVE"

        wall.advance(16)
        course_session_v3.set_paused(runtime.session_root, True)
        paused = runtime.task_operations_snapshot()
        assert parse_utc(paused["server_time_utc"]) == WALL + timedelta(seconds=16)
        assert paused["scheduler"] == {"state": "RUNNING", "detail": None}
        assert paused["runtime"]["session_state"] == "PAUSED"
        assert paused["runtime"]["simulation_time_utc"] == status["simulation_time_utc"]
    finally:
        runtime.close()


def test_completed_root_restart_replays_without_a_second_task_or_transfer(
    tmp_path, demo_api
):
    first = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=True, wall_clock=WallClock()
    )
    try:
        before = first.run(advance=2)["collection_executions"]
        assignment = first.evidence()["assignment"]
    finally:
        first.close()

    resumed = demo_api.CourseCollectionExecutionDemo(
        tmp_path, initialize=False, wall_clock=WallClock()
    )
    try:
        after = resumed.run(advance=0)["collection_executions"]
        evidence = resumed.evidence()
        assert after["replay_digest"] == before["replay_digest"]
        assert after["executions"] == before["executions"]
        assert evidence["assignment"] == assignment
        assert sum(
            row["record_kind"] == TASK_CREATED
            for row in evidence["edge_records"]
        ) == 1
    finally:
        resumed.close()
