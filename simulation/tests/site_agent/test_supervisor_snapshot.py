"""The additive Supervisor Snapshot: one coherent read, honest context states."""

from __future__ import annotations

import http.client
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import nxt_operational_context as oc
from nxt_site_agent import (
    API_SCHEMA_VERSION,
    SUPERVISOR_SNAPSHOT_SCHEMA,
    ClockBasis,
    ClockSource,
    CompositionSeam,
    SiteAgentApiServer,
    SiteAgentService,
)
from nxt_workflow_enablement import RANGE_OPS_WORKFLOW_ID
from scripts.operational_context_fixture import (
    FIXTURE_AS_OF,
    FIXTURE_DIR,
    ContextReader,
    admission_from_manifest,
    context_journal,
    import_file,
    import_fixture_set,
    load_profiles,
)
from scripts.site_agent_fixture import DEPLOYMENT_ID, SITE_ID, fixture_clock, service_composition_seam, service_manifest_payload


def _launch(runs_root: Path, seam: CompositionSeam) -> SiteAgentService:
    return SiteAgentService.launch(runs_root=runs_root, site_id=SITE_ID, deployment_id=DEPLOYMENT_ID, workflow_id=RANGE_OPS_WORKFLOW_ID, seam=seam)


def _with(seam: CompositionSeam, **overrides) -> CompositionSeam:
    return CompositionSeam(
        composer=seam.composer,
        materials_for=seam.materials_for,
        cycle_catalog=seam.cycle_catalog,
        clock=overrides.get("clock", seam.clock),
        context_for=overrides.get("context_for", seam.context_for),
    )


def _labels(section: dict) -> set[str]:
    return {item["label"] for item in section.values() if isinstance(item, dict) and "label" in item}


def test_snapshot_embeds_the_five_projections_verbatim_and_adds_the_context_sections(tmp_path) -> None:
    service = _launch(tmp_path, service_composition_seam())
    try:
        service.advance()
        snap = service.supervisor_snapshot()
        assert snap["snapshot_schema"] == SUPERVISOR_SNAPSHOT_SCHEMA
        assert snap["health"] == service.health_snapshot()
        assert snap["state"] == service.state_snapshot()
        assert snap["recommendations"] == service.recommendations_snapshot()
        assert snap["briefing"] == service.briefing_snapshot()
        assert snap["fixture"] == service.fixture_snapshot()
        assert snap["generation"]["snapshot_id"].startswith("svs_") and len(snap["generation"]["snapshot_id"]) == 28
        assert snap["generation"]["clock_basis"] == "FIXTURE_DECLARED"
        assert snap["generation"]["generated_at"] == "2026-08-08T10:30:00.000000Z"
        assert snap["generation"]["scenario_now"] is not None  # scenario time is reported beside the declared clock, never mixed
        assert snap["identity"]["site_id"] == SITE_ID and snap["identity"]["fixture_mode"] is True
        staffing, operations = snap["staffing"], snap["operations"]
        assert staffing["status"] == "ok" and staffing["scheduled_now"]["value"] == 3 and staffing["scheduled_now"]["label"] == "PLANNED"
        assert staffing["confirmed_present_now"]["label"] == "DERIVED"
        assert operations["sales"]["ball_units_sold_today"]["value"] == 2450
        assert operations["play"]["active_players_confirmed"]["label"] == "UNKNOWN"
        assert _labels(staffing) <= {"PLANNED", "SOURCE_RECORDED", "DERIVED", "UNKNOWN"}
        assert snap["operating_day"]["date"] == "2026-08-08" and snap["operating_day"]["timezone"] == "Asia/Shanghai"
        assert snap["coverage_recommendations"] == []
        assert snap["data_quality"]["context"] == {
            "status": "ok", "code": None, "detail": None, "as_of": "2026-08-08T10:30:00.000000Z", "as_of_basis": "FIXTURE_DECLARED",
            "clock_declared": True, "reader_declared": True,
        }
        assert set(snap["context_sources"]) == {"staffing", "sales", "play"}
    finally:
        service.stop()


