# Shadow Ops v0.1 — operational decision layer

Status: deterministic shadow-mode core, validated against the repository's
simulation contracts. It is not validated operating policy or a real-pilot
performance claim.

## Architecture and ownership

`nxt_pilot_ops` is a downstream sibling package:

```text
FacilityState object / offline FacilityState JSONL replay
    -> explicit read-only adapter
    -> OperationalSnapshot
    -> Ball Availability Guardian
    -> Recommendation + DecisionTrace
    -> human response / execution acknowledgement / recorded outcome
    -> append-only tamper-evident ledger
```

Only `nxt_pilot_ops.adapters` may know the upstream FacilityState or export
shape. Policy, projection, workflow, serialization, and ledger modules consume
only Shadow Ops modules and the Python standard library. No existing
facility, simulator, replay, viewer, telemetry, memory, or twin package imports
`nxt_pilot_ops`.

A `Recommendation` is an advisory record. The package has no directive,
motion, path-planning, actuator, charging-control, e-stop, command-bus, ROS,
or hardware API. No UI or command bridge is included.

## Verified upstream mapping

The mapping below is pinned to baseline commit
`eb51c8a678b13a0ceaa89477be94e20d737f27bf`.

| Actual upstream source | Exact path and type | OperationalSnapshot field | Provenance | Missing-data behavior |
|---|---|---|---|---|
| Adapter context or stream sidecar | `stream.meta.json.site_id: str` | `site_id` | required caller context or validated sidecar value; `source_ref` names the episode/JSONL line | required; never inferred from scenario name |
| Adapter context or stream sidecar | `stream.meta.json.deployment_id: str` | `deployment_id` | required caller context or validated sidecar value | required |
| Facility clock | `FacilityState.meta.t_s: float` | `observed_at` | `meta.t_s` plus a caller-supplied timezone-aware simulation midnight | naive/missing origin rejected; `t_s` is seconds since midnight |
| Capture order | JSONL record order; no sequence in each state record | `source_sequence` | zero-based record ordinal plus physical JSONL line in `source_ref` | malformed or non-monotonic records rejected; blank lines follow the upstream reader and are ignored |
| Stream sidecar | `schema == "nxt-range-twin/facility-state-stream/v1"` | `source_schema_version` | exact sidecar field | wrong/missing schema rejected; object contract is explicitly unversioned |
| Facility inventory | `ball_flow.clean_available: int` | `clean_available` | direct `FacilityState` path; builder source is the conservation ledger | bool/non-integer/negative and non-conserving ball flow rejected |
| Facility sensed inventory | `ball_flow.clean_sensed: float` | `clean_sensed` | direct delayed sensed-buffer path plus explicit caller validity declaration | kept separate; record-zero validity is required because the v1 sidecar does not encode sensor-start state |
| Demand forecast | `demand.forecast_balls_per_minute: tuple[float, ...]` | `forecast_demand_balls_per_minute` | frozen forecast bucket series | series preserved; malformed/empty series rejected |
| Forecast cadence | `demand.forecast_bucket_minutes: int` | `forecast_bucket_minutes` | direct state field | bool/non-positive rejected |
| Closing horizon | `demand.minutes_to_close: float` | `minutes_to_close` | direct state field | caps the projection horizon |
| Current demand | no FacilityState field | `current_demand_balls_per_minute` | `unavailable: FacilityState exposes cumulative demand and forecast only` | `None`; cumulative counters are not relabeled as current demand |
| Timed inbound supply | no ETA-bearing batch records | `inbound_batches` | unavailable | empty tuple plus trace reason; `in_wash`, station buffers, and payloads are not assigned invented ETAs |
| Robot identity/status | `robots[*].robot_id`, `activity`, `health`, flags | `robots[*].robot_id/status` | exact snapshot fields | explicit enum map; unknown values fail loudly |
| Battery | `robots[*].battery_frac: float` | `battery_fraction` | direct simulator snapshot value | bool/non-finite/out-of-range rejected |
| Payload | `robots[*].payload_balls: int` | `payload_balls` | direct ledger-backed snapshot field | bool/non-integer/negative rejected |
| Collector capability | no FacilityState field | `capabilities` | unavailable | `None`; candidate excluded |
| Expected clean yield | no FacilityState field | `expected_clean_ball_yield` | unavailable | `None`; candidate excluded |
| Return ETA | no FacilityState field | `replenishment_eta_minutes` | unavailable | `None`; candidate excluded |
| Task/fault IDs | destination/assigned zone and coarse health only | `current_task_id`, `fault_code` | exact raw activity/health retained in trace | no task or fault code is fabricated |
| Facility hours | `meta.facility_open: bool` | `range_open` | direct state field | exact value used |
| Collection permission / route block | no global field | `collection_allowed` | unavailable | `None`; dispatch blocked and reason traced |
| Washer availability | throughput/WIP exist, live availability does not | `washer_available` | unavailable | `None`; washing-dependent dispatch blocked and reason traced |

