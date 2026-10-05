# Operational Context Ingestion V0 — design specification

**Date:** 2026-10-05 · **Status:** Proposed design, awaiting approval. Nothing in
this document is implemented. No code, package, endpoint, console section, or
test described here exists on any branch.
**Builds on:** the merged Site OS layers on `main` and the *unmerged* Pilot Site
Agent service and Manager Console on `feature/pilot-site-agent-service-v0`
(see §1, a blocking finding).
**Architecture ladder:** business-system records → source adapters → normalized
operational events → operational-context projection → one Supervisor Snapshot →
existing Supervisor Console. Physical inventory, washing, dispensing, and
machine state stay explicitly unknown in this slice.

This slice gives the Site Agent trusted *business* context (staffing, ball
sales, golf activity) before any physical inventory sensor is connected. It is
read-only toward every external system and toward every existing truth owner.
The task calls the browser surface the Supervisor Console; the branch that
implements it calls the same component the Manager Console. This document uses
the task's term.

---

## 0. Decision summary

| Question | Decision | Gate outcome |
|---|---|---|
| Where do staffing, sales, and play records live? | A new filesystem-free, stdlib-only leaf package `nxt_operational_context` owning the normalized-event and import-batch record contracts, idempotency and correction rules, and pure context projections. | **Proceed** on ownership (no existing owner fits; §2) |
| Do they enter `FacilityState`? | No. `FacilityState.StaffState` is the robot-assist technician pool and `DemandState` is the simulation forecast plus dispenser draws. Business records are a separate fact class. The already anticipated `UpstreamInputs` seam is preserved, not used (§2.4). | Proceed |
| How are they persisted? | The composition root wires `nxt_edge_task.journal.JsonlJournal` under a new schema label, exactly as `nxt_edge_interventions` does; the leaf writes no files (§6). | Proceed (precedent-backed) |
| Where is the Supervisor Snapshot composed? | In the existing application boundary `nxt_site_agent`, as one additive `/api/v0` endpoint that embeds the five existing projections verbatim plus the new context sections, under one lock acquisition (§8). | Proceed, **conditional on §1** |
| Where does the agent's demand-versus-coverage advice live? | `nxt_pilot_ops`, as a second named policy with its own stdlib-only contracts and a sibling ledger; never a third decision engine. Every element is a versioned contract addition, so it is a separately gated second PR (§9). | **Reshape** (own gate) |
| On which branch is this built? | The Supervisor Console exists only on an unmerged branch that conflicts with `main` in 16 files. | **Pause** until the base-branch decision in §1.4 is made |

---

## 1. Implementation status and the base-branch finding (blocking)

### 1.1 What was inspected

- Working branch `claude/admiring-euler-vvhgx6` is identical to `origin/main`
  at `e3f63ca`; the worktree is clean.
- `main` contains no Supervisor Console, Manager API, snapshot, or
  trust-semantics component. A repository-wide search for `supervisor`,
  `Manager API`, `/api/v`, and `SupervisorSnapshot` returns nothing on `main`.
  `agent_runtime_v1.md` states there is no HTTP server on `main`.
- Remote branch `feature/pilot-site-agent-service-v0` (head `4b86d9f`, 10
  commits ahead of `main`, 15 behind, merge-base `22af25a`) adds the Pilot Site
  Agent service `simulation/nxt_site_agent`, the loopback Manager API
  `nxt-site-agent/api/v0`, the static console `apps/site-agent-console`, their
  tests, two composition-root scripts, and governance edits registering them.
  This is the Supervisor Console the task refers to.
- Remote branch `feature/edge-gateway-live-input-v0` (head `c358ecf`, 5 ahead,
  15 behind) carries a script-local `SiteClock` that maps UTC wire times into
  the commissioned timezone's operating day. It is cited below only as
  *unmerged precedent*; nothing here depends on it.

### 1.2 Status labels used in this document

| Component | Status |
|---|---|
| `nxt_facility`, `nxt_telemetry`, `nxt_site_runtime`, `nxt_agent_runtime`, `nxt_pilot_ops`, `nxt_commissioning`, `nxt_workflow_enablement`, `nxt_edge_observation`, `nxt_edge_task`, `nxt_edge_interventions`, `nxt_memory` | merged / current checkout |
| `nxt_site_agent`, `apps/site-agent-console`, `simulation/docs/site_agent_v0.md`, `scripts/site_agent_fixture.py`, `scripts/site_agent_demo.py` | implemented on unmerged branch `feature/pilot-site-agent-service-v0` |
| `SiteClock` in `scripts/edge_gateway_live_input_v0.py` | implemented on unmerged branch `feature/edge-gateway-live-input-v0` (precedent only) |
| Everything in §§3–10 | proposed |

### 1.3 Observed baseline (this environment: Python 3.13.14, uv 0.8.17, Node 22.22.0)

Run against a detached worktree of `feature/pilot-site-agent-service-v0` at
`4b86d9f` after `uv venv --python 3.13.14` and `uv sync --frozen --all-extras`:

| Command (from `simulation/` or `apps/site-agent-console/`) | Observed |
|---|---|
| `pytest -o addopts='' -q -p no:cacheprovider tests/site_agent` | 98 passed |
| `pytest ... tests/edge_observation tests/workflow_enablement` | 437 passed |
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `npm test` | 39 passed (5 files: 18 component, 11 api, 5 boundaries, 3 actions, 2 smoke) |
| `npm run build` | static export produced |

Facts that constrain later verification:

- On Python 3.11 the same branch fails 42 tests and errors 18: `slots=True`
  dataclasses in `nxt_edge_observation/contracts.py` call zero-argument
  `super()`, which Python 3.11 rejects; readiness then reports `NOT_READY` and
  every launch is refused. CI pins 3.13.14, so this is an interpreter artifact,
  but every verification of this slice must use 3.13.14.
- `uv lock --check` passes on `main` and **fails** on the branch: `main`
  reconciled `uv.lock` with the `edge-gateway` extra in `890e0bf` after the
  branch's merge-base. `--locked` installs on the branch as-is fail.
- `git merge-tree --write-tree main origin/feature/pilot-site-agent-service-v0`
  reports content conflicts in 16 files: every governance file under
  `.agent/`, `AGENTS.md`, `README.md`, `docs/ARCHITECTURE.md`, `docs/CI.md`,
  `simulation/README.md`, `simulation/pyproject.toml`,
  `.github/workflows/verification.yml`, and three sibling architecture tests.
- Both unmerged branches carry a stale `tests/pilot_ops/test_boundaries.py`
  whose `UPSTREAM_PACKAGES` lacks `nxt_edge_task` and `nxt_edge_interventions`,
  and the site-agent branch's `OTHER_PACKAGES` and `BANNED_FIRST_PARTY_MENTIONS`
  omit them too. A merge must keep `main`'s lists and cross-register
  `nxt_site_agent` in those two packages' guards.

### 1.4 The base-branch decision the owner must make

The task requires reusing the existing Supervisor Console and keeping its trust
semantics intact. That console is absent from `main`. Routing code through a
component absent from the target branch is forbidden by the architecture gate
(§1.4 of `.agent/workflows/architecture-review.md`). Options:

| Option | Consequence |
|---|---|
| **A (recommended).** Merge `feature/pilot-site-agent-service-v0` into `main` first (resolving the 16-file conflict, the lock check, and the guard-list drift), then build this slice on `main`. | Clean base; this slice becomes an additive change over merged contracts. The merge is outside this slice's scope and needs its own review. |
| **B.** Stack this slice on `feature/pilot-site-agent-service-v0` after bringing `main` into it. | Faster start; a stacked PR that cannot merge before its base, with two concurrent governance edits to reconcile. |
| **C.** Build the ingestion package and snapshot on `main` without the console and service. | Satisfies neither "reuse the existing Supervisor Console" nor "extend the current fragmented reads"; the snapshot would have no server or client. Not recommended. |

This document is written against the contracts on
`feature/pilot-site-agent-service-v0` at `4b86d9f` so it is correct under A or
B. It does not assume the merge has happened.

---

## 2. Fact class, ownership, and placement (architecture gate §§2–5)

### 2.1 The missing responsibility, stated explicitly

The placement table in `.agent/workflows/architecture-review.md` has no row for
*records produced by a business system of record about people, sales, and
bookings*. The gate says: "If no row fits, stop and write the missing
responsibility explicitly." It is:

> **Business operational-context evidence.** Facts a staffing, point-of-sale,
> or tee-sheet system has *recorded* (a shift was scheduled; a clock-in was
> registered; a transaction was captured, voided, or refunded; a tee time was
> booked, started, finished, cancelled, or no-showed). These are neither
> physical observations of the facility nor commissioned static facts nor
> simulation truth. They have their own lifecycle (import batches, idempotent
> re-import, non-destructive corrections) and their own quality model (source
> freshness, mapping coverage, batch validity).

The repository already anticipated the *category*: `nxt_telemetry` defines
`SourceType.EXTERNAL_SYSTEM`, its approved design names "POS, tee sheet" as
external systems, and `edge_observation_v0.md` routes `staff.site.busy` and
`staff.site.queued` to "a staffing/POS external-system adapter". Those seams
carry single-valued channel readings and assembled aggregates into
`FacilityState`; they do not carry transactions with corrections. §2.4 keeps
them intact.

