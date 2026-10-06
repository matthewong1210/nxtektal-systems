# Operational Context Ingestion V0

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

`nxt_operational_context` gives the Site Agent trusted *business* context
(staffing, ball-unit sales, golf play) before any physical inventory sensor is
connected. It is a standard-library-only, filesystem-free leaf that owns one
new fact class, **business operational-context evidence**: facts a staffing,
point-of-sale, or tee-sheet system has *recorded*. These facts are neither
physical observations nor commissioned static facts nor policy outputs; they
never enter `FacilityState`, and nothing here reaches a schedule, a till, a
payroll system, a robot, or a language model.

The design rationale, the reconciliation of the implementation base, and the
behavior-parity gate are in
[`operational_context_design.md`](operational_context_design.md); this document
describes what is built.

## Evidence labels

Every projected value carries exactly one label and the four are never
merged:

| Label | Meaning | Examples |
|---|---|---|
| `PLANNED` | What a schedule or booking says will happen | shifts scheduled now, booked players |
| `SOURCE_RECORDED` | What a business system recorded as having happened | absences recorded, captured transactions, started sessions |
| `DERIVED` | A count or aggregate this package computed from records | clocked-in now, ball units sold today, active sessions |
| `UNKNOWN` | Nothing trustworthy was recorded; always `null` plus a reason, never a zero | confirmed player count when no actual count exists |

Boundaries the projections keep: scheduled is not present; clocked-in is not
availability (the word is never used as a value); a change request is not an
approval and never alters coverage; a booking is not a start; a start is not a
finish; a missing clock-out or finish yields no duration; sold ball units are
entitlement evidence and say nothing about physical ball stores.

## Contracts

- **Identity.** `ContextAdmissionFacts(site_id, deployment_id, site_timezone,
  manifest_digest)` is projected by the composition root from the commissioned
  manifest. Every identifier is bounded and opaque
  (`^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$`): a pseudonymous worker reference such
  as `W-017` fits; a name or phone number never does.
- **Source profile** (`nxt-operational-context/source-profile/v1`): the source
  system, adapter id and version, `source_timezone` (interprets naive export
  timestamps), `stale_after_s`, `clock_skew_tolerance_s`, `demand_window_s`,
  and the declared `sku_ball_units` mapping. Its digest is part of the batch
  identity.
- **Batch.** `import_batch_id = "ocb_" + sha256({site_id, deployment_id,
  source_system, adapter_id, adapter_version, source_file_digest,
  profile_digest})[:24]`. The file's SHA-256 is the only thing retained about
  the raw file; `exported_at` is the declared coverage end when supplied.
- **Event** (`nxt-operational-context/event/v1`): `event_id = "oce_" +
  sha256({site_id, source_system, source_record_id, source_revision_key,
  event_type})[:24]`, a `content_digest` over the verbatim source content
  (profile-derived `mapped_*` fields excluded), `occurred_at` and
  `received_at` in UTC, `source_timezone`, `source_status`, an optional
  `correction` (`REPLACEMENT` or `REVERSAL` with its target), the typed
  payload, and provenance (adapter, file digest, row number, profile digest).
  Event types: `SHIFT_SCHEDULED`, `SHIFT_CANCELLED`, `CLOCK_IN`, `CLOCK_OUT`,
  `SHIFT_CHANGE_REQUESTED`, `SHIFT_CHANGE_APPROVED`, `SHIFT_CHANGE_REJECTED`,
  `ABSENCE_RECORDED`; `SALE_CAPTURED`, `SALE_REVERSED`; `SESSION_BOOKED`,
  `SESSION_STARTED`, `SESSION_FINISHED`, `SESSION_CANCELLED`,
  `PLAYER_COUNT_UPDATED`.
- **Time.** Timestamps are stored in UTC (`…Z`, microseconds). Operating days
  are derived in the commissioned site timezone (`Asia/Shanghai` for the China
  fixture) as half-open `[local midnight, next local midnight)` windows; an
  ambiguous or nonexistent midnight fails closed
  (`operating_day_boundary_ambiguous`).
- **Canonical JSON** is redeclared locally (sorted keys, compact separators,
  `ensure_ascii=False`, `allow_nan=False`) and pinned by a parity test to the
  repository's shared rule.

## Adapters and the privacy rule

Three synthetic CSV adapters with fixed column sets (`scripts/
operational_context_fixture/README.md` lists them). They are not vendor
adapters: no real sample export exists yet, so no vendor-specific mapping is
shipped. Every adapter applies the privacy rule first:

- only allow-listed columns are admitted; an unexpected, duplicate or missing
  column rejects the batch;
- a header carrying a forbidden token (names, contact details, pay, health,
  demographics, free text, performance) rejects the batch
  (`forbidden_column`), split on separators and CamelCase boundaries;
- values must match the bounded shape their column declares (identifier,
  code, count, timestamp); naive timestamps are interpreted in the declared
  source timezone and DST gaps or folds are refused;
- errors carry the row number, column name and reason code only; no value
  from the file ever reaches a journal, a projection, an API payload or a log;
  the raw file is not retained.

Whole-batch rejection is the V0 rule: when headers, schema or any row is
invalid the adapter returns a `BatchRejection` with every error, nothing is
admitted, and the composition root journals `import_batch_rejected` with the
safe diagnostics.

## Import decisions and the journal

`decide_import(records, result, received_at)` is a pure function of the
verified journal records and one adapter outcome, run by the composition root
inside the journal's exclusive lock (`JsonlJournal.append_via`):

- a batch whose `import_batch_id` is already committed appends one
  `import_batch_duplicate` record and no event;
- an event whose `event_id` is recorded with the same content is skipped; the
  same `event_id` with different content is a conflict and rejects the whole
  batch (`event_conflict`);
