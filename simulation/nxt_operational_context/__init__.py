"""Operational Context Ingestion V0 — business records as a separate fact class.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A standard-library-only, filesystem-independent leaf.  It imports no other
package of this repository.  Composition roots under ``simulation/scripts/``
hand it the commissioned identity as plain data, the declared source
profiles, the text of a synthetic export, and the verified records of the
existing JSONL journal; it returns normalized source-recorded events, pure
import decisions (idempotent, append-only corrections, whole-batch
rejection), and deterministic projections labelled PLANNED,
SOURCE_RECORDED, DERIVED or UNKNOWN.

It owns no facility state, observation, commissioning, guidance, workflow, or
execution semantics; sold ball units are never physical ball stores; a
schedule is never presence; a request is never an approval; a booking is
never a start.  No language-model, robot, actuator, or vendor API path
exists here.
"""

from __future__ import annotations

from .adapters import (
    ADAPTER_VERSION,
    PLAY_ADAPTER_ID,
    SALES_ADAPTER_ID,
    STAFFING_ADAPTER_ID,
    AdapterResult,
    BatchRejection,
    parse_csv,
    parse_play_csv,
    parse_sales_csv,
    parse_staffing_csv,
)
from .contracts import (
    CONTEXT_SCHEMA,
    EVENT_SCHEMA,
    JOURNAL_SCHEMA,
    SOURCE_PROFILE_SCHEMA,
    BatchContext,
    ContextAdmissionFacts,
    Correction,
    CorrectionKind,
    EventType,
    EvidenceLabel,
    OperatingDay,
    OperationalContextError,
    OperationalEvent,
    SourceProfile,
    SourceSystem,
    canonical_json,
    content_digest,
    operating_day,
    parse_utc,
    stable_digest,
    utc_text,
)
from .importer import (
    CONTEXT_ORIGINS,
    CONTEXT_RECORD_KINDS,
    ORIGIN_ADAPTER,
    ORIGIN_SERVICE,
    RECORD_KIND_BATCH_COMMITTED,
    RECORD_KIND_BATCH_DUPLICATE,
    RECORD_KIND_BATCH_REJECTED,
    RECORD_KIND_EVENT,
    RECORD_KIND_PROFILE,
    ImportDecision,
    RecordSpecData,
    decide_import,
    profile_spec,
    recorded_batches,
    recorded_events,
)
from .privacy import FORBIDDEN_HEADER_TOKENS, RowError
from .projection import STATUS_MISSING, STATUS_OK, STATUS_STALE, head_events, project_context, source_freshness

__all__ = [
    "ADAPTER_VERSION",
    "CONTEXT_ORIGINS",
    "CONTEXT_RECORD_KINDS",
    "CONTEXT_SCHEMA",
    "EVENT_SCHEMA",
    "FORBIDDEN_HEADER_TOKENS",
    "JOURNAL_SCHEMA",
    "ORIGIN_ADAPTER",
    "ORIGIN_SERVICE",
    "PLAY_ADAPTER_ID",
    "RECORD_KIND_BATCH_COMMITTED",
    "RECORD_KIND_BATCH_DUPLICATE",
    "RECORD_KIND_BATCH_REJECTED",
    "RECORD_KIND_EVENT",
    "RECORD_KIND_PROFILE",
    "SALES_ADAPTER_ID",
    "SOURCE_PROFILE_SCHEMA",
    "STAFFING_ADAPTER_ID",
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_STALE",
    "AdapterResult",
    "BatchContext",
    "BatchRejection",
    "ContextAdmissionFacts",
    "Correction",
    "CorrectionKind",
    "EventType",
    "EvidenceLabel",
    "ImportDecision",
    "OperatingDay",
    "OperationalContextError",
    "OperationalEvent",
    "RecordSpecData",
    "RowError",
    "SourceProfile",
    "SourceSystem",
    "canonical_json",
    "content_digest",
    "decide_import",
    "head_events",
    "operating_day",
    "parse_csv",
    "parse_play_csv",
    "parse_sales_csv",
    "parse_staffing_csv",
    "parse_utc",
    "profile_spec",
    "project_context",
    "recorded_batches",
    "recorded_events",
    "source_freshness",
    "stable_digest",
    "utc_text",
]
