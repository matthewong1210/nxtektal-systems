"""Shared helpers for the operational-context tests (synthetic data only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import nxt_operational_context as oc

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = SIMULATION_ROOT / "scripts" / "operational_context_fixture"

RECEIVED = datetime(2026, 8, 8, 10, 45, tzinfo=timezone.utc)  # 18:45 Asia/Shanghai
AS_OF = datetime(2026, 8, 8, 10, 30, tzinfo=timezone.utc)  # 18:30 Asia/Shanghai
EXPORTED = datetime(2026, 8, 8, 10, 40, tzinfo=timezone.utc)  # 18:40 Asia/Shanghai
MANIFEST_DIGEST = "sha256:" + "ab" * 32


@pytest.fixture()
def admission() -> oc.ContextAdmissionFacts:
    return oc.ContextAdmissionFacts("pilot-course-a", "pilot-a-site-agent-v0", "Asia/Shanghai", MANIFEST_DIGEST)


@pytest.fixture()
def profiles() -> dict[oc.SourceSystem, oc.SourceProfile]:
    return {
        oc.SourceSystem.STAFFING: oc.SourceProfile(oc.SourceSystem.STAFFING, oc.STAFFING_ADAPTER_ID, oc.ADAPTER_VERSION, "Asia/Shanghai", 7200),
        oc.SourceSystem.SALES: oc.SourceProfile(
            oc.SourceSystem.SALES, oc.SALES_ADAPTER_ID, oc.ADAPTER_VERSION, "Asia/Shanghai", 3600, sku_ball_units={"BUCKET-S": 50, "BUCKET-M": 100, "BUCKET-L": 150}
        ),
        oc.SourceSystem.PLAY: oc.SourceProfile(oc.SourceSystem.PLAY, oc.ADAPTER_VERSION and oc.PLAY_ADAPTER_ID, oc.ADAPTER_VERSION, "Asia/Shanghai", 3600),
    }


def fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def make_batch(admission, profile, text: str, *, name: str = "export.csv", received: datetime = RECEIVED, exported: datetime | None = EXPORTED) -> oc.BatchContext:
    digest = "sha256:" + oc.stable_digest(text)
    return oc.BatchContext(admission, profile, name, digest, received, exported)


def specs_to_records(records: list[dict], decision: oc.ImportDecision) -> None:
    for spec in decision.specs:
        records.append({"record_kind": spec.record_kind, "origin": spec.origin, "recorded_at_utc": spec.recorded_at_utc, "payload": spec.payload})


def import_text(records: list[dict], admission, profile, text: str, **kwargs) -> oc.ImportDecision:
    result = oc.parse_csv(text, make_batch(admission, profile, text, **kwargs))
    decision = oc.decide_import(records, result, kwargs.get("received", RECEIVED))
    specs_to_records(records, decision)
    return decision


def import_fixture_set(records: list[dict], admission, profiles) -> list[oc.ImportDecision]:
    return [
        import_text(records, admission, profiles[oc.SourceSystem.STAFFING], fixture_text("staffing_2026-08-08.csv"), name="staffing_2026-08-08.csv"),
        import_text(records, admission, profiles[oc.SourceSystem.SALES], fixture_text("sales_2026-08-08.csv"), name="sales_2026-08-08.csv"),
        import_text(records, admission, profiles[oc.SourceSystem.PLAY], fixture_text("play_2026-08-08.csv"), name="play_2026-08-08.csv"),
    ]


def snapshot(records: list[dict], admission, profiles, as_of: datetime = AS_OF) -> dict:
    return oc.project_context(
        admission=admission,
        events=oc.recorded_events(records),
        batches=oc.recorded_batches(records),
        profiles=profiles,
        as_of=as_of,
        as_of_basis="FIXTURE_DECLARED",
    )