- a revision (same record id, later `correction_time`) supersedes the current
  head of its chain and keeps the original; a stale revision never supersedes
  a newer head; a reversal targets the captured sale it names, and an unknown
  original is counted as unmatched and never subtracted;
- record kinds are closed (`import_batch_committed`,
  `import_batch_rejected`, `import_batch_duplicate`, `event_recorded`,
  `source_profile_declared`), origins are `ADAPTER` and `SERVICE`, and the
  journal schema label is `nxt-operational-context/journal/v1`.

The journal is `nxt_edge_task.journal.JsonlJournal` (verified append-only
JSONL with a high-water anchor), wired only by
`simulation/scripts/operational_context_fixture.py`. A torn or rolled-back
journal fails closed: the reader reports `journal_unreadable` and nothing
partial is projected.

## Projections

`project_context(...)` is deterministic: the same records and the same
`as_of` always yield the same bytes.

- **Freshness** per source: `coverage_end` is the newest declared export time
  (or the newest recorded event when none was declared) across accepted
  batches; `age_s = max(0, as_of − coverage_end)`; the source is `stale` once
  the age reaches the profile's `stale_after_s`, `missing` when no batch was
  accepted. Every value of a stale source carries `source_status: stale`; a
  missing source yields `UNKNOWN` values with reasons.
- **Staffing today:** `scheduled_today`, `scheduled_now` (PLANNED, approved
  changes applied, cancellations removed), `confirmed_present_now`,
  `presence_unknown_now`, `present_unscheduled_now`, `present_scheduled_now`,
  `worked_intervals_today`, `open_clock_ins` (DERIVED),
  `confirmed_absent_today`, `approved_shift_changes_today`,
  `pending_change_requests` (SOURCE_RECORDED), `next_material_change`
  (PLANNED or UNKNOWN), a per-role table and the operating day.
- **Operations today:** sales (`ball_units_sold_today`, `recent_window`,
  `unmapped_sku_transactions_today`, `unmatched_reversals` DERIVED;
  `transactions_today`, `reversals_today` SOURCE_RECORDED) and play
  (`booked_sessions_today`, `booked_players_today`,
  `upcoming_booked_players`, `active_players_booked` PLANNED;
  `started_sessions_today` SOURCE_RECORDED; `active_sessions_now`,
  `active_players_confirmed`, `completed_sessions_today`,
  `sessions_missing_finish` DERIVED or UNKNOWN). A reversal is attributed to
  the day of the sale it reverses; the recent window is `UNKNOWN` when
  coverage ends before the window and flagged when coverage is partial.

## Composition, API and console

- `simulation/scripts/operational_context_fixture.py` loads the declared
  profiles, wires the journal under `<run root>/context/`, imports the three
  synthetic exports idempotently (launch, resume, restart and reset converge
  on the same recorded set), and exposes a read-only `ContextReader` whose
  `snapshot(as_of, basis)` re-derives the projection from the verified
  journal. `admission_from_manifest` projects the commissioned identity.
- `nxt_site_agent` gains the optional seam fields `CompositionSeam.clock`
  (`ClockSource` with basis `FIXTURE_DECLARED` or `SYSTEM_UTC`) and
  `CompositionSeam.context_for` (a per-run reader factory). Both default to
  `None`, in which case the business context is `unavailable` and nothing
  else changes. The service never names the leaf; it reads plain data.
- `GET /api/v0/supervisor-snapshot` composes, under one lock acquisition, the
  five existing projections verbatim plus `staffing`, `operations`,
  `operating_day`, `context_sources`, `physical_stores` and `machines`
  (explicitly unknown or labelled fixture facility state), an always-empty
  reserved `coverage_recommendations`, `exceptions`, `data_quality` and a
  `generation` block (`snapshot_id`, `generated_at`, `clock_basis`,
  `scenario_now`). Business-context failures never raise; facility-evidence
  read failures keep their existing codes (`ledger_unreadable`,
  `queue_unreadable`, `journal_unreadable` map to 503).
- The Manager Console reads that one snapshot per refresh and adds the
  compact "Staffing today" and "Operations today" sections; every value shows
  its label, a stale view downgrades every green badge, an unavailable
  context shows its code and nothing partial, and the prerendered page is the
  pending state.

## Fixture

Pilot Course A, operating date 2026-08-08 in `Asia/Shanghai`, declared fixture
clock 18:30 local (`2026-08-08T10:30:00Z`). Six pseudonymous workers
(`W-001` … `W-006`), 18 sales transactions including a void, a partial refund
and an unmapped SKU, and 8 bookings including a cancellation, an actual player
count and a session with no finish. Two files under `rejected/` demonstrate
whole-batch rejection (a forbidden header; one invalid row with its row
number). No name, phone number, address, pay, health record or free text
exists anywhere in the fixture.

## Boundaries and guards

`tests/operational_context/test_architecture.py` pins: a stdlib whitelist
(`csv`, `dataclasses`, `datetime`, `enum`, `hashlib`, `io`, `json`, `re`,
`typing`, `zoneinfo`), no repository imports, no clock, file, network or
randomness calls, no execution, language-model, HR, payroll, ranking,
schedule-write, inventory or advisory vocabulary, composition-root-only
importers, a bare-interpreter import, and wheel and CI registration. Every
sibling reverse guard and the Site Agent guard name the package so no existing
package can import or mention it.

## Not implemented

Vendor adapters and real sample exports; availability records; demand-versus-
coverage recommendations (`coverage_recommendations` stays empty and
reserved); any Kimi or other LLM integration; automatic schedule changes; POS
or workforce-system writeback; employee task apps; robot or equipment
control; per-row quarantine; a wall-clock expiry on the Manager API read;
physical inventory, washer or dispenser evidence (all explicitly unknown).
