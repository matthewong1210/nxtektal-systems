"""Service-state file behavior: launch record, cursor, and events."""

from __future__ import annotations

import pytest

from nxt_site_agent import ServiceStorage, SiteAgentError, SourceCursor


@pytest.fixture()
def storage(tmp_path):
    return ServiceStorage(
        tmp_path / "run-001",
        site_id="pilot-course-a",
        deployment_id="pilot-a-site-agent-v0",
        workflow_id="range.closed_loop_collection_handoff",
    )


def test_cursor_round_trip(storage):
    cursor = SourceCursor(consumed_cycles=4, next_sequence_number=2)
    storage.write_cursor(cursor)
    assert storage.read_cursor() == cursor


def test_cursor_identity_mismatch_fails_closed(storage):
    storage.write_cursor(SourceCursor(0, 0))
    text = storage.cursor_path.read_text(encoding="utf-8")
    storage.cursor_path.write_text(
        text.replace("pilot-a-site-agent-v0", "another-deployment"),
        encoding="utf-8",
    )
    with pytest.raises(SiteAgentError) as excinfo:
        storage.read_cursor()
    assert excinfo.value.code == "service_state_identity_mismatch"


def test_cursor_foreign_schema_fails_closed(storage):
    storage.write_cursor(SourceCursor(0, 0))
    text = storage.cursor_path.read_text(encoding="utf-8")
    storage.cursor_path.write_text(
        text.replace("nxt-site-agent/service-state/v0", "foreign/v9"),
        encoding="utf-8",
    )
    with pytest.raises(SiteAgentError) as excinfo:
        storage.read_cursor()
    assert excinfo.value.code == "service_state_invalid"


def test_negative_cursor_values_are_invalid():
    with pytest.raises(SiteAgentError):
        SourceCursor(consumed_cycles=-1, next_sequence_number=0)
    with pytest.raises(SiteAgentError):
        SourceCursor(consumed_cycles=0, next_sequence_number=True)


def test_launch_record_round_trip_and_validation(storage):
    storage.write_launch_record(
        report_id="wer_abc", plan_payload={"workflow_id": "x"}
    )
    record = storage.read_launch_record()
    assert record["report_id"] == "wer_abc"
    assert record["plan"] == {"workflow_id": "x"}
    assert record["disclaimer"].startswith("SIMULATED")
    text = storage.launch_record_path.read_text(encoding="utf-8")
    storage.launch_record_path.write_text(
        text.replace('"launch"', '"other"'), encoding="utf-8"
    )
    with pytest.raises(SiteAgentError):
        storage.read_launch_record()


def test_workflow_root_emptiness_observations(storage):
    assert storage.workflow_root_is_empty() is True
    storage.workflow_evidence_root.mkdir(parents=True)
    assert storage.workflow_root_is_empty() is True
    (storage.workflow_evidence_root / "ledger.jsonl").write_text(
        "x\n", encoding="utf-8"
    )
    assert storage.workflow_root_is_empty() is False


def test_file_valued_workflow_root_is_a_collision(tmp_path):
    storage = ServiceStorage(
        tmp_path / "run-001",
        site_id="pilot-course-a",
        deployment_id="pilot-a-site-agent-v0",
        workflow_id="range.closed_loop_collection_handoff",
    )
    storage.identity_root.mkdir(parents=True)
    storage.workflow_evidence_root.write_text("not a dir", encoding="utf-8")
    assert storage.workflow_root_is_empty() is False


def test_events_append_and_torn_line_tolerance(storage):
    assert storage.append_event({"event": "cycle", "outcome": "evaluated"})
    assert storage.append_event({"event": "stopped"})
    with storage.events_path.open("a", encoding="utf-8") as handle:
        handle.write('{"schema": "nxt-site-agent/service-events/v0", "ev')
    events = storage.read_events()
    assert [item["event"] for item in events] == ["cycle", "stopped"]


def test_events_read_ignores_foreign_lines(storage):
    storage.service_dir.mkdir(parents=True)
    storage.events_path.write_text(
        '{"schema": "foreign", "event": "x"}\n'
        '{"schema": "nxt-site-agent/service-events/v0", "event": "cycle"}\n',
        encoding="utf-8",
    )
    events = storage.read_events()
    assert [item["event"] for item in events] == ["cycle"]
