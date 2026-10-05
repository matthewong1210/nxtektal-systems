# Operational Context Ingestion V0 — design specification

**Date:** 2026-10-05 · **Status:** Proposed design, awaiting approval. Nothing in
this document is implemented. No code, package, endpoint, console section, or
test described here exists on any branch. Revision 2 after an adversarial
self-review (§15 records what changed).
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

- Working branch `claude/admiring-euler-vvhgx6` is `origin/main` at `e3f63ca`
  plus this document; the worktree is otherwise clean.
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
| `nxt_sim`, `nxt_range_ops`, `nxt_facility`, `nxt_memory`, `nxt_telemetry`, `nxt_range_twin`, `nxt_pilot_ops`, `nxt_commissioning`, `nxt_site_runtime`, `nxt_agent_runtime`, `nxt_edge_observation`, `nxt_workflow_enablement`, `nxt_course_world_model`, `nxt_edge_task`, `nxt_edge_interventions` (the `simulation/pyproject.toml` wheel list) and the repository-local `nxt_range_agent`, `nxt_range_viewer`, `nxt_range_demo` | merged / current checkout |
| `nxt_site_agent`, `apps/site-agent-console`, `simulation/docs/site_agent_v0.md`, `scripts/site_agent_fixture.py`, `scripts/site_agent_demo.py` | implemented on unmerged branch `feature/pilot-site-agent-service-v0` |
| `SiteClock` in `scripts/edge_gateway_live_input_v0.py` | implemented on unmerged branch `feature/edge-gateway-live-input-v0` (precedent only) |
| `nxt_operational_context`, every contract, endpoint, console panel, test, and guard edit named in §§3–11 | proposed |

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
  `nxt_site_agent` in `tests/edge_task/test_architecture.py` (the
  `nxt_edge_interventions` guard discovers packages dynamically).

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
> coverage and freshness, mapping coverage, batch validity).

Source-of-truth tier (gate §5): **observation/evidence**. The context journal
is the primary record of business facts the facility has no other
representation of, the analogue of `ObservationFrame` plus `AssemblyReport`;
it is not a derived history of `FacilityState` and therefore not the
`nxt_memory` tier. Proposed `source-of-truth.md` row:
`| Business operational-context evidence | nxt_operational_context journal
records and pure projections at an explicit as_of | A physical observation, a
commissioned fact, a FacilityState field, operational history, advice, or
proof that any physical act occurred |`.

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
| `nxt_memory` | Append-only *history* that must not feed the live loop; this slice's projection is live context by design. |
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
| `SourceAdapter` protocol, the three CSV adapters, the replay-fixture adapter, the source-profile contract, the privacy allow-list rules | Any vendor API, transport, credential, browser automation, or scraping |
| Import-batch admission: identity check, all-or-nothing validation, row-level rejection report, duplicate, correction, and conflict dispositions, the record specs for one batch | Writing files, opening files, locks, or anchors (the composition root wires the journal, §6) |
| Pure projection functions (staffing / demand / play context, data quality, exceptions) over a verified record view at an explicit `as_of` | Scheduling, approval, payroll, pricing, or any write to a source system |
| Coverage and freshness classification given declared thresholds and the caller's clock value | Reading a wall clock, randomness, UUIDs, the filesystem, the network |
| The operating-day derivation from an IANA timezone handed in as plain data | Owning the timezone (commissioning does) |

Dependency rules, to be enforced by `tests/operational_context/test_architecture.py`
mirroring `tests/edge_interventions/test_architecture.py`:

- imports no other `nxt_*` package; site identity, commissioned timezone,
  manifest digest, and source profiles reach it as plain data;
- stdlib allow-list (whitelist, bans by omission): `__future__`,
  `collections`, `csv`, `dataclasses`, `datetime`, `decimal`, `enum`,
  `hashlib`, `io`, `json`, `math`, `re`, `typing`, `zoneinfo`. Banned by
  omission: `os`, `pathlib`, `fcntl`, `time`, `uuid`, `random`, `secrets`,
  `socket`, `http`, `urllib`, `subprocess`, `threading`, `asyncio`, `sqlite3`;
- banned call names as in the sibling guards (`now`, `utcnow`, `today`,
  `monotonic`, `perf_counter`, `uuid1`, `uuid4`, `random`, `randint`,
  `getenv`): every function that needs "now" takes `as_of: datetime`
  (timezone-aware) as an argument;
- no execution tokens, foreign-surface tokens, or the sibling guards'
  language-model patterns; additionally banned source tokens `payroll`,
  `salary`, `wage`, `rank`, `approve_shift`, and the advisory vocabulary
  reserved to `nxt_facility.decisions`/`nxt_pilot_ops` (`rule_id`,
  `OPERATOR_INTERVENTION`, `DISPATCH_COLLECTOR`, `robot_down`,
  `assist_backlog`, `battery_reserve`). Because these are substring checks
  over package source, no contract field in §§4–5 contains them;
- the package may not define classes named `Observation`, `ObservationFrame`,
  `FacilityState`, `AssemblyReport`, `UpstreamInputs`, `OperationalSnapshot`,
  `Recommendation`, `DecisionTrace`, or `CommissionedSite`;
- no existing package may import it except the designated consumers in §2.6,
  and its source may never contain the literal `nxt_site_agent`,
  `nxt_pilot_ops`, or `nxt_edge_task`.

Canonical JSON: the package redeclares the repository rule in about twenty
lines. The `json.dumps` call mirrors `nxt_edge_task/contracts.py::canonical_json`
(`sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`,
`allow_nan=False`); the value pre-pass mirrors
`nxt_pilot_ops.serialization.to_primitive` and `_utc_iso` (timezone-aware
datetimes as UTC ISO-8601 with microseconds and a `Z` suffix; `-0.0`
normalized to `0.0`; non-finite floats rejected; digests as `sha256` hex). A
test pins the package's output to `nxt_pilot_ops.serialization.canonical_json`
for datetime, negative-zero, nested-mapping, and decimal-string samples so a
divergent canonicalizer cannot appear. Monetary amounts are carried as decimal
strings, never floats. Identifier style follows the repository: record and
business ids are `<prefix>_<24 lowercase hex>`; whole-document digests are
`sha256:<64 hex>`.

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

### 2.5 Identity, identifiers, and time binding

- `site_id` and `deployment_id` are the commissioned identity the Site Agent
  launched with (`RangeOpsLaunchPlan.site_id`/`deployment_id`). They reach the
  package as a plain-data `ContextAdmissionFacts(site_id, deployment_id,
  site_timezone, manifest_digest)`, the `nxt_edge_task.AdmissionFacts`
  pattern; `manifest_digest` must match the `sha256:<64 hex>` shape and is
  recorded on every batch record for provenance. A batch whose declared
  `site_id` differs is rejected whole (`identity_mismatch`), never partially
  imported.
- **Identifier shape.** `source_record_id`, `staff_ref`, `session_ref`,
  `source_transaction_id`, `original_transaction_id`, `shift_record_id`, and
  `request_record_id` must match `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` (the
  commissioning identifier shape, shortened). A value containing whitespace,
  `@`, or `,` is the row error `identifier_not_opaque`: an export whose stable
  key is an e-mail address or a display name cannot be imported.
- The **site timezone** for operating-day boundaries is
  `CommissionedSite.timezone` (an IANA name validated with `zoneinfo.ZoneInfo`
  in `nxt_commissioning/validation.py`), supplied through
  `ContextAdmissionFacts`. Nothing on `main` consumes that field at runtime
  today; this slice is its first consumer. Without a timezone the staffing and
  play day projections are `UNKNOWN` with reason `site_timezone_unavailable`;
  nothing defaults to UTC.
- **Operating day, formally.** `operating_day(t) = t.astimezone(zone).date()`
  for every timezone-aware instant `t`; explicit instants are never refused.
  `day_start` is local midnight of that date and `day_end` local midnight of
  the next date, both as aware instants computed with `zoneinfo`; the day is
  the half-open interval `[day_start, day_end)` and may be 23 or 25 hours on a
  DST day. If either midnight does not exist or is ambiguous in the zone
  (detected by `fold=0`/`fold=1` round-trip comparison), the day projections
  are `UNKNOWN` with reason `operating_day_boundary_ambiguous`; the unmerged
  `SiteClock.local_midnight` refusal is the precedent, not a dependency. A
  day rolls at local midnight (a facility-close rollover is an open question,
  §14). Every "today" and "for the day" phrase in §7 means this interval.
- The **source timezone** is a per-source profile declaration (an IANA name),
  because a tee sheet or POS export may stamp local wall time without
  offsets. Every event stores the original cell text (`occurred_at_source_text`),
  the normalized UTC instant (`occurred_at`), and the `source_timezone` used. A
  cell that carries an explicit offset or `Z` is honoured as written. A local
  time is **ambiguous** when `fold=0` and `fold=1` give different UTC offsets
  and both round-trip to the same wall time, and **non-existent** when neither
  round-trips; both are row errors (§3.4).
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
| `nxt_site_agent` (unmerged) | receives a **per-run context reader** through its `CompositionSeam` (§8.2), the same injected-callable pattern as `CompositionSeam.cycle_catalog` (seam, process-lifetime) and `ComposedRuntime.adapter_reports` (per run). The reader yields plain data whose `schema`, `owner`, `data_class`, and `source_kind` strings are produced inside the leaf's projection and copied verbatim; the shell validates the schema id and never spells the package name. `ALLOWED_FIRST_PARTY_MODULES` does not change; `nxt_operational_context` is **added** to both `BANNED_FIRST_PARTY_MENTIONS` (the shell never mentions it) and `OTHER_PACKAGES` (it never mentions the shell). |
| `nxt_pilot_ops.adapters` (§9, second PR) | a new designated adapter module imports the public projection contract only |

Dependency direction is fixed now: the new package is **upstream** of
`nxt_pilot_ops`. It is added to `UPSTREAM_PACKAGES` in
`tests/pilot_ops/test_boundaries.py`, which bans it from the policy core and
proves it never mentions `nxt_pilot_ops`; `test_adapter_is_the_only_upstream_dependency_boundary`
already permits any root outside its explicit ban set, so the adapter module
needs no mechanical relaxation (the *documented* package-map row does change
in PR 2, §9.3). The package may never import `nxt_pilot_ops`, which is why it
redeclares the canonical JSON lines instead of importing them.

