# Continuous Collection Execution V4 Runbook

Status: **IMPLEMENTED AND LOCALLY VERIFIED — UNMERGED**

This runbook operates the continuous V4 composition service in deterministic
`SIMULATION` mode. V4 accepts more than one confirmed Planning plan during one
finite V3 session and runs the resulting collection tasks sequentially. It does
not replace the fixed single-task 3C service documented in
[the V3 runbook](collection_execution_v3_runbook.md).

The evidence recorded below was produced from repository head
`27fcea2d4639dc695f57bd342998803967ecbd09` on 2026-10-01. The branch remains
local and unmerged.

## 1. Scope and ownership

V4 composes existing owners; it does not introduce a second scheduler or a new
execution authority.

| Fact or action | Owner |
|---|---|
| Planning input, plan, confirmation, and manual outcome | `nxt_pilot_ops` |
| Due-time admission and schedule state | `nxt_edge_task.ScheduleService` |
| Edge task and device lifecycle | `nxt_edge_task` and the simulator-backed task device |
| Binding, request, receipt, attempt, and execution evidence | collection execution V1 store |
| Action proposal | existing `JointDispatchPolicy` and execution arbiter |
| Final action admission | `SafetyShield` |
| Mutable simulation state | `RangeSimulation` |
| Ball quantity and location | `BallLedger` |
| Recovery, materialization, and one simulation step per iteration | the single V4 background driver |
| HTTP transport | `nxt_site_agent.api` callbacks |
| Strict parsing and presentation | Site Agent Console |

The browser has no collection-execution POST route. A confirmed Planning plan
persists intent; only the serialized driver may recover the schedule, bind the
Edge task, admit the simulator-backed device, and advance the V3 session. The
original policy still runs first, an execution proposal may fill only a `Wait`
slot, and every selected action still passes through `SafetyShield`.

This service is one finite session. It remains `ACTIVE` after ordinary task
terminals, but it does not create the next session, day, or round.

## 2. Prerequisites

Run from a clean checkout containing the V4 implementation. The normative
environment is the locked all-extras Python 3.13 environment and Node
`>=22.13.0`. Do not update `uv.lock` or `package-lock.json` while following this
runbook.

Build the static console before starting a service that serves the UI:

```bash
cd apps/site-agent-console
npm ci
npm run build
cd ../../simulation
```

If only API evidence is needed, add `--api-only`; this skips the static console
requirement without changing Planning, execution, or durable-evidence behavior.

Check that the chosen port is free. Port `8774` is used below, but it is not part
of any durable identity. If it is occupied, use another free port and record the
actual value. Do not stop an unrelated process.

## 3. Create an empty evidence root

`--initialize` accepts only a new empty root. Keep logs, request bodies, and HTTP
responses outside that root because creating any of them inside it makes
initialization fail.

```bash
PROOF_ROOT="$(mktemp -d /private/tmp/nxt-continuous-v4-proof.XXXXXX)"
REQUEST_ROOT="$(mktemp -d /private/tmp/nxt-continuous-v4-requests.XXXXXX)"
FRESH_SERVICE_LOG="$(mktemp /private/tmp/nxt-continuous-v4-service-fresh.log.XXXXXX)"
RESTART_SERVICE_LOG="$(mktemp /private/tmp/nxt-continuous-v4-service-restart.log.XXXXXX)"
PURITY_SERVICE_LOG="$(mktemp /private/tmp/nxt-continuous-v4-service-purity.log.XXXXXX)"
PORT=8774
```

After initialization, the root marker must be exactly this service family:

```json
{"environment":"SIMULATION","schema":"nxt-course-continuous-collection-service/v1"}
```

The fixed 3C service marker is deliberately incompatible with this root.

## 4. Prepare the exact two Planning chains

The two request chains are derived from the production helper
`scripts.course_collection_execution_demo.planning_requests()`. The mutations
are frozen by
`tests/site_agent/test_continuous_collection_execution_service.py::planning_chain_requests`.
The following creates the six request templates outside the evidence root:

```bash
REQUEST_ROOT="$REQUEST_ROOT" uv run --no-sync python -B <<'PY'
from copy import deepcopy
import json
import os
from pathlib import Path

from scripts.course_collection_execution_demo import planning_requests

root = Path(os.environ["REQUEST_ROOT"])

def chain(revision: int, start_at_utc: str, inventory: int):
    source, plan, confirmation = deepcopy(planning_requests())
    suffix = f"{revision:03d}"
    source["request_id"] = f"continuous-input-{suffix}"
    source["expected_revision"] = revision - 1
    source["reason"] = f"Continuous input revision {revision}."
    source["inventory_clean_balls"]["source_ref"] = (
        f"continuous-clean-bin-{suffix}"
    )
    source["inventory_clean_balls"]["value"] = inventory
    plan["request_id"] = f"continuous-plan-{suffix}"
    plan["input_revision"] = revision
    plan["selection"]["start_at_utc"] = start_at_utc
    plan["reason"] = f"Continuous collection plan {revision}."
    confirmation["request_id"] = f"continuous-confirmation-{suffix}"
    for name, body in (
        ("input", source),
        ("plan", plan),
        ("confirmation-template", confirmation),
    ):
        (root / f"{suffix}-{name}.json").write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

chain(1, "2026-09-16T08:10:00Z", 600)
chain(2, "2026-09-16T08:40:00Z", 3000)
PY
```

The distinguishing fields are:

| Field | Chain 1 | Chain 2 |
|---|---|---|
| input request | `continuous-input-001` | `continuous-input-002` |
| expected revision | `0` | `1` |
| inventory source | `continuous-clean-bin-001` | `continuous-clean-bin-002` |
| inventory value | 600 | 3000 |
| plan request | `continuous-plan-001` | `continuous-plan-002` |
| plan input revision | 1 | 2 |
| selected start | `2026-09-16T08:10:00Z` | `2026-09-16T08:40:00Z` |
| confirmation request | `continuous-confirmation-001` | `continuous-confirmation-002` |

Never copy a `plan_id` from recorded evidence. Read it from that chain's plan
POST response and inject it into the confirmation template.

## 5. Start a fresh service

Before executing this section, copy the `post_chain` function from §6 into the
same shell. That lets the readiness loop submit chain 1 immediately rather than
spending the first six-second window preparing shell state.

From `simulation/`, start the canonical console-serving service:

```bash
UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache \
uv run --no-sync python -B -m scripts.course_collection_execution_v4_service \
  --out "$PROOF_ROOT" \
  --initialize \
  --port "$PORT" \
  --driver-interval 6 \
  >"$FRESH_SERVICE_LOG" 2>&1 &
SERVICE_PID=$!
```

The Manager Console is at `http://127.0.0.1:$PORT/`. API readiness requires
more than an open socket. Poll `GET /api/v0/task-ops` until all of these are
true:

- HTTP status is 200;
- `data.service_capabilities.schema` is
  `nxt-pilot-dispatch/service-capabilities/v2`;
- `data.service_capabilities.mode` is `CONTINUOUS_V3_EXECUTION`;
- `data.scheduler.state` is `RUNNING`;
- `data.runtime.session_state` is `ACTIVE`.

Before sending chain 1, also require
`data.runtime.simulation_time_utc == "2026-09-16T08:10:00Z"`. The default
driver advances 600 simulated seconds every six wall-clock seconds. If the first
tick has already passed 08:10, stop the process cleanly, discard this root, and
start again from a new empty root. Prepare requests before starting the service
and automate the first three POSTs to avoid this race. A human demonstration may
use `--driver-interval 30`, but the recorded proof below used `6`.

This readiness loop makes at most 50 attempts, caps each HTTP request at one
second, records the response outside the evidence root, and submits immediately.
Every failure path checks the child process, stops it if necessary, waits for it,
and prints the last response and service log. If the simulated time check fails,
create a new root rather than continuing with a missed schedule:

