"""Operational-context composition root: journal wiring and synthetic imports.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

The only place that joins the stdlib-only ``nxt_operational_context`` leaf to
a file: it wires the existing JSONL journal (``nxt_edge_task.journal``) under
the operational-context schema label, reads synthetic CSV exports, runs the
leaf's pure import decision inside the journal's exclusive lock, and exposes
a ``ContextReader`` whose ``snapshot(as_of)`` re-derives the projection from
the verified journal every time.  Nothing here connects to a vendor system,
a device, or the network; the fixture exports are invented data for Pilot
Course A on 2026-08-08 (Asia/Shanghai).
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SIM_ROOT = Path(__file__).resolve().parents[1]
if str(SIM_ROOT) not in sys.path:
    sys.path.insert(0, str(SIM_ROOT))

import nxt_operational_context as oc  # noqa: E402
from nxt_edge_task.journal import JournalIntegrityError, JsonlJournal, RecordSpec  # noqa: E402

FIXTURE_DIR = SIM_ROOT / "scripts" / "operational_context_fixture"
CONTEXT_JOURNAL_NAME = "context_journal.jsonl"

#: The declared fixture clock: 18:30 on operating date 2026-08-08 in
#: Asia/Shanghai, expressed in UTC.  Never a wall clock.
FIXTURE_AS_OF = datetime(2026, 8, 8, 10, 30, tzinfo=timezone.utc)
FIXTURE_RECEIVED_AT = datetime(2026, 8, 8, 10, 45, tzinfo=timezone.utc)

FIXTURE_FILES: Mapping[oc.SourceSystem, str] = {
    oc.SourceSystem.STAFFING: "staffing_2026-08-08.csv",
    oc.SourceSystem.SALES: "sales_2026-08-08.csv",
    oc.SourceSystem.PLAY: "play_2026-08-08.csv",
}


def load_profiles(path: Path = FIXTURE_DIR / "profiles.json") -> tuple[dict[oc.SourceSystem, oc.SourceProfile], dict[oc.SourceSystem, datetime]]:
    """Declared source profiles and declared export times from the fixture."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != oc.SOURCE_PROFILE_SCHEMA:
        raise oc.OperationalContextError("schema_mismatch", "profiles.json carries an unsupported schema")
    profiles: dict[oc.SourceSystem, oc.SourceProfile] = {}
    exported: dict[oc.SourceSystem, datetime] = {}
    for name, payload in raw["profiles"].items():
        system = oc.SourceSystem(name)
        profiles[system] = oc.SourceProfile(
            source_system=system,
            adapter_id=payload["adapter_id"],
            adapter_version=payload["adapter_version"],
            source_timezone=payload["source_timezone"],
            stale_after_s=int(payload["stale_after_s"]),
            clock_skew_tolerance_s=int(payload["clock_skew_tolerance_s"]),
            demand_window_s=int(payload["demand_window_s"]),
            sku_ball_units={str(k): int(v) for k, v in payload.get("sku_ball_units", {}).items()},
        )
    for name, text in raw.get("exported_at", {}).items():
        exported[oc.SourceSystem(name)] = oc.parse_utc(text)
    return profiles, exported


def context_journal(context_dir: Path) -> JsonlJournal:
    """The operational-context journal under its own schema label and closed vocabulary."""

    return JsonlJournal(
        Path(context_dir) / CONTEXT_JOURNAL_NAME,
        allowed_kinds=oc.CONTEXT_RECORD_KINDS,
        allowed_origins=oc.CONTEXT_ORIGINS,
        schema=oc.JOURNAL_SCHEMA,
    )


def _to_spec(spec: oc.RecordSpecData) -> RecordSpec:
    return RecordSpec(record_kind=spec.record_kind, origin=spec.origin, recorded_at_utc=spec.recorded_at_utc, payload=dict(spec.payload))


def import_file(
    journal: JsonlJournal,
    *,
    admission: oc.ContextAdmissionFacts,
    profile: oc.SourceProfile,
    path: Path,
    received_at: datetime,
    exported_at: datetime | None,
) -> oc.ImportDecision:
    """Import one export file: parse, decide inside the lock, append the decision.

    The file's bytes are digested for identity and then discarded; only the
    normalized events and safe diagnostics reach the journal.
    """

    data = Path(path).read_bytes()
    text = data.decode("utf-8")
    batch = oc.BatchContext(
        admission=admission,
        profile=profile,
        source_file_name=Path(path).name,
        source_file_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        received_at=received_at,
        exported_at=exported_at,
    )
    result = oc.parse_csv(text, batch)
    outcome: dict[str, oc.ImportDecision] = {}

    def builder(records):
        decision = oc.decide_import(records, result, received_at)
        outcome["decision"] = decision
        return [_to_spec(spec) for spec in decision.specs]

    journal.append_via(builder)
    return outcome["decision"]


