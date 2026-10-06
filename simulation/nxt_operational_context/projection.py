"""Pure, deterministic operational-context projections.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

``project_context`` turns the recorded events and batch outcomes of one site
into one plain-data context snapshot for one instant ``as_of``: the staffing
section ("Staffing today"), the operations section ("Operations today":
ball-unit sales and play sessions), per-source freshness, and data quality.
It reads no clock and no file; the same inputs always yield the same bytes.

Label discipline (see ``contracts.EvidenceLabel``): a schedule or booking is
``PLANNED``; what a system recorded is ``SOURCE_RECORDED``; every count or
aggregate computed here is ``DERIVED``; anything not backed by a trustworthy
record is ``UNKNOWN`` with a reason, never a zero.  Scheduled is not present,
clocked-in is not available, requested is not approved, booked is not
started, started is not finished, and sold ball units say nothing about
physical ball stores.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .contracts import (
    CONTEXT_SCHEMA,
    ContextAdmissionFacts,
    EventType,
    EvidenceLabel,
    OperatingDay,
    OperationalContextError,
    OperationalEvent,
    SourceProfile,
    SourceSystem,
    operating_day,
    parse_utc,
    utc_text,
)
from .importer import RECORD_KIND_BATCH_COMMITTED, RECORD_KIND_BATCH_DUPLICATE, RECORD_KIND_BATCH_REJECTED

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"

UNMAPPED_ROLE = "UNMAPPED"

#: Sold ball units are what a till recorded; they say nothing about physical
#: ball stores or about what any machine handed out.
SALES_NOTE = "Sold ball units are entitlement evidence only; they say nothing about physical ball stores or about what any machine handed out."


def value(label: EvidenceLabel, amount: Any, *, basis: str, source_status: str, reason: str | None = None) -> dict[str, Any]:
    """One labelled value.  ``UNKNOWN`` always carries ``None`` and a reason."""

    if label is EvidenceLabel.UNKNOWN:
        return {"value": None, "label": str(label), "basis": basis, "source_status": source_status, "reason": reason or "no trustworthy record"}
    return {"value": amount, "label": str(label), "basis": basis, "source_status": source_status, "reason": None}


def unknown(basis: str, reason: str, source_status: str = STATUS_MISSING) -> dict[str, Any]:
    return value(EvidenceLabel.UNKNOWN, None, basis=basis, source_status=source_status, reason=reason)


# ---------------------------------------------------------------------------
# Heads, chains and freshness
# ---------------------------------------------------------------------------


def _chain_key(event: OperationalEvent) -> tuple[str, str, str]:
    return (str(event.source_system), event.source_record_id, str(event.event_type))


def head_events(events: Sequence[OperationalEvent]) -> tuple[OperationalEvent, ...]:
    """The current revision of every chain, in deterministic order.

    A superseded revision stays in history and is never counted twice; a
    stale revision (older than an already recorded head) never wins.
    """

    heads: dict[tuple[str, str, str], OperationalEvent] = {}
    for event in events:
        key = _chain_key(event)
        current = heads.get(key)
        if current is None or (event.source_revision_key, event.event_id) > (current.source_revision_key, current.event_id):
            heads[key] = event
    return tuple(sorted(heads.values(), key=lambda e: (e.occurred_at, e.event_id)))


def source_freshness(batches: Sequence[Mapping[str, Any]], profile: SourceProfile | None, as_of: datetime) -> dict[str, Any]:
    """Coverage-end freshness of one source.

    ``coverage_end`` is the newest declared export time (or newest recorded
    event when no export time was declared) across accepted batches.  Age is
    ``as_of - coverage_end``; the source is stale once that age reaches the
    profile's ``stale_after_s``, and missing when no batch was accepted.
    """

    accepted = [b for b in batches if b["record_kind"] == RECORD_KIND_BATCH_COMMITTED]
    rejected = [b for b in batches if b["record_kind"] == RECORD_KIND_BATCH_REJECTED]
    duplicates = [b for b in batches if b["record_kind"] == RECORD_KIND_BATCH_DUPLICATE]
    coverage_end: datetime | None = None
    basis = None
    for batch in accepted:
        text = batch.get("coverage_end")
        if text is None:
            continue
        instant = parse_utc(text)
        if coverage_end is None or instant > coverage_end:
            coverage_end, basis = instant, batch.get("coverage_basis")
    stale_after = None if profile is None else profile.stale_after_s
    if profile is None:
        status, age_s = STATUS_MISSING, None
        reason = "no source profile declared"
    elif coverage_end is None:
        status, age_s = STATUS_MISSING, None
        reason = "no accepted import batch"
    else:
        # Coverage that extends past as_of (a declared export time ahead of
        # the clock) is simply current: age never goes negative.
        age_s = max(0.0, (as_of - coverage_end).total_seconds())
        status = STATUS_STALE if age_s >= stale_after else STATUS_OK
        reason = None if status == STATUS_OK else f"coverage end is {int(age_s)} s old; stale after {stale_after} s"
    last_rejection = None
    if rejected:
        last = rejected[-1]
        last_rejection = {
            "import_batch_id": last.get("import_batch_id"),
            "recorded_at": last.get("received_at"),
            "error_count": last.get("error_count"),
            "errors": list(last.get("errors", []))[:10],
        }
    return {
        "status": status,
        "coverage_end": None if coverage_end is None else utc_text(coverage_end),
        "coverage_basis": basis,
        "age_s": None if age_s is None else round(age_s, 3),
        "stale_after_s": stale_after,
        "accepted_batches": len(accepted),
        "rejected_batches": len(rejected),
        "duplicate_batches": len(duplicates),
        "last_rejection": last_rejection,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Staffing today
# ---------------------------------------------------------------------------


def _ts(payload: Mapping[str, Any], key: str) -> datetime | None:
    text = payload.get(key)
    return None if text is None else parse_utc(text)


def project_staffing(events: Sequence[OperationalEvent], day: OperatingDay, as_of: datetime, status: str) -> dict[str, Any]:
    heads = [e for e in head_events(events) if e.source_system is SourceSystem.STAFFING]
    unknown_reason = "staffing source missing" if status == STATUS_MISSING else None
    if status == STATUS_MISSING:
        return {
            "status": status,
            "operating_day": day.to_dict(),
            "scheduled_today": unknown("count of planned shifts overlapping the operating day", "staffing source missing"),
            "scheduled_now": unknown("count of planned shifts covering as_of", "staffing source missing"),
            "confirmed_present_now": unknown("workers with a clock-in and no later clock-out at as_of", "staffing source missing"),
            "confirmed_absent_today": unknown("recorded absences for today's shifts", "staffing source missing"),
            "presence_unknown_now": unknown("scheduled now, neither clocked in nor recorded absent", "staffing source missing"),
            "present_unscheduled_now": unknown("clocked in at as_of with no shift covering as_of", "staffing source missing"),
            "by_role": [],
            "worked_intervals_today": unknown("closed clock-in/clock-out pairs today", "staffing source missing"),
            "open_clock_ins": unknown("clock-ins without a clock-out", "staffing source missing"),
            "next_material_change": unknown("next planned shift boundary after as_of today", "staffing source missing"),
            "approved_shift_changes_today": unknown("approved change records", "staffing source missing"),
            "pending_change_requests": unknown("requests with no approval or rejection", "staffing source missing"),
            "notes": ["Scheduled is not present. Clocked-in is not available. Requested is not approved."],
        }
    shifts: dict[str, dict[str, Any]] = {}
    cancelled: set[str] = set()
    approvals: dict[str, OperationalEvent] = {}
    requests: dict[str, OperationalEvent] = {}
    request_outcomes: set[str] = set()
    absences: set[str] = set()
    clock_ins: dict[str, list[datetime]] = {}
    clock_outs: dict[str, list[datetime]] = {}
    for event in heads:
        payload = event.payload
        staff_ref = str(payload.get("staff_ref"))
        if event.event_type is EventType.SHIFT_SCHEDULED:
            shifts[event.source_record_id] = {
                "staff_ref": staff_ref,
                "role_code": payload.get("role_code") or UNMAPPED_ROLE,
                "start": _ts(payload, "shift_start"),
                "end": _ts(payload, "shift_end"),
            }
        elif event.event_type is EventType.SHIFT_CANCELLED:
            cancelled.add(str(payload.get("shift_record_id")))
        elif event.event_type is EventType.SHIFT_CHANGE_APPROVED:
            approvals[str(payload.get("shift_record_id"))] = event
            request_outcomes.add(str(payload.get("request_record_id")))
        elif event.event_type is EventType.SHIFT_CHANGE_REJECTED:
            request_outcomes.add(str(payload.get("request_record_id")))
        elif event.event_type is EventType.SHIFT_CHANGE_REQUESTED:
            requests[str(payload.get("request_record_id"))] = event
        elif event.event_type is EventType.ABSENCE_RECORDED:
            absences.add(str(payload.get("shift_record_id")))
        elif event.event_type is EventType.CLOCK_IN:
            clock_ins.setdefault(staff_ref, []).append(event.occurred_at)
        elif event.event_type is EventType.CLOCK_OUT:
            clock_outs.setdefault(staff_ref, []).append(event.occurred_at)
    # Effective schedule: an approval replaces the interval; a request never does.
    for shift_id, approval in approvals.items():
        shift = shifts.get(shift_id)
        if shift is None:
            continue
        shift["start"] = _ts(approval.payload, "shift_start") or shift["start"]
        shift["end"] = _ts(approval.payload, "shift_end") or shift["end"]
    today = {sid: s for sid, s in shifts.items() if sid not in cancelled and s["start"] is not None and s["end"] is not None and day.overlaps(s["start"], s["end"])}
    now_shifts = {sid: s for sid, s in today.items() if s["start"] <= as_of < s["end"]}
    present_refs: set[str] = set()
    open_clock_ins = 0
    worked_pairs = 0
    worked_minutes = 0.0
    for staff_ref in sorted(set(clock_ins) | set(clock_outs)):
        ins = sorted(t for t in clock_ins.get(staff_ref, []) if day.contains(t) and t <= as_of)
        outs = sorted(t for t in clock_outs.get(staff_ref, []) if day.contains(t) and t <= as_of)
        # Pair each clock-in with the first later clock-out; an unpaired
        # clock-in means present now and no duration is ever fabricated.
        remaining_outs = list(outs)
        for clock_in in ins:
            later = [t for t in remaining_outs if t > clock_in]
            if later:
                clock_out = later[0]
                remaining_outs.remove(clock_out)
                worked_pairs += 1
                worked_minutes += (clock_out - clock_in).total_seconds() / 60.0
            else:
                open_clock_ins += 1
                present_refs.add(staff_ref)
    scheduled_refs_now = {s["staff_ref"] for s in now_shifts.values()}
    absent_shift_now = {s["staff_ref"] for sid, s in now_shifts.items() if sid in absences}
    present_scheduled = scheduled_refs_now & present_refs
    unknown_refs = scheduled_refs_now - present_refs - absent_shift_now
    unscheduled_present = present_refs - scheduled_refs_now
    roles: dict[str, dict[str, int]] = {}
    for s in now_shifts.values():
        bucket = roles.setdefault(s["role_code"], {"scheduled_now": 0, "present_now": 0, "unknown_now": 0, "absent_now": 0})
        bucket["scheduled_now"] += 1
        if s["staff_ref"] in present_refs:
            bucket["present_now"] += 1
        elif s["staff_ref"] in absent_shift_now:
            bucket["absent_now"] += 1
        else:
            bucket["unknown_now"] += 1
    by_role = [{"role_code": role, **counts, "label": str(EvidenceLabel.DERIVED)} for role, counts in sorted(roles.items())]
    if unscheduled_present:
        by_role.append({"role_code": UNMAPPED_ROLE, "scheduled_now": 0, "present_now": len(unscheduled_present), "unknown_now": 0, "absent_now": 0, "label": str(EvidenceLabel.DERIVED)})
    boundaries: list[tuple[datetime, str]] = []
    for s in today.values():
        for instant, kind in ((s["start"], "shift_start"), (s["end"], "shift_end")):
            if instant > as_of and day.contains(instant):
                boundaries.append((instant, kind))
    next_change = None
    if boundaries:
        first = min(boundaries)
        count = sum(1 for b in boundaries if b == first)
        next_change = {"at_utc": utc_text(first[0]), "kind": first[1], "count": count}
    pending_requests = [rid for rid in requests if rid not in request_outcomes]
    approved_today = [a for a in approvals.values() if day.contains(a.occurred_at)]
    return {
        "status": status,
        "operating_day": day.to_dict(),
        "scheduled_today": value(EvidenceLabel.PLANNED, len(today), basis="planned shifts overlapping the operating day, cancellations removed, approved changes applied", source_status=status),
        "scheduled_now": value(EvidenceLabel.PLANNED, len(now_shifts), basis="planned shifts whose interval covers as_of", source_status=status),
        "confirmed_present_now": value(EvidenceLabel.DERIVED, len(present_refs), basis="workers with a recorded clock-in today and no later clock-out at as_of; presence only, never readiness to work", source_status=status),
        "confirmed_absent_today": value(EvidenceLabel.SOURCE_RECORDED, len([sid for sid in today if sid in absences]), basis="absence records naming today's shifts", source_status=status),
        "presence_unknown_now": value(EvidenceLabel.DERIVED, len(unknown_refs), basis="scheduled now, neither clocked in nor recorded absent; unknown is not absent", source_status=status),
        "present_unscheduled_now": value(EvidenceLabel.DERIVED, len(unscheduled_present), basis="clocked in at as_of with no planned shift covering as_of", source_status=status),
        "present_scheduled_now": value(EvidenceLabel.DERIVED, len(present_scheduled), basis="scheduled now and clocked in", source_status=status),
        "by_role": by_role,
        "worked_intervals_today": value(EvidenceLabel.DERIVED, {"count": worked_pairs, "total_minutes": round(worked_minutes, 1)}, basis="closed clock-in/clock-out pairs today; open intervals carry no duration", source_status=status),
        "open_clock_ins": value(EvidenceLabel.DERIVED, open_clock_ins, basis="clock-ins today without a later clock-out; no duration is derived", source_status=status),
        "next_material_change": (
            value(EvidenceLabel.PLANNED, next_change, basis="earliest planned shift boundary after as_of within the operating day", source_status=status)
            if next_change is not None
            else unknown("earliest planned shift boundary after as_of within the operating day", "no further planned boundary today", status)
        ),
        "approved_shift_changes_today": value(EvidenceLabel.SOURCE_RECORDED, len(approved_today), basis="approval records dated today; only approvals change the effective schedule", source_status=status),
        "pending_change_requests": value(EvidenceLabel.SOURCE_RECORDED, len(pending_requests), basis="change requests with no approval or rejection; requests never change coverage", source_status=status),
        "notes": ["Scheduled is not present. Clocked-in is not available. Requested is not approved."] + ([] if unknown_reason is None else [unknown_reason]),
    }


# ---------------------------------------------------------------------------
# Operations today: ball-unit sales and play sessions
# ---------------------------------------------------------------------------


def project_sales(events: Sequence[OperationalEvent], day: OperatingDay, as_of: datetime, status: str, profile: SourceProfile | None, freshness: Mapping[str, Any]) -> dict[str, Any]:
    heads = [e for e in head_events(events) if e.source_system is SourceSystem.SALES]
    if status == STATUS_MISSING:
        basis_units = "ball units of captured sales today minus reversed units, by the declared SKU mapping"
        return {
            "status": status,
            "ball_units_sold_today": unknown(basis_units, "sales source missing"),
            "transactions_today": unknown("captured sale records today", "sales source missing"),
            "reversals_today": unknown("reversal records today", "sales source missing"),
            "unmapped_sku_transactions_today": unknown("captured sales whose SKU has no declared ball-unit mapping", "sales source missing"),
            "unmatched_reversals": unknown("reversals naming an unknown original transaction", "sales source missing"),
            "recent_window": unknown("ball units sold in the recent window", "sales source missing"),
            "notes": [SALES_NOTE],
        }
    window_s = profile.demand_window_s if profile is not None else 3600
    mapping = dict(profile.sku_ball_units) if profile is not None else {}
    captured = [e for e in heads if e.event_type is EventType.SALE_CAPTURED]
    reversals = [e for e in heads if e.event_type is EventType.SALE_REVERSED]
    captured_by_id = {e.source_record_id: e for e in captured}
    units_today = 0
    unmapped = 0
    transactions_today = 0
    window_start = as_of - timedelta(seconds=window_s)
    window_units = 0
    window_transactions = 0
    for sale in captured:
        if sale.occurred_at > as_of:
            continue
        units_per = mapping.get(str(sale.payload.get("sku")))
        qty = int(sale.payload.get("quantity", 0))
        if day.contains(sale.occurred_at):
            transactions_today += 1
            if units_per is None:
                unmapped += 1
            else:
                units_today += units_per * qty
        if units_per is not None and window_start < sale.occurred_at <= as_of:
            window_units += units_per * qty
            window_transactions += 1
    reversals_today = 0
    unmatched = 0
    reversed_units_today = 0
    for reversal in reversals:
        if reversal.occurred_at > as_of:
            continue
        original_id = str(reversal.payload.get("original_transaction_id"))
        original = captured_by_id.get(original_id)
        if reversal.correction is None or reversal.correction.target_event_id is None or original is None:
            unmatched += 1
            continue
        units_per = mapping.get(str(original.payload.get("sku")))
        reversed_qty = int(reversal.payload.get("reversed_quantity", 0))
        # A reversal is attributed to the day of the sale it reverses, so a
        # refund never turns a later day negative.
        if day.contains(original.occurred_at) and units_per is not None:
            reversed_units_today += units_per * reversed_qty
        if day.contains(reversal.occurred_at):
            reversals_today += 1
        if units_per is not None and window_start < original.occurred_at <= as_of:
            window_units -= units_per * reversed_qty
    coverage_end = freshness.get("coverage_end")
    window_covered = coverage_end is not None and parse_utc(coverage_end) >= as_of
    if coverage_end is not None and parse_utc(coverage_end) <= window_start:
        recent = unknown("ball units sold in the recent window", f"source coverage ends before the {window_s} s window", status)
    else:
        recent = value(
            EvidenceLabel.DERIVED,
            {"window_s": window_s, "ball_units": window_units, "transactions": window_transactions, "window_fully_covered": window_covered, "coverage_end": coverage_end},
            basis="mapped ball units of captured sales minus reversals inside (as_of - window, as_of]; partial coverage is flagged, never extrapolated",
            source_status=status,
        )
    return {
        "status": status,
        "ball_units_sold_today": value(EvidenceLabel.DERIVED, units_today - reversed_units_today, basis="ball units of captured sales today minus reversed units, by the declared SKU mapping; unmapped SKUs excluded and counted separately", source_status=status),
        "transactions_today": value(EvidenceLabel.SOURCE_RECORDED, transactions_today, basis="captured sale records dated today", source_status=status),
        "reversals_today": value(EvidenceLabel.SOURCE_RECORDED, reversals_today, basis="void and refund records dated today", source_status=status),
        "unmapped_sku_transactions_today": value(EvidenceLabel.DERIVED, unmapped, basis="captured sales today whose SKU has no declared ball-unit mapping; their units are unknown", source_status=status),
        "unmatched_reversals": value(EvidenceLabel.DERIVED, unmatched, basis="reversals naming an original transaction this journal has not recorded; not subtracted", source_status=status),
        "recent_window": recent,
        "notes": [SALES_NOTE],
    }


def _duration_stats(minutes: Sequence[float]) -> dict[str, Any]:
    if not minutes:
        return {"count": 0, "mean_minutes": None, "min_minutes": None, "max_minutes": None}
    return {
        "count": len(minutes),
        "mean_minutes": round(sum(minutes) / len(minutes), 1),
        "min_minutes": round(min(minutes), 1),
        "max_minutes": round(max(minutes), 1),
    }


def project_play(events: Sequence[OperationalEvent], day: OperatingDay, as_of: datetime, status: str) -> dict[str, Any]:
    heads = [e for e in head_events(events) if e.source_system is SourceSystem.PLAY]
    if status == STATUS_MISSING:
        return {
            "status": status,
            "booked_sessions_today": unknown("bookings with a scheduled start today, cancellations removed", "play source missing"),
            "booked_players_today": unknown("sum of booked player counts today", "play source missing"),
            "upcoming_booked_players": unknown("booked players for sessions starting after as_of today", "play source missing"),
            "started_sessions_today": unknown("sessions with a recorded start today", "play source missing"),
            "active_sessions_now": unknown("started, not finished, not cancelled at as_of", "play source missing"),
            "active_players_booked": unknown("booked headcount of active sessions", "play source missing"),
            "active_players_confirmed": unknown("recorded actual player counts of active sessions", "play source missing"),
            "completed_sessions_today": unknown("sessions with both a recorded start and finish today", "play source missing"),
            "sessions_missing_finish": unknown("started sessions with no recorded finish", "play source missing"),
            "notes": ["Booked is not started. Started is not finished. Duration needs both a start and a finish."],
        }
    sessions: dict[str, dict[str, Any]] = {}
    for event in heads:
        ref = str(event.payload.get("session_ref"))
        session = sessions.setdefault(ref, {"scheduled_start": None, "player_count": None, "actual_start": None, "actual_finish": None, "actual_player_count": None, "cancelled": False, "booked": False})
        p = event.payload
        if event.event_type is EventType.SESSION_BOOKED:
            session["booked"] = True
            session["scheduled_start"] = _ts(p, "scheduled_start")
            session["player_count"] = p.get("player_count")
        elif event.event_type is EventType.SESSION_STARTED:
            session["actual_start"] = _ts(p, "actual_start") or event.occurred_at
            if p.get("actual_player_count") is not None:
                session["actual_player_count"] = p.get("actual_player_count")
        elif event.event_type is EventType.SESSION_FINISHED:
            session["actual_finish"] = _ts(p, "actual_finish") or event.occurred_at
        elif event.event_type is EventType.SESSION_CANCELLED:
            session["cancelled"] = True
        elif event.event_type is EventType.PLAYER_COUNT_UPDATED:
            session["actual_player_count"] = p.get("actual_player_count")
    booked_today = {ref: s for ref, s in sessions.items() if s["booked"] and not s["cancelled"] and s["scheduled_start"] is not None and day.contains(s["scheduled_start"])}
    booked_players = sum(int(s["player_count"] or 0) for s in booked_today.values())
    upcoming_players = sum(int(s["player_count"] or 0) for s in booked_today.values() if s["scheduled_start"] > as_of)
    started_today = {ref: s for ref, s in sessions.items() if s["actual_start"] is not None and day.contains(s["actual_start"]) and s["actual_start"] <= as_of}
    active = {ref: s for ref, s in started_today.items() if not s["cancelled"] and (s["actual_finish"] is None or s["actual_finish"] > as_of)}
    active_booked = sum(int(s["player_count"] or 0) for s in active.values() if s["player_count"] is not None)
    confirmed_counts = [int(s["actual_player_count"]) for s in active.values() if s["actual_player_count"] is not None]
    durations = []
    missing_finish = 0
    for s in started_today.values():
        if s["actual_finish"] is not None and s["actual_finish"] <= as_of:
            if s["actual_finish"] > s["actual_start"]:
                durations.append((s["actual_finish"] - s["actual_start"]).total_seconds() / 60.0)
        elif s["actual_finish"] is None:
            missing_finish += 1
    return {
        "status": status,
        "booked_sessions_today": value(EvidenceLabel.PLANNED, len(booked_today), basis="bookings with a scheduled start today, cancellations removed", source_status=status),
        "booked_players_today": value(EvidenceLabel.PLANNED, booked_players, basis="sum of booked player counts today; a booking is not a start", source_status=status),
        "upcoming_booked_players": value(EvidenceLabel.PLANNED, upcoming_players, basis="booked players for sessions scheduled after as_of today", source_status=status),
        "started_sessions_today": value(EvidenceLabel.SOURCE_RECORDED, len(started_today), basis="sessions with a recorded actual start today at or before as_of", source_status=status),
        "active_sessions_now": value(EvidenceLabel.DERIVED, len(active), basis="recorded start at or before as_of, no recorded finish at or before as_of, not cancelled", source_status=status),
        "active_players_booked": value(EvidenceLabel.PLANNED, active_booked, basis="booked headcount of active sessions; a booking is not a confirmed player", source_status=status),
        "active_players_confirmed": (
            value(EvidenceLabel.DERIVED, sum(confirmed_counts), basis="recorded actual player counts of active sessions", source_status=status)
            if len(confirmed_counts) == len(active) and active
            else unknown("recorded actual player counts of active sessions", "no actual player count recorded for every active session" if active else "no active session", status)
        ),
        "completed_sessions_today": value(EvidenceLabel.DERIVED, _duration_stats(durations), basis="sessions with both a recorded start and a recorded finish today; duration from those two records only", source_status=status),
        "sessions_missing_finish": value(EvidenceLabel.DERIVED, missing_finish, basis="started sessions with no recorded finish; their duration is unknown", source_status=status),
        "notes": ["Booked is not started. Started is not finished. Duration needs both a start and a finish."],
    }


# ---------------------------------------------------------------------------
# The whole context snapshot
# ---------------------------------------------------------------------------


def project_context(
    *,
    admission: ContextAdmissionFacts,
    events: Sequence[OperationalEvent],
    batches: Sequence[Mapping[str, Any]],
    profiles: Mapping[SourceSystem, SourceProfile],
    as_of: datetime,
    as_of_basis: str,
) -> dict[str, Any]:
    """One deterministic context snapshot for ``as_of``.

    ``as_of_basis`` names the clock the composition root declared
    (``FIXTURE_DECLARED`` or ``SYSTEM_UTC``); scenario time and UTC are never
    subtracted from each other here because every input is UTC.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise OperationalContextError("naive_datetime", "as_of must be timezone-aware")
    for event in events:
        if event.site_id != admission.site_id:
            raise OperationalContextError("site_mismatch", "an event of another site reached the projection")
    day = operating_day(as_of, admission.site_timezone)
    sources: dict[str, Any] = {}
    exceptions: list[dict[str, Any]] = []
    statuses: dict[SourceSystem, str] = {}
    for system in (SourceSystem.STAFFING, SourceSystem.SALES, SourceSystem.PLAY):
        freshness = source_freshness([b for b in batches if b.get("source_system") == str(system)], profiles.get(system), as_of)
        sources[str(system)] = freshness
        statuses[system] = freshness["status"]
        if freshness["status"] != STATUS_OK:
            exceptions.append({"code": f"source_{freshness['status']}", "source_system": str(system), "detail": freshness["reason"]})
        if freshness["rejected_batches"]:
            exceptions.append({"code": "import_batches_rejected", "source_system": str(system), "detail": f"{freshness['rejected_batches']} batch(es) rejected; see data_quality"})
    staffing = project_staffing(events, day, as_of, statuses[SourceSystem.STAFFING])
    sales = project_sales(events, day, as_of, statuses[SourceSystem.SALES], profiles.get(SourceSystem.SALES), sources[str(SourceSystem.SALES)])
    play = project_play(events, day, as_of, statuses[SourceSystem.PLAY])
    if sales.get("unmatched_reversals", {}).get("value"):
        exceptions.append({"code": "unmatched_reversals", "source_system": str(SourceSystem.SALES), "detail": "reversals naming unknown original transactions were not subtracted"})
    worst = STATUS_OK
    for status in statuses.values():
        if status == STATUS_MISSING or (status == STATUS_STALE and worst == STATUS_OK):
            worst = status if worst != STATUS_MISSING else worst
    return {
        "schema": CONTEXT_SCHEMA,
        "owner": "nxt_operational_context",
        "data_class": "business operational-context evidence",
        "site_id": admission.site_id,
        "deployment_id": admission.deployment_id,
        "manifest_digest": admission.manifest_digest,
        "as_of": utc_text(as_of),
        "as_of_basis": as_of_basis,
        "operating_day": day.to_dict(),
        "status": worst,
        "label_vocabulary": [str(label) for label in EvidenceLabel],
        "staffing": staffing,
        "operations": {"status": _worst(statuses[SourceSystem.SALES], statuses[SourceSystem.PLAY]), "sales": sales, "play": play},
        "sources": sources,
        "exceptions": exceptions,
        "event_count": len(events),
        "head_event_count": len(head_events(events)),
    }


def _worst(*statuses: str) -> str:
    if STATUS_MISSING in statuses:
        return STATUS_MISSING
    if STATUS_STALE in statuses:
        return STATUS_STALE
    return STATUS_OK


__all__ = [
    "STATUS_MISSING",
    "STATUS_OK",
    "STATUS_STALE",
    "UNMAPPED_ROLE",
    "head_events",
    "project_context",
    "project_play",
    "project_sales",
    "project_staffing",
    "source_freshness",
    "unknown",
    "value",
]