The viewer's `nxt-range-viewer/episode/v1` frames are not accepted as policy
input because they omit sensed inventory, the demand forecast, site identity,
permissions, ETA, and yield. The native FacilityState stream is accepted only
for deterministic offline replay. Its validated sidecar identity, simulator
version, policy, disclaimer, sensed-validity declaration, and unknown extension
keys are retained as capture metadata. USD and other twin projections are never
an input to the policy path.

### Robot status mapping

Emergency-stop and failed-health flags override activity. `IDLE` with `OK`
health maps to `AVAILABLE`; `TRAVELING`, `COLLECTING`, `QUEUED_HANDOFF`, and
`UNLOADING` map to `BUSY`; charger queue/charging map to `CHARGING`; `PAUSED`
maps to `PAUSED`; failed and emergency-stop states map to `FAULTED` and
`ESTOPPED`; human-wait states map to `AWAITING_HUMAN`. Degraded health is
conservatively unavailable because the upstream snapshot has no degraded-speed
ETA. Every unmapped activity or health value is an adapter error.

## Inventory and projection semantics

`clean_available` is the facility accounting value. `clean_sensed` is a direct
sensed estimate and may be fractional. They are never added, averaged, or
substituted silently. When a valid sensed estimate exists, it is the decision
basis; otherwise accounting inventory is used. Every trace records both raw
values, both provenance strings, the selected basis, and the selected value.
The object adapter requires an explicit sensed-validity flag. The offline reader
also requires the caller to declare record zero as `valid` or `pre-sensor`; it
does not infer missingness from record position.

Demand is projected as the per-bucket maximum of current and forecast rates
when both exist, or the sole available source. The real FacilityState has no
current-rate field, so repository-native adaptation uses the forecast series
and says so. Projection stops at the earliest of the policy risk horizon, the
available forecast horizon, and closing time; it never repeats a forecast past
its exported horizon.

Committed inbound batches are included only when an input provides both an ETA
and positive clean-ball quantity. Collector counterfactuals are eligible only
when the collector is safe/available, has explicit capability, ETA and yield,
arrives strictly before the first projected stockout, and produces a positive
extension. Ranking is greatest extension, then earlier ETA, then robot ID.
Inbound source IDs must be unique, and committed batches cannot use the
reserved `counterfactual:` namespace, so one upstream arrival cannot collide
with or impersonate a candidate action.

The shipped FacilityState contract cannot support a collector-dispatch
recommendation because it lacks capability, ETA, yield, collection permission,
and washer availability. A real risk therefore escalates to operator
intervention rather than inventing those facts. Enriched synthetic inputs are
used only to test the dispatch policy itself.

Every trace includes the complete normalized policy configuration, operational
constraint values and provenance, normalized candidates, deterministic winner,
projection relationships, and a content-bound trace ID. Recommendation IDs are
likewise bound to the complete normalized advisory record. Ledger issuance
re-derives the exact policy demand basis/rates/horizon, replays baseline and
candidate projections, and revalidates verdict, action, target, alert window,
deadline, and canonical advisory summary against the trace. Equivalent
timezone representations therefore produce the same IDs.

## Offline review CLI

The CLI reads only a completed repository-native capture and prints deterministic
JSON. For the complete stream written by `scripts/facility_twin_capture.py`, use
the explicit `pre-sensor` declaration; use `valid` for a post-tick or sliced
stream whose first sensed value is known to be valid.

