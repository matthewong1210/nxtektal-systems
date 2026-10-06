"""Contracts of the Operational Context Ingestion V0 leaf.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

Business operational-context evidence: facts a staffing, point-of-sale, or
tee-sheet system has *recorded*.  They are neither physical observations of
the facility nor commissioned static facts nor policy outputs, so they live
in their own fact class with their own owner (this package) and never enter
``FacilityState``.

Every value this package produces carries one of four evidence labels and
the four are never merged:

- ``PLANNED``          what a schedule or booking says will happen;
- ``SOURCE_RECORDED``  what a business system recorded as having happened;
- ``DERIVED``          a count or aggregate this package computed from the
                       records above;
- ``UNKNOWN``          nothing trustworthy was recorded; never a zero.

Nothing here reads a clock, the filesystem, or the network.  Canonical JSON
and the content digests are redeclared locally (the leaf imports no other
repository package); a parity test pins them to the repository's shared
rule.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EVENT_SCHEMA = "nxt-operational-context/event/v1"
SOURCE_PROFILE_SCHEMA = "nxt-operational-context/source-profile/v1"
JOURNAL_SCHEMA = "nxt-operational-context/journal/v1"
CONTEXT_SCHEMA = "nxt-operational-context/context/v1"

#: The identifier shape every reference, code, and record id must satisfy.
#: Bounded, ASCII, no whitespace: a pseudonymous worker reference such as
#: ``W-017`` fits; a name, a phone number, or a sentence never does.
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")


class EvidenceLabel(StrEnum):
    PLANNED = "PLANNED"
    SOURCE_RECORDED = "SOURCE_RECORDED"
    DERIVED = "DERIVED"
    UNKNOWN = "UNKNOWN"


class SourceSystem(StrEnum):
    STAFFING = "staffing"
    SALES = "sales"
    PLAY = "play"


class EventType(StrEnum):
    # staffing
    SHIFT_SCHEDULED = "SHIFT_SCHEDULED"
    SHIFT_CANCELLED = "SHIFT_CANCELLED"
    CLOCK_IN = "CLOCK_IN"
    CLOCK_OUT = "CLOCK_OUT"
    SHIFT_CHANGE_REQUESTED = "SHIFT_CHANGE_REQUESTED"
    SHIFT_CHANGE_APPROVED = "SHIFT_CHANGE_APPROVED"
    SHIFT_CHANGE_REJECTED = "SHIFT_CHANGE_REJECTED"
    ABSENCE_RECORDED = "ABSENCE_RECORDED"
    # sales
    SALE_CAPTURED = "SALE_CAPTURED"
    SALE_REVERSED = "SALE_REVERSED"
    # play
    SESSION_BOOKED = "SESSION_BOOKED"
    SESSION_STARTED = "SESSION_STARTED"
    SESSION_FINISHED = "SESSION_FINISHED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    PLAYER_COUNT_UPDATED = "PLAYER_COUNT_UPDATED"


EVENT_TYPES_BY_SOURCE: Mapping[SourceSystem, frozenset[EventType]] = {
    SourceSystem.STAFFING: frozenset(
        {
            EventType.SHIFT_SCHEDULED,
            EventType.SHIFT_CANCELLED,
            EventType.CLOCK_IN,
            EventType.CLOCK_OUT,
            EventType.SHIFT_CHANGE_REQUESTED,
            EventType.SHIFT_CHANGE_APPROVED,
            EventType.SHIFT_CHANGE_REJECTED,
            EventType.ABSENCE_RECORDED,
        }
    ),
    SourceSystem.SALES: frozenset({EventType.SALE_CAPTURED, EventType.SALE_REVERSED}),
    SourceSystem.PLAY: frozenset(
        {
            EventType.SESSION_BOOKED,
            EventType.SESSION_STARTED,
            EventType.SESSION_FINISHED,
            EventType.SESSION_CANCELLED,
            EventType.PLAYER_COUNT_UPDATED,
        }
    ),
}


class CorrectionKind(StrEnum):
    #: A later revision of the same source record; supersedes the head.
    REPLACEMENT = "REPLACEMENT"
    #: A reversal (void or refund) that points at a captured sale.
    REVERSAL = "REVERSAL"


class OperationalContextError(ValueError):
    """A contract violation with a stable machine-readable ``code``."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


# ---------------------------------------------------------------------------
# Canonical JSON and digests (redeclared; parity-tested against the shared rule)
# ---------------------------------------------------------------------------