def test_physical_stores_and_machines_stay_explicitly_unknown_or_labelled_by_their_source(tmp_path) -> None:
    service = _launch(tmp_path, service_composition_seam())
    try:
        before = service.supervisor_snapshot()
        assert before["physical_stores"]["clean_balls_in_dispenser"]["label"] == "UNKNOWN"
        service.advance()
        snap = service.supervisor_snapshot()
        count = snap["physical_stores"]["clean_balls_in_dispenser"]
        # The channel is bound to a sensor, but the service replays a fixture
        # through the adapter path: both facts are shown and nothing is physical.
        assert count["label"] == "FIXTURE_FACILITY_STATE" and count["physical"] is False
        assert count["source_type"] == "sensor" and count["service_source"] == "fixture"
        assert count["value"] == snap["state"]["dispenser"]["clean_available_balls"]
        for key in ("clean_ball_weight_kg", "awaiting_wash_balls"):
            assert snap["physical_stores"][key] == {"value": None, "label": "UNKNOWN", "reason": snap["physical_stores"][key]["reason"]}
        assert {m["label"] for m in snap["machines"].values()} == {"UNKNOWN"}
        text = json.dumps(snap["staffing"]) + json.dumps(snap["operations"])
        assert "dispens" not in text.lower() and "inventory" not in text.lower()
    finally:
        service.stop()


def test_without_a_declared_clock_or_reader_the_context_is_unavailable_and_everything_else_unchanged(tmp_path) -> None:
    seam = service_composition_seam(with_context=False)
    service = _launch(tmp_path, seam)
    try:
        service.advance()
        snap = service.supervisor_snapshot()
        assert snap["health"] == service.health_snapshot()
        assert snap["staffing"] == {"status": "unavailable", "code": "clock_undeclared", "detail": snap["staffing"]["detail"]}
        assert snap["operations"]["status"] == "unavailable"
        assert snap["generation"]["generated_at"] is None and snap["generation"]["clock_basis"] is None
        assert snap["data_quality"]["context"]["clock_declared"] is False and snap["data_quality"]["context"]["reader_declared"] is False
        assert snap["exceptions"][0]["code"] == "clock_undeclared"
        assert snap["coverage_recommendations"] == []
    finally:
        service.stop()
    service = _launch(tmp_path / "clock-only", _with(service_composition_seam(), context_for=None))
    try:
        snap = service.supervisor_snapshot()
        assert snap["staffing"]["code"] == "context_reader_undeclared"
        assert snap["generation"]["generated_at"] == "2026-08-08T10:30:00.000000Z"
    finally:
        service.stop()


def test_a_broken_reader_factory_or_torn_journal_never_fails_the_service(tmp_path) -> None:
    def exploding(run_root):
        raise RuntimeError("disk on fire")

    service = _launch(tmp_path, _with(service_composition_seam(), context_for=exploding))
    try:
        assert service.health_snapshot()["service_state"] == "serving"
        snap = service.supervisor_snapshot()
        assert snap["staffing"]["code"] == "context_reader_failed" and "disk on fire" in snap["staffing"]["detail"]
        assert snap["health"]["service_state"] == "serving"
    finally:
        service.stop()
    service = _launch(tmp_path / "torn", service_composition_seam())
    try:
        journal_path = service.storage.run_root / "context" / "context_journal.jsonl"
        data = journal_path.read_bytes()
        journal_path.write_bytes(data[:-5])
        snap = service.supervisor_snapshot()
        assert snap["data_quality"]["context"]["status"] == "unavailable"
        assert snap["data_quality"]["context"]["code"] == "journal_unreadable"
        assert snap["staffing"] == {"status": "unavailable", "code": "journal_unreadable", "detail": snap["staffing"]["detail"]}
        assert "scheduled_now" not in snap["staffing"]  # nothing partial
        assert service.health_snapshot()["service_state"] == "serving"
        journal_path.write_bytes(data)
        assert service.supervisor_snapshot()["staffing"]["status"] == "ok"
    finally:
        service.stop()


def test_a_rejected_import_batch_publishes_nothing_partial_and_is_visible_in_data_quality(tmp_path) -> None:
    admission = admission_from_manifest(service_manifest_payload())
    profiles, exported = load_profiles()

    def context_for(run_root):
        journal = context_journal(Path(run_root) / "context")
        import_fixture_set(journal, admission)
        import_file(
            journal, admission=admission, profile=profiles[oc.SourceSystem.SALES], path=FIXTURE_DIR / "rejected" / "sales_bad_row.csv",
            received_at=FIXTURE_AS_OF, exported_at=exported[oc.SourceSystem.SALES],
        )
        return ContextReader(journal=journal, admission=admission, profiles=profiles)

    good = _launch(tmp_path / "good", service_composition_seam())
    bad = _launch(tmp_path / "bad", _with(service_composition_seam(), context_for=context_for))
    try:
        reference = good.supervisor_snapshot()
        snap = bad.supervisor_snapshot()
        assert json.dumps(snap["operations"], sort_keys=True) == json.dumps(reference["operations"], sort_keys=True)
        assert json.dumps(snap["staffing"], sort_keys=True) == json.dumps(reference["staffing"], sort_keys=True)
        sales = snap["context_sources"]["sales"]
        assert sales["rejected_batches"] == 1 and sales["accepted_batches"] == 1
        assert sales["last_rejection"]["errors"] == [{"row_number": 3, "column": "quantity", "reason": "invalid_count"}]
        assert any(e["code"] == "import_batches_rejected" for e in snap["exceptions"])
        assert "REDACTED-77" not in json.dumps(snap)
    finally:
        good.stop()
        bad.stop()