### 2.2 Why no existing owner fits (rejected owners, recorded per gate §3)

| Candidate | Why rejected |
|---|---|
| `nxt_commissioning` | Owns immutable *static* physical facts; `deployment.md` says it "does not own live battery, pose, payload, inventory, task, **demand**, observation, availability, or transport state". It does own the site timezone and identity this slice binds to (§2.5). Its channel vocabulary is closed; a new `staff.*`/`pos.*` channel would be a commissioning, assembler, and parity-test change. |
| `nxt_telemetry` | Owns *observations* and the assembler. An `ObservationFrame` is "all observations visible at one instant", one scalar per channel, each needing `calibration_id` and `confidence`; a roster row or sale line is a many-field record with none of those. `QualityGate` rejects any stale or missing input, so a late tee-sheet export would block facility publication site-wide. `UpstreamInputs` is a fixed five-field aggregate hashed into every `envelope_id`. |
| `nxt_facility` | `FacilityState` is the frozen canonical downstream state. `StaffState(capacity, busy, queued_requests)` is the robot-assist technician pool (a simpy resource); `DemandState` is forecast buckets plus cumulative dispenser draws. Adding business facts would change every `envelope_id`, fire the twin and Shadow Ops exact-key guards, break builder parity and every hand-built fixture, and blur planned/recorded facts with assembled state; the repository has twice rejected new field groups for that reason. `nxt_facility.decisions` advises only over `FacilityState`. |
| `nxt_site_runtime` | Orchestration of observation assembly only; guard-tested to own no domain semantics and no network. |
| `nxt_agent_runtime` | Composition/lifecycle leaf; owns no domain semantics; single-policy by design. |
| `nxt_pilot_ops` | Owns policy trust, trace, workflow, ledger. It will *consume* the context projection for one named policy (§9); it must not own ingestion or projection, and its `OperationalSnapshot` digest is journaled into every evaluation id. |
| `nxt_memory` | Append-only history that must not feed the live loop; this slice's projection is live context by design. |
| `nxt_edge_observation` | Converts already-read *device* samples; explicitly names staffing facts as "system-of-record fact, not an edge device". |
| `nxt_workflow_enablement` | Readiness gating; "registration never implies implementation". No new workflow or prerequisite is registered by this slice. |
| `nxt_site_agent` (unmerged) | Application shell; guard-tested to own no facts and to mention no first-party package beyond three approved surfaces. It composes and serves; it must not become the owner. |
| `simulation/scripts/` | Composition roots without guards or wheel membership; a persistent versioned record contract with correction rules needs a guard, the same reason `nxt_edge_task` and `nxt_edge_interventions` became packages. |

### 2.3 Proposed owner: `nxt_operational_context` (new package)

A new package is admissible under gate §3 because the fact class is distinct,
the lifecycle (idempotent import, correction chains, freshness) is distinct,
the dependency position is explicit (a leaf), no existing owner fits, and a
mechanical guard is planned. It follows the `nxt_edge_interventions` shape: a
pure leaf that imports no other `nxt_*` package, receives upstream facts as
plain data, emits record specifications, and lets a composition root own
files, clocks, and processes.

| Owns | Does not own |
|---|---|
| Versioned normalized-event record contract and the three payload families (staffing, sales, play) | Any physical fact: inventory, washing, dispensing, machine state |
| `SourceAdapter` protocol, the three CSV adapters, the replay-fixture adapter, the source-profile contract | Any vendor API, transport, credential, browser automation, or scraping |
| Import-batch admission: identity check, all-or-nothing validation, row-level rejection report, duplicate and correction dispositions, the record specs for one batch | Writing files, opening files, locks, or anchors (the composition root wires the journal, §6) |
| Pure projection functions (staffing / demand / play context) over a verified record view at an explicit `as_of` | Scheduling, approval, payroll, pricing, or any write to a source system |
| Freshness classification given declared thresholds and the caller's clock value | Reading a wall clock, randomness, UUIDs, the filesystem, the network |
| The operating-day derivation from an IANA timezone handed in as plain data | Owning the timezone (commissioning does) |

Dependency rules, to be enforced by `tests/operational_context/test_architecture.py`
mirroring `tests/edge_interventions/test_architecture.py`:

- imports no other `nxt_*` package; site identity, commissioned timezone,
  manifest digest, and source profiles reach it as plain data;
- stdlib allow-list (whitelist, bans by omission): `__future__`,
  `collections`, `csv`, `dataclasses`, `datetime`, `decimal`, `enum`,
  `hashlib`, `io`, `json`, `math`, `typing`, `zoneinfo`. Banned by omission:
  `os`, `pathlib`, `fcntl`, `time`, `uuid`, `random`, `secrets`, `socket`,
  `http`, `urllib`, `subprocess`, `threading`, `asyncio`, `sqlite3`;
- banned call names as in the sibling guards (`now`, `utcnow`, `today`,
  `monotonic`, `perf_counter`, `uuid1`, `uuid4`, `random`, `randint`,
  `getenv`): every function that needs "now" takes `as_of: datetime`
  (timezone-aware) as an argument;
- no execution tokens, foreign-surface tokens, or the sibling guards'
  language-model patterns; additionally banned tokens `payroll`, `salary`,
  `wage`, `rank`, `approve_shift`, and the advisory vocabulary reserved to
  `nxt_facility.decisions`/`nxt_pilot_ops` (`rule_id`, `OPERATOR_INTERVENTION`,
  `DISPATCH_COLLECTOR`, `robot_down`, `assist_backlog`, `battery_reserve`);
- the package may not define classes named `Observation`, `ObservationFrame`,
  `FacilityState`, `AssemblyReport`, `UpstreamInputs`, `OperationalSnapshot`,
  `Recommendation`, `DecisionTrace`, or `CommissionedSite`;
- no existing package may import it except the designated consumers in §2.6,
  and its source may never contain the literal `nxt_site_agent`.

Canonical JSON: the package redeclares the repository rule in about twenty
lines, as `nxt_edge_task/contracts.py::canonical_json` does
(`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`,
`allow_nan=False`; timezone-aware datetimes as UTC ISO-8601 with microseconds
and a `Z` suffix; `-0.0` normalized to `0.0`; digests as `sha256` hex). A test
pins its output to `nxt_pilot_ops.serialization.canonical_json` for shared
sample values so a third divergent canonicalizer cannot appear. Monetary
amounts are carried as decimal strings, never floats. Identifier style follows
the repository: record and business ids are `<prefix>_<24 lowercase hex>`;
whole-document digests are `sha256:<64 hex>`.

### 2.4 Relationship to `FacilityState` and the anticipated `UpstreamInputs` seam

This slice **does not** populate `UpstreamInputs`, `staff.site.busy`,
`staff.site.queued`, or any `FacilityState` field. Reasons:

1. `UpstreamInputs.demand_balls_served` means balls served in the simulation
   model and feeds `DemandState`; sold entitlement is a contractual promise,
   not dispensing. Populating it from sales would change `FacilityState`
   demand provenance and every `envelope_id`.
2. `UpstreamInputs.forecast_balls_per_minute` is a forecast; producing one from
   sales history is follow-on slice 5 and must be a labelled estimate with its
   own versioned method.
3. `staff.site.busy`/`queued` count technicians occupied by robot assist
   requests, not presence or role coverage.

The seam is preserved: a later, separately approved projection from this
package's journal into `UpstreamInputs` or those channels can be built in a
composition root once the semantics are agreed. Until then the Supervisor
Snapshot shows the two fact classes side by side with their owners named
(§8.3) and never merges them. `deployment.md`'s "Not implemented" row listing
POS among absent physical telemetry transports is **unchanged** by this slice:
it ingests exported files and recorded fixtures, never a POS connection.

### 2.5 Identity and time binding

- `site_id` and `deployment_id` are the commissioned identity the Site Agent
  launched with (`RangeOpsLaunchPlan.site_id`/`deployment_id`). They reach the
  package as a plain-data `ContextAdmissionFacts(site_id, deployment_id,
  site_timezone, manifest_digest)`, the `nxt_edge_task.AdmissionFacts`
  pattern; `manifest_digest` must match the `sha256:<64 hex>` shape and is
  recorded on every batch record for provenance. A batch whose declared
  `site_id` differs is rejected whole (`identity_mismatch`), never partially
  imported.
- The **site timezone** for operating-day boundaries ("scheduled today", "next
  staffing change") is `CommissionedSite.timezone` (an IANA name validated with
  `zoneinfo.ZoneInfo` in `nxt_commissioning/validation.py`), supplied through
  `ContextAdmissionFacts`. Nothing on `main` consumes that field at runtime
  today; this slice is its first consumer. The operating day is the local
  calendar date of `as_of` in that zone; a day rolls at local midnight (a
  facility-close rollover is an open question, §14). Without a timezone the
  staffing and play day projections are `UNKNOWN` with reason
  `site_timezone_unavailable`; nothing defaults to UTC.