---

## 3. Source assumptions, adapter interfaces, and the privacy rule

### 3.1 What V0 assumes about sources

- Sources deliver **files or recorded fixtures**, not live APIs: one CSV file
  per source per import, UTF-8, header row, RFC 4180 quoting. Delimiter and
  header names are declared per source profile; no auto-detection.
- A source export is a **snapshot of records as of an export instant**.
  Re-exporting may repeat earlier rows unchanged (must be idempotent) or carry
  changed rows for the same source record id (a correction). The export
  instant is either declared by the operator at import time or read from a
  profile-mapped export header; when neither exists, coverage is derived
  conservatively from the records themselves (§7.1).
- Every source has a stable `source_record_id` per record with the identifier
  shape of §2.5. If a vendor export lacks one, the adapter refuses the
  profile; it never synthesizes ids from row position.
- Vendor-specific semantics (status vocabularies, SKU meanings, role codes) are
  **declared in the source profile**, not inferred. Unknown values are
  preserved (within the privacy rule of §3.2) and classified `UNMAPPED`,
  never dropped and never guessed.
- No vendor API, webhook, OAuth, screen automation, or scraping is designed or
  stubbed. `SourceAdapter` is the only seam a future vendor integration may
  fill.

### 3.2 `SourceAdapter` protocol, profiles, and the privacy rule for every adapter

```python
class SourceAdapter(Protocol):
    adapter_id: str            # e.g. "csv.staffing/v0"
    adapter_version: str       # bumped on any parsing or semantic change
    source_kind: SourceKind    # STAFFING | SALES | PLAY

    def read(self, source: SourceInput, profile: SourceProfile,
             batch: BatchContext) -> BatchResult: ...
```

- `SourceInput` is a text stream plus `source_file_name` and the file's
  `sha256:` digest over the **exact bytes as read** (no decoding or newline
  normalization), computed by the caller. Adapters never open paths.
- `SourceProfile` (`nxt-operational-context/source-profile/v1`) is the
  per-source declaration: `source_system` label, `source_timezone`, the column
  mapping (only columns listed for that adapter in §3.3 may be mapped), the
  status vocabulary mapping, role-code mapping (staffing), SKU-to-entitlement
  table with `mapping_version` (sales), activity-type vocabulary (play), an
  optional `export_time_header` or `export_time_column` the adapter maps to
  `exported_at`, and two **mandatory** thresholds: `stale_after_s` (§7.1) and
  `clock_skew_tolerance_s` (§4). Profiles are versioned JSON documents loaded
  with duplicate-key rejection; their content digest is recorded on every
  batch. Profile validation refuses a profile lacking any mandatory field.
- `BatchContext` carries `ContextAdmissionFacts`, `import_batch_id`,
  `received_at`, `exported_at` (declared by the operator, else `None`), the
  profile digest, and `clock_basis` (§8.4).
- `BatchResult` is either `NormalizedBatch(events=..., exported_at=...)` or
  `BatchRejection(errors=(RowError(file, row_number, column, reason), ...))`.
  An adapter never returns a partial batch.

**Privacy rule, binding on every adapter (staffing, sales, play).**

1. The adapter normalizes only the columns the profile maps, and the profile
   may map only the columns §3.3 lists for that adapter. **Every other column
   is discarded before any value is read.**
2. A header is **forbidden** when, after casefolding and splitting on
   `[^a-z0-9]+`, any token is in `{name, first, last, salary, pay, wage, rate,
   address, street, phone, mobile, tel, ssn, national, passport, dob, birth,
   age, gender, medical, sick, diagnosis, email, home, customer, member, card,
   pan, last4, iban, account, contact, note, notes, comment, comments,
   remark}` or the whole header contains `sick_note`, `national_id`, or
   `date_of_birth`. Profile validation refuses a mapping to a forbidden header
   (`forbidden_column_mapping`), and the adapter refuses a **file** whose
   header row contains a forbidden header even unmapped
   (`forbidden_column_present`), so such an export cannot be imported until it
   is re-exported without it.
3. Verbatim text values (`source_status`, `role_code`, `availability`, `sku`,
   `register_id`, `course_or_area`, raw `activity_type`) are stored **only**
   when they match the bounded token pattern `^[A-Za-z0-9][A-Za-z0-9 _./:-]{0,63}$`.
   Otherwise the event stores `sha256` of the value under
   `<field>_digest`, sets `free_text_discarded: true`, and the text is
   discarded before normalization. Unmapped values surface in projections and
   data quality only as **counts per token or digest**, never as free text.
4. No error reason, detail string, `read_errors` entry, exception message, or
   script log line may contain a cell value, a header outside the §3.3
   allow-list, or any identifier of a person. Reasons are closed tokens;
   details name only file name, row number, and column.
5. Identifiers obey the shape rule of §2.5 (`identifier_not_opaque`).
6. The composition root never persists raw source files; only the `sha256:`
   digest is recorded (§6.5).

Tests: `test_privacy.py::test_forbidden_headers_refuse_profile_and_file`,
`test_free_text_values_are_digested_not_stored`,
`test_identifier_values_must_be_opaque`,
`test_a_name_in_every_column_reaches_no_record_field_or_error`.

### 3.3 The three CSV adapters and the replay adapter