```bash
fail_fresh_startup() {
  failure_message="$1"
  echo "$failure_message" >&2
  if kill -0 "$SERVICE_PID" 2>/dev/null; then
    kill -INT "$SERVICE_PID"
  fi
  service_exit=0
  wait "$SERVICE_PID" || service_exit=$?
  echo "service exit status: $service_exit" >&2
  if [ -s "$REQUEST_ROOT/ready-task-ops.json" ]; then
    echo "last task-ops response:" >&2
    cat "$REQUEST_ROOT/ready-task-ops.json" >&2
  fi
  echo "last 80 service log lines ($FRESH_SERVICE_LOG):" >&2
  tail -n 80 "$FRESH_SERVICE_LOG" >&2
  exit 1
}

READINESS_ATTEMPTS=50
readiness_attempt=1
ready=0
while [ "$readiness_attempt" -le "$READINESS_ATTEMPTS" ]; do
  if ! kill -0 "$SERVICE_PID" 2>/dev/null; then
    fail_fresh_startup "service exited before API readiness"
  fi
  if curl --fail --silent --show-error --max-time 1 \
      "http://127.0.0.1:$PORT/api/v0/task-ops" \
      >"$REQUEST_ROOT/ready-task-ops.json" &&
    jq -e '
      .data.service_capabilities.schema ==
        "nxt-pilot-dispatch/service-capabilities/v2" and
      .data.service_capabilities.mode == "CONTINUOUS_V3_EXECUTION" and
      .data.scheduler.state == "RUNNING" and
      .data.runtime.session_state == "ACTIVE"
    ' "$REQUEST_ROOT/ready-task-ops.json" >/dev/null; then
    simulation_time_utc="$(jq -er '.data.runtime.simulation_time_utc' \
      "$REQUEST_ROOT/ready-task-ops.json")" ||
      fail_fresh_startup "ready response lacks simulation_time_utc"
    if [ "$simulation_time_utc" != "2026-09-16T08:10:00Z" ]; then
      fail_fresh_startup \
        "missed the 08:10 admission window (observed $simulation_time_utc); use a new root"
    fi
    post_chain 001 || fail_fresh_startup "chain 1 submission failed"
    ready=1
    break
  fi
  sleep 0.1
  readiness_attempt=$((readiness_attempt + 1))
done

if [ "$ready" -ne 1 ]; then
  fail_fresh_startup \
    "service was not API-ready after $READINESS_ATTEMPTS attempts"
fi
```

Verify the marker after readiness:

```bash
jq -e '
  .environment == "SIMULATION" and
  .schema == "nxt-course-continuous-collection-service/v1"
' "$PROOF_ROOT/continuous-service.json"
```

## 6. Submit a Planning chain

For each chain, POST input, plan, and confirmation in that order. Every response
must be HTTP 200 and its complete envelope should be retained outside the
evidence root. This shell function injects the returned plan identity:

```bash
post_chain() {
  suffix="$1"
  base="http://127.0.0.1:$PORT"

  curl --fail-with-body --silent --show-error \
    -H 'Content-Type: application/json' \
    --data-binary "@$REQUEST_ROOT/$suffix-input.json" \
    "$base/api/v1/planning/inputs" \
    >"$REQUEST_ROOT/$suffix-input-response.json"

  curl --fail-with-body --silent --show-error \
    -H 'Content-Type: application/json' \
    --data-binary "@$REQUEST_ROOT/$suffix-plan.json" \
    "$base/api/v1/planning/plans" \
    >"$REQUEST_ROOT/$suffix-plan-response.json"

  plan_id="$(jq -er '.data.record.plan_id' \
    "$REQUEST_ROOT/$suffix-plan-response.json")"
  plan_version="$(jq -er '.data.record.version' \
    "$REQUEST_ROOT/$suffix-plan-response.json")"
  jq --arg plan_id "$plan_id" --argjson plan_version "$plan_version" \
    '.plan_id = $plan_id | .plan_version = $plan_version' \
    "$REQUEST_ROOT/$suffix-confirmation-template.json" \
    >"$REQUEST_ROOT/$suffix-confirmation.json"

  curl --fail-with-body --silent --show-error \
    -H 'Content-Type: application/json' \
    --data-binary "@$REQUEST_ROOT/$suffix-confirmation.json" \
    "$base/api/v1/planning/confirmations" \
    >"$REQUEST_ROOT/$suffix-confirmation-response.json"
}
```

If the readiness loop in §5 was used, chain 1 is already submitted. Otherwise,
submit it immediately after the 08:10 readiness check:

```bash
post_chain 001
```

Confirmation means the intent is durable. It does not mean that the device has
accepted or started it.

## 7. Wait for chain 1, then submit chain 2

Poll `GET /api/v1/collection-executions`. Locate the chain by causal references,
not by array index:

1. read `confirmation_id` from the confirmation receipt;
2. find the matching row in `bindings[]`;
3. use its `binding_id` to find the execution.

The chain-1 RUNNING predicate is:

- `state == "RUNNING"`;
- `started_sim_t_s != null`;
- `assignment_id != null`;
- `raw_quantity.balls > 0`;
- the entire snapshot contains at most one `RUNNING` execution.

Only after this predicate is observed, submit chain 2. This ordering starts with
the chain-2 input POST: revision 2 supersedes revision 1 for dispatch, so posting
the second input before the first schedule is dispatched and observed `RUNNING`
can cause the first schedule to be rejected as superseded. The `RUNNING`
observation is the barrier that makes it safe to post the entire second chain:

```bash
post_chain 002
```

Poll every 200–500 ms and save the full execution, Planning, and task-operations
snapshots at these boundaries:

1. chain 1 first observed RUNNING;
2. chain 1 reaches one of `SUCCEEDED`, `PARTIAL`, `REJECTED`, `MISSED`, `FAILED`,
   or `INCONCLUSIVE`;
3. both executions are terminal and the `RUNNING` count is zero.

Also require `started_sim_t_s` for chain 2 to be greater than or equal to
`terminal_sim_t_s` for chain 1. A normal terminal does not end the driver or
session.

Stop cleanly after collecting the final evidence:

```bash
kill -INT "$SERVICE_PID"
wait "$SERVICE_PID"
```

The expected exit code is 0.

## 8. Uninterrupted proof observed on 2026-10-01

The uninterrupted run used the API-equivalent command above with `--api-only`,
port `8774`, and a fresh root. Both chains were sent through public Planning HTTP
routes; chain 2 was sent only after chain 1 was observed RUNNING with nonzero
ledger-backed quantity. The process was not restarted.

### 8.1 Session and final service state

| Field | Observed value |
|---|---|
| series | `collection-execution-series-v3` |
| session | `collection-execution-session-v3` |
| round | `collection-execution-round-v3` |
| epoch | `2026-09-16T00:00:00Z` |
| final simulated time | `2026-09-16T09:00:00Z` (`t=32400.0`) |
| session state | `ACTIVE` |
| driver after first / second terminal | `RUNNING` / `RUNNING` |
| scheduler after first / second terminal | `RUNNING` / `RUNNING` |
| Planning outcomes | `[]` |
| config digest | `518deac1f992a453ea40a440b346ebfcf3fcf54f8b1cb9c32b6cb1ee694393b3` |
| engine digest | `b3e31f4904a3e58713474a87c879be3480f5ece6bc6b5bd6302311baabba0d29` |
| final replay digest | `ad3b5c32518050522c753e964ebb751407cd702f2ddb94e27ecbf49000e0d1cd` |

### 8.2 Complete causal identities

| Identity | Chain 1 | Chain 2 |
|---|---|---|
| input request | `continuous-input-001` | `continuous-input-002` |
| plan request | `continuous-plan-001` | `continuous-plan-002` |
| confirmation request | `continuous-confirmation-001` | `continuous-confirmation-002` |
| plan | `plan-9e2b1e474adc5fde3d8002a1` v1 | `plan-e53a2e38dc66d227c8b0677c` v1 |
| confirmation | `confirmation_3a06c3c3f65c7aebee43a54f` | `confirmation_676eaa75723a52b123d8ac09` |
| schedule | `schedule_b7cb7f2d896ab910ace1a1e1` | `schedule_826becf5e8d940da07d59423` |
| task | `task_e67abab7fe5666824af72300` | `task_51b158a10d9deba0b1aae545` |
| binding | `c729a7dfd1b2128a235ff03461fbc6571946ffb20dca0b47bcdcebaf4458af1c` | `0660a893d8304b121a556969ae2e0e66b0dbc2d7be435925fb84073382a2bc63` |
| execution request | `exec_req_579b8bb9dac804ea425d948c` | `exec_req_9ec7f8a0eac9d63fe98f102e` |
| execution | `75ac1b88002c03ecdec352ed27d9c306893ac4c29674c891eac1ddf96808f215` | `125688930584b23999ff42f630c60f77f7daef28019dfb65c033db17151a3a24` |
| attempt | `attempt-75ac1b88002c03ecdec352ed27d9c306893ac4c29674c891eac1ddf96808f215` | `attempt-125688930584b23999ff42f630c60f77f7daef28019dfb65c033db17151a3a24` |
| assignment | `assignment-cb92a8f8c137db1f65e5a92f672945956048d5f3f971329900e150cc7616461d` | `assignment-112f751b8329d287d128072099a217c0fb69db1ede97a7be951edd69992c8d73` |

Both schedules finished as `DISPATCHED`, with no schedule reason or detail. Task
1 was created at `2026-09-16T08:10:00.000000Z` and ended Edge-side as
`SUCCEEDED`. Task 2 was created at `2026-09-16T08:40:00.000000Z`; collection
execution correctly reports `PARTIAL / ZONE_EMPTY`, while the existing Edge task
projection maps that partial result to `FAILED / unknown:partial_execution`.
These are separate owner vocabularies, not a contradiction.