def test_stale_business_context_is_never_ok_while_facility_sections_keep_their_own_status(tmp_path) -> None:
    late = ClockSource(read=lambda: FIXTURE_AS_OF + timedelta(hours=6), basis=ClockBasis.FIXTURE_DECLARED)
    service = _launch(tmp_path, _with(service_composition_seam(), clock=late))
    try:
        service.advance()
        snap = service.supervisor_snapshot()
        assert snap["staffing"]["status"] == "stale" and snap["operations"]["status"] == "stale"
        for section in (snap["staffing"], snap["operations"]["sales"], snap["operations"]["play"]):
            for item in section.values():
                if isinstance(item, dict) and "source_status" in item:
                    assert item["source_status"] == "stale"
        assert {s["status"] for s in snap["context_sources"].values()} == {"stale"}
        assert snap["data_quality"]["context"]["status"] == "stale"
        assert snap["state"]["available"] is True  # facility evidence is judged by its own scenario-time age
    finally:
        service.stop()


def test_snapshot_identity_is_stable_until_something_changes(tmp_path) -> None:
    service = _launch(tmp_path, service_composition_seam())
    try:
        first = service.supervisor_snapshot()
        second = service.supervisor_snapshot()
        assert first["generation"]["snapshot_id"] == second["generation"]["snapshot_id"]
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
        service.advance()
        third = service.supervisor_snapshot()
        assert third["generation"]["snapshot_id"] != first["generation"]["snapshot_id"]
    finally:
        service.stop()


def test_reset_and_restart_recompose_the_reader_per_run_without_duplicating_events(tmp_path) -> None:
    service = _launch(tmp_path, service_composition_seam())
    try:
        run1 = service.storage.run_root
        first = service.supervisor_snapshot()
        service.restart_runtime()
        restarted = service.supervisor_snapshot()
        assert restarted["staffing"]["scheduled_now"] == first["staffing"]["scheduled_now"]
        assert restarted["context_sources"]["staffing"]["duplicate_batches"] == 1
        assert restarted["staffing"]["scheduled_today"]["value"] == first["staffing"]["scheduled_today"]["value"]
        service.reset()
        run2 = service.storage.run_root
        assert run2 != run1 and (run2 / "context" / "context_journal.jsonl").is_file()
        after = service.supervisor_snapshot()
        assert after["context_sources"]["staffing"]["duplicate_batches"] == 0
        assert after["staffing"]["scheduled_today"]["value"] == first["staffing"]["scheduled_today"]["value"]
        assert after["generation"]["run_directory"] == run2.name
    finally:
        service.stop()


@pytest.fixture()
def served(tmp_path):
    service = _launch(tmp_path, service_composition_seam())
    server = SiteAgentApiServer(service, port=0)
    server.start_background()
    connection = http.client.HTTPConnection(server.host, server.port, timeout=10)
    yield service, connection
    connection.close()
    server.shutdown()
    service.stop()


def _get(connection, path):
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def test_endpoint_is_additive_read_only_and_leaves_evidence_untouched(served) -> None:
    service, connection = served
    status, payload = _get(connection, "/api/v0/supervisor-snapshot")
    assert status == 200 and payload["schema"] == API_SCHEMA_VERSION
    assert payload["data"]["snapshot_schema"] == SUPERVISOR_SNAPSHOT_SCHEMA
    context_path = service.storage.run_root / "context" / "context_journal.jsonl"
    before = context_path.read_bytes()
    evidence_before = {name: (service.storage.workflow_evidence_root / name).read_bytes() for name in ("ledger.jsonl", "evaluations.jsonl", "snapshots.jsonl") if (service.storage.workflow_evidence_root / name).is_file()}
    for _ in range(3):
        assert _get(connection, "/api/v0/supervisor-snapshot")[0] == 200
    assert context_path.read_bytes() == before
    assert {name: (service.storage.workflow_evidence_root / name).read_bytes() for name in evidence_before} == evidence_before
    connection.request("POST", "/api/v0/supervisor-snapshot", body="{}", headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    json.loads(response.read())
    assert response.status == 404
    # Existing endpoints are untouched and clock-free.
    status, health = _get(connection, "/api/v0/health")
    assert status == 200 and "generated_at" not in json.dumps(health)