| Adapter | Required columns (via profile mapping) | Optional columns |
|---|---|---|
| `csv.staffing/v0` | `record_id`, `record_type` (§5.1 enum), `staff_ref`, `occurred_at` (the source's record time for the row) | `role_code`, `shift_start`, `shift_end`, `status`, `shift_record_id`, `request_record_id`, `availability`, `correction_time` |
| `csv.sales/v0` | `transaction_id`, `transaction_time`, `sku`, `quantity`, `status` | `correction_time`, `original_transaction_id`, `refunded_quantity`, `currency`, `amount`, `register_id` |
| `csv.play/v0` | `record_id`, `record_type` (§5.3 enum), `session_ref`, `occurred_at` (the source's record time for the row) | `scheduled_start`, `actual_start`, `actual_finish`, `player_count`, `actual_player_count`, `status`, `activity_type`, `course_or_area`, `correction_time` |
| `replay.fixture/v0` | reads a committed JSON fixture of already-normalized events; used by tests and the demo | — |

Per-type required payload columns are enforced by the adapter (§5): for
example `SESSION_STARTED` requires `actual_start`, `SESSION_FINISHED` requires
`actual_finish`, `SHIFT_SCHEDULED` requires `shift_start` and `shift_end`,
`SHIFT_CHANGE_APPROVED` requires `shift_record_id`, and a sales reversal row
requires `correction_time`. The replay adapter exists so acceptance tests can
exercise projection, correction, and snapshot behavior without coupling them to
CSV parsing. Fixture CSVs, profiles, and replay files live under
`simulation/tests/operational_context/fixtures/`; the demo script reads the
same files by path argument. No `configs/` directory is added, so
`scripts/validate_configs.py` is untouched.

### 3.4 Row-level and disposition-level failure contract

**Adapter row errors** (produced by `adapter.read`, which sees no journal):
`missing_required_column`, `empty_required_value`, `unparseable_timestamp`,
`ambiguous_local_time`, `nonexistent_local_time`, `negative_quantity`,
`unknown_record_type`, `unknown_status`, `planned_time_for_recorded_event`,
`missing_correction_time`, `partial_refund_quantity_unavailable`,
`identifier_not_opaque`, `forbidden_column_present`, `duplicate_record_in_batch`
(two rows in one file produce the same `event_id`), `profile_mismatch` (the
profile's `source_system` or `source_kind` differs from the adapter's or the
batch's).

**Disposition errors** (produced by `decide_import` against the journal view,
§6.2): `identity_mismatch`, `supersession_target_mismatch`,
`illegal_supersession`, `over_reversal`, `occurred_after_received`.

Each error names `source_file_name`, 1-based `row_number`, the `column` when
applicable, and the stable `reason` token. Any adapter row error or disposition
error rejects the **whole batch** (§6.4) with the complete error list. Unknown
SKU or role codes are *not* errors; they are preserved under §3.2 rule 3 and
surface as `UNMAPPED`.

A permanently invalid historical row (for example a clock-in stamped inside a
DST fall-back fold) therefore blocks every future export of that source until
the vendor re-exports without it, and the source reads `stale` meanwhile. **No
quarantine exists in V0**; a journaled per-row quarantine is listed in §14 as a
follow-on decision, and `test_import.py::test_repeated_invalid_row_keeps_source_rejected_and_stale`
pins the V0 behavior.

---

## 4. Common event envelope

Every normalized event is the `payload` of one journal record of kind
`event_recorded` (§6). Its fields:

| Field | Type | Rule |
|---|---|---|
| `event_id` | `oce_<24 hex>` | `"oce_" + sha256(canonical JSON of {site_id, source_system, source_record_id, source_revision_key, event_type})[:24]`. The seed excludes `received_at`, `import_batch_id`, provenance, and row position, so re-imports never mint new ids. |
| `content_digest` | `sha256:<64 hex>` | over canonical JSON of `{event_type, occurred_at, source_status, verbatim_payload}` where `verbatim_payload` holds only **source-verbatim** fields (§5 marks them); profile-derived fields (`status_class`, `operational_capability`, `ball_entitlement`, `entitlement_mapping`, mapped `activity_type`) are excluded so a mapping change never turns a re-export into a conflict. "Different content" in §6 means this digest differs. |
| `source_revision_key` | `str` | the normalized UTC `Z` text of `source_correction_time` when the row carries one, else `content_digest` |
| `site_id`, `deployment_id` | `str` | from `ContextAdmissionFacts`; mismatch rejects the batch |
| `source_system` | `str` | profile label, e.g. `"tee-sheet-export"`; never a vendor product claim |
| `source_record_id` | `str` | verbatim from the source; identifier shape enforced |
| `event_type` | enum | one of `StaffingEventType`, `SaleEventType`, `PlayEventType` (§5) |
| `occurred_at` | UTC ISO-8601 `Z` | **the instant the source system recorded the act**, for every event type: schedule entry time for `SHIFT_SCHEDULED`, clock time for `CLOCK_IN`/`CLOCK_OUT`, request/approval/rejection time for `SHIFT_CHANGE_*`, `transaction_time` for `SALE_CAPTURED`, the reversal's own time for `SALE_REVERSED`, booking time for `TEE_TIME_BOOKED`, `actual_start`/`actual_finish` for `SESSION_STARTED`/`SESSION_FINISHED`, update time for `PLAYER_COUNT_UPDATED`. Planned instants (`shift_start`, `shift_end`, `scheduled_start`) live only in `payload` and may be in the future. `occurred_at` may be back-dated relative to recording order (§6.3) but may not exceed `received_at + clock_skew_tolerance_s`; beyond that tolerance the disposition error `occurred_after_received` rejects the batch. A row that supplies only a planned time for a recorded type is `planned_time_for_recorded_event`. |
| `occurred_at_source_text` | `str` | the original cell text, preserved |
| `received_at` | UTC ISO-8601 `Z` | the batch's `received_at` from the composition root's clock |
| `import_batch_id` | `ocb_<24 hex>` | `"ocb_" + sha256(canonical JSON of {site_id, deployment_id, source_system, adapter_id, adapter_version, source_file_digest, profile_digest})[:24]`; the same file imported twice with the same adapter and profile produces the same id, and a parser or profile change produces a new one |
| `source_timezone` | IANA `str` | from the profile |
| `source_status` | `str` or digest | verbatim source status text under §3.2 rule 3 |
| `status_class` | enum | the profile's mapping of `source_status` into the closed §5 vocabulary for the family, or `UNMAPPED` |
| `chain_ref` | `str` | the key this event belongs to or corrects, set by the adapter from declared columns (§6.3): `source_record_id` by default; `original_transaction_id` for a sales reversal row; `shift_record_id` for staffing lifecycle events; `session_ref` for play events |
| `correction` | object or null | `{kind: REPLACEMENT | REVERSAL | STALE_REVISION, supersedes_event_id, source_correction_time}`, set by `decide_import`, never by an adapter (§6.3) |
| `mapping_at_import` | object | the profile-derived fields as computed at import, for audit; projections re-apply the current declared mapping (§7.4) |
| `provenance` | object | `{adapter_id, adapter_version, profile_digest, source_file_name, source_file_digest, row_number, manifest_digest, clock_basis}` |
| `schema` | `str` | `"nxt-operational-context/event/v1"` |
| `payload` | object | the family-specific fields of §5 |

The journal record wrapping the event carries its own `record_id`
(`rec_<24 hex>`, which includes the journal sequence) and `recorded_at_utc`;
cross-batch deduplication uses `event_id`, never `record_id`.

---

## 5. Domain payloads and boundaries

Two vocabularies are used and never mixed. **Source status** is the
repository's lowercase `ok | stale | missing` (§7.1). **Value evidence labels**
are the five the task requires:

| Label | Meaning in this slice |
|---|---|
| `SYSTEM_RECORDED` | the source system recorded this fact; attaches to individual events (and to list entries that are one event each), never to an aggregate |
| `DERIVED` | computed deterministically from system-recorded facts and declared mappings or the clock value (a count, a sum, a duration from two recorded instants, an operating day) |
| `ESTIMATED` | a labelled estimate with a named method; **not produced by any V0 projection**, reserved for follow-on forecasting |
| `UNKNOWN` | no admissible evidence, or the source is stale or missing, or the mapping is unavailable, or the clock is unavailable |
| `MEASURED` | reserved for sensor evidence; **never** produced by this package |

Every aggregate in §7 is therefore `DERIVED` or `UNKNOWN`; its `evidence`
entries cite the `SYSTEM_RECORDED` events behind it.

### 5.1 Staffing

Event types (closed enum `StaffingEventType`): `SHIFT_SCHEDULED`,
`SHIFT_CANCELLED`, `CLOCK_IN`, `CLOCK_OUT`, `SHIFT_CHANGE_REQUESTED`,
`SHIFT_CHANGE_APPROVED`, `SHIFT_CHANGE_REJECTED`, `ABSENCE_RECORDED`,
`LATE_RECORDED`, `AVAILABILITY_DECLARED`.

Staffing `status_class` vocabulary (profile-mapped): `SCHEDULED`, `CANCELLED`,
`CLOCKED_IN`, `CLOCKED_OUT`, `REQUESTED`, `APPROVED`, `REJECTED`, `ABSENT`,
`LATE`, `AVAILABLE`, `UNAVAILABLE`, `UNMAPPED`.

Payload (verbatim fields marked †): `staff_ref`†, `role_code`†,
`operational_capability` (the profile's mapping of `role_code` into a declared
site vocabulary such as `washer`, `dispenser`, `front_desk`, `range_picker`,
or `UNMAPPED`), `shift_start`†/`shift_end`† (UTC, for `SHIFT_SCHEDULED` and
`SHIFT_CHANGE_APPROVED`), `shift_record_id`† (the `source_record_id` of the
`SHIFT_SCHEDULED` record this event belongs to or replaces; required on
`SHIFT_CHANGE_APPROVED` and `SHIFT_CANCELLED`, optional on clock and absence
events), `request_record_id`† (the `SHIFT_CHANGE_REQUESTED` an approval or
rejection answers), `availability`† (only for `AVAILABILITY_DECLARED`; verbatim
under §3.2 rule 3 plus mapped class). Human-origin records (approvals,
rejections) carry one explicit no-effect field in the `nxt_edge_interventions`
style, `schedule_system_effect: "none"`, restating that recording an approval
writes nothing back to the staffing source. No HR or payroll system is
referenced, modelled, or disclaimed; the package has no concept of either.

Lifecycle versus correction (§6.3): `SHIFT_SCHEDULED` opens a shift chain
keyed by `shift_record_id = source_record_id`; `SHIFT_CHANGE_APPROVED` is a
`REPLACEMENT` of that chain's head; `SHIFT_CANCELLED` is a `REVERSAL` of it
(terminal). `CLOCK_IN`, `CLOCK_OUT`, `ABSENCE_RECORDED`, `LATE_RECORDED`,
`SHIFT_CHANGE_REQUESTED`, `SHIFT_CHANGE_REJECTED`, and `AVAILABILITY_DECLARED`
are independent recorded events related to a shift by `shift_record_id` (or
to a request by `request_record_id`); they are never corrections and never
alter the shift chain.

Boundaries the projection enforces (each is an acceptance test in §11):

- scheduled ≠ present: `confirmed_present_now` requires a `CLOCK_IN` inside the
  shift's admission window and no later `CLOCK_OUT`, both as of `as_of`;
- clocked-in ≠ available: **no projection field asserts availability.**
  `AVAILABILITY_DECLARED` is admitted and journaled as `SYSTEM_RECORDED`
  evidence, ingested only as a record exported by the staffing system (no
  worker-facing input, form, or app exists or is implied), and no V0 projection
  reads it; a future availability field is a follow-on decision. Acceptance
  test 4 asserts that no key matching `avail` exists in the staffing
  projection and that `CLOCK_IN` events contribute to presence only;
- requested ≠ approved: coverage uses the shift chain head (`SHIFT_SCHEDULED`
  or its superseding `SHIFT_CHANGE_APPROVED`); `SHIFT_CHANGE_REQUESTED` appears
  only in `pending_change_requests`, which `SHIFT_CHANGE_REJECTED` (via
  `request_record_id`) or an approval removes it from;
- cancelled ≠ scheduled: a `SHIFT_CANCELLED` head removes the shift from every
  effective-shift derivation;
- missing clock events ⇒ presence `UNKNOWN`, never `absent`;
  `confirmed_absent_now` requires `ABSENCE_RECORDED` for a shift overlapping
  `as_of`;
- a `CLOCK_IN` without `CLOCK_OUT` yields no duration; no duration is ever
  inferred from the scheduled end, and no key matching `duration|worked|hours`
  exists anywhere in the staffing projection;
- `LATE_RECORDED` is recorded-only: it never alters presence, coverage, or any
  projected value in V0;
- no field, derived value, ranking, or ordering of staff by any performance
  notion exists; projections expose counts and the next change time only, and
  no per-person record reaches the Supervisor Snapshot or any endpoint.

**Minimum operational identity.** The staffing adapter may map only the
columns in §3.3 and discards every other column (§3.2). `staff_ref` is the
source's opaque identifier (an employee number or badge id) and must satisfy
the identifier shape; names are not ingested in V0. Free-text statuses,
availabilities, and role codes are digested, not stored (§3.2 rule 3), so a
status such as a medical reason never reaches the journal.

### 5.2 Sales and ball demand

Event types (closed enum `SaleEventType`): `SALE_CAPTURED`, `SALE_REVERSED`.
`status_class` qualifies them: `CAPTURED` for `SALE_CAPTURED`; `VOIDED`,
`CANCELLED`, `REFUNDED`, `PARTIALLY_REFUNDED` for `SALE_REVERSED`; `UNMAPPED`.

`SaleEvent` payload (verbatim fields marked †): `source_transaction_id`†,
`transaction_time`†, `correction_time`† (required on `SALE_REVERSED`), `sku`†,
`quantity`† (units sold on a capture; units reversed on a reversal),
`refunded_quantity`† (required on `PARTIALLY_REFUNDED` under the status-change
model), `original_transaction_id`† (required on a new-row reversal),
`ball_entitlement` (integer or null, derived), `entitlement_mapping`
(`{sku, balls_per_unit, mapping_version}` or `UNMAPPED`), `currency`† and
`amount`† (decimal string; both optional and carried only when supplied),
`register_id`† (optional).

Reversal rules (with §6.3):

- A reversal is a `REVERSAL` correction of the sale chain keyed by
  `chain_ref = original_transaction_id` (new-row model) or
  `source_record_id` (status-change model). **Reversals never become the chain
  head**; the head of a sale chain is its latest `REPLACEMENT` or the
  original capture.
- Under the status-change model `VOIDED`, `CANCELLED`, and `REFUNDED` reverse
  the full head quantity; `PARTIALLY_REFUNDED` requires a profile-mapped
  `refunded_quantity`, else the row error `partial_refund_quantity_unavailable`
  (the sale is never reduced by a guess).
- Under the new-row model several reversals with distinct `source_record_id`
  may target one head; each row's `quantity` is the reversed units and the
  cumulative reversed units may not exceed the head's `quantity`
  (`over_reversal` disposition error).
- **Window attribution.** A window `(as_of − w, as_of]` is evaluated over the
  head capture's `transaction_time`; every reversal of that head subtracts
  from exactly the windows containing the head's `transaction_time`, regardless
  of the reversal's own time, so a window total is never negative.

Boundaries:

- `ball_entitlement` is a **sold entitlement**, labelled so everywhere; no
  projection key may contain `inventory`, `dispensed`, `available_balls`,
  `stock`, `clean`, or `revenue` (guard-tested);
- the SKU table is declared data with a `mapping_version`; a SKU absent from the
  table yields `UNMAPPED`, and the demand projection reports
  `unmapped_transactions` and `unmapped_quantity` beside the mapped totals
  rather than silently excluding them. Because projections re-apply the
  current declared mapping (§7.4), adding a SKU later maps already-imported
  sales without re-import, with the `mapping_version` cited in evidence;
- `amount` and `currency` are stored when supplied and are **not** summed,
  averaged, or projected in V0; no revenue, savings, or price field exists, and
  no ROI formula is reimplemented.

### 5.3 Golf activity

Event types (closed enum `PlayEventType`): `TEE_TIME_BOOKED`,
`BOOKING_CANCELLED`, `BOOKING_NO_SHOW`, `SESSION_STARTED`, `SESSION_FINISHED`,
`PLAYER_COUNT_UPDATED`.

Play `status_class` vocabulary: `BOOKED`, `CANCELLED`, `NO_SHOW`, `STARTED`,
`FINISHED`, `UNMAPPED`.

Payload (verbatim fields marked †): `session_ref`† (required; session identity;
every play event for a session carries the same value and every status-bearing
rule selects a session's events by `session_ref` only), `scheduled_start`†,
`actual_start`† (required on `SESSION_STARTED`), `actual_finish`† (required on
`SESSION_FINISHED`), `player_count`† (booked party size), `actual_player_count`†
(optional; a starter-recorded head count on `SESSION_STARTED`),
`activity_type`† raw plus the mapped class (`range`, `course_9`, `course_18`,
`lesson`, `event`, or `UNMAPPED`), `course_or_area`† (bounded label under §3.2
rule 3).

Lifecycle versus correction: `TEE_TIME_BOOKED` opens the session chain;
`PLAYER_COUNT_UPDATED` is the only play `REPLACEMENT` (it requires
`correction_time`, copies every other field from the head it supersedes, and
may differ only in `player_count`); `BOOKING_CANCELLED`, `BOOKING_NO_SHOW`,
`SESSION_STARTED`, and `SESSION_FINISHED` are independent recorded events
related by `session_ref`. Effective session status is derived by precedence:
`FINISHED` beats `STARTED`; `CANCELLED` and `NO_SHOW` beat `BOOKED`; a
`STARTED` session that is later `CANCELLED` is a data-quality issue
(`contradictory_session_status`), not a projection change.

Boundaries:

- booked ≠ started: `upcoming_booked_players` sums the chain-head
  `player_count` of `BOOKED` sessions with `scheduled_start` inside the
  configured look-ahead window and no `STARTED`, `CANCELLED`, or `NO_SHOW`
  evidence; it is a **booked headcount** and is labelled so;
- started ≠ finished: `confirmed_active_sessions` counts sessions with a
  `SESSION_STARTED` and no `SESSION_FINISHED` as of `as_of`, stating
  `basis = "SESSION_STARTED events"` and the newest play event time;
  `active_sessions_booked_players` sums those sessions' booked `player_count`
  (a plan, labelled as such); `confirmed_active_players` exists **only** when
  every active session carries `actual_player_count`, otherwise it is
  `UNKNOWN` with reason `no_actual_player_count`;
- `duration` is computed only when both `actual_start` and `actual_finish`
  are system-recorded for the same `session_ref`, finish is after start, and
  neither instant is ambiguous; otherwise `UNKNOWN`. Scheduled start is never
  used as a start proxy;
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
             allowed_origins=frozenset({"ADAPTER", "SERVICE"}))
```

| Record kind | Origin | Produced by |
|---|---|---|
| `context_started` | `SERVICE` | the composition root's start-up spec (identity, timezone, profile digests, clock basis) |
| `import_batch_rejected`, `import_batch_duplicate`, `event_recorded`, `event_conflict`, `correction_resolved`, `import_batch_committed` | `ADAPTER` | `decide_import` over the adapter's `BatchResult` |

No operator-origin record exists in V0; a human correction must arrive as a
source re-export. What the journal buys, by precedent: newline-terminated
canonical lines re-verified byte-for-byte on read, POSIX `fcntl` advisory
locking, a sibling high-water anchor (`<journal>.hwm`, schema
`nxt-edge-task/journal-anchor/v1`, written with fsync, atomic replace, and
directory sync after each append), rollback detection as state loss, and the
property that **all specs returned by one `append_via` builder are written in
one buffered `write()` plus fsync under one lock**. Durability is therefore
all-or-nothing **at the line boundary**: a crash that falls on a line boundary
leaves complete records without a commit marker (handled in §6.4); a crash
that leaves a byte-torn last line, a missing anchor with records present, or a
journal shorter than its anchor is a `JournalIntegrityError` on every read and
append. The journal never repairs itself. Scripts importing
`nxt_edge_task.journal` follow the existing `edge_intervention_service_v0.py`
precedent; the new package's own guard asserts it never imports
`nxt_edge_task`, and the composition root is the only importer. POSIX-only
persistence is inherited and stated.

### 6.2 Import pipeline

```text
composition root: open file, sha256 digest over raw bytes, clock() -> received_at,
                  declared --exported-at (optional)
    -> adapter.read(SourceInput, SourceProfile, BatchContext)   # pure
    -> journal.append_via(lambda records:
           decide_import(records, batch, received_at).specs)    # pure builder, run under the lock
    -> projections recompute from journal.read() on demand; nothing is cached as truth
```

`JsonlJournal.append_via(builder)` runs the builder inside the exclusive lock
over **every** verified record (committed or not) and writes all returned
specs in one write plus fsync before advancing the anchor, so `decide_import`
sees a consistent view and its output lands atomically. `decide_import(records,
batch, received_at)` is pure and derives two views from the same records:

- the **dedup view**: every `event_recorded` record regardless of commit
  status, used for steps 2 and 4 below;
- the **effective view**: only events whose `import_batch_id` has a
  later-sequence `import_batch_committed` record, used by every projection
  (§7).

It applies, in order:

1. **Identity**: `site_id`/`deployment_id` mismatch ⇒ one
   `import_batch_rejected` spec, zero events.
2. **Duplicate batch**: an `import_batch_id` that already has
   `import_batch_committed` ⇒ exactly one `import_batch_duplicate` audit
   record naming the prior record id and carrying this attempt's
   `received_at` and declared `exported_at`, zero events (the receiver-ledger
   precedent; never one record per duplicate row). An `import_batch_id` that
   already has `import_batch_rejected` and whose error digest is identical ⇒
   one `import_batch_duplicate` with `prior_outcome: REJECTED` per attempt;
   repeat attempts are operator actions and the growth is accepted.
3. **Row validity**: any adapter `RowError` ⇒ one `import_batch_rejected` spec
   with the complete error list, zero events.
4. **Per-event disposition** (§6.3 defines the chain algorithm) for each
   normalized event against the dedup view:
   - `event_id` already recorded ⇒ not appended; counted and listed in the
     batch record's `duplicate_event_ids`;
   - otherwise classify as *root*, *correction*, *stale revision*, *lifecycle
     event*, or *conflict* per §6.3; any disposition error (§3.4) aborts the
     batch: `decide_import` returns exactly one `import_batch_rejected` spec
     carrying every disposition error found across all rows and zero
     `event_recorded` specs.
5. **Commit**: `event_recorded` specs, then `event_conflict` and
   `correction_resolved` specs, then the single `import_batch_committed` spec
   **last** (carrying `import_batch_id`, `received_at`, `exported_at`,
   `coverage_end`, `clock_basis`, counts, `duplicate_event_ids`), so a
   line-aligned torn write can only leave events without a commit marker.

### 6.3 Corrections and supersession: one resolution algorithm

The journal is append-only; nothing is edited or deleted. Rules, drawn from
`nxt_course_world_model.validate_revision`, the `ModifiedRecommendation`
pattern, and the Edge terminal-conflict gate:

**Chain key.** `(source_system, event family, chain_ref)` where `chain_ref` is
set by the adapter from declared columns, never by a reader-resolved pointer:
`source_record_id` for a same-row re-export; `original_transaction_id` for a
sales reversal row (else `source_record_id` under the status-change model);
`shift_record_id` for `SHIFT_CHANGE_APPROVED` and `SHIFT_CANCELLED`;
`session_ref` for `PLAYER_COUNT_UPDATED`. Lifecycle events (§5.1, §5.3) carry
the same `chain_ref` for grouping but never enter the correction algorithm.

**Head.** The head of a chain is the admitted non-reversal event with the
greatest `(source_correction_time, journal sequence)`; roots have a null
correction time, which sorts lowest. Reversals never become head.

**Correction indicator.** A row is a correction iff it carries
`correction_time`, or its `status_class` is a reversal class, or its
`event_type` is `SHIFT_CHANGE_APPROVED`, `SHIFT_CANCELLED`, or
`PLAYER_COUNT_UPDATED` (which require `correction_time`; without it the row
error is `missing_correction_time`).

**Dispositions**, evaluated per event in `decide_import`:

| Arriving event | Chain state | Disposition |
|---|---|---|
| no correction indicator | no root for the key | **root**: `event_recorded` |
| no correction indicator | root exists, same `content_digest` | duplicate (same `event_id`) |
| no correction indicator | root exists, different `content_digest` | **conflict**: `event_conflict` naming both ids; both retained; key marked `CONFLICT`; no projection change; data-quality exception. The only sanctioned way to change an accepted fact is a correction. |
| no correction indicator | only unresolved corrections exist for the key (they arrived first) | **root arrives late**: `event_recorded` plus one `correction_resolved` record per waiting correction `{correction_event_id, root_event_id}`; the chain becomes readable |
| correction, `source_correction_time` > head's | head exists | **correction**: `event_recorded` with `correction = {kind: REPLACEMENT or REVERSAL, supersedes_event_id: <head>, source_correction_time}` |
| correction, `source_correction_time` ≤ head's, different content | head exists | **stale revision**: `event_recorded` with `correction.kind = STALE_REVISION`; never becomes head; surfaced in data quality as `stale_revision`. An operator importing an older export after a newer one therefore rewinds nothing. |
| correction, equal `source_correction_time`, different content, both claim headship | head exists | `event_conflict` (two revisions of one instant); key marked `CONFLICT` |
| two corrections naming the same head across batches | — | both retained; key marked `CONFLICT`; no resolver |
| correction | no root and no prior correction | **orphan**: admitted with `supersedes_event_id: "unresolved"`; the chain is `UNKNOWN` for that key and contributes nothing to any total until its root arrives |
| `REVERSAL` of a head already fully reversed, or cumulative reversed units > head quantity | — | `over_reversal` disposition error (batch rejected) |
| `REPLACEMENT` of a reversed (terminal) head, or `SHIFT_CHANGE_APPROVED` naming a cancelled shift | — | `illegal_supersession` disposition error |
| a `replay.fixture/v0` event carrying an explicit `supersedes_event_id` that is not the computed head | — | `supersession_target_mismatch` disposition error (CSV adapters never emit the pointer, so this can only come from a fixture) |

**Reading a chain.** Projections read the effective view, compute each chain's
head and reversals from `supersedes_event_id` and `correction_resolved` links,
treat `UNKNOWN` and `CONFLICT` chains as contributing nothing, and cite every
contributing `event_id` (§7.4). Recorded order (journal sequence) strictly
increases; `occurred_at` and `source_correction_time` may be back-dated. This
separation of effective time from recording order is what lets legitimate late
corrections in while the stale-revision rule keeps an older export from
rewinding a newer one.

### 6.4 Atomic rejection and the two torn-tail cases

A batch is accepted only if every row normalizes and every disposition rule
passes. Otherwise no event is appended; one `import_batch_rejected` record with
the complete `errors` list is appended (so the rejection is auditable); the
projection and the Supervisor Snapshot are unchanged except that data quality
reports `last_import_outcome = REJECTED` for that source with the error count
and file name.

Two torn-tail cases are distinguished:

1. **Line-aligned tail without a commit marker** (a crash after some complete
   lines of a batch were durable). Projections use the effective view, so the
   tail contributes nothing, and data quality reports `uncommitted_tail` with
   the batch id. The next import of the same file sees the tail's `event_id`s
   in the dedup view, appends no duplicate events, and lands the commit marker
   listing them under `duplicate_event_ids`; the journal gains exactly one
   record (`test_import.py::test_uncommitted_tail_recommits_with_one_record`).
2. **Byte-torn last line, missing anchor with records present, or rollback
   below the anchor.** `JsonlJournal` raises `JournalIntegrityError` on every
   read and append. Context becomes `UNAVAILABLE` in the snapshot with reason
   `journal_integrity_error`, imports are refused, the existing runtime is
   unaffected, and recovery is an operator action outside the package
   (restore from the last durable copy or re-provision an empty context
   directory, re-importing the source files). No self-repair exists
   (`test_import.py::test_byte_torn_journal_fails_closed_and_marks_context_unavailable`).

### 6.5 Storage layout and the import process model

```text
<out>/run-NNN/<site_id>/<deployment_id>/
  context/
    profiles/<source_system>.<digest>.json     # the exact profile used (content-addressed)
    context_journal.jsonl                      # nxt-operational-context/journal/v1
    context_journal.jsonl.hwm                  # high-water anchor
```

`context/` sits beside the existing `service/` directory and the canonical
workflow evidence root; it is neither Site Runtime evidence nor the Shadow Ops
ledger, and it is **per run**, so a fixture `reset()` starts an empty context
directory. Raw source files are **not** retained (§3.2 rule 6). In V0 the
`--import <source_system> <file>` flags run **inside the service process at
launch, before serving**; no separate import CLI process exists, so the Site
Agent's one-process-per-runs-directory rule applies unchanged. At launch and
resume the context reader (§8.2) re-verifies the journal (schema, anchor,
identity); a failure marks context `UNAVAILABLE` without touching the existing
runtime's fail-closed behavior.

---

## 7. Operational projections

All projection functions are pure: `project_*(effective_view, config, as_of,
admission_facts, profiles) -> ...Context`. `config` is the composition root's
`ContextConfig` (the `EdgeTaskConfig` pattern): `clock_in_admission_before_s`,
demand windows, play look-ahead; `profiles` supply the current mappings and
`stale_after_s`. Every numeric field is wrapped as `{value, label, evidence,
freshness}` where `label` is a §5 evidence label.

### 7.1 Coverage and freshness (shared)

```text
status  ∈ {ok, stale, missing}            # the repository's ObservationStatus vocabulary
freshness = {status, as_of, coverage_end, coverage_basis, newest_batch_received_at,
             age_s, stale_after_s, source_system}
```

- An **accepted batch** for a source is any `import_batch_committed` record or
  any `import_batch_duplicate` whose prior batch is committed; both carry
  `received_at` and `exported_at`.
- **Coverage end** is the instant up to which the source is known to describe
  the business: the newest declared `exported_at` over accepted batches when
  one exists (`coverage_basis = declared_export_time`); otherwise, the newest
  `occurred_at` of a recorded (non-planning) event in the effective view for
  that source (`coverage_basis = newest_recorded_event`). The second basis is
  conservative by construction: a quiet source with no declared export time
  cannot prove it is current and will read `stale` once `stale_after_s` has
  passed since its last recorded event. Operators who want a quiet source to
  read `ok` declare the export time.
- `age_s = as_of − coverage_end`. `status = missing` iff no accepted batch
  exists; `stale` iff `age_s ≥ stale_after_s`; `ok` iff `0 ≤ age_s <
  stale_after_s`. A negative age, or any accepted batch whose `clock_basis`
  differs from the projection's `as_of` basis, yields `status = missing` with
  reason `negative_age` or `clock_basis_mismatch`, `age_s = null`, and no
  derived number. `newest_batch_received_at` is shown beside coverage as the
  availability time, never used for the age.
- A `stale` or `missing` source forces **every** value derived from it in
  §7.2–§7.3 to `label = UNKNOWN`, while the last known numbers remain visible
  under `last_known` with their own `coverage_end`. This makes acceptance test
  10 hold at the data layer, not only in the UI.
- A window whose end exceeds the source's `coverage_end` is `UNKNOWN` with
  reason `window_not_covered`; a covered window with no transactions is
  `DERIVED 0` with `evidence.coverage = {coverage_start, coverage_end}`, so no
  evidence is never read as zero.

Thresholds live in profiles (mandatory); windows and the admission window live
in `ContextConfig`; none is a commissioned fact, and none has a default.

### 7.2 Staffing context

Presence is partitioned on **one scope, the instant `as_of`**; the day count is
plan-only.

| Field | Derivation | Label |
|---|---|---|
| `operating_day` | `as_of` in the commissioned timezone → `[day_start, day_end)` (§2.5), with the zone name | DERIVED or UNKNOWN |
| `scheduled_today` | count of distinct `staff_ref`s with an effective shift (chain head `SHIFT_SCHEDULED` or `SHIFT_CHANGE_APPROVED`, not cancelled) whose `[shift_start, shift_end)` overlaps the operating day (`shift_start < day_end and shift_end > day_start`); a plan, labelled as such | DERIVED or UNKNOWN |
| `scheduled_now` | `staff_ref`s with an effective shift whose `[shift_start − clock_in_admission_before_s, shift_end)` contains `as_of` | DERIVED or UNKNOWN |
| `confirmed_present_now` | those of `scheduled_now` with a `CLOCK_IN` satisfying `shift_start − clock_in_admission_before_s ≤ occurred_at ≤ as_of` and no `CLOCK_OUT` with `clock_in.occurred_at < occurred_at ≤ as_of`; events with `occurred_at > as_of` are not yet evidence | DERIVED or UNKNOWN |
| `confirmed_absent_now` | those of `scheduled_now` with an `ABSENCE_RECORDED` referencing (via `shift_record_id`) an effective shift overlapping `as_of`, or, without the link, with `occurred_at` inside that shift | DERIVED or UNKNOWN |
| `presence_unknown_now` | `scheduled_now − confirmed_present_now − confirmed_absent_now` (a non-empty intersection of present and absent is a data-quality issue, not a negative) | DERIVED or UNKNOWN |
| `present_unscheduled_now` | `staff_ref`s with an admitting `CLOCK_IN` and no `CLOCK_OUT` as above but no effective shift containing `as_of`; reported, never clamped away | DERIVED or UNKNOWN |
| `by_operational_capability` | per declared capability `{scheduled_now, confirmed_present_now, presence_unknown_now}` using the head shift's mapped capability; `UNMAPPED` is its own row | DERIVED or UNKNOWN |
| `next_material_change` | the earliest instant `t` with `as_of < t < day_end` at which some capability's `scheduled_now` count (`UNMAPPED` included) differs from its value at `as_of`, considering effective `shift_start` and `shift_end` instants and the admission offset; with the capability and delta; `null` when none remains before `day_end` | DERIVED or UNKNOWN |
| `approved_shift_changes` | count and list of today's `SHIFT_CHANGE_APPROVED` heads as `{shift_record_id, capability, effective_from}`; each list entry is one `SYSTEM_RECORDED` event | DERIVED (count) over SYSTEM_RECORDED entries |
| `pending_change_requests` | count of `SHIFT_CHANGE_REQUESTED` events for today with no `SHIFT_CHANGE_APPROVED` or `SHIFT_CHANGE_REJECTED` naming their `request_record_id`; never affects coverage | DERIVED or UNKNOWN |

`clock_in_admission_before_s` is declared configuration; without it
`scheduled_now`, `confirmed_present_now`, and the partition are `UNKNOWN`.

### 7.3 Demand and play context

| Field | Derivation | Label |
|---|---|---|
| `entitlement_sold` | per configured window (for example last 30, 60, 180 minutes and operating day to date): sum of `ball_entitlement` over effective `SALE_CAPTURED` heads attributed by `transaction_time`, minus their reversals (§5.2), using the current declared SKU mapping; with `mapped_transactions`, `unmapped_transactions`, `unmapped_quantity`, `mapping_version` | DERIVED or UNKNOWN |
| `sales_rate` | entitlement per hour over the shortest covered window, only when the sales source is `ok` | DERIVED or UNKNOWN |
| `upcoming_booked_players` | §5.3; booked headcount; look-ahead window configured | DERIVED or UNKNOWN |
| `confirmed_active_sessions` | §5.3, with `basis` and the newest play event time | DERIVED or UNKNOWN |
| `active_sessions_booked_players` | §5.3; a plan, labelled "booked headcount of started sessions" | DERIVED or UNKNOWN |
| `confirmed_active_players` | §5.3; only from `actual_player_count` | DERIVED or UNKNOWN |
| `completed_session_duration` | §5.3 statistics with exclusion counts | DERIVED or UNKNOWN |
| per-source `freshness` | §7.1 | — |

No field converts entitlement into inventory, consumption, or dispensing.

### 7.4 Traceability and mappings

Every value's `evidence` lists the `event_id`s (and through them the
`import_batch_id`s) that produced it, or, for large windows, the window bounds
plus the batch ids and a `derivation_digest` over the sorted contributing
event ids; values that depend on a mapping cite the profile digest and
`mapping_version` used. Projections apply the **current** declared mapping
from the profiles passed in, so a SKU or role added later maps earlier events
without re-import; `mapping_at_import` on each event preserves what was known
then. A test reconstructs each projected number from the cited events and the
cited mapping alone (acceptance test 12).

### 7.5 Data quality and exceptions (owned by the leaf)

`project_context_quality(view, as_of, admission_facts, profiles)` returns, as
plain data, `schema: "nxt-operational-context/context/v1"`,
`owner: "nxt_operational_context"`, `data_class: "BUSINESS_RECORDS"`,
`context_available`, `reasons`, per source `{source_system, source_kind ∈
{csv_file, replay_fixture}, last_import_outcome, last_batch_id, received_at,
exported_at, coverage_end, error_count, unmapped_counts (per token or digest),
profile_digest, freshness}`, and an `exceptions` list (rejected batches,
duplicate batches, conflicts, stale revisions, orphan corrections, uncommitted
tails, unmapped SKUs and roles as counts, unlinked approvals, contradictory
session statuses) whose entries already carry `owner` and the ids involved.
The shell copies these strings; it never composes them (§2.6).

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

### 8.2 One generation, one identity, and the seam

The only coherent multi-source read on the branch today is
`briefing_snapshot()`: one `with self._lock:` acquisition that excludes
`advance` and `respond`. Every other endpoint reads live evidence
independently, and the console's `Promise.all` over five endpoints can render
state at sequence N beside recommendations from N+1.

**Seam.** `CompositionSeam` gains two optional fields:

- `clock: ClockSource | None = None`, where `ClockSource(read: Callable[[],
  datetime], basis: Literal["FIXTURE_DECLARED", "SYSTEM_UTC"])` is a frozen
  plain-data dataclass in `nxt_site_agent/contracts.py`; `basis` is declared
  by the composition root, never inferred;
- `context_for: Callable[[Path], ContextReader] | None = None`, a **per-run
  factory** in the shape of the existing `composer`/`materials_for` fields,
  invoked by the service with `storage.identity_root` in `_launch_fresh`,
  `_resume`, `reset`, and `restart_runtime`, so the reader binds to the run's
  `context/` directory (§6.5). `ContextReader` exposes `verify() -> Mapping`
  and `snapshot(as_of: datetime) -> Mapping`, returning the leaf's plain-data
  projections (§7). A factory or `verify()` failure marks
  `context_available = false` with reason `context_unavailable` and never
  affects launch, resume, or the existing runtime.

Both default to `None`, so the three existing seam constructions and the 98
Site Agent tests are untouched.

**Generation.** `SiteAgentService.supervisor_snapshot()` reads
`as_of = clock.read()` exactly once, then under one `RLock` acquisition
(excluding `advance`, `respond`, `restart_runtime`, `reset`, and `stop`) calls
the **existing** `health_snapshot()`, `state_snapshot()`,
`recommendations_snapshot()`, `briefing_snapshot()`, and `fixture_snapshot()`
once each and `reader.snapshot(as_of)` once. Because `briefing_snapshot()`
itself nests `state_snapshot()`, `health_snapshot()`, and `_ledger_index()`,
and `recommendations_snapshot()` reads the ledger again, the embedded
sections repeat file reads; every read observes the same generation because
the lock excludes all mutators. It assembles:

```text
snapshot_schema   "nxt-site-agent/supervisor-snapshot/v1"
snapshot_id       "svs_" + stable_digest(data without snapshot_id)[:24]
                  using the already approved nxt_pilot_ops.serialization.stable_digest
generation        {generated_at: as_of as UTC ISO Z, scenario_time_s, clock_basis}
health, state, recommendations, briefing, fixture     # the five existing payloads, verbatim
staffing, demand, play                                 # §7 context sections
physical_stores, machines                              # derived explicit-unknown shapes (§8.3)
coverage_recommendations                               # reserved key, [] in this slice (§9)
exceptions        {facility: briefing.exceptions verbatim, context: [...] from §7.5}
data_quality      {facility: state.quality verbatim, context: {...} from §7.5,
                   context_read_errors: [...]}
```

When `clock` is `None`: `generation = {generated_at: null, clock_basis:
"UNAVAILABLE", scenario_time_s}`, every context section is `UNKNOWN` with
reason `clock_unavailable`, and the endpoint still returns 200. No guard
widening is needed: `nxt_pilot_ops.serialization` is already in
`ALLOWED_FIRST_PARTY_MODULES`, so `hashlib` stays out of the shell.

**Failure tiers.** Facility-evidence read failures raised by the five existing
calls (`SiteAgentError` codes `ledger_unreadable`, `queue_unreadable`,
`journal_unreadable`) propagate unchanged, exactly as their own endpoints
behave today and as `test_hardening.py` pins; the endpoint returns the coded
error (those three codes gain explicit `_STATUS_BY_CODE` entries mapping to
503) and the console keeps the previous generation with its STALE banner.
Context-reader failures never raise: they land in
`data_quality.context_read_errors` and the context sections read `UNKNOWN`.

### 8.3 Section rules

| Section | Content and owner |
|---|---|
| `health`, `state`, `recommendations`, `briefing`, `fixture` | the five existing projections, byte-identical to their endpoints; owners unchanged (`health.mode_label` stays `SERVICE_MODE_LABEL`; `fixture_mode` and `source_type: "fixture"` describe the existing runtime source, not the context sources). `recommendations` is, in every `supervisor-snapshot` version, the verbatim Guardian array (`Recommendation[]`); its shape never changes. |
| `staffing`, `demand`, `play` | §7, owner `nxt_operational_context`; the `schema`, `owner`, `data_class`, and per-source `source_kind` strings are produced by the leaf (§7.5) and copied verbatim, so the console can say "SYSTEM-RECORDED (CSV import)" for `csv_file` sources and "FIXTURE (replay)" for `replay_fixture` sources |
| `physical_stores` | derived, never hard-coded: for each of `clean_available`, `clean_sensed`: `{status: <SourceReference.status or "missing">, source_type: <SourceReference.source_type>, physical: source_type == "sensor", value_ref: "state.dispenser.<field>"}` taken from the same generation's `state.dispenser`; for `in_wash`, `dirty_buffered`, `awaiting_wash`: `{status: "missing", reason: "no_channel_declared", physical: false}`. `available = any(field.physical)`; `reason` is `"all inventory channels are simulated"` in fixture mode or `"no physical inventory source connected"` when no channel exists. Known-ness is derived from `AssemblyReport` and `source_references`, never from `FacilityState` values, because `FacilityState` is a total contract: a missing channel is backfilled from the previous state or a conservative default and a stale channel keeps its old value, so a value is never evidence of knowledge. The existing `StatePanel` is unchanged. |
| `machines` | `{available: false, reason: "no machine state source connected", fields: {washer_running, washer_fault, dispenser_state}}` each `{status: "missing", physical: false}` |
| `coverage_recommendations` | reserved for the second PR (§9); `[]` in this slice; never merged with `recommendations` |
| `exceptions.context` | from §7.5, each entry carrying `owner` and the ids involved |
| `data_quality.context` | from §7.5 |

### 8.4 Two clocks, stated honestly

The branch deliberately uses scenario time (latest observation timestamp) and
bans wall-clock reads so evidence is byte-identical across runs. Production
operational context needs a real "now" for freshness. The design keeps both
explicit and never subtracts across them:

- the composition root owns the clock and declares its basis through
  `ClockSource`; the fixture seam supplies a **declared** fixture clock (a
  fixed value, not the advancing scenario clock) whose operating day in the
  commissioned timezone equals the launch plan's `simulation_midnight_iso`
  date, asserted by a fixture consistency test; production supplies `utcnow`
  with basis `SYSTEM_UTC`;
- `generation.clock_basis` is `FIXTURE_DECLARED`, `SYSTEM_UTC`, or
  `UNAVAILABLE` and the console shows it in text;
- scenario time continues to drive the existing dispenser reading age and
  `responded_at` exactly as today; `health_snapshot()` and every existing
  endpoint stay clock-free so the branch's byte-identity tests, including the
  `--no-serve` stdout comparison, keep passing;
- every `import_batch_*` record carries `clock_basis`; a mismatch with the
  projection's basis is a data-quality issue (§7.1), never a number.

Pre-existing risk recorded for the owner: the `main` pilot fixture anchors
scenario midnight at `2026-08-08T00:00:00+00:00` while the same manifest
declares `"timezone": "Asia/Shanghai"`, and nothing cross-checks them. The
fixture clock value must be pinned so that its Asia/Shanghai date is
2026-08-08 (for example `2026-08-08T04:00:00Z`, 12:00 local), or the manifest
timezone changed; §14 asks which.

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
- New `StaffingTodayPanel`: `scheduled_today` (labelled as a plan),
  `scheduled_now`, `confirmed_present_now`, `presence_unknown_now` (never shown
  as absent), `present_unscheduled_now` when non-zero, role coverage table
  (`scheduled_now / present_now / unknown_now` per capability, `UNMAPPED` row
  shown), next material change, approved shift changes, pending requests, the
  operating day with its zone name. Every number carries its evidence label
  badge and its source freshness in text.
- New `DemandNowPanel`: entitlement sold per window with mapped/unmapped
  counts, sales rate (or `UNKNOWN` with reason), active sessions with their
  booked headcount labelled as booked, confirmed active players only when
  proven, upcoming booked players, completed-session duration statistics with
  exclusion counts, per-source coverage and freshness, and `clock_basis`.
- Implementation constraints from the branch guards: no new runtime npm
  dependency; no `WebSocket`/`EventSource`/browser storage; no `http(s)://`
  strings; a null-safe `formatCount` that renders `null` as "—" and a recorded
  `0` as "0"; reuse `Badge`, `Section`, `KeyValue`, `EmptyNote` and existing
  `.panel`/`.kv-grid` classes; do not reuse the inventory-specific
  `.inventory-*` classes; unique section titles (they double as aria-labels).
  No other visual redesign.
- No raw transactions, no employee rows, no names anywhere. No authenticated
  drill-down exists because the service has no authentication; that is a
  separate gate, not an unauthenticated endpoint.

Trust semantics preserved and extended (each a Vitest case): explicit no-data
states instead of zeros; `UNKNOWN`/`stale`/`missing` as text, not color alone;
never literal `null`/`undefined`; disclaimer end to end; schema check on the
envelope and `snapshot_schema` check on the data. On the Python side
`/api/v0/supervisor-snapshot` is appended to the `test_api.py`
schema/disclaimer and read-only endpoint lists, and a new
`test_snapshot.py::test_supervisor_snapshot_read_leaves_every_store_byte_identical`
extends the byte-equality check to `context/context_journal.jsonl`, its
`.hwm` anchor, every `context/profiles/*.json`, and `service/service_events.jsonl`
beside the existing three evidence streams.

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
`station_buffer_pressure`. Three are adjacent in theme and are named in the
divergence contract:

| Existing advice | What it reads | Relationship declared here |
|---|---|---|
| `stockout_demand_bound` | the simulation/telemetry **forecast** (`DemandState.forecast_balls_per_minute`) against washer throughput | **Intentional divergence, different input class.** The new policy reads bookings and sold entitlement (recorded business facts), never the forecast, and never computes washer capacity or stockout. |
| `stockout_dirty_supply`, `stockout_in_progress` | `FacilityState.ball_flow` and washable supply | **Intentional divergence, different input class.** The new policy has no inventory, in-wash, or washer-state input (§8.3 keeps them unknown). When it names a wash cycle it is advising a *human preparation step given coverage and demand*, with the explicit sentence that clean-ball inventory is unknown; it never asserts supply, shortage, or washer throughput. |
| `robot_down`, `assist_backlog`; the Guardian's `washer_available` fact | `StaffState`, the robot-assist technician pool; the Guardian's unavailable washer fact | **Different fact class.** Role coverage from schedules is not technician-pool capacity; the policy never reads `StaffState` and never supplies or consumes the Guardian's washer availability. |

The Ball Availability Guardian (`policy_id "ball-availability-guardian"`,
`policy_version "0.1.0"`) has no staffing or booking input and fails closed on
unavailable demand; it is unchanged. No component merges, ranks, or
deduplicates the new policy's output with either existing owner.

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
| Manager view | populates the reserved snapshot key `coverage_recommendations` (§8.3) with `owner: "nxt_pilot_ops"` and `policy_id` per entry; `recommendations` stays the verbatim Guardian array; the console renders the two lists separately, never ranked or merged. |
| Guards and documented contracts | `nxt_operational_context` in `UPSTREAM_PACKAGES` (§2.6); extend `FORBIDDEN_RULE_TOKENS` in `tests/edge_task/test_architecture.py` with the new policy and id prefixes; the `nxt_pilot_ops` row of `.agent/context/package-map.md` changes from "Only `adapters/` may import `nxt_facility.state`" to also permit the public `nxt_operational_context` projection contract, a documented-contract change that is part of this PR's gate; tests pin the divergence contract and the stale-forces-`NO_ACTION` rule. |

**Alternative rejected for now:** generalizing `Recommendation`,
`DecisionTrace`, `PolicyEvaluation`, and `RecommendationCase` behind policy-id
dispatch inside `nxt-pilot-ops-ledger/v1`. It touches every decoder and replay
path and needs a schema bump with migration; it is the better long-term shape
if a third policy appears and is noted as such.

Because every element in this table is a versioned `nxt_pilot_ops` contract
addition, the advisory piece is **its own architecture-review gate** and ships
as the second PR of the slice. The first PR carries no `nxt_pilot_ops` change,
its snapshot shows only existing Guardian output, and `coverage_recommendations`
is `[]`.

### 9.4 What the policy may say and when it stops

- Detect: a capability's `scheduled_now` falls at a future instant within the
  horizon while upcoming booked players or the entitlement sales rate exceed a
  declared threshold in an overlapping window.
- Recommend: an operational preparation for a human
  (`"Prepare a wash cycle before 14:45"`, `"Confirm front-desk coverage for
  15:30"`) with the evidence ids that produced it and the explicit statement
  that clean-ball inventory is unknown because no inventory source is
  connected. Under §9.1 the wash-cycle sentence is a staffing preparation
  given coverage and demand; the policy never names washer throughput, supply,
  or stockout.
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
| `simulation/nxt_operational_context/` (new) | `contracts.py`, `adapters/` (`csv_staffing.py`, `csv_sales.py`, `csv_play.py`, `replay.py`), `profiles.py`, `privacy.py`, `importer.py` (`decide_import`, chain algorithm), `projection.py`, `quality.py`, `freshness.py`, `operating_day.py`, `__init__.py` |
| `simulation/tests/operational_context/` (new) | behavioral tests, `fixtures/`, `test_architecture.py` guard with a negative control |
| `simulation/pyproject.toml` | add the package to the single-line `packages` list (wheel membership) |
| `.github/workflows/verification.yml` | add a focused `Test Operational Context` step, the architecture-list entry, the `compileall` entry, and the `shipped` tuple entry, as every package since Site Runtime has |
| Sibling reverse guards on `main` | append `nxt_operational_context` to `tests/site_runtime/test_architecture.py`, `tests/agent_runtime/test_architecture.py`, `tests/edge_observation/test_architecture.py`, `tests/workflow_enablement/test_architecture.py` (three lists), `tests/course_world_model/test_architecture.py` (four lists), `tests/pilot_ops/test_boundaries.py`, exactly the six files the `nxt_edge_interventions` commit touched |
| `simulation/nxt_site_agent/` (branch) | `contracts.py`: `ClockSource`, `ContextReader` protocol, `CompositionSeam.clock` and `CompositionSeam.context_for` (both default `None`); `service.py`: `supervisor_snapshot()` and reader lifecycle in `_launch_fresh`/`_resume`/`reset`/`restart_runtime`; `api.py`: one GET route and `_STATUS_BY_CODE` entries for `ledger_unreadable`, `queue_unreadable`, `journal_unreadable`; `projections.py`: snapshot assembly; `tests/site_agent/test_architecture.py`: `nxt_operational_context` into `BANNED_FIRST_PARTY_MENTIONS` and `OTHER_PACKAGES`, plus a positive assertion that the two scripts import `nxt_edge_task.journal` only; `test_api.py` endpoint lists; new `test_snapshot.py`. No stdlib whitelist change. |
| `simulation/scripts/site_agent_fixture.py`, `site_agent_demo.py` (branch) | compose the journal (`nxt_edge_task.journal.JsonlJournal`), profiles, replay fixture, admission facts, `ContextConfig`, the declared fixture `ClockSource`, and the per-run `context_for` factory; flags `--context-dir`, `--import <source_system> <file>`, `--exported-at <ISO-8601>`; `SCRIPT_BANNED_IMPORT_ROOTS` is a ban list that does not name `nxt_edge_task`, so no widening is required; `--no-serve` output remains `health_snapshot()` only |
| `apps/site-agent-console/` (branch) | `lib/api.ts` snapshot types and method; `lib/snapshot.ts`; `app/page.tsx` single fetch; two panels; `lib/format.ts::formatCount`; tests and fixtures |
| `simulation/docs/site_agent_v0.md` (branch) | add the `GET /api/v0/supervisor-snapshot` row to the Manager API table; replace "Wall clock is never read: reading age, `responded_at`, and every briefing time use observation/scenario time, so identical action sequences produce byte-identical canonical evidence across runs" with "No canonical evidence reads a wall clock: reading age, `responded_at`, and every briefing time use observation/scenario time, so identical action sequences produce byte-identical canonical evidence across runs; only `GET /api/v0/supervisor-snapshot` carries a `generation.generated_at` from the composition root's declared `ClockSource`, which never enters canonical evidence, `responded_at`, or any other endpoint" |
| Governance | `.agent/context/package-map.md`, `source-of-truth.md` (the §2.1 row), `architecture.md` (seam bullet, guarded-boundary row), `deployment.md` ("Also added after that baseline" paragraph, placement row, statement that the "Not implemented" rows are unchanged), `.agent/workflows/architecture-review.md` (placement row), `testing.md` (suite lists), `AGENTS.md` (dependency bullet, source-precedence doc list), `docs/ARCHITECTURE.md`, `docs/CI.md`, `README.md`, `simulation/README.md`, `.agent/context/product.md`, `simulation/docs/operational_context_v0.md` (stable doc written at implementation) |

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
| 2 | Corrections and refunds adjust totals, history preserved | `test_corrections.py::test_refund_subtracts_once_and_keeps_original` (full, partial under both models, re-import of the refund does not subtract twice, journal record count asserted); `test_stale_revision_never_becomes_head`; `test_orphan_correction_resolves_when_root_arrives`; `test_over_reversal_rejects_batch` |
| 3 | Scheduled ≠ present | `test_staffing.py::test_scheduled_without_clock_in_is_presence_unknown` |
| 4 | Clocked-in ≠ available | `test_staffing.py::test_no_projection_key_asserts_availability_and_clock_in_feeds_presence_only` |
| 5 | Unapproved requests do not alter coverage | `test_staffing.py::test_requested_change_leaves_coverage_unchanged_until_approved`; `test_rejected_request_leaves_pending_count` |
| 6 | Missing clock-out ⇒ no duration | `test_staffing.py::test_clock_in_without_clock_out_sets_no_end_and_no_duration_key_exists` |
| 7 | Booked ≠ started | `test_play.py::test_booked_players_are_not_active_and_active_players_unknown_without_actual_count` |
| 8 | Missing finish ⇒ unknown duration | `test_play.py::test_missing_finish_excluded_from_duration_stats` |
| 9 | Entitlement never labelled dispensed or available | `test_architecture.py::test_projection_keys_contain_no_inventory_vocabulary` plus label assertions in `test_demand.py` |
| 10 | Stale ⇒ no green status | `test_freshness.py::test_stale_source_forces_unknown_labels`; `test_quiet_source_without_declared_export_time_goes_stale`; console `component.test.tsx` shows `stale` in text for both panels |
| 11 | Partial invalid import ⇒ no inconsistent snapshot | `test_import.py::test_one_bad_row_rejects_whole_batch`; `tests/site_agent/test_snapshot.py::test_rejected_batch_leaves_snapshot_unchanged_and_reports_it`; `test_import.py::test_uncommitted_tail_recommits_with_one_record`; `test_byte_torn_journal_fails_closed_and_marks_context_unavailable` |
| 12 | Every value traceable | `test_traceability.py::test_each_projected_value_recomputes_from_cited_events_and_mapping` |
| 13 | Existing console trust tests pass | the existing 39 Vitest cases untouched; the 98 `tests/site_agent` cases keep their assertions and stay green, with list literals extended as recorded in §8.5/§10 (the two endpoint tuples in `test_api.py`, `BANNED_FIRST_PARTY_MENTIONS`, `OTHER_PACKAGES`); baseline observed in §1.3 |
| 14 | Physical inventory and machine fields remain unknown | `tests/site_agent/test_snapshot.py::test_physical_stores_and_machines_derive_unknown_from_source_references` (fixture mode: `physical == false` for every field, `available == false`) |

Plus: determinism (same effective view, `as_of`, and profiles ⇒ byte-identical
projection and `snapshot_id`); privacy (§3.2 tests); identity mismatch
rejection; identifier shape; timezone and DST row errors; operating-day
boundary ambiguity; `event_conflict` on unlinked content change;
`supersession_target_mismatch` only from a fixture row; `SHIFT_CANCELLED`
removes the shift from coverage; `LATE_RECORDED` changes nothing; mapping
change maps earlier events without re-import and without conflict; window
attribution never yields a negative total; `occurred_after_received` beyond
tolerance; fixture clock operating-day consistency with
`simulation_midnight_iso`; `clock is None` ⇒ 200 with `UNAVAILABLE`;
one-generation coherence (a concurrent `advance` on a second thread cannot
interleave inside `supervisor_snapshot`); the read-only byte-equality test of
§8.5; the package guard with negative control; `splitSnapshot` deep-equality;
no name in any record field, snapshot field, or emitted log line.

---

## 12. Explicit cuts (not implemented, not stubbed)

Employee self-service; staff mobile app; shift scheduling or approval
workflows; payroll; performance scoring or any per-person ranking; POS
functionality or connection; customer CRM; pricing optimization; revenue or
labor-savings claims; physical sensor ingestion; robot or washer commands;
generic dashboards; visual redesign beyond the two sections; vendor APIs;
browser automation or scraping; authentication (and therefore any drill-down
endpoint); forecasting; raw source-file retention; an availability projection;
a per-row quarantine; a separate import process.

## 13. Follow-on seams preserved

| Follow-on | Seam left clean by this design |
|---|---|
| 1 Clean-ball weight ingestion | `physical_stores.clean_*` derive `physical: true` only when a `sensor` source type appears in the envelope, fed later through the existing adapter and Site Runtime path, never by this package |
| 2 Awaiting-wash inventory | `physical_stores.awaiting_wash` placeholder; same path |
| 3 Washer running state | `machines.washer_running/washer_fault` placeholders; `edge_observation_v0.md` already records `washer_running` as unmapped raw data awaiting a canonical decision |
| 4 Dispensed-ball evidence | a future dispensing source is the only thing allowed to relate `entitlement_sold` to dispensing, through an explicit, labelled reconciliation projection |
| 5 Demand and supply forecasting | an `ESTIMATED` projection over this journal, versioned, possibly feeding `UpstreamInputs.forecast_balls_per_minute` through the anticipated telemetry seam (§2.4) |
| 6 Supervised robot execution interface | untouched; nothing here reaches `RobotTaskInterface`, `apply_directive()`, or any adapter |

## 14. Unresolved vendor and product questions

1. **Staffing source:** does the export carry a stable opaque record id per
   shift and per clock event, or only per employee per day? Are approved
   changes new shift rows or change records with a link to the original
   (`shift_record_id`)? Does it carry an export timestamp?
2. **Role codes:** who declares the role-code to operational-capability
   mapping, and can one person hold several capabilities per shift?
3. **POS:** are void and refund a status change on the original row (requires
   a correction time and, for partial refunds, a refunded-quantity column) or
   a new row (requires `original_transaction_id`)? Does the export carry an
   export timestamp?
4. **SKU table:** who owns and versions the SKU to balls mapping; are bundles a
   single SKU?
5. **Tee sheet:** is actual start recorded by a starter, a gate, or not at
   all? Is finish recorded? Is an actual head count ever recorded? Without
   them, play duration and `confirmed_active_players` stay `UNKNOWN`.
6. **Export cadence and timezones:** how often are files exported, do they
   carry offsets, and is the commissioned timezone correct for the pilot site?
7. **Operating day:** local midnight (V0) or facility close?
8. **Admission window, thresholds, tolerances:** who declares
   `clock_in_admission_before_s`, each source's `stale_after_s` and
   `clock_skew_tolerance_s`, and the demand windows?
9. **Fixture clock:** pin a fixture clock value whose Asia/Shanghai date is
   2026-08-08, or change the pilot manifest timezone; the UTC-midnight anchor
   and the manifest zone currently disagree.
10. **Base branch:** Option A, B, or C in §1.4.
11. **Endpoint path:** `/api/v0/supervisor-snapshot` (proposed) or a `/api/v1`
    transport version.
12. **Quarantine:** accept V0 strict rejection (a permanently invalid historical
    row blocks a source until re-export), or approve a journaled per-row
    quarantine as the next slice.
13. **Audit retention:** whether any raw source file may ever be retained
    (this design says no and records only digests).
14. **Availability:** whether a later slice projects `AVAILABILITY_DECLARED`
    (with a declared validity window) or the field stays out.
15. **Advisory contract (second PR):** the §9.3 route versus generalizing the
    existing recommendation contracts; whether a ledger-level expiry event
    should exist; whether `nxt_agent_runtime` stays single-policy.

## 15. Self-review record

**Revision 2.** A six-lens adversarial review (planned/recorded/derived
confusion, ambiguity, scope creep, architecture gate, console trust semantics,
corrections and idempotency) produced 79 findings. Fifty-five of them were
verified against the specification and the repository by two independent
skeptics. The review run was interrupted twice by container restarts, so the
remaining 24 (16 under corrections and idempotency, 3 under the architecture
gate, 2 under scope creep, and 1 each under ambiguity, console trust, and fact
confusion) were adjudicated by the author alone against the same sources.
Every finding was then judged by the author. Changes made in response:

- freshness now ages the source's **coverage end** (declared export time or
  newest recorded event), not the import time, with explicit `ok`/`stale`
  boundaries and a definition of "accepted batch";
- `occurred_at` is the source's record time for every event type; planned
  instants live only in payloads; a tolerance-bounded `occurred_after_received`
  rule replaces the undefined "scheduled" exemption;
- the correction model is one algorithm with family-specific chain keys, a
  head rule, stale revisions, orphan resolution, conflicts, over-reversal, and
  a `content_digest` that excludes profile-derived fields so mapping changes
  never create conflicts; projections re-apply the current mapping;
- `SHIFT_CANCELLED`, `SHIFT_CHANGE_REJECTED`, `LATE_RECORDED`, and
  `PLAYER_COUNT_UPDATED` have defined effects; `related_record_id` became
  `shift_record_id` and `request_record_id`; `session_ref` is required;
- presence is partitioned on one instant scope with an unclamped
  `present_unscheduled_now`; the day count is plan-only; `available_now` was
  removed and acceptance test 4 restated; `confirmed_active_players` was split
  from session counts and booked headcount;
- aggregate labels are `DERIVED or UNKNOWN`; `SYSTEM_RECORDED` attaches to
  events and list entries only;
- the privacy rule binds every adapter, defines the forbidden-header test,
  digests free-text values, validates identifier shape, and keeps values out
  of error details and logs; HR and payroll vocabulary was removed from the
  payload;
- the seam is a per-run `context_for` factory plus a `ClockSource` with a
  declared basis and defined `None` behavior; `snapshot_id` uses the already
  approved `stable_digest`, so no stdlib whitelist widening exists; the script
  guard needs no change; `OTHER_PACKAGES` is also extended;
- `recommendations` stays the verbatim Guardian array forever and
  `coverage_recommendations` is reserved; the divergence table names the
  washer-supply rules and the Guardian's washer fact explicitly;
- facility-evidence read failures keep today's endpoint semantics, while
  context-reader failures never raise; the single-ledger-read claim was
  dropped; `physical_stores` is derived from source references instead of
  hard-coded; `site_agent_v0.md` joins the governance edits; the read-only
  test covers the context store;
- torn-write behavior is stated in two cases with no self-repair claim; the
  import process model is explicit; strict rejection of a permanently invalid
  row is stated and tested.

Checked and retained from revision 1: no second mutable facility truth;
`FacilityState`, `UpstreamInputs`, and the channel vocabulary untouched;
business records are a named new fact class with one owner; projections are
regenerable from the journal; recommendations stay advisory and
owner-identified; nothing reaches an execution surface; `MEASURED` is never
produced here, and the dispenser channel pair is passed through with its own
`SourceReference.source_type` so fixture data reads as simulated; scenario
time and UTC are never subtracted from each other; the console and service are
labelled as an unmerged branch throughout; `SiteClock` is precedent only; the
observed baseline is reported as observed.

**Gate outcome: Pause.** Ownership and placement are resolved (Proceed), the
advisory piece needs its own gate (Reshape), and implementation cannot start
until the base-branch decision (§1.4) is made.
