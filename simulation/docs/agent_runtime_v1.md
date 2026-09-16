# Agent Runtime V1

`nxt_agent_runtime` is the deterministic, restart-safe, human-in-the-loop
composition and lifecycle layer over the existing Site Runtime and Shadow
Ops contracts:

```text
ObservationSource (at-least-once)
    -> SequencedObservationFrame
    -> SiteRuntimePipeline (existing publication contract)
    -> FacilitySnapshotEnvelope
    -> nxt_pilot_ops.adapters.adapt_facility_state (existing adapter)
    -> BallAvailabilityGuardian.evaluate (existing policy)
    -> PolicyEvaluation (NO_ACTION or RECOMMEND)
    -> evaluation journal + Shadow Ops ledger + existing DecisionTrace
    -> pending manager-decision view -> existing human workflow records
    -> deferred source acknowledgement
```

V1 runs against deterministic synthetic or fixture-backed observations only.
It does not connect to a physical robot, transport, or live facility.

## Why Agent Runtime is not Shadow Ops

`nxt_pilot_ops` owns every decision semantic: `OperationalSnapshot`, the
Guardian, `PolicyEvaluation`, `EvaluationVerdict`, `Recommendation`,
`DecisionTrace`, human workflow records, and the hash-chained ledger.  The
Agent Runtime creates none of those facts and re-models none of them.  It
invokes the existing adapter and guardian, appends the existing issuance and
response events, and stores references to their content-derived IDs.  Shadow
Ops has no concept of a continuous cycle, a restart, or "which envelope has
been evaluated" — that lifecycle is the runtime's only decision-adjacent
ownership.

## Why Agent Runtime is not Site Runtime

`nxt_site_runtime` owns observation admission and state publication:
validation ordering, the quality gate, the deterministic envelope, the
publication checkpoint, and idempotent publication.  It is guard-tested to
never import policy.  The Agent Runtime drives that pipeline through its
public API and owns only what happens *around* it: when a cycle runs, when
the source acknowledgement is released, whether the published envelope has
been evaluated, and where the evaluation evidence lands.  The Site Runtime
publication checkpoint is never overloaded with evaluation, policy, or
human-workflow state; the runtime keeps a separate `EvaluationCheckpoint`.

## Ownership map

| Fact | Owner | Runtime's relationship |
|---|---|---|
| Observation frames, assembly, `AssemblyReport` | `nxt_telemetry` | passed through untouched |
| Envelope, publication checkpoint, ack/reject semantics | `nxt_site_runtime` | driven via public `SiteRuntimePipeline` |
| Snapshot, verdicts, recommendation, trace, workflow, ledger | `nxt_pilot_ops` | invoked; records referenced/serialized verbatim |
| Evaluation lifecycle checkpoint | `nxt_agent_runtime` | owned (`EvaluationCheckpoint`) |
| Evaluation journal (`nxt-agent-runtime/evaluation/v1`) | `nxt_agent_runtime` | owned append-only evidence |
| Pending manager-decision view + deferral metadata | `nxt_agent_runtime` | owned projection over ledger replay |
| Health/status | `nxt_agent_runtime` | owned noncanonical diagnostics |

## Lifecycle

`AgentRuntime` is single-threaded and deterministic.  Its API:

- `run_once()` — one bounded cycle: peek, publish, evaluate, acknowledge.
- `run(max_cycles=None, failure_policy=STOP)` — bounded loop; ends on the
  bound, `SourceExhausted`, a stop request, a rejected cycle (STOP policy),
  a deferred evaluation, or an acknowledgement failure.  `max_cycles=None`
  is the production-style entrypoint: the source must block in `observe()`
  or raise `SourceExhausted`; the runtime adds no polling, no busy loop,
  and no background thread.  `FailurePolicy.CONTINUE` is for sources that
  discard terminal input; a run still ends after two consecutive identical
  rejections, so a retained bad frame can never spin the loop.