```bash
python -m nxt_pilot_ops facility_states.jsonl \
  --meta stream.meta.json \
  --simulation-midnight 2026-08-08T00:00:00+00:00 \
  --first-record-sensed-state pre-sensor
```

Output contains `mode`, validated `source` metadata (including the upstream
disclaimer), and `evaluations`. It has no command or execution capability.

## Human workflow and ledger

The original recommendation is immutable. A modification creates a linked,
immutable modified-recommendation record; it never overwrites the original.
Accept, reject, modify, execution-requested, execution-acknowledged, and outcome
records enforce legal ordering and time/reference integrity. Execution records
are human workflow acknowledgements only.

The JSONL ledger is append-only through its API, uses canonical serialization,
stable event IDs, duplicate rejection, monotonic sequence numbers, and a SHA-256
hash chain. Verification rejects duplicate JSON keys, non-canonical or truncated
records (including CRLF rewrites), unrehashed edits, chain breaks, content-ID
mismatches, unknown
references, and illegal or semantically inconsistent transitions. Replay then
reconstructs the same immutable workflow state.

The chain is not externally anchored. A party able to replace the entire file
and recompute every content/event/record ID can create a different internally
valid history. External head-hash anchoring is deferred. This is scoped
tamper evidence, not cryptographic non-repudiation or a compliance claim.

## Adversarial review fixes

The separate adversarial pass found and fixed:

- acceptance of corrupted, non-conserving or boolean-valued object ball flow;
- projection past the declared horizon, skipped tiny buckets, and tolerance that
  could let a genuinely late arrival hide an earlier stockout;
- duplicate inbound source IDs and falsey invalid guardian configuration;
- ordinal-based sensed missingness, foreign status-enum acceptance, and test
  coverage that did not actually reach malformed state JSON;
- trace/issuance records that were shape-valid but could disagree on target,
  ranking, exclusions, projections, demand basis, horizon, policy thresholds,
  alert admission, verdict, or deadlines;
- committed inbound records that could collide with generated counterfactual
  source IDs, and timezone-equivalent inputs whose human summary text produced
  different recommendation IDs;
- duplicate ledger JSON keys, non-canonical/truncated records, and detached
  offline capture disclaimers, including physical CRLF normalization that had
  previously bypassed the canonical-byte check; and
- an unconditional POSIX import that broke ledger-module import elsewhere.

## Verification record

The frozen baseline was commit
`eb51c8a678b13a0ceaa89477be94e20d737f27bf`. With the repository's optional
test extras installed, its full suite passed 412 tests before integration. The
final branch was verified from `simulation/` with:

```text
.venv/bin/python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/pilot_ops
155 passed in 0.42s

.venv/bin/python -B -m pytest -o addopts='' -q -p no:cacheprovider
567 passed in 15.48s

.venv/bin/python -B scripts/validate_configs.py
7 configurations validated; 0 errors, 0 warnings

uv build --out-dir <temporary-directory>
source distribution and wheel built successfully; the wheel contains nxt_pilot_ops

git diff --check
clean
```

`uv.lock` remained byte-identical to the baseline. The repository configures no
formatter, linter, or static type checker; Ruff and Black were not available in
the environment, so no unconfigured-tool result is claimed.

## Known limitations and deferred work

- No real telemetry adapter currently supplies collector ETA/yield/capability,
  route permission, current demand, or washer availability.
- The native v1 sidecar does not encode first-record sensor validity; every
  reader invocation must supply that fact explicitly and it is retained in
  provenance.
- FacilityState objects assembled from telemetry require their separate
  missingness/provenance report; callers must explicitly declare sensed-value
  validity to the object adapter.
- Thresholds and counterfactual results are deterministic policy configuration,
  not validated physical performance.
- `JsonlEventLedger` currently requires POSIX `fcntl` locking. The module imports
  portably and fails explicitly when that backend is unavailable; a Windows
  ledger backend is deferred.
- Detecting a complete, consistently rehashed ledger replacement requires an
  external anchor for the previously trusted head hash.
- UI, command bridge, automatic execution, LLM policy calls, RL, and policy
  self-modification are deferred and absent.
