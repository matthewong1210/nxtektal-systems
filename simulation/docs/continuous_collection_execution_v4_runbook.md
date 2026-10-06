# Continuous Collection Execution V4 Runbook

Status: **IMPLEMENTED AND LOCALLY VERIFIED — UNMERGED**

This runbook operates the continuous V4 composition service in deterministic
`SIMULATION` mode. V4 accepts more than one confirmed Planning plan during one
finite V3 session and runs the resulting collection tasks sequentially. It does
not replace the fixed single-task 3C service documented in
[the V3 runbook](collection_execution_v3_runbook.md).

The original live-service proof was recorded on 2026-10-01. The frozen
witnesses and source-derived identities below were refreshed on 2026-10-05
using the merged engine and real Manager API callbacks, and again on
2026-10-06 through the same service flows when the `nxt_operational_context`
leaf was added and `nxt_site_agent` gained the Supervisor Snapshot seam (the
engine fingerprint hashes every `nxt_*` Python file). Task states, action
order, timestamps, and ledger-backed 600/600 and 296/296 quantities are unchanged.
The original live-service purity hash in §11 remains a dated historical record.
The branch remains local and unmerged.

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

## 8. Uninterrupted proof, refreshed on 2026-10-05

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
| engine digest | `59b290daec0f52d6bd209d056a6e259e1999213639b9a20fd1c80d225a018a31` |
| final replay digest | `7854b8c3b63c1edb051f9f741ad64545542061d361eabf5a93db125aa1272202` |

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
| binding | `e73127a1beb85093b5796e30380172331fa97bdad095c0146b7b3815836f0ff6` | `8b1342ebbfaf14bb6e9a593c8b07b65152b02ac1134dc4542f37cc2e95d6198f` |
| execution request | `exec_req_d9868c8b13cfde62e6cd9be1` | `exec_req_84f2819cd9ee88f33997db9c` |
| execution | `2fc7a0fed1da1f1d8f5a775354fb6b3afebffc9f6a302173147035a3dca139d5` | `1d0a38608fd4cd16775795c530869374a8a35135bb2d063a7e94c5b63a0cabfd` |
| attempt | `attempt-2fc7a0fed1da1f1d8f5a775354fb6b3afebffc9f6a302173147035a3dca139d5` | `attempt-1d0a38608fd4cd16775795c530869374a8a35135bb2d063a7e94c5b63a0cabfd` |
| assignment | `assignment-02ff5a9d7b8124a3e6c74ba31174b6ca969f116416c4ce56137be1a71891c96d` | `assignment-1ab55d0414efacad77a63d598aeb4ccf9c1d3b0262e8c08760b35eca977de51f` |

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
| event digest | `cc13626fc1a38c34e0b504055d80daaaae2b5e34d1615d1a67e44669df75b552` | `2575ae5008072085ac4237717cf005e6055c9d0819cd47ea6d47128f281351cb` |
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
assignment-event-a1f2589b02355412e4674a6622f3d81bb1f1bfbeec76bfcd0eb75bbb64191183
assignment-event-ff8ad1b9e4ef2af475b5c67fd295746b941dcd6b116758e842514f19e83fb2f4
assignment-event-3ff9f3b93a5f4a936609e0bdc9ba6093485c326f327db8b3a0105c5491e4d1ba
assignment-event-0121c6c7097a226f13761d775712b30e497d55168008f39553ca13c1e946985a
assignment-event-4520301d25cf1a424b97c9c7b29508e28e368672e525f5bee3a21d6e964c022b
assignment-event-cc64eb673bf4085eb3290b60ddeb8851c8885944c4d0f8664ccdb39f4e2b0d2e
assignment-event-8792d0a9f185269a4e5d6f7ab55e21f69aa067ea4c7b54afc4a07f5fa36f7da9
assignment-event-16cbd547cd778cb18e6fde110c1a693871f6bf11c5777b17acbdcb4c7e77a23c
assignment-event-48897a13bc8f4612843b8073c4a647ec266c071a360113c37d365a36b202d153
assignment-event-c208045b42b95f33f0cbaad9e53b043cb19ffb843dd3f0b940aa1810999f754f
assignment-event-95601ae1a0d70ba2966555a0e4060f56d527938ce99b28e9aea10aa8040d3a3e
assignment-event-d96e3dbfc264d1805e626aa84b997dcc85421106a482795919fbe70154922949
assignment-event-5415e12ac6687532db9c05064c1b4215e995f71a8720afff3c1ff8a614ac6dae
assignment-event-7fd93380ececbb1fc026812b885f3f3fb1444f43b22f49b1b36d6102706d1478
assignment-event-69213619f2d8613cc75190ab24bdbd5c806c00b3585341f0b1322a88cfb75476
```

Chain 1 unload source event:

```text
assignment-event-e6f11783f2a90f5a52374ab248375eb68e034214ca72bab25e7e9ae3ade95ae4
```

Chain 2 raw source events:

```text
assignment-event-7e3581536895ea8e843520faf0e8ef50bf53433d852acb51d7939e13f8b4263f
assignment-event-09a648f268a9d202c5cc1467edad52f0590da9fa1b235d88392d4aa3ac4f3f8c
assignment-event-bebc80a28aaed2be2b6f7b6696b4ad95933b10265e662db937f0c7f094416331
assignment-event-a1c07a2393af7bd2083781af8acd84a0c9a27cf3f7ae1a68e88d11bc44451d33
assignment-event-5a3b0b29ce85e2583a0816658ce488830392fdd547c972106ae41aaae485485b
assignment-event-bade50ec57c972513f99b328b5dcf42bc06b57748c1e13fb65c499b520bbae37
assignment-event-818a703bf0af8545e0a5e8a8a8c74087a7131b03ce1f7134229062b6abfca3b6
assignment-event-19197ba25baf5235196747268140d9a6c8a3d9272e13c75fd950ee4c59fd045f
```

Chain 2 unload source event:

```text
assignment-event-27552606544aa5cdf69eb0a22fed2bdabb5ddca9c5caf848b58135968bc34a7c
```

The verified Edge record IDs were:

| Chain | Edge records |
|---|---|
| 1 | `rec_4acb37c0e660cc0357314073`, `rec_7c9a3dfc4bdaa73161f08b11`, `rec_83ef63f96780f7a3e6f68469`, `rec_9031c454a7111e27ee68b6ca`, `rec_db2a6de47871c9bc918c06df`, `rec_efa584dbbbbacfe0286c5bd2` |
| 2 | `rec_194a83e296a26046396edbe6`, `rec_44c2d0f01aa2dcde0bf504cf`, `rec_3b0f637f850ee67b3d23968d`, `rec_570d7451ad690975c69062fe`, `rec_d2d6d1d4bf7d5c6beafe122f`, `rec_de9a3e38f2dcba667c8fd3b1` |

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

The original live restart proof used a second empty root and port `8775`.
The replay digests below were refreshed through the owner runtime and Manager
API callbacks on 2026-10-05:

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
| initial | 08:10 | chain 1 accepted | `0c9090a0b20de6a51ab8b0680f07c24c944aa69b806de50b184541f6382652ef` |
| initial | 08:20 | chain 1 RUNNING, 600 raw / 0 unload; chain 2 then submitted | `84680b3d3dd2e2c29e7f952296b09331c12956ff0f712fa19f76c624daaa55ac` |
| initial | 08:30 | chain 1 SUCCEEDED 600/600; chain 2 not due | `04bc0da286caf67479517fd3d8f4b7e553bedde5e6ef2a216e0044277b3d9b9e` |
| immediate restart | 08:30 | identical durable state before advancement | `04bc0da286caf67479517fd3d8f4b7e553bedde5e6ef2a216e0044277b3d9b9e` |
| restart | 08:40 | chain 2 due; execution not yet started | `7a99f5c26bc46bc6fc98f8f90d834ebe5fe1c1b28e3ab1c2fc6e0bb1f6267dcc` |
| restart | 08:50 | chain 2 RUNNING, 296 raw / 0 unload | `b02e5830219e7e3e9c5a605130ca8fbe30235cca370db76b1edcb44d65ff7d93` |
| restart | 09:00 | chain 2 PARTIAL, 296/296 | `72f68cd3bd8be42b6d8f596b25c5730cc4f29fde515fb2c7f87a075741ffcddd` |

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
10. `/api/v1/collection-executions/requests/exec_req_84f2819cd9ee88f33997db9c`
11. `/api/v1/collection-executions/requests/exec_req_d9868c8b13cfde62e6cd9be1`

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