### 8.3 Terminal quantities and runtime evidence

| Field | Chain 1 | Chain 2 |
|---|---|---|
| start / terminal `t` | `29400.0` / `30118.0336438065` | `31200.0` / `31893.30872699392` |
| execution terminal | `SUCCEEDED / UNLOADED_ALL_COLLECTED_BALLS` | `PARTIAL / ZONE_EMPTY` |
| raw status / milestone | `COMPLETE / RAW_COLLECTED_TO_ROBOT` | `COMPLETE / RAW_COLLECTED_TO_ROBOT` |
| raw | 600 to `R1` | 296 to `R1` |
| unload status / milestone | `COMPLETE / UNLOADED_TO_STATION` | `COMPLETE / UNLOADED_TO_STATION` |
| unload | 600 to `H1` | 296 to `H1` |
| quantity source | `RANGE_SIMULATION_BALL_LEDGER` | `RANGE_SIMULATION_BALL_LEDGER` |
| collection exit | `ROBOT_PAYLOAD_FULL` | `ZONE_EMPTY` |
| event sequence | 1–19, complete | 1–12, complete |
| event digest | `8feada8515a152816dbeda7ef12b340344c89eeb6fa7a226462ce865c104cc37` | `69301d39fd20c395d223d142a6f33da261f1dc0b5c0fc1c968acd0642c6fc72a` |
| conservation | passed | passed |
| payload parity | passed | passed |
| device protection | none | none |

Each raw and unload quantity names the same assignment ID shown in §8.2 for its
execution. Both Edge evidence projections have `accepted=true`,
`result_verification=VERIFIED`, and complete terminal evidence; their effective
states are `SUCCEEDED` for chain 1 and `FAILED` for chain 2, reflecting the Edge
projection rule for a partial collection result.

Chain 1 raw source events:

```text
assignment-event-d4d1eee09c9cd3296e06471fb14d9d7b903c2d7a4a77b1945a51137400e32255
assignment-event-8c86fbc8f6786000633395072fad6e5c604e4195d63a40431910d04f6f9eb662
assignment-event-8db89e31e1197d83066e136d795298df1bbf6b5a64d0c8c72690850a6649e666
assignment-event-915dd31b3025cf01249558703d00b3299a9ab6ccdf12c14d99796103217ec116
assignment-event-729139f7d8520b29ad263f9817bbd3d5a2ba261a6dcbf163d281557a9d37384f
assignment-event-c0800e631a421bb6703f6a82fff4d6f486a35dc2ca23ad929ebad765ee84772d
assignment-event-e1422dcd2dbe5ab4ef2c6f0b68e6cdb4f26e23a70b35f5477bdc9b331dc18aaf
assignment-event-7e43511ee1f5b79ba3584599609c08032a753bf9a82c819709c3434454821818
assignment-event-21efdd4f2ad4c800f87c41c6cf1e2417ccd24a861f865d824c3e4cbdc67b15d6
assignment-event-aca96a3f70013b053ddd319d748911a0edce49b588284e78d52cb30d4c299268
assignment-event-2bf4c5d07ef3ec829ba0e1bc0bae6129ab2402bbf453a779131aa48e56ffcb05
assignment-event-7f3eb588345aae9c67c19997e5b516c624ee636957d4ed027de4d0e5d8ba60a3
assignment-event-2aa70b4a256857f06928bcd005a97f268a9e18c373f6fb612120e007d3cda4ad
assignment-event-f43cd8ec2704c3ef5c8b5229780cba8a2bba3aa47aacf56849a6b2df90396fae
assignment-event-df3afd07e65e2504be154d670f9d79a58b86fadd0ce1d83b4ddae3b606bc091a
```

Chain 1 unload source event:

```text
assignment-event-833a52cb4650e8eabe5179e0340589ca7a88406a674f295ea2badb6641b142ef
```

Chain 2 raw source events:

```text
assignment-event-40622fcd4d016147a33433a9aaf2229336d62bfb802714049b0b59d1b6a833e3
assignment-event-e5412fc28d662271c02cde57387f9e0bb81a1a63bd77dde6f31f18fe703422b1
assignment-event-b591a9c148aac0120bb692bafefeef84f10bbe3618c8311d754b9b31c0fb45c2
assignment-event-598f7fd933b4b528297a0a5cd241f85266666bf696c0c44bb47d777d0806be4d
assignment-event-0252850928ff986021e511c65bb4a950cd339ae10ff8f4b3b5d1ff537862dd00
assignment-event-1eb4138667987c3c3ff6a2196f989265d2475bc79994f6ca18de68b3d2c4adee
assignment-event-e6510e524a80d0ac5381f10bd055de23e93d001fb484eaeed9aab476f937fb9e
assignment-event-311137d085b7065ffe3c1898760453f14ba7d6db7deeb22bba61e5885548a653
```

