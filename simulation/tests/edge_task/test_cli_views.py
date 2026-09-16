"""Read-time views must state evidence age, never re-stamp journaled state as current (Codex R1 #6)."""

from __future__ import annotations

from datetime import timedelta

from scripts.edge_task_cli import edge_lock_held, list_view, show_view
from tests.edge_task.conftest import Harness, run_until


def test_list_view_reports_journaled_state_and_read_time_freshness_separately(harness: Harness) -> None:
    harness.start_all()
    harness.step(3)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    assert harness.device("picker-01")["connectivity"] == "ONLINE"
    last_record_at = harness.edge_records()[-1].recorded_at_utc
    # An hour passes with no Edge process ticking: the journal cannot know the device went away.
    harness.clock.advance(3600)
    view = list_view(harness.edge_journal(), harness.config, now=harness.clock())
    assert view["read_at_utc"] == harness.clock().isoformat(timespec="microseconds").replace("+00:00", "Z")
    assert view["journal_last_record_at_utc"] == last_record_at
    assert view["journal_age_s"] >= 3600
    assert view["edge_lock_held"] is False
    picker = view["devices"]["picker-01"]
    assert picker["connectivity"] == "ONLINE"  # what the journal last derived
    assert picker["as_read"]["connectivity"] == "OFFLINE"  # what can be verified now
    assert picker["as_read"]["status_age_s"] >= 3600
    assert picker["as_read"]["basis"] == "journal_receipt_clock"
    # Within the stale window the two agree.
    fresh = list_view(harness.edge_journal(), harness.config, now=harness.clock() - timedelta(seconds=3599))
    assert fresh["devices"]["picker-01"]["as_read"]["connectivity"] == "ONLINE"


def test_show_view_carries_the_same_freshness_fields(harness: Harness) -> None:
    harness.start_all()
    harness.step(3)
    task_id = harness.create()["task_id"]
    run_until(harness, lambda: harness.task(task_id)["state"] == "SUCCEEDED")
    harness.clock.advance(20)
    shown = show_view(harness.edge_journal(), harness.config, task_id, now=harness.clock())
    assert shown["state"] == "SUCCEEDED" and shown["journal_age_s"] >= 20
    assert shown["target_device_as_read"]["connectivity"] == "OFFLINE"
    assert shown["read_at_utc"].endswith("Z")


def test_unknown_device_before_any_status_reads_unknown_not_offline(harness: Harness) -> None:
    harness.start_edge()
    view = list_view(harness.edge_journal(), harness.config, now=harness.clock() + timedelta(seconds=5))
    assert view["devices"]["picker-01"]["as_read"] == {"connectivity": "UNKNOWN", "status_age_s": None, "basis": "journal_receipt_clock"}


def test_edge_lock_probe_is_read_only(tmp_path) -> None:
    import fcntl

    journal_file = tmp_path / "edge_task_journal.jsonl"
    assert edge_lock_held(journal_file) is False
    assert not (journal_file.parent / ".edge.lock").exists()  # probing never creates the lock file
    lock = (journal_file.parent / ".edge.lock").open("a+b")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert edge_lock_held(journal_file) is True
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    assert edge_lock_held(journal_file) is False


def test_reader_clock_behind_the_journal_reads_unknown(harness: Harness) -> None:
    harness.start_all()
    harness.step(3)
    view = list_view(harness.edge_journal(), harness.config, now=harness.clock() - timedelta(seconds=30))
    picker = view["devices"]["picker-01"]["as_read"]
    assert picker["connectivity"] == "UNKNOWN" and picker["status_age_s"] < 0


def test_read_views_create_no_directories(tmp_path) -> None:
    from nxt_edge_task.cases import EDGE_RECORD_KINDS
    from nxt_edge_task.journal import JsonlJournal
    from tests.edge_task.conftest import T0, load_config

    config = load_config()
    journal = JsonlJournal(tmp_path / "never" / "edge_task_journal.jsonl", allowed_kinds=EDGE_RECORD_KINDS)
    assert list_view(journal, config, now=T0)["devices"]["picker-01"]["as_read"]["connectivity"] == "UNKNOWN"
    assert not (tmp_path / "never").exists()


def test_cli_reports_a_rolled_back_journal_as_a_machine_readable_error(tmp_path, capsys) -> None:
    from nxt_edge_task.cases import EDGE_RECORD_KINDS, edge_started_spec
    from nxt_edge_task.journal import JsonlJournal
    from scripts.edge_task_cli import journal_path, main
    from scripts.pilot_course_a_task_fixture import admission_facts, commissioned_site
    from tests.edge_task.conftest import CONFIG_PATH, T0, Harness, load_config

    config = load_config()
    facts = admission_facts(commissioned_site())
    path = journal_path(config, tmp_path)
    journal = JsonlJournal(path, allowed_kinds=EDGE_RECORD_KINDS)
    for _ in range(3):
        journal.append(edge_started_spec(config, facts, T0))
    Harness._tear(path, 1, keep_anchor=True)  # a valid one-record prefix below the anchor
    code = main(["--config", str(CONFIG_PATH), "--evidence-root", str(tmp_path), "list"])
    captured = capsys.readouterr()
    assert code == 3
    assert '"error": "journal_integrity"' in captured.err and "rolled back" in captured.err


def test_task_creation_needs_a_seen_incarnation_and_refuses_a_foreign_one(harness: Harness) -> None:
    from nxt_edge_task.cases import decide_task_create
    from nxt_edge_task.contracts import TaskRequest

    harness.start_edge()  # no robot has ever reported
    refused = harness.create()
    assert refused["status"] == "rejected" and refused["code"] == "device_incarnation_unknown" and refused["task_id"] is None
    rejected = [r for r in harness.edge_records() if r.record_kind == "task_create_rejected"]
    assert rejected and rejected[-1].payload["code"] == "device_incarnation_unknown" and rejected[-1].payload["request"] is None
    harness.start_robot("picker-01", initialize=True)
    harness.start_robot("carrier-01", initialize=True)
    harness.step(2)
    foreign = TaskRequest.build(
        site_id=harness.config.site_id, deployment_id=harness.config.deployment_id, simulation_env_id=harness.config.simulation_env_id,
        target_robot_id="picker-01", target_incarnation="boot-picker-01-deadbeef0000", task_type="COLLECT_BALLS_ZONE", zone_id="Z1",
        issued_at_utc="2026-09-12T08:00:00.000000Z", expires_at_utc="2026-09-12T08:10:00.000000Z", progress_window_s=20, issued_by="SIMULATION_TEST_ENTRY:test",
    )
    harness._refresh_edge()
    outcome, specs = decide_task_create(harness.edge.core.view, harness.facts, foreign, harness.clock())
    assert outcome.status == "rejected" and outcome.code == "incarnation_mismatch" and specs[0].record_kind == "task_create_rejected"
    assert harness.create()["status"] == "created"  # the CLI binds to the seen incarnation