def to_primitive(value: Any) -> Any:
    """JSON-compatible tree: datetimes become UTC microsecond ``Z`` text."""

    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, StrEnum):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    if isinstance(value, float) and value == 0.0:
        return 0.0
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        to_primitive(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def content_digest(value: Any) -> str:
    return "sha256:" + stable_digest(value)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OperationalContextError("naive_datetime", "datetime values must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    """Parse canonical ``Z`` text (or any offset) into an aware UTC datetime."""

    if not isinstance(text, str):
        raise OperationalContextError("invalid_timestamp", "timestamp must be text")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise OperationalContextError("invalid_timestamp", "timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OperationalContextError("invalid_timestamp", "timestamp has no UTC offset")
    return parsed.astimezone(timezone.utc)


def load_timezone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name or "/" not in name and name != "UTC":
        raise OperationalContextError("invalid_timezone", "timezone must be an IANA zone name")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OperationalContextError("invalid_timezone", "timezone is not a known IANA zone") from exc


def parse_source_timestamp(text: str, source_timezone: ZoneInfo) -> datetime:
    """A source timestamp: explicit offset, ``Z``, or naive local source time.

    A naive value is interpreted in the declared source timezone; the result
    is always stored in UTC.  Nonexistent or ambiguous local instants (DST
    gaps and folds) are refused because they cannot be stored honestly.
    """

    if not isinstance(text, str) or not text.strip():
        raise OperationalContextError("invalid_timestamp", "timestamp is empty")
    raw = text.strip()
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise OperationalContextError("invalid_timestamp", "timestamp is not ISO-8601") from exc
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc)
    local = parsed.replace(tzinfo=source_timezone)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise OperationalContextError("invalid_timestamp", "local timestamp is ambiguous (DST fold)")
    round_trip = local.astimezone(timezone.utc).astimezone(source_timezone).replace(tzinfo=None)
    if round_trip != parsed:
        raise OperationalContextError("invalid_timestamp", "local timestamp does not exist (DST gap)")
    return local.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class OperatingDay:
    """One local calendar day of the site, as a half-open UTC window."""

    date: str
    timezone: str
    start_utc: datetime
    end_utc: datetime

    def contains(self, instant: datetime) -> bool:
        return self.start_utc <= instant < self.end_utc

    def overlaps(self, start: datetime, end: datetime) -> bool:
        return start < self.end_utc and end > self.start_utc

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "timezone": self.timezone,
            "start_utc": utc_text(self.start_utc),
            "end_utc": utc_text(self.end_utc),
        }


def operating_day(instant: datetime, site_timezone: str) -> OperatingDay:
    """The site-local day that contains ``instant``; boundaries at local midnight.

    Refuses (``operating_day_boundary_ambiguous``) when either midnight does
    not map to exactly one UTC instant in that zone.
    """

    if instant.tzinfo is None or instant.utcoffset() is None:
        raise OperationalContextError("naive_datetime", "as_of must be timezone-aware")
    zone = load_timezone(site_timezone)
    local_date = instant.astimezone(zone).date()
    start = _local_midnight(local_date, zone, site_timezone)
    end = _local_midnight(local_date + timedelta(days=1), zone, site_timezone)
    return OperatingDay(date=local_date.isoformat(), timezone=site_timezone, start_utc=start, end_utc=end)


def _local_midnight(day: date, zone: ZoneInfo, name: str) -> datetime:
    local = datetime(day.year, day.month, day.day, tzinfo=zone)
    if local.utcoffset() != local.replace(fold=1).utcoffset():
        raise OperationalContextError("operating_day_boundary_ambiguous", f"midnight {day.isoformat()} is ambiguous in {name}")
    as_utc = local.astimezone(timezone.utc)
    if as_utc.astimezone(zone).replace(tzinfo=None) != datetime(day.year, day.month, day.day):
        raise OperationalContextError("operating_day_boundary_ambiguous", f"midnight {day.isoformat()} does not exist in {name}")
    return as_utc


# ---------------------------------------------------------------------------
# Identity and profiles (plain data handed in by composition roots)
# ---------------------------------------------------------------------------


def require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.match(value):
        raise OperationalContextError("identifier_not_opaque", f"{field_name} must be a bounded opaque identifier")
    return value


@dataclass(frozen=True, slots=True)
class ContextAdmissionFacts:
    """Site identity the composition root projects from commissioning.

    Plain data: this leaf never imports commissioning.  ``manifest_digest``
    is the composition root's digest of the commissioned manifest it used.
    """

    site_id: str
    deployment_id: str
    site_timezone: str
    manifest_digest: str

    def __post_init__(self) -> None:
        require_identifier(self.site_id, "site_id")
        require_identifier(self.deployment_id, "deployment_id")
        load_timezone(self.site_timezone)
        if not isinstance(self.manifest_digest, str) or not re.match(r"^sha256:[0-9a-f]{64}$", self.manifest_digest):
            raise OperationalContextError("invalid_manifest_digest", "manifest_digest must be sha256:<64 hex>")

    def to_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "deployment_id": self.deployment_id,
            "site_timezone": self.site_timezone,
            "manifest_digest": self.manifest_digest,
        }


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """Declared facts about one business source that no file can assert.

    ``stale_after_s`` bounds how old the source's coverage end may be before
    everything derived from it is stale; ``sku_ball_units`` is the operator's
    declared SKU-to-ball-units mapping (sales only); ``demand_window_s`` is the
    recent-sales window.  ``source_timezone`` interprets naive timestamps in
    the export.
    """

    source_system: SourceSystem
    adapter_id: str
    adapter_version: str
    source_timezone: str
    stale_after_s: int
    clock_skew_tolerance_s: int = 300
    demand_window_s: int = 3600
    sku_ball_units: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source_system, SourceSystem):
            raise OperationalContextError("invalid_profile", "source_system must be a SourceSystem")
        require_identifier(self.adapter_id, "adapter_id")
        require_identifier(self.adapter_version, "adapter_version")
        load_timezone(self.source_timezone)
        for name in ("stale_after_s", "clock_skew_tolerance_s", "demand_window_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise OperationalContextError("invalid_profile", f"{name} must be a positive integer")
        for sku, units in self.sku_ball_units.items():
            require_identifier(sku, "sku")
            if isinstance(units, bool) or not isinstance(units, int) or units <= 0:
                raise OperationalContextError("invalid_profile", "sku_ball_units values must be positive integers")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SOURCE_PROFILE_SCHEMA,
            "source_system": str(self.source_system),
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "source_timezone": self.source_timezone,
            "stale_after_s": self.stale_after_s,
            "clock_skew_tolerance_s": self.clock_skew_tolerance_s,
            "demand_window_s": self.demand_window_s,
            "sku_ball_units": {sku: self.sku_ball_units[sku] for sku in sorted(self.sku_ball_units)},
        }

    @property
    def profile_digest(self) -> str:
        return content_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class BatchContext:
    """What the composition root knows about one import that the file cannot.

    ``received_at`` is when the import ran (UTC); ``exported_at`` is the
    declared coverage end of the export when the operator or a profile-mapped
    header supplies it.  ``source_file_digest`` is the only thing retained
    about the raw file.
    """

    admission: ContextAdmissionFacts
    profile: SourceProfile
    source_file_name: str
    source_file_digest: str
    received_at: datetime
    exported_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source_file_name, str) or not self.source_file_name or len(self.source_file_name) > 255:
            raise OperationalContextError("invalid_batch", "source_file_name must be bounded text")
        if not re.match(r"^sha256:[0-9a-f]{64}$", self.source_file_digest or ""):
            raise OperationalContextError("invalid_batch", "source_file_digest must be sha256:<64 hex>")
        utc_text(self.received_at)
        if self.exported_at is not None:
            utc_text(self.exported_at)

    @property
    def import_batch_id(self) -> str:
        seed = {
            "site_id": self.admission.site_id,
            "deployment_id": self.admission.deployment_id,
            "source_system": str(self.profile.source_system),
            "adapter_id": self.profile.adapter_id,
            "adapter_version": self.profile.adapter_version,
            "source_file_digest": self.source_file_digest,
            "profile_digest": self.profile.profile_digest,
        }
        return "ocb_" + stable_digest(seed)[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "import_batch_id": self.import_batch_id,
            "site_id": self.admission.site_id,
            "deployment_id": self.admission.deployment_id,
            "source_system": str(self.profile.source_system),
            "adapter_id": self.profile.adapter_id,
            "adapter_version": self.profile.adapter_version,
            "profile_digest": self.profile.profile_digest,
            "source_file_name": self.source_file_name,
            "source_file_digest": self.source_file_digest,
            "received_at": utc_text(self.received_at),
            "exported_at": None if self.exported_at is None else utc_text(self.exported_at),
            "manifest_digest": self.admission.manifest_digest,
        }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Correction:
    kind: CorrectionKind
    #: For REPLACEMENT: the head event this revision supersedes (``None`` when
    #: no earlier revision is known).  For REVERSAL: the captured sale this
    #: reversal points at (``None`` when the original is unknown).
    target_event_id: str | None
    source_correction_time: datetime | None
    original_record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": str(self.kind),
            "target_event_id": self.target_event_id,
            "source_correction_time": None if self.source_correction_time is None else utc_text(self.source_correction_time),
            "original_record_id": self.original_record_id,
        }


@dataclass(frozen=True, slots=True)
class OperationalEvent:
    """One normalized, source-recorded operational fact.

    ``occurred_at`` is the source's own record time for this fact (UTC);
    planned instants live only in ``payload``.  ``source_revision_key`` is
    the source correction time of the revision (``""`` for an original), so
    a revision gets its own ``event_id`` and the original stays in history.
    """

    site_id: str
    source_system: SourceSystem
    source_record_id: str
    source_revision_key: str
    event_type: EventType
    occurred_at: datetime
    received_at: datetime
    import_batch_id: str
    source_timezone: str
    source_status: str
    payload: Mapping[str, Any]
    provenance: Mapping[str, Any]
    correction: Correction | None = None

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES_BY_SOURCE[self.source_system]:
            raise OperationalContextError("event_type_not_in_source", f"{self.event_type} is not a {self.source_system} event")
        require_identifier(self.source_record_id, "source_record_id")

    @property
    def event_id(self) -> str:
        seed = {
            "site_id": self.site_id,
            "source_system": str(self.source_system),
            "source_record_id": self.source_record_id,
            "source_revision_key": self.source_revision_key,
            "event_type": str(self.event_type),
        }
        return "oce_" + stable_digest(seed)[:24]

    @property
    def content_digest(self) -> str:
        """Digest of the verbatim source content only (never of profile-derived fields)."""

        return content_digest(
            {
                "occurred_at": utc_text(self.occurred_at),
                "source_status": self.source_status,
                "payload": {key: value for key, value in self.payload.items() if not key.startswith("mapped_")},
                "correction": None if self.correction is None else self.correction.to_dict(),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": EVENT_SCHEMA,
            "event_id": self.event_id,
            "content_digest": self.content_digest,
            "site_id": self.site_id,
            "source_system": str(self.source_system),
            "source_record_id": self.source_record_id,
            "source_revision_key": self.source_revision_key,
            "event_type": str(self.event_type),
            "occurred_at": utc_text(self.occurred_at),
            "received_at": utc_text(self.received_at),
            "import_batch_id": self.import_batch_id,
            "source_timezone": self.source_timezone,
            "source_status": self.source_status,
            "correction": None if self.correction is None else self.correction.to_dict(),
            "payload": to_primitive(dict(self.payload)),
            "provenance": to_primitive(dict(self.provenance)),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "OperationalEvent":
        if raw.get("schema") != EVENT_SCHEMA:
            raise OperationalContextError("schema_mismatch", "event schema is not supported")
        correction_raw = raw.get("correction")
        correction = None
        if correction_raw is not None:
            correction = Correction(
                kind=CorrectionKind(correction_raw["kind"]),
                target_event_id=correction_raw.get("target_event_id"),
                source_correction_time=(
                    None if correction_raw.get("source_correction_time") is None else parse_utc(correction_raw["source_correction_time"])
                ),
                original_record_id=correction_raw.get("original_record_id"),
            )
        event = cls(
            site_id=raw["site_id"],
            source_system=SourceSystem(raw["source_system"]),
            source_record_id=raw["source_record_id"],
            source_revision_key=raw["source_revision_key"],
            event_type=EventType(raw["event_type"]),
            occurred_at=parse_utc(raw["occurred_at"]),
            received_at=parse_utc(raw["received_at"]),
            import_batch_id=raw["import_batch_id"],
            source_timezone=raw["source_timezone"],
            source_status=raw["source_status"],
            payload=dict(raw["payload"]),
            provenance=dict(raw["provenance"]),
            correction=correction,
        )
        if event.event_id != raw.get("event_id"):
            raise OperationalContextError("event_id_mismatch", "stored event_id does not match its identity fields")
        if event.content_digest != raw.get("content_digest"):
            raise OperationalContextError("content_digest_mismatch", "stored content_digest does not match the content")
        return event


__all__ = [
    "CONTEXT_SCHEMA",
    "EVENT_SCHEMA",
    "EVENT_TYPES_BY_SOURCE",
    "IDENTIFIER_PATTERN",
    "JOURNAL_SCHEMA",
    "SOURCE_PROFILE_SCHEMA",
    "BatchContext",
    "ContextAdmissionFacts",
    "Correction",
    "CorrectionKind",
    "EventType",
    "EvidenceLabel",
    "OperatingDay",
    "OperationalContextError",
    "OperationalEvent",
    "SourceProfile",
    "SourceSystem",
    "canonical_json",
    "content_digest",
    "load_timezone",
    "operating_day",
    "parse_source_timestamp",
    "parse_utc",
    "require_identifier",
    "stable_digest",
    "to_primitive",
    "utc_text",
]