Chain 2 unload source event:

```text
assignment-event-c6285de432be0e5b1766293015ebfebd8a2704923408f342d8d5a40c03e7c208
```

The verified Edge record IDs were:

| Chain | Edge records |
|---|---|
| 1 | `rec_0fd359fcd477fb25089d1e5a`, `rec_28de8cedde616a4d037b4819`, `rec_76e4d3f09e74adb18a9e493c`, `rec_83ef63f96780f7a3e6f68469`, `rec_dd86bc055ae7fe70d4cfc048`, `rec_e9b24960a453e6946b2fe94c` |
| 2 | `rec_13d16d433494e5abea504317`, `rec_77390fb18d4eae06ef5ab943`, `rec_8384229d71b6645a4e5a8862`, `rec_a4172d8a4844e419cb7a08e1`, `rec_de9a3e38f2dcba667c8fd3b1`, `rec_f6fc4d230e8567c2579acb22` |

## 9. Resume the same durable root

There is no `--resume` flag. After a clean `SIGINT` stop, reuse the exact root
and omit `--initialize`:

```bash
UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache \
uv run --no-sync python -B -m scripts.course_collection_execution_v4_service \
  --out "$PROOF_ROOT" \
  --port "$PORT" \
  --driver-interval 6 \
  >"$RESTART_SERVICE_LOG" 2>&1 &
SERVICE_PID=$!
```

Do not create logs or request artifacts in the evidence root. A normal restart
keeps the provisioned incarnation and reuses durable admissions; it is not an
incarnation mismatch.

## 10. Restart-before-second-due proof

The restart proof used a second empty root and port `8775`:

1. start with `--initialize --driver-interval 6 --api-only`;
2. submit chain 1 at 08:10;
3. wait for chain 1 RUNNING with raw quantity, then submit chain 2;
4. wait for exactly simulated 08:30, where chain 1 is terminal and chain 2 is
   still `SCHEDULED`, with no task, binding, request, attempt, assignment, or
   ledger movement;
5. send `SIGINT` and wait for exit 0;
6. restart the same root without `--initialize`;
7. read immediately before the next driver step, then wait for chain 2 to reach
   terminal.

Observed timeline:

| Process | Simulated time | Observation | Replay digest |
|---|---:|---|---|
| initial | 08:10 | chain 1 accepted | `0185e769a51b6e60bb43c8360b0a79f26482576c4c35082599e257540fb9a6f1` |
| initial | 08:20 | chain 1 RUNNING, 600 raw / 0 unload; chain 2 then submitted | `58ba4167e8aa9f87ddcbbef20cfbeed8711b791c7d0320e5f2add0d7e2578ed9` |
| initial | 08:30 | chain 1 SUCCEEDED 600/600; chain 2 not due | `aa3a38bbe90b4501ee74084c1232d1df30eb8a2b862a3a2a8b14eb04f3b3ba52` |
| immediate restart | 08:30 | identical durable state before advancement | `aa3a38bbe90b4501ee74084c1232d1df30eb8a2b862a3a2a8b14eb04f3b3ba52` |
| restart | 08:40 | chain 2 due; execution not yet started | `a7501d28b183e11a457d18b070d58360249d3d24a87159979e288aa8bad095bc` |
| restart | 08:50 | chain 2 RUNNING, 296 raw / 0 unload | `22363af0ad815a17e58842fa1b8142d2c2a204db55236719f0f87c160cf3c953` |
| restart | 09:00 | chain 2 PARTIAL, 296/296 | `9dad437eb7940ee2b0c9d6fc0d441bbb7596b99678f39a41cec1ddde50fb248e` |

All confirmation, schedule, task, binding, execution-request, execution,
attempt, and assignment IDs match the uninterrupted proof. Each owner array has
exactly two records, each execution has one attempt, source event IDs are unique,
and chain 1's attempt, assignment, quantity objects, and event IDs remain
byte-for-byte unchanged after restart.