- `request_stop()` — graceful, idempotent; takes effect before the next
  cycle.  `run_once()` on a stopped runtime raises `runtime_stopped`.
- `recover()` — read-only store verification and checkpoint reconciliation;
  runs automatically before the first cycle.
- `status()` — read-only `RuntimeStatus`; never raises, never mutates.
- `queue` — the `ManagerDecisionQueue` view.

Error policy is two-tier and explicit:

- **Retryable** (`evaluation_checkpoint_unavailable`, `journal_unavailable`,
  `ledger_unavailable`, `acknowledge_failed`, `recovery_unavailable`, and
  every Site Runtime rejection): the cycle ends degraded, the frame stays
  un-acknowledged or retained, and a later cycle retries deterministically.
- **Fail-closed** (`evaluation_replay_mismatch`, `evaluation_sequence_gap`,
  `journal_divergence`, `journal_identity_mismatch`,
  `ledger_identity_mismatch`, `ledger_transition_rejected`,
  `evaluation_failed`, `evidence_verification_failed`,
  `checkpoint_divergence`, `evaluation_checkpoint_failed`): the runtime
  enters `FAILED`, refuses further cycles, and preserves the incident in
  status.  Divergent evidence is never replaced or resequenced.  Recovery
  verifies both directions: a journal ahead of the evaluation checkpoint
  *and* a completed evaluation missing from the journal fail closed, and
  evidence stores holding another `(site_id, deployment_id)` identity are
  rejected outright.  These validations are explicit runtime checks, never
  asserts, so they hold under optimized Python (`python -O`) as well — a
  malformed cross-package result (for example a RECOMMEND evaluation with
  no recommendation) fails closed before any evidence is written.

## Cross-layer checkpoint ordering

The runtime hands the pipeline a wrapper around the real observation source
that delegates rejections immediately (Site Runtime keeps sole ownership of
terminal-input rejection) but holds the acknowledgement until the evaluation
lifecycle completes.  One admitted cycle is ordered:

```text
1. site publication checkpoint prepare -> publish -> complete   (existing)
2. adapter + guardian evaluation (in memory, deterministic)
3. evaluation checkpoint prepare (sequence, envelope_id, evaluation_id)
4. ledger append of recommendation_issued (RECOMMEND only; idempotent)
5. journal append of the EvaluationRecord (idempotent)
6. evaluation checkpoint complete
7. source acknowledgement released
```