- The **source timezone** is a per-source profile declaration (an IANA name),
  because a tee sheet or POS export may stamp local wall time without
  offsets. Every event stores the original cell text (`occurred_at_source_text`),
  the normalized UTC instant (`occurred_at`), and the `source_timezone` used. A
  cell that already carries an explicit offset or `Z` is honoured as written;
  a local time that is ambiguous or non-existent under DST rules is a row
  error (§3.4). The operating-day mapping is a pure `zoneinfo` function inside
  the package; the unmerged `SiteClock` is cited as precedent for the refusal
  rules, not imported.
- **Wall clock.** `received_at` for a batch and `as_of` for a projection are
  supplied by the composition root. The merged precedent is
  `scripts/edge_task_gateway_v0.py`: `def utcnow() -> datetime: return
  datetime.now(timezone.utc)` and `clock: Callable[[], datetime] = utcnow`.
  Inside `nxt_site_agent` the clock must be a bare callable invoked as
  `clock()`, because its guard bans the callee *name* `now` whether attribute
  or bare. §8.4 defines how the snapshot carries two clock bases honestly.

### 2.6 Designated consumers and dependency direction

| Consumer | How it reaches the package |
|---|---|
| `simulation/scripts/` composition roots | import the public API; own the CSV file reads, the file digest, the journal instance (§6), and the clock |
| `nxt_site_agent` (unmerged) | receives the context projection as **plain data** through an extension of its existing `CompositionSeam` (the `adapter_reports`/`cycle_catalog` pattern), validates the projection's schema id, and composes the snapshot. `ALLOWED_FIRST_PARTY_MODULES` does not change; `nxt_operational_context` is **added** to `BANNED_FIRST_PARTY_MENTIONS` so the shell can never grow a direct dependency. |
| `nxt_pilot_ops.adapters` (§9, second PR) | a new designated adapter module imports the public projection contract only |

Dependency direction is fixed now: the new package is **upstream** of
`nxt_pilot_ops`. It is added to `UPSTREAM_PACKAGES` in
`tests/pilot_ops/test_boundaries.py`, which bans it from the policy core and
proves it never mentions `nxt_pilot_ops`; `test_adapter_is_the_only_upstream_dependency_boundary`
already permits any root outside its explicit ban set, so the adapter module
needs no relaxation. The package may never import `nxt_pilot_ops`, which is why
it redeclares the canonical JSON lines instead of importing them.

---

## 3. Source assumptions and adapter interfaces

### 3.1 What V0 assumes about sources

- Sources deliver **files or recorded fixtures**, not live APIs: one CSV file
  per source per import, UTF-8, header row, RFC 4180 quoting. Delimiter and
  header names are declared per source profile; no auto-detection.
- A source export is a **snapshot of records as of export time**. Re-exporting
  may repeat earlier rows unchanged (must be idempotent) or carry changed rows
  for the same source record id (a correction).
- Every source has a stable `source_record_id` per record. If a vendor export
  lacks one, the adapter refuses the profile; it never synthesizes ids from
  row position.
- Vendor-specific semantics (status vocabularies, SKU meanings, role codes) are
  **declared in the source profile**, not inferred. Unknown values are
  preserved verbatim and classified `UNMAPPED`, never dropped and never guessed.
- No vendor API, webhook, OAuth, screen automation, or scraping is designed or
  stubbed. `SourceAdapter` is the only seam a future vendor integration may
  fill.

### 3.2 `SourceAdapter` protocol

```python
class SourceAdapter(Protocol):
    adapter_id: str            # e.g. "csv.staffing/v0"
    adapter_version: str       # bumped on any parsing or semantic change
    source_kind: SourceKind    # STAFFING | SALES | PLAY

    def read(self, source: SourceInput, profile: SourceProfile,
             batch: BatchContext) -> BatchResult: ...
```

- `SourceInput` is a text stream plus `source_file_name` and the file's
  `sha256:` digest computed by the caller. Adapters never open paths.
- `SourceProfile` (`nxt-operational-context/source-profile/v1`) is the
  per-source declaration: `source_system` label, `source_timezone`, column
  mapping, status vocabulary, role-code mapping (staffing), SKU-to-entitlement
  table (sales), activity-type vocabulary (play), and the mandatory
  `stale_after_s` threshold (§7.1). Profiles are versioned JSON documents
  loaded with duplicate-key rejection; their content digest is recorded on
  every batch.
- `BatchContext` carries `ContextAdmissionFacts`, `import_batch_id`,
  `received_at`, and the profile digest.
- `BatchResult` is either `NormalizedBatch(events=...)` or
  `BatchRejection(errors=(RowError(file, row_number, column, reason), ...))`.
  An adapter never returns a partial batch.

### 3.3 The three CSV adapters and the replay adapter

| Adapter | Required columns (via profile mapping) | Optional columns |
|---|---|---|
| `csv.staffing/v0` | `record_id`, `record_type` (§5.1 enum), `staff_ref`, `occurred_at` | `role_code`, `shift_start`, `shift_end`, `status`, `related_record_id`, `availability`, `correction_time` |
| `csv.sales/v0` | `transaction_id`, `transaction_time`, `sku`, `quantity`, `status` | `correction_time`, `original_transaction_id`, `currency`, `amount`, `register_id` |
| `csv.play/v0` | `record_id`, `record_type` (§5.3 enum), `scheduled_start` or `occurred_at` | `actual_start`, `actual_finish`, `player_count`, `status`, `activity_type`, `course_or_area`, `related_record_id`, `correction_time` |
| `replay.fixture/v0` | reads a committed JSON fixture of already-normalized events; used by tests and the demo | — |

The replay adapter exists so acceptance tests can exercise projection,
correction, and snapshot behavior without coupling them to CSV parsing.
Fixture CSVs, profiles, and replay files live under
`simulation/tests/operational_context/fixtures/`; the demo script reads the
same files by path argument. No `configs/` directory is added, so
`scripts/validate_configs.py` is untouched.

### 3.4 Row-level failure contract

A row error names `source_file_name`, 1-based `row_number`, the `column` when
applicable, and a stable `reason` code: `missing_required_column`,
`empty_required_value`, `unparseable_timestamp`, `ambiguous_local_time`,
`nonexistent_local_time`, `negative_quantity`, `unknown_record_type`,
`unknown_status`, `duplicate_record_in_batch`, `identity_mismatch`,
`profile_mismatch`, `supersession_target_mismatch`, `illegal_supersession`,
`forbidden_column_present`. Any row error rejects the **whole batch** (§6.3).
Unknown SKU or role codes are *not* row errors; they are preserved and surface
as `UNMAPPED` in projections and data quality.

---

## 4. Common event envelope

Every normalized event is the `payload` of one journal record of kind
`event_recorded` (§6). Its fields:

| Field | Type | Rule |
|---|---|---|
| `event_id` | `oce_<24 hex>` | `sha256` over canonical JSON of the identity tuple `(site_id, source_system, source_record_id, source_revision_key, event_type)`. `source_revision_key` is the source's own correction time when the row carries one, else the canonical digest of the normalized payload. Same record and content ⇒ same id ⇒ duplicate on re-import. The seed excludes `received_at`, `import_batch_id`, and row position, so re-imports never mint new ids. |
| `site_id`, `deployment_id` | `str` | from `ContextAdmissionFacts`; mismatch rejects the batch |
| `source_system` | `str` | profile label, e.g. `"tee-sheet-export"`; never a vendor product claim |
| `source_record_id` | `str` | verbatim from the source |
| `event_type` | enum | one of the §5 vocabularies |
| `occurred_at` | UTC ISO-8601 `Z` | normalized instant (may legitimately be in the future for schedules and bookings; this is a deliberate divergence from the Site Runtime rule that rejects future sample times) |
| `occurred_at_source_text` | `str` | the original cell text, preserved |
| `received_at` | UTC ISO-8601 `Z` | the batch's `received_at` from the composition root; may not precede `occurred_at` for recorded (non-scheduled) event types |
| `import_batch_id` | `ocb_<24 hex>` | `sha256` over `(site_id, source_system, adapter_id, source_file_digest, profile_digest)`; two imports of the same file produce the same id |
| `source_timezone` | IANA `str` | from the profile |
| `source_status` | `str` | verbatim source status text |
| `status_class` | enum | the profile's mapping of `source_status` into the closed §5 vocabulary, or `UNMAPPED` |
| `correction` | object or null | `{kind: REPLACEMENT | REVERSAL, supersedes_event_id, source_correction_time}` (§6.2) |
| `provenance` | object | `{adapter_id, adapter_version, profile_digest, source_file_name, source_file_digest, row_number, manifest_digest}` |
| `schema` | `str` | `"nxt-operational-context/event/v1"` |
| `payload` | object | the family-specific fields of §5 |

The journal record wrapping the event carries its own `record_id`
(`rec_<24 hex>`, which includes the journal sequence) and `recorded_at_utc`;
cross-batch deduplication uses `event_id`, never `record_id`.

---

## 5. Domain payloads and boundaries