def import_fixture_set(journal: JsonlJournal, admission: oc.ContextAdmissionFacts, *, received_at: datetime = FIXTURE_RECEIVED_AT) -> list[oc.ImportDecision]:
    """Import the three synthetic exports (idempotent across launches)."""

    profiles, exported = load_profiles()
    decisions = []
    for system, name in FIXTURE_FILES.items():
        decisions.append(
            import_file(journal, admission=admission, profile=profiles[system], path=FIXTURE_DIR / name, received_at=received_at, exported_at=exported.get(system))
        )
    return decisions


@dataclass(frozen=True, slots=True)
class ContextReader:
    """Read-only view over one context journal for the service shell.

    ``verify()`` re-reads and re-verifies the journal (integrity errors are
    reported as plain data, never raised into the snapshot); ``snapshot``
    re-derives the projection from the verified records for ``as_of``.
    """

    journal: JsonlJournal
    admission: oc.ContextAdmissionFacts
    profiles: Mapping[oc.SourceSystem, oc.SourceProfile]

    def verify(self) -> dict[str, Any]:
        try:
            records = self.journal.read()
        except JournalIntegrityError as exc:
            return {"status": "unavailable", "code": "journal_unreadable", "detail": str(exc), "records": 0}
        except OSError as exc:
            return {"status": "unavailable", "code": "journal_unreadable", "detail": f"{type(exc).__name__}: {exc}", "records": 0}
        return {"status": "available", "code": None, "detail": None, "records": len(records)}

    def snapshot(self, as_of: datetime, as_of_basis: str) -> dict[str, Any]:
        """The context projection, or an explicit unavailable envelope."""

        try:
            records = self.journal.read()
            events = oc.recorded_events(records)
            batches = oc.recorded_batches(records)
            context = oc.project_context(admission=self.admission, events=events, batches=batches, profiles=self.profiles, as_of=as_of, as_of_basis=as_of_basis)
        except (JournalIntegrityError, OSError, oc.OperationalContextError, KeyError, TypeError, ValueError) as exc:
            return {
                "status": "unavailable",
                "code": "journal_unreadable" if isinstance(exc, (JournalIntegrityError, OSError)) else "projection_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "as_of": oc.utc_text(as_of),
                "as_of_basis": as_of_basis,
                "context": None,
            }
        return {"status": "available", "code": None, "detail": None, "as_of": context["as_of"], "as_of_basis": as_of_basis, "context": context}


ContextReaderFactory = Callable[[Path], ContextReader]


def fixture_context_for(admission: oc.ContextAdmissionFacts, *, received_at: datetime = FIXTURE_RECEIVED_AT) -> ContextReaderFactory:
    """Per-run factory: wires the journal under ``run_root/context`` and imports the fixture set.

    Re-imports are idempotent (duplicate batches are recorded, no event is
    duplicated), so launch, resume, restart and reset all converge on the
    same recorded set.
    """

    profiles, _ = load_profiles()

    def context_for(run_root: Path) -> ContextReader:
        journal = context_journal(Path(run_root) / "context")
        import_fixture_set(journal, admission, received_at=received_at)
        return ContextReader(journal=journal, admission=admission, profiles=profiles)

    return context_for


def admission_from_manifest(payload: Mapping[str, Any]) -> oc.ContextAdmissionFacts:
    """Project the commissioned identity facts this leaf needs from a manifest payload."""

    return oc.ContextAdmissionFacts(
        site_id=str(payload["site_id"]),
        deployment_id=str(payload["deployment_id"]),
        site_timezone=str(payload["timezone"]),
        manifest_digest="sha256:" + oc.stable_digest(payload),
    )


__all__ = [
    "CONTEXT_JOURNAL_NAME",
    "FIXTURE_AS_OF",
    "FIXTURE_DIR",
    "FIXTURE_FILES",
    "FIXTURE_RECEIVED_AT",
    "ContextReader",
    "ContextReaderFactory",
    "admission_from_manifest",
    "context_journal",
    "fixture_context_for",
    "import_file",
    "import_fixture_set",
    "load_profiles",
]
