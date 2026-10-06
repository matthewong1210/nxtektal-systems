"""The composition root: journal round trip, idempotent launches, fail-closed reads."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import nxt_operational_context as oc
from nxt_edge_task.journal import JournalIntegrityError
from scripts.operational_context_fixture import (
    FIXTURE_AS_OF,
    FIXTURE_DIR,
    FIXTURE_RECEIVED_AT,
    ContextReader,
    admission_from_manifest,
    context_journal,
    fixture_context_for,
    import_file,
    import_fixture_set,
    load_profiles,
)
from scripts.site_agent_fixture import service_manifest_payload


def _admission() -> oc.ContextAdmissionFacts:
    return admission_from_manifest(service_manifest_payload())


def test_admission_is_projected_from_the_commissioned_manifest() -> None:
    admission = _admission()
    assert admission.site_id == "pilot-course-a"
    assert admission.site_timezone == "Asia/Shanghai"
    assert admission.manifest_digest.startswith("sha256:")


def test_fixture_imports_persist_through_the_journal_and_project_identically(tmp_path: Path) -> None:
    admission = _admission()
    journal = context_journal(tmp_path / "context")
    decisions = import_fixture_set(journal, admission)
    assert [d.outcome for d in decisions] == ["committed"] * 3
    records = journal.read()
    assert {r.schema_version for r in records} == {"nxt-operational-context/journal/v1"}
    assert {r.origin for r in records} == {"ADAPTER"}
    assert records[-1].record_kind == "import_batch_committed"
    profiles, _ = load_profiles()
    reader = ContextReader(journal=journal, admission=admission, profiles=profiles)
    assert reader.verify()["status"] == "available"
    one = reader.snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")
    two = ContextReader(journal=context_journal(tmp_path / "context"), admission=admission, profiles=profiles).snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")
    assert one["status"] == "available" and one["context"]["staffing"]["status"] == "ok"
    assert oc.canonical_json(one) == oc.canonical_json(two)
    assert one["context"]["sources"]["sales"]["coverage_basis"] == "declared_export_time"
    assert one["context"]["staffing"]["scheduled_now"]["value"] == 3
    assert one["context"]["operations"]["sales"]["ball_units_sold_today"]["value"] == 2450


def test_relaunching_re_imports_idempotently_and_records_duplicate_batches(tmp_path: Path) -> None:
    admission = _admission()
    factory = fixture_context_for(admission)
    first = factory(tmp_path / "run-001")
    before = first.snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")
    event_records = len([r for r in first.journal.read() if r.record_kind == "event_recorded"])
    second = factory(tmp_path / "run-001")  # a resume or restart re-runs the same imports
    after = second.snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")
    assert len([r for r in second.journal.read() if r.record_kind == "event_recorded"]) == event_records
    assert len([r for r in second.journal.read() if r.record_kind == "import_batch_duplicate"]) == 3
    for section in ("staffing", "operations"):
        assert oc.canonical_json(after["context"][section]) == oc.canonical_json(before["context"][section])
    assert after["context"]["sources"]["staffing"]["duplicate_batches"] == 1


def test_a_rejected_fixture_file_is_journaled_safely_and_publishes_nothing(tmp_path: Path) -> None:
    admission = _admission()
    profiles, exported = load_profiles()
    journal = context_journal(tmp_path / "context")
    decision = import_file(
        journal,
        admission=admission,
        profile=profiles[oc.SourceSystem.STAFFING],
        path=FIXTURE_DIR / "rejected" / "staffing_forbidden_header.csv",
        received_at=FIXTURE_RECEIVED_AT,
        exported_at=exported[oc.SourceSystem.STAFFING],
    )
    assert decision.outcome == "rejected"
    records = journal.read()
    assert [r.record_kind for r in records] == ["import_batch_rejected"]
    assert "REDACTED" not in oc.canonical_json(records[0].to_dict())
    reader = ContextReader(journal=journal, admission=admission, profiles=profiles)
    snap = reader.snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")["context"]
    assert snap["staffing"]["status"] == "missing"
    assert snap["sources"]["staffing"]["rejected_batches"] == 1
    assert snap["sources"]["staffing"]["last_rejection"]["errors"] == [{"row_number": None, "column": "employee_name", "reason": "forbidden_column"}]


def test_a_torn_journal_tail_fails_closed_as_unavailable(tmp_path: Path) -> None:
    admission = _admission()
    journal = context_journal(tmp_path / "context")
    import_fixture_set(journal, admission)
    path = journal.path
    data = path.read_bytes()
    path.write_bytes(data[:-7])  # byte-torn last record
    profiles, _ = load_profiles()
    reader = ContextReader(journal=context_journal(tmp_path / "context"), admission=admission, profiles=profiles)
    verification = reader.verify()
    assert verification == {"status": "unavailable", "code": "journal_unreadable", "detail": verification["detail"], "records": 0}
    snap = reader.snapshot(FIXTURE_AS_OF, "FIXTURE_DECLARED")
    assert snap["status"] == "unavailable" and snap["code"] == "journal_unreadable"
    assert snap["context"] is None  # nothing partial is presented
    # Rolling the file back below its anchor is state loss and also fails closed.
    path.write_bytes(data[: data.index(b"\n") + 1])
    assert reader.verify()["status"] == "unavailable"


def test_the_leaf_never_touched_the_filesystem_itself(tmp_path: Path) -> None:
    # The journal file and its anchor are the only artifacts; no raw export copy exists.
    admission = _admission()
    journal = context_journal(tmp_path / "context")
    import_fixture_set(journal, admission)
    names = sorted(p.name for p in (tmp_path / "context").iterdir())
    assert names == ["context_journal.jsonl", "context_journal.jsonl.hwm"]
    text = journal.path.read_text(encoding="utf-8")
    assert "W-001" in text and "BUCKET-S" in text  # normalized fields are kept
    assert "+08:00" not in text  # every instant stored in UTC
    assert "2026-08-08T18:40:00+08:00" not in text
    assert FIXTURE_AS_OF + timedelta(0) == FIXTURE_AS_OF