Two vocabularies are used and never mixed. **Source freshness status** is the
repository's lowercase `ok | stale | missing` (§7.1). **Value evidence labels**
are the five the task requires:

| Label | Meaning in this slice |
|---|---|
| `SYSTEM_RECORDED` | the source system recorded this fact (a clock-in, a transaction, a booking) |
| `DERIVED` | computed deterministically from system-recorded facts and declared mappings (a count, a sum, a duration from two recorded instants) |
| `ESTIMATED` | a labelled estimate with a named method; **not produced by any V0 projection**, reserved for follow-on forecasting |
| `UNKNOWN` | no admissible evidence, or the source is stale or missing, or the mapping is unavailable |
| `MEASURED` | reserved for sensor evidence; **never** produced by this package |

### 5.1 Staffing

Event types (closed enum `StaffingEventType`): `SHIFT_SCHEDULED`,
`SHIFT_CANCELLED`, `CLOCK_IN`, `CLOCK_OUT`, `SHIFT_CHANGE_REQUESTED`,
`SHIFT_CHANGE_APPROVED`, `SHIFT_CHANGE_REJECTED`, `ABSENCE_RECORDED`,
`LATE_RECORDED`, `AVAILABILITY_DECLARED`.

Payload: `staff_ref`, `role_code` (verbatim), `operational_capability` (the
profile's mapping of `role_code` into a declared site vocabulary such as
`washer`, `dispenser`, `front_desk`, `range_picker`, or `UNMAPPED`),
`shift_start`/`shift_end` (UTC, for scheduled and approved events),
`related_record_id` (an approval or clock event points at its shift),
`availability` (only for `AVAILABILITY_DECLARED`; verbatim plus mapped class).
Human-origin records (approvals) also carry explicit no-effect fields in the
`nxt_edge_interventions` style: `schedule_system_effect: "none"`,
`hr_system_effect: "none"`, `payroll_effect: "none"`.

Boundaries the projection enforces (each is an acceptance test in §11):

- scheduled ≠ present: `confirmed_present` requires a `CLOCK_IN` for the
  staff_ref within the shift's admission window and no later `CLOCK_OUT`;
- clocked-in ≠ available: `available_now` is populated **only** from
  `AVAILABILITY_DECLARED`; otherwise `UNKNOWN`;
- requested ≠ approved: coverage uses `SHIFT_SCHEDULED` plus
  `SHIFT_CHANGE_APPROVED` (which supersedes the shift it references);
  `SHIFT_CHANGE_REQUESTED` appears only in a `pending_requests` count;
- missing clock events ⇒ `presence = UNKNOWN`, never `absent`;
  `confirmed_absent` requires `ABSENCE_RECORDED`;
- a `CLOCK_IN` without `CLOCK_OUT` yields no duration; no duration is ever
  inferred from the scheduled end;
- no field, derived value, ranking, or ordering of staff by any performance
  notion exists; projections expose no per-person durations at all.

**Privacy boundary (minimum operational identity).** The staffing profile may
map exactly these columns and the adapter **discards every other column before
normalization**: `record_id`, `record_type`, `staff_ref`, `role_code`,
timestamps, `status`, `related_record_id`, `availability`, `correction_time`.
`staff_ref` is the source's opaque identifier (an employee number or badge
id). Names are not ingested in V0. Profile validation refuses any mapping to a
column named like `name`, `salary`, `pay`, `wage`, `rate`, `address`, `phone`,
`mobile`, `ssn`, `national_id`, `dob`, `birth`, `medical`, `sick_note`,
`email`, or `home` (`forbidden_column_mapping`), and the adapter refuses a
file whose header contains any of those even unmapped
(`forbidden_column_present`), so such an export cannot be imported until it is
re-exported without them. The composition root never persists raw staffing
files; only the `sha256:` digest is recorded. Projections expose counts, role
coverage, and the next change time; no per-person record reaches the
Supervisor Snapshot or any endpoint in this slice.

### 5.2 Sales and ball demand

`SaleEvent` payload: `source_transaction_id`, `transaction_time`,
`correction_time` (null unless a correction), `sku`, `quantity`,
`ball_entitlement` (integer or null), `entitlement_mapping`
(`{sku, balls_per_unit, mapping_version}` or `UNMAPPED`), `status_class`
(`CAPTURED`, `VOIDED`, `CANCELLED`, `REFUNDED`, `PARTIALLY_REFUNDED`,
`UNMAPPED`), `original_transaction_id` (for corrections), `currency` and
`amount` (decimal string; both optional and carried only when supplied),
`register_id` (optional).

Boundaries:

- `ball_entitlement` is a **sold entitlement**, labelled so everywhere; no
  projection key may contain `inventory`, `dispensed`, `available_balls`,
  `stock`, `clean`, or `revenue` (guard-tested);
- the SKU table is declared data with a `mapping_version`; a SKU absent from the
  table yields `ball_entitlement = null` and `UNMAPPED`, and the demand
  projection reports `unmapped_transactions` and `unmapped_quantity` beside the
  mapped totals rather than silently excluding them;
- a refund, void, or cancellation is a `REVERSAL` correction (§6.2): the
  original `CAPTURED` event stays in the journal, the reversal is appended,
  and windowed totals subtract the reversed entitlement with both ids in
  evidence; a partial refund subtracts the refunded quantity's entitlement;
- `amount` and `currency` are stored when supplied and are **not** summed,
  averaged, or projected in V0; no revenue, savings, or price field exists, and
  no ROI formula is reimplemented.

### 5.3 Golf activity

Event types (closed enum `PlayEventType`): `TEE_TIME_BOOKED`,
`BOOKING_CANCELLED`, `BOOKING_NO_SHOW`, `SESSION_STARTED`, `SESSION_FINISHED`,
`PLAYER_COUNT_UPDATED`.

Payload: `session_ref`, `scheduled_start`, `actual_start`, `actual_finish`,
`player_count`, `status_class` (`BOOKED`, `CANCELLED`, `NO_SHOW`, `STARTED`,
`FINISHED`, `UNMAPPED`), `activity_type` (profile vocabulary: `range`,
`course_9`, `course_18`, `lesson`, `event`, or `UNMAPPED`), `course_or_area`
(verbatim label). `PLAYER_COUNT_UPDATED` is a `REPLACEMENT` correction of the
session's current head and changes only `player_count`.

Boundaries:

- booked ≠ started: `upcoming_booked_players` counts `BOOKED` sessions with
  `scheduled_start` inside the configured look-ahead window and no `STARTED`,
  `CANCELLED`, or `NO_SHOW` evidence;
- started ≠ finished: `confirmed_active_players` counts sessions with a
  `SESSION_STARTED` and no `SESSION_FINISHED`, and the projection states
  `basis = "SESSION_STARTED events"` and the newest play event time used;
- `duration` is computed only when both `actual_start` and `actual_finish`
  are system-recorded for the same `session_ref`, finish is after start, and
  neither instant is `UNMAPPED` or ambiguous; otherwise `UNKNOWN`. Scheduled
  start is never used as a start proxy;
- completed-session statistics (`count`, `min`, `median`, `max`) use only
  sessions passing that rule; the projection reports how many finished sessions
  were excluded and why.

---

## 6. Import, idempotency, corrections, and persistence

### 6.1 Persistence mechanism (decision)

The repository has three journal implementations and tolerates no fourth
without reason. `nxt_edge_interventions` shows the pattern this slice adopts:
the leaf is filesystem-free and emits `RecordSpec`-shaped specifications; the
composition root instantiates `nxt_edge_task.journal.JsonlJournal` with the
leaf's schema label and closed vocabularies. For this slice the composition
root wires:

```python
JsonlJournal(path, schema="nxt-operational-context/journal/v1",
             allowed_kinds=CONTEXT_RECORD_KINDS,
             allowed_origins=frozenset({"ADAPTER", "OPERATOR", "SERVICE"}))
```

Record kinds (`CONTEXT_RECORD_KINDS`): `context_started`,
`import_batch_rejected`, `import_batch_duplicate`, `event_recorded`,
`event_conflict`, `import_batch_committed`. What this buys, by precedent:
newline-terminated canonical lines re-verified byte-for-byte on read, POSIX
`fcntl` advisory locking, a sibling high-water anchor (`<journal>.hwm`, schema
`nxt-edge-task/journal-anchor/v1`, written with fsync, atomic replace, and
directory sync after each append), rollback detection as state loss, and the
guarantee that **all specs returned by one `append_via` builder are written
in one `write()` + fsync under one lock**, so a batch is all-or-nothing at the
line boundary. Scripts importing `nxt_edge_task.journal` are the existing
`edge_intervention_service_v0.py` precedent; the new package's own guard
asserts it never imports `nxt_edge_task`, and the composition root is the only
importer. POSIX-only persistence is inherited and stated.

### 6.2 Import pipeline

```text
composition root: open file, sha256 digest, clock() -> received_at
    -> adapter.read(SourceInput, SourceProfile, BatchContext)   # pure
    -> journal.append_via(lambda records:
           decide_import(records, batch, received_at).specs)    # pure builder, run under the lock
    -> projections recompute from journal.read() on demand; nothing is cached as truth
```