Because the assembler and the guardian are pure functions of the frame, an
un-acknowledged frame redelivered after a crash reproduces the identical
envelope (enforced by the pipeline's replay-mismatch check) and identical
evaluation/recommendation/trace IDs.  Restart cases:

- **Published, not evaluated** — redelivered frame takes the pipeline's
  completed-replay path (no republish), then evaluates once.
- **Prepared, not persisted** — the pending evaluation recomputes to the
  same `evaluation_id` and appends exactly once.
- **Persisted, not completed** — the identical journal/ledger records are
  detected and the checkpoint completes without duplication.
- **Identical replay** — `replay_skipped`: no new evidence, no duplicate
  pending decision, acknowledgement released.
- **Divergent replay** — fail closed at the site layer (`replay_mismatch`)
  or the evaluation layer (`evaluation_replay_mismatch`); the incident is
  retained and evidence is never silently replaced.

No canonical ID depends on wall clock, UUID, process ID, filesystem path,
randomness, or dict ordering; all IDs are content-derived SHA-256 digests,
and all times are scenario/observation time.

## NO_ACTION semantics

`EvaluationVerdict.NO_ACTION` is the existing Shadow Ops verdict and is
recorded positively: every admitted envelope yields exactly one
`EvaluationRecord` in the append-only journal.  A `NO_ACTION` record embeds
the canonical `DecisionTrace` payload (serialized with the existing
`nxt_pilot_ops` canonical serializer) because no other durable store records
why the Agent did not act; the trace carries the evidence considered, the
per-candidate exclusion reasons, missing-data reasons, and rationale.  A
`RECOMMEND` record instead references the ledger issuance event that
canonically stores the recommendation and trace — the journal never becomes
a competing policy store.  A rejected Site Runtime input produces **no**
record of either kind: rejection evidence stays in the `RuntimeSink` and
runtime status, never as facility or policy truth.

## Manager decision queue

`ManagerDecisionQueue` is a deterministic read-model over
`JsonlEventLedger.replay()` joined with the journal, plus thin operation
helpers that create the existing workflow records:

- `pending()` / `entries()` / `entry_for()` — projections carrying the
  recommendation ID, action, trace ID, source envelope ID and sequence,
  issued/execute-before observation times, workflow state, and response
  status.  Owner identity is preserved; nothing is aggregated, ranked, or
  reconciled across decision surfaces.  A queue scoped to a
  `(site_id, deployment_id)` — the runtime always scopes its own — never
  shows or operates on another identity's recommendations.
- `accept` / `reject` / `modify` — build the existing
  `RecommendationResponse` and append the existing response event.  The
  ledger enforces transition legality; the original recommendation is
  immutable, and a modification is a linked `ModifiedRecommendation` that
  preserves it verbatim.  Acceptance is a human workflow record only — it
  does not create, imply, or schedule any robot command.
- `defer` — the existing workflow has no defer response, so deferral is
  modeled only as narrow, non-persistent runtime scheduling metadata
  (`deferred_until`, note) on the queue view.  It writes no ledger event,
  never touches the recommendation, is cleared by any response, and does
  not survive restart; `due(as_of=...)` filters by deterministic scenario
  time.

## Health/status

`RuntimeStatus` is a frozen, JSON-ready dataclass with runtime state,
degraded flag, cycle/evaluation counters, source exhaustion, last observed/
published/evaluated sequences and envelope IDs, last evaluation ID and
verdict, both checkpoints' pending flags, pending and deferred decision
counts, last observation timestamp and effective confidence, and the last
failure code/detail.  It is diagnostics only: it reads no clock, is never
canonical evidence, and is never an input to any decision.  There is no
HTTP server and no network dependency.

## Evidence and artifact output

One evidence directory per `(site_id, deployment_id)`:

| File | Writer | Nature |
|---|---|---|
| `snapshots.jsonl` | `JsonlSnapshotPublisher` | Published envelope `to_dict()` stream; regenerable presentation/replay projection, idempotent by `envelope_id`; **not** a state truth store and never an input to assembly, policy, or any live-loop decision (presentation readers such as the Site Agent Manager Console may display it) |
| `ledger.jsonl` | `nxt_pilot_ops.JsonlEventLedger` | Canonical decision/workflow evidence (hash-chained, append-only) |
| `evaluations.jsonl` | `EvaluationJournal` | Canonical runtime evaluation evidence (`nxt-agent-runtime/evaluation/v1`), one record per admitted envelope, idempotent by `evaluation_id`, contiguous by sequence |
| `checkpoints/site/…` | `nxt_site_runtime.JsonCheckpointStore` | Publication progress (existing contract) |
| `checkpoints/evaluation/…` | `JsonEvaluationCheckpointStore` | Evaluation progress (runtime-owned) |

Field ownership inside an `EvaluationRecord`: envelope identity fields are
Site Runtime facts; verdict/policy/trace/recommendation fields are Shadow
Ops facts referenced by ID or embedded verbatim through the existing
canonical serializer; only `evaluation_id`, the schema version, and the
record framing are runtime-owned.  Duplicate writes are prevented by
content-derived IDs plus verify-before-append; identical re-appends no-op
and divergent re-appends fail closed.

Journal and snapshot-stream appends verify incrementally: each store
instance fully verifies the file once (a fresh instance verifies
everything on its first write), then verifies only the bytes appended
since, anchored on the last verified line, so a run of N cycles stays
linear instead of quadratic (measured: 1,000 journal appends of realistic
~7 KB records dropped from ~180 s to 0.27 s; 10,000 appends complete in
~2.6 s).  Shrinkage or tail edits fail closed at append time; an in-place
edit inside the already-verified region is caught by the full
verification that `read()`, `recover()`, and every queue projection
always perform.  Agent Runtime V1 is POSIX-only: the journal, the
snapshot publisher, and the JSON evaluation checkpoint store all require
`fcntl` advisory locking and fail loudly at construction elsewhere.  Intentionally absent: copied
FacilityState fields, denormalized policy fields, wall-clock timestamps,
and any success/outcome scoring.  These files are append-only historical
evidence for Operational Memory, Operational Replay, or a future Manager
Console to consume later; none of them feeds the live loop, and Operational
Replay is not a runtime dependency.

## Deterministic Pilot fixture

`scripts/agent_runtime_demo.py` runs **Pilot Course A — Evening Demand
Spike**, clearly labeled `SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER
DATA`.  Frames derive from one synthetic-bank sample of the existing
`normal_weekday` scenario with hand-set, ball-conserving evening values:

- 17:30 — inventory covers the forecast through the risk horizon →
  `NO_ACTION` with full trace evidence;
- 18:30 — the spike projects a stockout; because native `FacilityState`
  lacks collector capability, ETA, expected yield, collection permission,
  current demand, and live washer availability, every candidate is
  fail-closed excluded and the policy escalates with
  `OPERATOR_INTERVENTION` rather than fabricating a dispatch;
- a simulated crash before the second acknowledgement, a restart with an
  idempotent `replay_skipped` recovery, a scripted deterministic manager
  acceptance (workflow record only), final status, and byte-identical
  artifacts across identical invocations.

```bash
.venv/bin/python scripts/agent_runtime_demo.py --out reports/agent_runtime
```

The enriched synthetic dispatch fixture in `tests/pilot_ops/conftest.py`
remains policy-test evidence only; the runtime demo does not use it and
does not present dispatch as a native deployment-path capability.

## Physical execution boundary

No path from this package can reach physical robot execution, ROS, an
actuator, navigation, or an emergency stop.  The package imports only the
public Site Runtime and Shadow Ops surfaces (both guard-tested to have no
command surface), and `tests/agent_runtime/test_architecture.py`
mechanically bans simulator/robot/network imports, execution tokens,
wall-clock/UUID calls, and any reverse dependency.  Accepting a
recommendation records human workflow evidence; nothing translates it into
a command.  LLMs and generative agents remain outside this loop entirely.

## Current limitations and next seam

- Synthetic/fixture observation sources only; no physical adapter,
  transport, production publisher/sink, service scheduler, or live-site
  operation exists.  The local JSONL publisher is a demo/evidence sink,
  not a production delivery service.
- POSIX hosts only: evidence and checkpoint stores require `fcntl`
  advisory locking and refuse construction elsewhere.
- `clean_sensed_valid` is a caller-declared constant per runtime instance.
- Deferral metadata does not survive restart by design.
- Operational Memory harvesting of the evaluation journal is a future,
  separately designed seam.
- The exact next integration seam for real observations: implement the
  existing `nxt_site_runtime.ObservationSource` protocol (peek-until-ack,
  redelivery of un-acknowledged frames, terminal-input discard) over a real
  transport, after the separate architecture review that
  `.agent/context/deployment.md` requires.  The runtime composes such a
  source unchanged.

## Verification

From `simulation/` with the all-extras environment:

```bash
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/agent_runtime
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_runtime tests/pilot_ops
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
uv run --no-sync python -B scripts/validate_configs.py
```

The architecture guard for this package is
`tests/agent_runtime/test_architecture.py`; the demo determinism guard is
`tests/agent_runtime/test_demo_script.py`.