Stable comparisons across these deterministic runs are the causal IDs, terminal
state/reason, assignment-scoped quantities, event digest, conservation, and
payload parity. Do not require all response envelopes or all replay digests to
match across process histories. Wall-clock `server_time_utc`, boot/status journal
records, Edge lifecycle record IDs appended by restart, and the resulting final
replay digest may differ. Within the immediate 08:30 stop/restart boundary, the
durable replay digest did remain exactly stable as shown above.

## 11. Prove GET purity

An ACTIVE session continues to tick after both tasks terminate. To isolate reads
from driver writes, stop the six-second service cleanly and restart the same root
without `--initialize`, using a 3600-second interval:

```bash
UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache \
uv run --no-sync python -B -m scripts.course_collection_execution_v4_service \
  --out "$PROOF_ROOT" \
  --port "$PORT" \
  --driver-interval 3600 \
  --api-only \
  >"$PURITY_SERVICE_LOG" 2>&1 &
SERVICE_PID=$!
```

Hash regular files in POSIX-relative-path order. Exclude the top-level
`site-agent/` subtree and every file whose basename ends in `.lock`. For each
included file, feed SHA-256 with:

```text
uint64be(path byte length) || UTF-8 relative path ||
uint64be(content byte length) || exact content bytes
```

This covers `continuous-service.json`, `edge/`, `device/`, and `session-v3/`, and
detects both content and path changes. It deliberately excludes the independent
Site Agent fixture/runtime subtree.

The following helper implements that exact rule. Save its output outside the
evidence root:

```bash
PROOF_ROOT="$PROOF_ROOT" python3 - <<'PY'
from hashlib import sha256
import os
from pathlib import Path
from struct import pack

root = Path(os.environ["PROOF_ROOT"])
rows = []
for path in root.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(root)
    if relative.parts[0] == "site-agent" or path.name.endswith(".lock"):
        continue
    rows.append((relative.as_posix(), path.read_bytes()))

digest = sha256()
total = 0
for relative, content in sorted(rows):
    encoded = relative.encode("utf-8")
    digest.update(pack(">Q", len(encoded)))
    digest.update(encoded)
    digest.update(pack(">Q", len(content)))
    digest.update(content)
    total += len(content)

print(f"files={len(rows)} bytes={total} sha256={digest.hexdigest()}")
PY
```

Before the first background tick, compute the hash, perform two full rounds of
these 11 GETs, and recompute after each round:

1. `/api/v1/planning`
2. `/api/v0/task-ops`
3. `/api/v1/collection-executions`
4. `/api/v1/planning/requests/continuous-input-001`
5. `/api/v1/planning/requests/continuous-plan-001`
6. `/api/v1/planning/requests/continuous-confirmation-001`
7. `/api/v1/planning/requests/continuous-input-002`
8. `/api/v1/planning/requests/continuous-plan-002`
9. `/api/v1/planning/requests/continuous-confirmation-002`
10. `/api/v1/collection-executions/requests/exec_req_9ec7f8a0eac9d63fe98f102e`
11. `/api/v1/collection-executions/requests/exec_req_579b8bb9dac804ea425d948c`

Every response must be HTTP 200, and request recovery must return the original
request's durable receipt. On 2026-10-01, all three hash points were identical:

```text
b38ed808ed1c976cf86c7af850f62b3360d2f54b96ae158e301aa1e2e5474efd
```

The manifest contained 12 files and 2,361,065 content bytes. Both GET rounds
completed before the first 3600-second driver tick. Response bytes themselves
may differ because wall-clock timestamps are not durable evidence.

## 12. Evidence interpretation

These milestones are intentionally separate:

- `raw_quantity` proves assignment-scoped `BallLedger` movement into robot
  `R1` and nothing later.
- `unload_quantity` proves assignment-scoped `BallLedger` movement from the
  robot to bound station `H1`.
- `runtime_evidence.conservation_passed` and `payload_parity_passed` validate the
  simulated ledger path for that execution.
- a Planning outcome is independent human evidence. The two recorded proofs
  leave `outcomes=[]`.
- the execution contract has no evidence that balls were washed, added to clean
  inventory, supplied to customers, or physically collected on a real course.

Never infer washing, replenishment, supply, clean inventory, or physical robot
performance from raw or unload quantity.

## 13. Capabilities and Console behavior

The continuous service publishes the exact
`nxt-pilot-dispatch/service-capabilities/v2` declaration with mode
`CONTINUOUS_V3_EXECUTION`:

| Operation | Capability |
|---|---|
| Planning input create | supported |
| Planning plan create | supported |
| Planning confirmation create | supported |
| Planning outcome create | supported |
| direct schedule create | unavailable |
| pending schedule cancel | supported |
| notification acknowledge | supported |
| notification resolve | supported, subject to record conditions |

Static capability never replaces dynamic health. Scheduler freshness, session
state, protection/failure state, request validation, busy state, and record-level
conditions can still close a supported control. The Console renders execution
records in service order and offers only read retry on the execution panel; it
does not admit, start, stop, or rerun an execution.

## 14. Verification record

| Verification | Result recorded for this implementation |
|---|---|
| fresh real HTTP, one process, two Planning chains | PASS on 2026-10-01; exit 0; no deviations |
| restart before second due, same durable root | PASS on 2026-10-01; all three processes exit 0 |
| two rounds of all 11 GETs | PASS; every response 200 |
| durable tree before/between/after GETs | PASS; identical 12-file digest |
| causal cross-checks and unique IDs | PASS |
| conservation / payload parity | PASS for both executions |
| locked all-extras Python suite | PASS on 2026-10-02; 3,218 passed in 1,190.82 s |
| configuration validation | PASS; 0 errors, 0 warnings |
| Python distribution | PASS; sdist and wheel built outside the repository and their package membership verified |
| Site Agent Console | PASS; 26 files / 658 tests, typecheck, production build, and HTTP smoke; lint 0 errors / 2 unchanged warnings |
| Operational Replay dependency regression | PASS; 7 files / 81 tests, typecheck, lint, production build, HTTP smoke, and the 12-scene responsive/browser fallback verification |
| production npm audit | PASS; 0 vulnerabilities in both Next applications after `next` and `eslint-config-next` were pinned to `16.3.8` |
| repository policy and verifier | PASS; 111 policy tests and 732 paths / 89 Markdown files |
| final independent reviews | PASS at `e35c984`; architecture/safety and React/contract/security/docs reviewers both APPROVED with no Critical, Important, or Minor findings; sealed local security scan reported zero findings |

The real HTTP proof and normative Python run used Python 3.13.14. Frontend
verification used local Node v25.8.2 and npm 11.11.1. These were local runs,
not CI. The initial production audit found the critical
`GHSA-vcvr-r3jv-pc5j` advisory in `next@16.3.5`; local commit `1ea8950` upgrades
both Next applications and their matching lint configuration to `16.3.8`, after
which both production audits report zero vulnerabilities. A supplemental full
audit still reports one transitive, development-only `brace-expansion` High in
each application; it is outside the production graph and was not silently
rewritten as part of the Next security patch.

## 15. Troubleshooting

**Initialization says the root is not empty.** Create a new root with `mktemp`.
Put service logs, requests, and responses in separate paths.

**The initial simulation time is later than 08:10.** The first six-second driver
tick won the race. Stop cleanly, discard the root, and restart from a new one
with request templates already prepared.

**The port is occupied.** Select another free port, update `PORT`, and record it.
Do not terminate an unknown process.

**The service cannot find Console output.** Run `npm ci` and `npm run build` in
`apps/site-agent-console`, or use `--api-only` for API-only evidence.

**A completed task did not stop the driver.** This is expected while the finite
session is still `ACTIVE`; the service remains ready for another admissible
Planning confirmation in that session.

**A due schedule was rejected while the robot was busy.** V4 preserves the Edge
owner's busy-at-due-time rejection. It does not postpone or silently requeue the
schedule. Choose non-overlapping windows.

**Service is `FAILED`, `PROTECTED`, or session is `ENDED`.** Writes fail closed.
Use GET to inspect durable evidence and the original receipts. There is no UI or
LLM path to clear protection or mutate execution state.

**`npm audit` cannot reach the registry.** Record the network/permission failure
as not run. Do not claim a passing audit.

## 16. Explicitly not implemented

- physical robots, real devices, vendor transport, ROS, actuators, or field
  deployment;
- cart cameras, divot or bunker vision, live weather, or soil sensors;
- automatic cross-session or cross-day rollover and a true 24/7 session manager;
- multiple robots, dynamic multi-zone binding, or multiple handoff-station
  selection;
- automatic delay, priority queueing, or rescheduling of busy, rejected, or
  missed tasks;
- browser or LLM execution mutation and any physical-command authority;
- automatic Planning outcomes;
- washing, supply, clean-inventory evidence, or per-task washed/supplied lineage;
- a Course Ops projection bound to this current V3 execution session;
- production publishers/sinks, real-site performance, or commercial-delivery
  proof.