`JsonlJournal.append_via(builder)` runs the builder inside the exclusive lock
over every verified record and writes all returned specs in one `write()` plus
fsync before advancing the anchor, so `decide_import` sees a consistent view
and its output lands atomically. `decide_import(records, batch, received_at)`
is pure and applies, in order:

1. **Identity**: `site_id`/`deployment_id` mismatch ⇒ one
   `import_batch_rejected` spec, zero events.
2. **Duplicate batch**: an `import_batch_id` that already has
   `import_batch_committed` ⇒ exactly one `import_batch_duplicate` audit
   record naming the prior record id, zero events (the receiver-ledger
   precedent; never one record per duplicate row).
3. **Row validity**: any `RowError` ⇒ one `import_batch_rejected` spec with the
   complete error list, zero events.
4. **Per-event disposition** for each normalized event against the view:
   - `event_id` already recorded ⇒ not appended; counted and listed in the
     batch record's `duplicate_event_ids`;
   - same `(source_system, source_record_id)` exists with different content
     **and** the row carries a correction indicator (a correction time or a
     reversal status) ⇒ a correction event linked to the current chain head
     (§6.3);
   - same key, different content, **no** correction indicator ⇒ an
     `event_conflict` record naming both ids; both retained, the key marked
     `CONFLICT`, no projection change, surfaced in data quality (the
     `conflicting_replay` precedent). The only sanctioned way to change an
     accepted fact is a correction.
5. **Commit**: `event_recorded` specs, then `event_conflict` specs, then the
   single `import_batch_committed` spec **last**, so a torn write can only
   leave events without a commit marker.

### 6.3 Corrections and supersession

The journal is append-only; nothing is edited or deleted. Rules, drawn from
`nxt_course_world_model.validate_revision`, the `ModifiedRecommendation`
pattern, and the Edge terminal-conflict gate:

| Situation | Representation |
|---|---|
| Source re-exports a record with changed content and a newer correction time | New event, new `event_id`, `correction = {kind: REPLACEMENT, supersedes_event_id: <current head>, source_correction_time}`. The prior event remains. Projections read the chain head. |
| Refund, void, or cancellation of a sale | New `SaleEvent` with the reversing `status_class`, `correction = {kind: REVERSAL, supersedes_event_id: <captured event>}`, and `original_transaction_id`. A reversed original is terminal: a later `REPLACEMENT` of it is `illegal_supersession`. |
| Approved shift change | `SHIFT_CHANGE_APPROVED` with `related_record_id` of the request and `correction = {kind: REPLACEMENT, supersedes_event_id: <the SHIFT_SCHEDULED head>}` when the profile supplies the link; without the link the approval is recorded, coverage keeps the original shift, and data quality reports `unlinked_approval`. |
| Supersession target is not the current head | `supersession_target_mismatch` row error; the batch is rejected. |
| Two corrections name the same head (across batches) | Both retained; the key marked `CONFLICT` via `event_conflict`; no resolver; surfaced in data quality. |
| A correction arrives before the record it corrects | Admitted; `supersedes_event_id` recorded as `unresolved`; the chain is `UNKNOWN` for that key until the original arrives. Nothing is fabricated. |
| The same correction re-imported | Same `event_id` ⇒ duplicate ⇒ no second subtraction. |

Recorded order (journal sequence) strictly increases; `occurred_at` and
`source_correction_time` may be back-dated. This separation of effective time
from recording order is what lets legitimate late corrections in.

### 6.4 Atomic rejection and torn-tail handling

A batch is accepted only if every row normalizes and every disposition rule
passes. Otherwise no event is appended; one `import_batch_rejected` record with
the complete `errors` list is appended (so the rejection is auditable); the
projection and the Supervisor Snapshot are unchanged except that data quality
reports `last_import_outcome = REJECTED` for that source with the error count
and file name. A reader that finds `event_recorded` records after the last
`import_batch_committed` for their batch treats them as an uncommitted tail:
it excludes them from projections and reports `torn_tail` with the batch id in
data quality; the next import of that batch recommits normally because the
tail's `event_id`s are then duplicates of themselves and the commit marker
lands.

### 6.5 Storage layout (within the existing run directory)

```text
<out>/run-NNN/<site_id>/<deployment_id>/
  context/
    profiles/<source_system>.<digest>.json     # the exact profile used (content-addressed)
    context_journal.jsonl                      # nxt-operational-context/journal/v1
    context_journal.jsonl.hwm                  # high-water anchor
```

`context/` sits beside the existing `service/` directory and the canonical
workflow evidence root; it is neither Site Runtime evidence nor the Shadow Ops
ledger. Raw source files are **not** retained (§5.1). The one-process-per-runs-
directory rule of the Site Agent is inherited. At resume the composition root
re-verifies the journal (schema, anchor, identity); a failure marks context
`UNAVAILABLE` in the snapshot without touching the existing runtime's
fail-closed behavior.

---

## 7. Operational projections

All projection functions are pure: `project_*(view, config, as_of,
admission_facts) -> ...Context`. Every numeric field is wrapped as
`{value, label, evidence, freshness}` where `label` is a §5 evidence label.

### 7.1 Freshness model (shared)

```text
status  ∈ {ok, stale, missing}            # the repository's ObservationStatus vocabulary
freshness = {status, as_of, newest_event_occurred_at, newest_batch_received_at,
             age_s, stale_after_s, source_system}
```

- `stale_after_s` is **mandatory** in every source profile; profile validation
  refuses a profile without it, so no fourth status value is needed and no
  default is invented;
- `age_s = as_of − newest accepted batch received_at` for that source (a quiet
  source with a current export is not stale); the newest event time is shown
  beside it; `missing` means no accepted batch exists for the source;
- a `stale` or `missing` source forces every status-bearing value derived from
  it (`confirmed_present`, `confirmed_active_players`, `sales_rate`,
  `scheduled_today`) to `label = UNKNOWN`, while the last known numbers remain
  visible under `last_known` with their own timestamp. This makes acceptance
  test 10 hold at the data layer, not only in the UI.

Thresholds and windows are composition-root configuration (the
`EdgeTaskConfig.stale_after_s` pattern), not commissioned facts; changing one
is not a commissioning revision.

### 7.2 Staffing context

| Field | Derivation | Label |
|---|---|---|
| `operating_day` | `as_of` in the commissioned timezone → local date | DERIVED |
| `scheduled_today` | count of staff_refs with an effective (`SHIFT_SCHEDULED` or superseding `SHIFT_CHANGE_APPROVED`) shift overlapping the operating day | DERIVED |
| `confirmed_present` | staff_refs with `CLOCK_IN` ≥ (shift_start − `clock_in_admission_before_s`) and no later `CLOCK_OUT`, as of `as_of` | DERIVED |
| `confirmed_absent` | staff_refs with `ABSENCE_RECORDED` for the day | SYSTEM_RECORDED |
| `presence_unknown` | scheduled − confirmed_present − confirmed_absent (never negative; a mismatch is a data-quality issue) | DERIVED |
| `by_operational_capability` | per declared capability `{scheduled_now, confirmed_present_now, unknown_now}`; `UNMAPPED` is its own row | DERIVED |
| `next_material_change` | the earliest future instant at which any capability's scheduled headcount changes (shift start or end, or approved change) with the capability and delta; `null` when none remains today | DERIVED |
| `approved_shift_changes` | count and list of `{related_record_id, capability, effective_from}` for today | SYSTEM_RECORDED |
| `pending_change_requests` | count only; never affects coverage | SYSTEM_RECORDED |

`clock_in_admission_before_s` is declared configuration; without it
`confirmed_present` is `UNKNOWN`.

### 7.3 Demand and play context

| Field | Derivation | Label |
|---|---|---|
| `entitlement_sold` | per configured window (for example last 30, 60, 180 minutes and operating day to date): sum of `ball_entitlement` over effective `CAPTURED` events minus reversals, with `mapped_transactions`, `unmapped_transactions`, `unmapped_quantity` | DERIVED |
| `sales_rate` | entitlement per hour over the shortest window, only when the sales source is `ok` | DERIVED or UNKNOWN |
| `upcoming_booked_players` | §5.3, look-ahead window configured | DERIVED |
| `confirmed_active_players` | §5.3, with `basis` and the newest play event time | DERIVED or UNKNOWN |
| `completed_session_duration` | §5.3 statistics with exclusion counts | DERIVED |
| per-source `freshness` | §7.1 | — |

No field converts entitlement into inventory, consumption, or dispensing.

### 7.4 Traceability

Every value's `evidence` lists the `event_id`s (and through them the
`import_batch_id`s) that produced it, or, for large windows, the window bounds
plus the batch ids and a `derivation_digest` over the sorted contributing
event ids. A test reconstructs each projected number from the cited events
alone (acceptance test 12).

---

## 8. Supervisor Snapshot and Manager API

### 8.1 Endpoint and versioning decision

The task suggests `GET /api/v1/supervisor-snapshot`. The existing transport is
`nxt-site-agent/api/v0`; the console's boundary test allows only `/api/v0`
paths, `decode()` rejects any other envelope schema, and the branch docs state
no API surface exists outside `/api/v0/`. Two consistent options:

| Option | Trade-off |
|---|---|
| **A (proposed).** Additive `GET /api/v0/supervisor-snapshot` whose `data` carries `snapshot_schema = "nxt-site-agent/supervisor-snapshot/v1"`. | Existing endpoints, envelope, error codes, tests, docs, and the console boundary test stay intact; the snapshot versions itself. |
| B. New transport version `/api/v1/…`. | Coordinated edits to `lib/api.ts`, `boundaries.test.ts`, `contracts.py`, `test_api.py`, the README, `site_agent_v0.md`, `AGENTS.md`, and the package map, plus two coexisting envelope schemas. |

A is proposed. If the owner prefers the literal `/api/v1` path, B is a
mechanical change but should be its own commit.

### 8.2 One generation, one identity

The only coherent multi-source read on the branch today is
`briefing_snapshot()`: one `with self._lock:` acquisition that excludes
`advance` and `respond`. Every other endpoint reads live evidence
independently, and the console's `Promise.all` over five endpoints can render
state at sequence N beside recommendations from N+1. The new
`SiteAgentService.supervisor_snapshot(as_of)` mirrors the briefing: under one
lock acquisition it calls the **existing** `health_snapshot()`,
`state_snapshot()`, `recommendations_snapshot()`, `briefing_snapshot()`, and
`fixture_snapshot()` once each (one `_ledger_index()` read for the generation),
calls the context provider once with the same `as_of`, and assembles:

```text
snapshot_schema   "nxt-site-agent/supervisor-snapshot/v1"
snapshot_id       "svs_" + sha256(canonical JSON of data without snapshot_id)[:24]
generation        {generated_at (UTC ISO Z, from the injected clock), scenario_time_s,
                   clock_basis: FIXTURE_DECLARED | SYSTEM_UTC}
health, state, recommendations, briefing, fixture     # the five existing payloads, verbatim
staffing, demand, play                                 # §7 context sections
physical_stores, machines                              # explicit unknown shapes (§8.3)
exceptions        {facility: briefing.exceptions verbatim, context: [...]}
data_quality      {facility: state.quality verbatim, context: {...}, read_errors: [...]}
```

Per-source read failures never blank the snapshot: they land in
`data_quality.read_errors` exactly as `briefing_snapshot()` records
`evidence_unreadable`, and the endpoint never raises for them. The
`snapshot_id` needs `hashlib`, which is outside the branch's
`ALLOWED_STDLIB_ROOTS`; this slice adds `hashlib` to that whitelist (a
one-line guard widening, justified by the repository-wide content-addressed id
convention) and updates the branch `AGENTS.md`/package-map sentence that lists
the whitelist. New error codes get explicit `_STATUS_BY_CODE` entries.

### 8.3 Section rules

| Section | Content and owner |
|---|---|
| `health`, `state`, `recommendations`, `briefing`, `fixture` | the five existing projections, byte-identical to their endpoints; owners unchanged (`health.mode_label` stays `SERVICE_MODE_LABEL`; `fixture_mode` and `source_type: "fixture"` describe the existing runtime source, not the context sources) |
| `staffing`, `demand`, `play` | §7, owner `nxt_operational_context`, each carrying `data_class: "BUSINESS_RECORDS"` and `source_kind ∈ {csv_file, replay_fixture}` per source so the console can say "SYSTEM-RECORDED (CSV import)" |
| `physical_stores` | `{available: false, reason: "no physical inventory source connected", fields: {clean_available, clean_sensed, in_wash, dirty_buffered, awaiting_wash}}` as an explicit shape, never omitted keys. The two dispenser channels the existing fixture path publishes stay in `state.dispenser`, with their `SourceReference` status and source type, so in fixture mode they read as simulated data under the disclaimer. Known-ness is derived from `AssemblyReport` and `source_references`, never from `FacilityState` values, because `FacilityState` is a total contract that backfills zeros. |
| `machines` | `{available: false, reason: "no machine state source connected", fields: {washer_running, washer_fault, dispenser_state}}` |
| `recommendations` | the existing queue projection, owner and policy identity preserved, nothing ranked or merged; §9 adds a second owner-labelled list later |
| `exceptions.context` | rejected batches, duplicate batches, conflicts, torn tails, unmapped SKUs and roles, unlinked approvals, each with `owner: "nxt_operational_context"` and the ids involved |
| `data_quality.context` | per source `{source_system, source_kind, last_import_outcome, last_batch_id, received_at, error_count, unmapped_counts, profile_digest, freshness}` plus `context_available: bool` and `reasons` |

### 8.4 Two clocks, stated honestly

The branch deliberately uses scenario time (latest observation timestamp) and
bans wall-clock reads so evidence is byte-identical across runs. Production
operational context needs a real "now" for freshness. The design keeps both
explicit and never subtracts across them:

- the composition root owns the clock and passes it through the seam
  (`CompositionSeam.clock: Callable[[], datetime]`); the fixture seam supplies a
  **declared** fixture clock whose operating day equals the launch plan's
  `simulation_midnight_iso` date, asserted by a fixture consistency test;
  production supplies `utcnow` from the script;
- `generation.clock_basis` is `FIXTURE_DECLARED` or `SYSTEM_UTC` and the
  console shows it in text;
- scenario time continues to drive the existing dispenser reading age and
  `responded_at` exactly as today; `health_snapshot()` and every existing
  endpoint stay clock-free so the branch's byte-identity tests keep passing;
- a mismatch of clock bases is a data-quality issue, never a number.

Pre-existing risk recorded for the owner: the `main` pilot fixture anchors
scenario midnight at `2026-08-08T00:00:00+00:00` while the same manifest
declares `"timezone": "Asia/Shanghai"`, and nothing cross-checks them.
Business context will use the commissioned zone; facility facts use the plan's
anchor; the fixture must be made consistent or the mismatch declared (§14).

### 8.5 Console changes (two compact sections only)

- `app/page.tsx` fetches `client.supervisorSnapshot()` once for everything it
  renders. A pure `lib/snapshot.ts::splitSnapshot(snapshot): ConsoleData`
  hands the embedded `health`, `state`, `recommendations`, `briefing`, and
  `fixture` to the existing panels with **unchanged prop shapes**, proven by a
  test that deep-equals its outputs to the existing fixture builders. No vitest
  test imports `page.tsx` today, so the 39 existing cases are unaffected by the
  acquisition change; `tsc`, the boundary test, and the HTTP smoke (banner,
  "Site Agent", "Manager Console" text in the initial render) still gate it.
  The last-good-view rule now applies to the whole snapshot: one generation or
  the previous one, never a mix.
- New `StaffingTodayPanel`: scheduled, confirmed present, unknown (never
  shown as absent), role coverage table (`scheduled_now / present_now /
  unknown_now` per capability, `UNMAPPED` row shown), next material change,
  approved shift changes. Every number carries its evidence label badge and its
  source freshness in text.
- New `DemandNowPanel`: entitlement sold per window with mapped/unmapped
  counts, sales rate (or `UNKNOWN` with reason), active players with basis and
  freshness, upcoming bookings, completed-session duration statistics with
  exclusion counts, per-source freshness and `clock_basis`.
- Implementation constraints from the branch guards: no new runtime npm
  dependency; no `WebSocket`/`EventSource`/browser storage; no `http(s)://`
  strings; a null-safe `formatCount` returning "—" never "0"; reuse `Badge`,
  `Section`, `KeyValue`, `EmptyNote` and existing `.panel`/`.kv-grid` classes;
  do not reuse the inventory-specific `.inventory-*` classes; unique section
  titles (they double as aria-labels). No other visual redesign.
- No raw transactions, no employee rows, no names anywhere. No authenticated
  drill-down exists because the service has no authentication; that is a
  separate gate, not an unauthenticated endpoint.

Trust semantics preserved and extended (each a Vitest case): explicit no-data
states instead of zeros; `UNKNOWN`/`stale`/`missing` as text, not color alone;
never literal `null`/`undefined`; disclaimer end to end; schema check on the
envelope and `snapshot_schema` check on the data; `/api/v0/supervisor-snapshot`
appended to the Python `test_api.py` schema/disclaimer and read-only endpoint
lists so the new route is guarded like the others.

---

## 9. Agent behavior (advisory; a separately gated second PR)

### 9.1 Semantic owner and divergence contract

Owner: `nxt_pilot_ops`. The gate assigns to it "a named policy whose purpose
includes explicit evaluation, decision trace, trust evidence, human workflow,
or ledger records"; demand-versus-coverage preparation advice needs all five.
`nxt_facility.decisions` is excluded because its rules must be pure functions
of `FacilityState`, and the inputs here are business records.

Duplication search result. Rule ids in `nxt_facility/decisions.py` (code emits
nine; two documents still say eight): `stockout_in_progress`,
`stockout_dirty_supply`, `stockout_demand_bound`, `battery_reserve`,
`robot_down`, `assist_backlog`, `idle_capacity`, `payload_stranded`,
`station_buffer_pressure`. Two are adjacent in theme and are named in the
divergence contract:

| Existing rule | What it reads | Relationship declared here |
|---|---|---|
| `stockout_demand_bound` | the simulation/telemetry **forecast** (`DemandState.forecast_balls_per_minute`) against washer throughput | **Intentional divergence, different input class.** The new policy reads bookings and sold entitlement (recorded business facts), never the forecast, and advises *people and preparation*, never washer capacity. Neither reuses nor parity-locks the other; both stay owner-identified. |
| `robot_down`, `assist_backlog` | `StaffState`, the robot-assist technician pool | **Different fact class.** Role coverage from schedules is not technician-pool capacity; the policy never reads `StaffState`. |

The Ball Availability Guardian (`policy_id "ball-availability-guardian"`,
`policy_version "0.1.0"`) has no staffing or booking input and fails closed on
unavailable demand; it is unchanged.

### 9.2 Why the existing issuance path cannot be reused as-is (recorded facts)

- `OperationalSnapshot` is the Guardian's private input; its digest is
  `DecisionTrace.snapshot_digest`, journaled into `evaluation_id`; adding
  fields changes every replayed id and trips the runtime's
  `evaluation_replay_mismatch` fail-closed path.
- `Recommendation` has four mandatory stockout fields and no generic payload;
  `DecisionTrace` requires a `BallAvailabilityPolicyConfig` and recomputes
  stockout semantics; `semantics.expected_policy_result` admits only
  `DISPATCH_COLLECTOR` and `OPERATOR_INTERVENTION` with exact summary text;
  `PolicyEvaluation` validates itself against the Guardian. Adding a
  `RecommendationAction` value is **not additive**.
- The ledger `nxt-pilot-ops-ledger/v1` closes `_EVENT_TYPES` and every decoder
  over the Guardian shape; an older reader raises on the first unknown event
  type. `RecommendationOutcome` is ball-shaped.
- `nxt_agent_runtime` is single-policy (`isinstance` pin on
  `BallAvailabilityGuardian`) and evaluates once per admitted envelope.

### 9.3 Proposed route (for approval, not assumed)

A second named policy **inside `nxt_pilot_ops`** with its own stdlib-only
contracts, evaluated by a composition root per context generation rather than
per envelope, writing to a **sibling ledger** so the existing ledger schema,
decoders, replay, `nxt_agent_runtime`, and the 98 Site Agent tests are
untouched:

| Element | Proposal |
|---|---|
| Input | `nxt_pilot_ops/adapters/operational_context.py` builds a frozen stdlib-only `ContextSnapshot` (identity, `as_of`, per-capability scheduled/present/unknown now and at the next change, upcoming booked players, entitlement sold per window, every fact `None` plus a mandatory `*_provenance` string, sorted `missing_data_reasons`) from the §7 projection: the FacilityState-adapter pattern applied to a second upstream. |
| Policy | `nxt_pilot_ops/coverage.py`: `DemandCoveragePreparationPolicy` (`policy_id "demand-coverage-preparation"`, `policy_version "0.1.0"`), deterministic, thresholds in a frozen config tagged placeholder; verdict `NO_ACTION` whenever any contributing source is not `ok`. |
| Output | `PreparationRecommendation` and `PreparationTrace` with content-derived ids (`prep_`, `ptrace_`), a human-readable `summary`, the explicit sentence that clean-ball inventory is unknown, and `evidence_event_ids`. No `target_robot_id`; no action enum shared with the Guardian. |
| Ledger | `JsonlEventLedger` mechanics against a sibling file `coverage_ledger.jsonl` with schema `nxt-pilot-ops-coverage-ledger/v1` and its own closed event types (`preparation_issued`, `preparation_response`). Human responses accept/reject only. |
| Manager view | The snapshot's `recommendations` section lists Guardian cases and coverage cases as **two owner-labelled lists**, never one ranked list; the existing `RecommendationsPanel` renders the Guardian list unchanged. |
| Guards | `nxt_operational_context` in `UPSTREAM_PACKAGES` (§2.6); extend `FORBIDDEN_RULE_TOKENS` in `tests/edge_task/test_architecture.py` with the new policy and id prefixes; tests pin the divergence contract and the stale-forces-`NO_ACTION` rule. |

**Alternative rejected for now:** generalizing `Recommendation`,
`DecisionTrace`, `PolicyEvaluation`, and `RecommendationCase` behind policy-id
dispatch inside `nxt-pilot-ops-ledger/v1`. It touches every decoder and replay
path and needs a schema bump with migration; it is the better long-term shape
if a third policy appears and is noted as such.

Because every element in this table is a versioned `nxt_pilot_ops` contract
addition, the advisory piece is **its own architecture-review gate** and ships
as the second PR of the slice. The first PR carries no `nxt_pilot_ops` change
and its snapshot shows only existing Guardian output.

### 9.4 What the policy may say and when it stops

- Detect: a capability's `scheduled_now` falls at a future instant within the
  horizon while upcoming booked players or the entitlement sales rate exceed a
  declared threshold in an overlapping window.
- Recommend: an operational preparation for a human
  (`"Prepare a wash cycle before 14:45"`, `"Confirm front-desk coverage for
  15:30"`) with the evidence ids that produced it and the explicit statement
  that clean-ball inventory is unknown because no inventory source is
  connected.
- Downgrade: if any contributing source is `stale` or `missing` the next
  evaluation is `NO_ACTION` with `missing_data_reasons` naming the source. An
  already issued, still-pending recommendation cannot be withdrawn through
  existing contracts (`CaseStatus` has no expired or superseded value; the
  ledger has no withdrawal event; recommendations are immutable with an
  `execute_before` deadline). The snapshot marks it with a presentation flag
  `evidence_status: stale` derived from current data quality (never a ledger
  write), shown in text beside the manager controls; a ledger-level expiry
  event is listed in §14.
- Never: change a schedule, approve a request, write to any source, change a
  price or transaction, assert inventory sufficiency, claim savings, or rank
  people. Guard-tested with the existing execution-token bans plus `approve`,
  `payroll`, `price`, `rank`, `salary`.

---

## 10. Affected subsystems and registration checklist

| Surface | Change |
|---|---|
| `simulation/nxt_operational_context/` (new) | `contracts.py`, `adapters/` (`csv_staffing.py`, `csv_sales.py`, `csv_play.py`, `replay.py`), `profiles.py`, `importer.py` (`decide_import`), `projection.py`, `freshness.py`, `operating_day.py`, `__init__.py` |
| `simulation/tests/operational_context/` (new) | behavioral tests, `fixtures/`, `test_architecture.py` guard with a negative control |
| `simulation/pyproject.toml` | add the package to the single-line `packages` list (wheel membership) |
| `.github/workflows/verification.yml` | add a focused `Test Operational Context` step, the architecture-list entry, the `compileall` entry, and the `shipped` tuple entry, as every package since Site Runtime has |
| Sibling reverse guards on `main` | append `nxt_operational_context` to `tests/site_runtime/test_architecture.py`, `tests/agent_runtime/test_architecture.py`, `tests/edge_observation/test_architecture.py`, `tests/workflow_enablement/test_architecture.py` (three lists), `tests/course_world_model/test_architecture.py` (four lists), `tests/pilot_ops/test_boundaries.py` |
| `simulation/nxt_site_agent/` (branch) | `contracts.py`: `CompositionSeam` gains `operational_context: Callable[[datetime], Mapping] | None` and `clock: Callable[[], datetime] | None`; `service.py`: `supervisor_snapshot(as_of)`; `api.py`: one GET route and `_STATUS_BY_CODE` entries; `projections.py`: snapshot assembly; `tests/site_agent/test_architecture.py`: `hashlib` into `ALLOWED_STDLIB_ROOTS`, `nxt_operational_context` into `BANNED_FIRST_PARTY_MENTIONS`; `test_api.py` endpoint lists |
| `simulation/scripts/site_agent_fixture.py`, `site_agent_demo.py` (branch) | compose the journal (`nxt_edge_task.journal.JsonlJournal`), profiles, replay fixture, admission facts, and clock; flags `--context-dir`, `--import <source_system> <file>`; the script guard gains the journal import as allowed |
| `apps/site-agent-console/` (branch) | `lib/api.ts` snapshot types and method; `lib/snapshot.ts`; `app/page.tsx` single fetch; two panels; `lib/format.ts::formatCount`; tests and fixtures |
| Governance | `.agent/context/package-map.md`, `source-of-truth.md` (new fact-class row), `architecture.md` (seam bullet, guarded-boundary row), `deployment.md` ("Also added after that baseline" paragraph, placement row, statement that the "Not implemented" rows are unchanged), `.agent/workflows/architecture-review.md` (placement row), `testing.md` (suite lists), `AGENTS.md` (dependency bullet, source-precedence doc list), `docs/ARCHITECTURE.md`, `docs/CI.md`, `README.md`, `simulation/README.md`, `.agent/context/product.md`, `simulation/docs/operational_context_v0.md` (stable doc written at implementation) |

Nothing changes in `nxt_facility`, `nxt_telemetry`, `nxt_site_runtime`,
`nxt_agent_runtime`, `nxt_commissioning`, `nxt_edge_observation`,
`nxt_edge_task` (the journal is used, not modified), `nxt_edge_interventions`,
`nxt_memory`, `nxt_range_*`, `nxt_sim`, or `scripts/validate_configs.py`.
Pre-existing drift noted, not fixed here: `testing.md` lists
`tests/edge_interventions/test_architecture.py` in the architecture suite but
`verification.yml` and `docs/CI.md` do not; `docs/MILESTONES.md` is frozen at
2026-08-09 and no package since has updated it.

---

## 11. Acceptance tests (mapping of the fourteen required proofs)

| # | Proof | Test (proposed location) |
|---|---|---|
| 1 | Re-importing the same file creates no new events | `tests/operational_context/test_import.py::test_same_file_twice_is_one_duplicate_record_and_zero_events`; projection byte-identical before and after |
| 2 | Corrections and refunds adjust totals, history preserved | `test_corrections.py::test_refund_subtracts_once_and_keeps_original`; re-import of the refund does not subtract twice; journal record count asserted; partial refund case |
| 3 | Scheduled ≠ present | `test_staffing.py::test_scheduled_without_clock_in_is_presence_unknown` |
| 4 | Clocked-in ≠ available | `test_staffing.py::test_clock_in_does_not_set_available` |
| 5 | Unapproved requests do not alter coverage | `test_staffing.py::test_requested_change_leaves_coverage_unchanged_until_approved` |
| 6 | Missing clock-out ⇒ no duration | `test_staffing.py::test_missing_clock_out_yields_no_duration` plus a guard that no per-person duration key exists |
| 7 | Booked ≠ started | `test_play.py::test_booked_players_not_counted_active` |
| 8 | Missing finish ⇒ unknown duration | `test_play.py::test_missing_finish_excluded_from_duration_stats` |
| 9 | Entitlement never labelled dispensed or available | `test_architecture.py::test_projection_keys_contain_no_inventory_vocabulary` plus label assertions in `test_demand.py` |
| 10 | Stale ⇒ no green status | `test_freshness.py::test_stale_source_forces_unknown_labels`; console `component.test.tsx` shows `stale` in text for `StaffingTodayPanel` and `DemandNowPanel` |
| 11 | Partial invalid import ⇒ no inconsistent snapshot | `test_import.py::test_one_bad_row_rejects_whole_batch`; `tests/site_agent/test_snapshot.py::test_rejected_batch_leaves_snapshot_unchanged_and_reports_it`; `test_import.py::test_torn_tail_is_excluded_and_reported` |
| 12 | Every value traceable | `test_traceability.py::test_each_projected_value_recomputes_from_cited_events` |
| 13 | Existing console trust tests pass | the existing 39 Vitest cases and 98 `tests/site_agent` cases unchanged and green (baseline observed in §1.3) |
| 14 | Physical inventory and machine fields remain unknown | `tests/site_agent/test_snapshot.py::test_physical_stores_and_machines_are_explicit_unknown_shapes` |

Plus: determinism (same view and `as_of` ⇒ byte-identical projection and
`snapshot_id`); privacy (`test_privacy.py::test_forbidden_columns_refuse_profile_and_file`,
`test_journal_never_contains_forbidden_headers`); identity mismatch rejection;
timezone and DST row errors; `event_conflict` on unlinked content change;
supersession-target mismatch rejection; fixture clock operating-day
consistency with `simulation_midnight_iso`; one-generation coherence (a
concurrent `advance` cannot interleave inside `supervisor_snapshot`); the
package guard with negative control; `splitSnapshot` deep-equality.

---

## 12. Explicit cuts (not implemented, not stubbed)

Employee self-service; staff mobile app; scheduling or approval workflows;
payroll; performance scoring or any per-person ranking; POS functionality or
connection; customer CRM; pricing optimization; revenue or labor-savings
claims; physical sensor ingestion; robot or washer commands; generic
dashboards; visual redesign beyond the two sections; vendor APIs; browser
automation or scraping; authentication (and therefore any drill-down
endpoint); forecasting; raw source-file retention.

## 13. Follow-on seams preserved

| Follow-on | Seam left clean by this design |
|---|---|
| 1 Clean-ball weight ingestion | `physical_stores.fields.clean_*` stay explicit unknown placeholders fed later through the existing adapter and Site Runtime path, never by this package |
| 2 Awaiting-wash inventory | `physical_stores.fields.awaiting_wash` placeholder; same path |
| 3 Washer running state | `machines.fields.washer_running/washer_fault` placeholders; `edge_observation_v0.md` already records `washer_running` as unmapped raw data awaiting a canonical decision |
| 4 Dispensed-ball evidence | a future dispensing source is the only thing allowed to relate `entitlement_sold` to dispensing, through an explicit, labelled reconciliation projection |
| 5 Demand and supply forecasting | an `ESTIMATED` projection over this journal, versioned, possibly feeding `UpstreamInputs.forecast_balls_per_minute` through the anticipated telemetry seam (§2.4) |
| 6 Supervised robot execution interface | untouched; nothing here reaches `RobotTaskInterface`, `apply_directive()`, or any adapter |

## 14. Unresolved vendor and product questions

1. **Staffing source:** does the export carry a stable record id per shift and
   per clock event, or only per employee per day? Are approved changes new
   shift rows or change records with a link to the original?
2. **Role codes:** who declares the role-code to operational-capability
   mapping, and can one person hold several capabilities per shift?
3. **POS:** are void and refund a status change on the original row (requires
   a correction time) or a new row (requires `original_transaction_id`)? Are
   partial refunds possible?
4. **SKU table:** who owns and versions the SKU to balls mapping; are bundles a
   single SKU?
5. **Tee sheet:** is actual start recorded by a starter, a gate, or not at
   all? Is finish recorded? Without both, play duration stays `UNKNOWN` and
   `confirmed_active_players` will rarely be provable.
6. **Export cadence and timezones:** how often are files exported, do they
   carry offsets, and is the commissioned timezone correct for the pilot site?
7. **Operating day:** local midnight (V0) or facility close?
8. **Admission window and thresholds:** who declares
   `clock_in_admission_before_s`, each source's `stale_after_s`, and the
   demand windows?
9. **Fixture clock:** reconcile the pilot fixture's UTC scenario midnight with
   the manifest's `Asia/Shanghai` timezone, or declare the mismatch.
10. **Base branch:** Option A, B, or C in §1.4.
11. **Endpoint path:** `/api/v0/supervisor-snapshot` (proposed) or a `/api/v1`
    transport version.
12. **Guard widenings on the branch:** `hashlib` into the Site Agent stdlib
    whitelist; the site-agent script guard allowing `nxt_edge_task.journal`.
13. **Audit retention:** whether any raw source file may ever be retained
    (this design says no for staffing and records only digests).
14. **Advisory contract (second PR):** the §9.3 route versus generalizing the
    existing recommendation contracts; whether a ledger-level expiry event
    should exist; whether `nxt_agent_runtime` stays single-policy.

## 15. Self-review record

Checked against each item below; findings were folded into the text above.

- **Planned vs recorded vs derived vs estimated vs observed:** every staffing
  field is a plan (`SHIFT_SCHEDULED`), a system record (`CLOCK_IN`,
  `ABSENCE_RECORDED`), or a derivation; no field mixes them. `ESTIMATED` is
  produced by nothing in V0. `MEASURED` is reserved for sensors; the only
  physical reading in the snapshot is the existing dispenser channel pair,
  passed through with its own `SourceReference.source_type` and status, so in
  fixture mode it reads as simulated data under the disclaimer. Scenario time
  and UTC are never subtracted from each other.
- **Ambiguity removed:** "present", "available", "approved", "active",
  "duration", "today", "fresh", "duplicate", and "correction" each have one
  definition and one evidence rule. `source_revision_key`, the supersession
  head rule, back-dated corrections, conflicts, and the torn-tail rule are
  defined. Admission windows, thresholds, and windows are mandatory
  configuration, never defaults.
- **Scope creep refused:** no forecasting, no inventory reconciliation, no
  `nxt_pilot_ops` change in the first PR, no new API version, no console
  redesign, no authentication, no POS connection, no raw-file retention.
- **Truth ownership:** no second mutable facility truth; `FacilityState`,
  `UpstreamInputs`, and the channel vocabulary untouched; business records are
  a named new fact class with one owner; projections are regenerable from the
  journal; recommendations stay advisory and owner-identified; nothing reaches
  an execution surface.
- **Fail-closed and missingness:** unknown mappings, missing clock events,
  missing timezone, stale sources, torn journals, identity mismatch, unlinked
  content changes, and partial batches all resolve to `UNKNOWN`, `CONFLICT`,
  or rejection with a reason.
- **Honesty of status words:** the console and service are labelled as an
  unmerged branch throughout; `SiteClock` is precedent only; no claim that any
  part of this slice exists; the observed baseline is reported as observed.

**Gate outcome: Pause.** Ownership and placement are resolved (Proceed), the
advisory piece needs its own gate (Reshape), and implementation cannot start
until the base-branch decision (§1.4) is made.
