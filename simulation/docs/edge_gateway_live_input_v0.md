# Edge Gateway Live Input V0

## Architecture review decision

**Decision: RESHAPE, THEN PROCEED.**

The requested live-input slice is admissible only as a deployment composition
root under `simulation/scripts/`. MQTT, process clocks, sockets, HTTP serving,
and device-sequence bookkeeping do not enter `nxt_edge_observation`,
`nxt_site_runtime`, `nxt_agent_runtime`, or any other shipped `nxt_*` package.
The change adds no package, runtime truth model, canonical observation/state
contract, assembler, state store, decision engine, command surface, or reverse
dependency.

Review status at the gate:

- branch `feature/edge-gateway-live-input-v0` is clean and based directly on
  `origin/main@633715c1ff117e34490b09dbf23321e55a05b491`;
- Commissioning, the Edge Observation Adapter Kit, Site Runtime, Agent Runtime,
  Shadow Ops, and the Pilot Course A fixture are merged in that checkout;
- no MQTT client, `nxt.edge.load-cell.raw/v1` schema, live reader, or deployable
  Edge Gateway exists on the base branch;
- open draft PR #12 (`feature/pilot-site-agent-service-v0`) is unmerged and adds
  a fixture-backed manager service with no physical transport. This change does
  not depend on it or duplicate its manager API: the V0 endpoints here expose
  only gateway health, readiness, and noncanonical current diagnostics; and
- the similarly named Operational Replay CAD work is a read-only conceptual
  presentation and is not a runtime owner or dependency.

Rejected placements:

- `nxt_edge_observation`: owns transport-neutral raw-sample conversion and is
  mechanically guarded against MQTT, networking, wall clocks, and threads;
- `nxt_site_runtime`: owns input ordering, assembly invocation, publication
  quality, envelopes, and state-publication recovery only;
- `nxt_agent_runtime`: owns deterministic evaluation lifecycle only and is
  guarded against networking and wall-clock input;
- a new shipped `nxt_*` package or a fourth root implementation surface: V0 has
  no distinct reusable fact class that justifies changing the package DAG; and
- a new observation, facility-state, assembler, policy, state-store, or command
  contract: every one of those concerns already has an owner or is explicitly
  out of scope.

If implementation requires a reusable application package, durable transport
cursor, production publisher, multi-site service, or new cross-package
contract, work stops after design for a separate architecture decision.

## Boundary card

| Question | V0 boundary |
|---|---|
| Behavior and owner | The script-level Edge Gateway composition owns strict MQTT wire decoding, topic/routing validation, commissioned-identity checks, UTC-to-site-calendar mapping, process-local device replay tracking, broker lifecycle, and read-only gateway diagnostics. |
| Static truth | The validated `CommissionedSite` remains authoritative for `site_id`, `deployment_id`, IANA timezone, sensor binding, canonical unit, asset association, and calibration identity/validity. Gateway and MQTT device routing IDs are strict deployment config, never commissioned facts or write-back inputs. |
| Observation/evidence | `nxt_edge_observation` remains the sole raw-device-to-canonical `Observation` converter and `EdgeAdapterReport` owner. Wire UTC values and replay incidents are transport diagnostics, not facility truth. |
| Downstream state and advice | `nxt_telemetry` keeps the existing assembler and `AssemblyReport`; `FacilityState` stays unchanged; Site Runtime keeps admission/envelope/checkpoint ownership; Agent Runtime and Shadow Ops keep evaluation and human-workflow semantics. |
| Inputs | One exact `nxt.edge.load-cell.raw/v1` JSON object received on `nxt/v1/sites/{site_id}/devices/{device_id}/load-cell`, a validated deployment config, the commissioned Pilot Course A manifest, and an injected `SiteClock`. |
| Diagnostic output | Existing `LoadCellSample` and `RawSampleBatch` feed the existing adapter kit. Output is canonical `Observation` evidence plus the unmodified `EdgeAdapterReport`, an explicit statement that no complete `FacilityState` was produced, and gateway diagnostics. |
| Hybrid output | Only the canonical channel bound to the admitted MQTT `sensor_id` is overlaid as `SourceType.SENSOR`. Every other Pilot Course A observation and every upstream reference is explicitly simulation-labelled. The complete frame then enters the existing Site and Agent Runtime path with a visible hybrid disclaimer. |
| Consumers | Local stdout/JSON diagnostics, read-only HTTP clients, and—only in hybrid mode—the existing Site/Agent Runtime public APIs. No existing package imports the scripts. |
| Persistence | Device deduplication, boot epochs, pending delivery, and current gateway status are in-memory V0 diagnostics. Existing Agent Runtime JSONL evidence/checkpoints remain owned by their existing implementations. No raw-message spool, SQLite database, cloud outbox, or new state store is added. |
| Allowed imports | Composition scripts may use stdlib JSON/timezone/HTTP/concurrency modules, the optional Paho MQTT client, and public Commissioning, Edge Observation, telemetry, Site Runtime, Agent Runtime, Shadow Ops, and Pilot Course A fixture APIs. Core packages remain unaware of the scripts and MQTT dependency. |
| Forbidden reverse path | No `nxt_*` package may import the gateway scripts; transport cannot enter Edge Observation, Site Runtime, or Agent Runtime; no observation, report, state, recommendation, HTTP request, or LLM output can reach directives, robots, ROS, Nav2, actuators, register/coil writes, or e-stop mutation. |
| Version/drift | The wire schema is exactly `nxt.edge.load-cell.raw/v1`; unknown schema versions, missing or unknown fields, duplicate JSON keys, and identity disagreement fail closed. V0 has no migration or compatibility fallback. Existing canonical schemas and bytes are unchanged. |
| Replay/order | `(device_id, boot_id, device_sequence)` identifies a wire delivery. In hybrid mode, an identical redelivery matching the current unacknowledged delivery re-drives the existing immutable pending frame at the same site sequence; it does not create a second frame. After acknowledgement or terminal rejection, an identical replay is ignored as a duplicate. Conflicting reuse and unseen lower sequence values fail closed. A new boot opens a new device epoch. The independent contiguous Site Runtime sequence drives `RawSampleBatch.cycle_index` and canonical observation IDs; device sequence never does. |
| Time | Wire timestamps must be UTC and remain visible in diagnostics. `SiteClock` uses the commissioned IANA timezone to return local `operating_day_id` and civil seconds since calendar midnight; the host timezone is irrelevant. Mixed-day samples fail closed. UTC input cannot denote a nonexistent local instant; spring-forward skips that wall interval. Ambiguous fall-back folds are refused in V0 because the downstream scalar `t_s` cannot disambiguate them without hiding ordering or staleness. A hybrid runtime is bound to one operating day and refuses rollover rather than applying a new day to the old Agent Runtime midnight. |
| Missing/stale/default | `null`, device fault, calibration mismatch, and unit mismatch flow through the existing adapter as explicit `MISSING` with `value=None`; they never become zero. Stale values remain `STALE` with their value and are rejected by Site Runtime before policy evaluation. The composition never adjusts simulated inventory to force conservation. |
| Determinism | Canonical IDs depend on validated message content, explicit mapped site time, commissioned bindings, and the independent site sequence. Wall-clock calls, broker timing, HTTP requests, UUIDs, randomness, host timezone, and dictionary order do not participate. A pending frame is immutable and repeated `observe()` calls return identical content until acknowledge or reject. |
| Failure semantics | Malformed wire input fails before raw contracts; adapter failures remain named report evidence; Site Runtime rejection creates no evaluation. MQTT/process failures degrade gateway health without inventing data. A rejected runtime delivery reuses its site sequence. |
| Human/safety authority | Endpoints are GET/HEAD only and cannot mutate state, policy, calibration, workflow, robots, or e-stop. Recommendations remain advisory human-workflow evidence. No command, actuator, autonomous execution, or LLM surface exists. |

## Implemented local composition

The V0 deployment bundle is intentionally narrow:

```text
deterministic mock publisher
    -> local Mosquitto broker (MQTT 3.1.1, QoS 1, non-retained)
    -> scripts/edge_gateway_live_input_v0.py
       -> strict wire/topic/identity/time/replay admission
       -> existing LoadCellSample + RawSampleBatch
       -> existing EdgeObservationAdapterKit
       -> LOAD_CELL_DIAGNOSTIC, or
       -> HYBRID_RUNTIME_REHEARSAL -> existing Site Runtime -> Agent Runtime
    -> read-only local status and existing local evidence writers
```

The publisher is deterministic test equipment, not a physical-device emulator.
Mosquitto is anonymous and nonpersistent for a loopback-bound local smoke stack.
The script is a composition root rather than a shipped package, and no
`nxt_*` package imports it or the optional Paho dependency.

## Strict deployment configuration

The shipped example is
[`configs/edge_gateway/pilot-course-a.example.yaml`](../configs/edge_gateway/pilot-course-a.example.yaml):

```yaml
edge_gateway:
  schema: nxt-edge-gateway/config/v0
  mode: HYBRID_RUNTIME_REHEARSAL
  site_id: pilot-course-a
  deployment_id: pilot-a-edge-v0
  gateway_id: gw-pilot-a-01
  broker:
    host: mosquitto
    port: 1883
    keepalive_s: 30
    qos: 1
    client_id: gw-pilot-a-01
  devices:
    - device_id: loadcell-controller-01
      sensor_ids:
        - sensor-lc-dispenser-count
        - sensor-lc-dispenser-sensed
  status:
    host: 0.0.0.0
    port: 8080
  evidence_dir: reports/edge-gateway-v0
  fixture_cycle_index: 0
```

The wrapper and every nested mapping use exact keys. Duplicate YAML keys,
unknown fields, unsupported modes, invalid numeric types/ranges, repeated
device or sensor routes, and an undeclared fixture cycle fail closed. The
configuration site, deployment, and sensor identities are checked against the
validated Pilot Course A `CommissionedSite`; the device route and gateway ID
remain deployment configuration and are never written into commissioning.

The example uses the Compose service name `mosquitto` and binds the status
server to the container interface. Compose publishes both broker and status
ports to host loopback only. A native host run must use a separate validated
config whose broker and status hosts match that local environment; do not
rewrite commissioning or treat the Compose names as physical facts.

## The two honest modes

### `LOAD_CELL_DIAGNOSTIC`

One admitted wire message becomes the existing `LoadCellSample` and a narrow
existing `RawSampleBatch`. The existing adapter kit converts only the claimed
commissioned binding, returning canonical `Observation` values and the
unchanged `EdgeAdapterReport`. It does not synthesize silence for unrelated
equipment and does not invoke the assembler, Site Runtime, Agent Runtime, or a
decision policy. `complete_facility_state` is always `false`.

A calibration or unit mismatch, `null` value, device fault, or other adapter
failure remains an explicit `MISSING` observation with `value=null` and a named
rejection. Staleness remains `STALE` with its value. Neither condition becomes
zero inventory.

### `HYBRID_RUNTIME_REHEARSAL`

The admitted message replaces only the matching load-cell sample in the
selected Pilot Course A cycle. After existing adapter conversion, only that
matching canonical channel remains `SourceType.SENSOR`. Every other adapter
observation is relabelled `SourceType.SIMULATION` with a `synthetic.*` source
ID, and the fixture-only scanning/facility channels and upstream reference
remain explicitly simulation-sourced.

The complete hybrid `ObservationFrame` then enters the existing Site Runtime
and Agent Runtime public path. Site Runtime retains the exact existing
`FacilityState` and `AssemblyReport`; Agent Runtime retains its existing
evaluation, trace, workflow, and checkpoint semantics. A missing or stale live
channel is rejected before evaluation, creates no `NO_ACTION` or recommendation
record, and reuses the unadvanced site sequence. Every result and status payload
includes the hybrid/simulation disclaimer.

Hybrid admission is deliberately limited to the two commissioned Pilot Course
A dispenser load-cell sensor IDs. Other commissioned load cells remain valid
diagnostic inputs but cannot silently broaden this approved rehearsal into a
different live overlay.

## MQTT wire contract

The only V0 topic is:

```text
nxt/v1/sites/{site_id}/devices/{device_id}/load-cell
```

The topic must exactly match the configured and wire `site_id`/`device_id`;
wildcards, extra levels, another version, or a topic/message disagreement are
rejected. The payload schema is exactly `nxt.edge.load-cell.raw/v1`:

```json
{
  "schema": "nxt.edge.load-cell.raw/v1",
  "site_id": "pilot-course-a",
  "deployment_id": "pilot-a-edge-v0",
  "gateway_id": "gw-pilot-a-01",
  "device_id": "loadcell-controller-01",
  "sensor_id": "sensor-lc-dispenser-count",
  "boot_id": "boot-mock-001",
  "device_sequence": 0,
  "sampled_at_utc": "2026-08-08T09:29:55.000Z",
  "published_at_utc": "2026-08-08T09:30:00.000Z",
  "raw_value": 288.5,
  "raw_unit": "kg",
  "device_status": "ok",
  "calibration_id": "CAL-LC-PILOTA-2026",
  "diagnostic_code": null
}
```

All 15 fields are required; unknown or duplicate JSON keys are rejected. JSON
must be UTF-8, have an object root, and fit within the V0 65,536-byte payload
limit enforced both before JSON decoding and by the local broker packet limit.
Deeply recursive JSON and attacker-controlled diagnostic text fail within a
bounded error detail. `device_sequence` is a non-negative integer, never a
boolean or float. `raw_value` is a finite JSON number or `null`; booleans, NaN,
and infinities are rejected. `calibration_id` and `diagnostic_code` may be
`null` so the existing adapter can report explicit missing/calibration
evidence. Unknown device-status vocabulary reaches that adapter and fails
closed there rather than being normalized optimistically.

The mock publisher sends with configured QoS 1, retain disabled, and a distinct
deterministic MQTT client ID. Its default `288.5 kg` is the Pilot Course A
synthetic fixture value for 6,000 dispenser balls; it is not a measured golf
ball mass, device calibration, or product default.

The gateway also verifies the broker-delivered metadata: QoS 0 and retained
PUBLISH packets are rejected before wire processing. This is required because
the V0 pending-frame redelivery contract depends on non-retained QoS 1 and a
withheld application PUBACK.

The gateway uses its stable configured client ID, an MQTT 3.1.1 persistent
session (`clean_session=false`), and application-managed QoS 1 acknowledgement.
It sends PUBACK only after the delivery reaches a terminal gateway outcome,
including an accepted result, an already-completed duplicate, or a strict
delivery rejection. If Agent Runtime retains a hybrid frame for retry or PUBACK
itself fails, the gateway leaves the delivery unacknowledged and reconnects the
same persistent session in the same process after a fixed one-second process
retry backoff. That preserves the in-memory source/site cursor while asking the
broker to redeliver the exact QoS 1 packet; the backoff never enters canonical
time or identity. A bounded smoke that reaches its message limit before
readiness instead exits nonzero. A fail-closed runtime incident also remains
unacknowledged and stops the gateway for repair. These behaviors permit the
running broker session to retain a delivery; they are not a durable replay
guarantee. The process-local source cursor and pending frame reset on process
restart, and the local Compose broker is itself nonpersistent.

## Clock and ordering contracts

Wire timestamps must carry UTC as `Z` or `+00:00`, and
`published_at_utc >= sampled_at_utc`. `SiteClock` uses the commissioned IANA
timezone rather than host timezone and maps each timestamp to:

```text
operating_day_id = local YYYY-MM-DD
sample_timestamp_s / available_timestamp_s = civil seconds since local midnight
```

For the shipped mock message in `Asia/Shanghai`, the mapping is operating day
`2026-08-08`, sample time `62995.0`, and available/frame time `63000.0`. Unix
epoch seconds never enter the canonical frame. A delivery crossing local
midnight is refused. Spring-forward follows the real IANA jump, while an
ambiguous fall-back fold is refused because the downstream scalar time cannot
represent its ambiguity. Hybrid V0 binds one Agent Runtime to one operating day
and refuses rollover.

Device delivery order and site publication order are separate:

- `(device_id, boot_id, device_sequence)` identifies a device delivery;
- an identical redelivery matching the current unacknowledged hybrid delivery
  re-drives that existing immutable pending frame at the same site sequence;
- after acknowledgement or terminal rejection, an identical replay is an
  idempotent duplicate and produces no new frame;
- conflicting reuse of a seen sequence fails closed;
- an unseen lower sequence in the active boot fails closed;
- a new boot begins a new device epoch, and an old retired boot is refused; and
- this tracker is process-local V0 state and resets on restart. It retains at
  most 4,096 sequence digests per boot and 64 retired boot IDs per configured
  device. An evicted old sequence remains below the high-water mark and fails
  closed as out of order; excess boot churn fails closed rather than silently
  forgetting retired identities.

The hybrid source independently assigns contiguous site sequences starting at
zero. Only acknowledgement advances that sequence. A terminally rejected
device delivery is discarded while the site sequence is reused. A device boot
change never resets or chooses Site Runtime ordering.

## Read-only status endpoints

The local HTTP surface exposes only GET and HEAD:

| Endpoint | Result |
|---|---|
| `/healthz` | HTTP 200 process/status snapshot; Compose additionally requires `broker_connected=true` before starting the publisher. |
| `/readyz` | HTTP 200 only when mode-specific readiness is true, otherwise 503. |
| `/api/v0/status` | HTTP 200 current noncanonical diagnostics using `nxt-edge-gateway/status/v0`. |

Every snapshot exposes `broker_connected`, `sensor_seen`, `adapter_healthy`,
`runtime_ready`, `ready`, current transport/runtime diagnostics, the last
failure, identity, mode, and disclaimer. Diagnostic readiness requires a broker
connection and a trustworthy target adapter result. Hybrid readiness also
requires an admitted/evaluated runtime frame. POST, PUT, PATCH, and DELETE
return 405; unknown paths return 404. Reads never mutate state, calibration,
policy, workflow, robot, or e-stop behavior.

## Evidence and restart limits

Gateway stdout emits JSON lines for broker connection, message results, typed
rejections, and terminal service failure. Message results keep the canonical
observations and `EdgeAdapterReport` separate; the report never enters a
facility snapshot.

In hybrid mode, the existing Agent Runtime writers use:

```text
reports/edge-gateway-v0/{site_id}/{deployment_id}/
├── snapshots.jsonl
├── ledger.jsonl
├── evaluations.jsonl
└── checkpoints/
    ├── site/
    └── evaluation/
```

Those files retain their existing owners and meanings: snapshot envelopes,
Shadow Ops workflow evidence, runtime evaluation evidence, and two separate
progress checkpoints. They are not a new facility state store and never feed
the live loop. The Compose bundle mounts no evidence volume, so its evidence is
ephemeral with the container. Diagnostic mode writes no facility snapshot or
policy evidence.

Raw MQTT messages, device deduplication, boot epochs, current status, and the
pending hybrid delivery are in memory only. Restart-safe raw spooling and
resumption are not implemented; V0 therefore makes no durable at-least-once
claim across a gateway or broker process restart. The persistent MQTT session
and withheld PUBACK above support bounded same-process retry and a fail-stop
retention posture; they are not a replacement for the deferred durable
cursor/spool.

## Exact local commands

From `simulation/`, install only the required transport and runtime extras:

```bash
uv sync --locked --extra edge-gateway --extra range-ops
```

Validate the strict config without importing Paho or opening a connection, and
render the deterministic message without publishing it:

```bash
uv run --no-sync python -B scripts/edge_gateway_live_input_v0.py \
  --config configs/edge_gateway/pilot-course-a.example.yaml \
  --check-config
uv run --no-sync python -B scripts/mock_edge_load_cell_publisher.py \
  --config configs/edge_gateway/pilot-course-a.example.yaml \
  --dry-run
```

The committed example addresses the Compose service name `mosquitto`; use the
Compose flow below for the actual local broker smoke test.

## Docker Compose smoke test

The bundle uses Python 3.13.14, uv 0.11.29, a pinned Mosquitto image, exactly
three services, and loopback-only host port publication:

```bash
docker compose -f deploy/edge-gateway-v0/compose.yaml config
docker compose -f deploy/edge-gateway-v0/compose.yaml up --build -d
docker compose -f deploy/edge-gateway-v0/compose.yaml wait publisher
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
curl --fail --silent --show-error http://127.0.0.1:8080/readyz
curl --fail --silent --show-error http://127.0.0.1:8080/api/v0/status
docker compose -f deploy/edge-gateway-v0/compose.yaml logs --no-color \
  gateway publisher
docker compose -f deploy/edge-gateway-v0/compose.yaml down \
  --volumes --remove-orphans
```

The dependency order is broker healthy → gateway connected to the broker →
publisher. Gateway health intentionally checks `/healthz` plus
`broker_connected=true`; it does not wait on `/readyz`, because the publisher's
first message is what makes a valid hybrid runtime ready.

### Native broker smoke used for synchronized verification

The synchronized diagnostic and hybrid results below were observed with native
Mosquitto 2.1.2, Paho 2.1.0, the committed broker configuration, the actual
gateway and publisher CLIs, and temporary config/evidence paths. This is the
exact command run from `simulation/`; it leaves the temporary evidence outside
the repository and proves port 1883 is released at the end:

```bash
set -euo pipefail
native_smoke_tmp=$(mktemp -d)
mosquitto -c deploy/edge-gateway-v0/mosquitto.conf \
  >"$native_smoke_tmp/mosquitto.log" 2>&1 &
broker_pid=$!
gateway_pid=
cleanup_native_smoke() {
  if test -n "$gateway_pid" && kill -0 "$gateway_pid" 2>/dev/null; then
    kill "$gateway_pid" 2>/dev/null || true
    wait "$gateway_pid" 2>/dev/null || true
  fi
  if kill -0 "$broker_pid" 2>/dev/null; then
    kill "$broker_pid" 2>/dev/null || true
    wait "$broker_pid" 2>/dev/null || true
  fi
}
trap cleanup_native_smoke EXIT
for attempt in $(seq 1 100); do
  if nc -z 127.0.0.1 1883; then break; fi
  kill -0 "$broker_pid"
  sleep 0.05
done
nc -z 127.0.0.1 1883

run_native_mode() {
  mode=$1
  config_path="$native_smoke_tmp/$mode.yaml"
  gateway_log="$native_smoke_tmp/$mode.gateway.jsonl"
  cp configs/edge_gateway/pilot-course-a.example.yaml "$config_path"
  sed -i.bak \
    -e "s/mode: HYBRID_RUNTIME_REHEARSAL/mode: $mode/" \
    -e 's/host: mosquitto/host: 127.0.0.1/' \
    -e 's/host: 0.0.0.0/host: 127.0.0.1/' \
    -e 's/port: 8080/port: 0/' \
    -e "s#evidence_dir: .*#evidence_dir: $native_smoke_tmp/$mode-evidence#" \
    "$config_path"
  uv run --no-sync python -B scripts/edge_gateway_live_input_v0.py \
    --config "$config_path" --max-messages 1 >"$gateway_log" 2>&1 &
  gateway_pid=$!
  for attempt in $(seq 1 200); do
    if rg -q '"event":"mqtt_connected"' "$gateway_log"; then break; fi
    kill -0 "$gateway_pid"
    sleep 0.05
  done
  rg -q '"event":"mqtt_connected"' "$gateway_log"
  uv run --no-sync python -B scripts/mock_edge_load_cell_publisher.py \
    --config "$config_path" --timeout-s 5
  wait "$gateway_pid"
  gateway_pid=
  uv run --no-sync python -B - "$mode" "$gateway_log" <<'PY'
import json
import sys
from pathlib import Path

mode, path = sys.argv[1], Path(sys.argv[2])
records = [json.loads(line) for line in path.read_text().splitlines() if line]
result = next(item for item in records if item.get("event") == "message_result")
assert result["kind"] == "accepted"
if mode == "LOAD_CELL_DIAGNOSTIC":
    assert result["complete_facility_state"] is False
    assert len(result["observations"]) == 1
    assert result["observations"][0]["channel"] == "inventory.dispenser.count"
    assert result["adapter_report"]["rejected"] == []
    summary = {
        "mode": mode,
        "channel": result["observations"][0]["channel"],
        "adapter_rejections": 0,
        "complete_facility_state": False,
    }
else:
    live = [
        item for item in result["observations"]
        if item["source_type"] == "sensor"
    ]
    simulated = [
        item for item in result["observations"]
        if item["source_type"] == "simulation"
    ]
    assert len(live) == 1 and len(simulated) == 29
    assert result["complete_facility_state"] is True
    assert result["runtime_outcome"]["acknowledged"] is True
    summary = {
        "mode": mode,
        "live_channels": [item["channel"] for item in live],
        "simulation_channels": len(simulated),
        "runtime_kind": result["runtime_outcome"]["kind"],
        "runtime_acknowledged": True,
        "complete_facility_state": True,
    }
print(json.dumps(summary, sort_keys=True))
PY
}

run_native_mode LOAD_CELL_DIAGNOSTIC
run_native_mode HYBRID_RUNTIME_REHEARSAL
cleanup_native_smoke
trap - EXIT
! nc -z 127.0.0.1 1883
printf '%s\n' "$native_smoke_tmp"
```

The terminal summaries were:

```text
{"adapter_rejections": 0, "channel": "inventory.dispenser.count", "complete_facility_state": false, "mode": "LOAD_CELL_DIAGNOSTIC"}
{"complete_facility_state": true, "live_channels": ["inventory.dispenser.count"], "mode": "HYBRID_RUNTIME_REHEARSAL", "runtime_acknowledged": true, "runtime_kind": "evaluated", "simulation_channels": 29}
```

## Acceptance contract

Acceptance evidence must cover:

1. exact wire/schema/identity/numeric/timestamp validation, including duplicate
   JSON keys and explicit unknown-field behavior;
2. terminal QoS 1 acknowledgement versus withheld acknowledgement for a
   retained retry-required frame, identical replay/redrive, conflicting replay,
   lower sequence, boot epoch, and independent contiguous Site Runtime
   sequencing;
3. site-local midnight, host-timezone independence, mixed operating days, and
   spring-forward/fall-back behavior;
4. existing adapter calibration, unit, missing, fault, stale, non-finite, and
   duplicate-channel behavior, including proof that missing is never zero;
5. one-channel live overlay, all-other-channel simulation labelling, visible
   hybrid disclaimer, exact state/report retention, stable IDs, and proof that
   rejected input creates no policy evidence;
6. broker/sensor/adapter/runtime health dimensions, readiness before and after
   valid evidence, and side-effect-free status endpoints;
7. architecture guards proving MQTT is optional and script-confined, no shipped
   package or canonical contract was added, core packages remain transport-free,
   and no execution/LLM/persistence/cloud/OTA surface appeared; and
8. focused and relevant package suites, architecture/safety suite, full Python
   suite, config validation, package metadata/build, Docker Compose validation,
   and a real broker smoke test, with exact observed results.

## Claims boundary

`LOAD_CELL_DIAGNOSTIC` demonstrates a real MQTT-to-canonical-observation path
against a local broker. It does not demonstrate complete facility state or a
deployed customer integration.

`HYBRID_RUNTIME_REHEARSAL` demonstrates the existing Site/Agent Runtime path
with one transport-backed load-cell channel and explicitly simulated remaining
inputs. It is not fully live state, production delivery, cloud sync, physical
command admission, robot execution, or safety integration.

The Compose path demonstrates a local mock publisher and broker. It is not a
customer device, physical load cell, field installation, production MQTT
service, validated sensor-accuracy result, or operational uptime claim.

## Non-goals

V0 deliberately does not add:

- a shipped gateway package, second observation/state/assembler/decision model,
  or command surface;
- SQLite, a raw-message spool, durable transport cursor, cloud sync/outbox,
  multi-site control plane, or OTA update path;
- a physical device/vendor integration, Modbus, serial, OPC-UA, camera, POS,
  weather, scanning-rig, or production state publisher;
- ROS 2, Nav2, robot dispatch, register/coil writes, actuators, motion, charging,
  e-stop set/clear/reset, autonomous execution, or hardware acknowledgement; or
- an LLM or generative agent anywhere in transport, admission, policy,
  execution, actuator, or safety loops.

## Verification commands and results

The post-synchronization run against the current Course World Model mainline
used the lock-consistent all-extras environment and observed these exact results
from `simulation/`:

```text
uv sync --locked --all-extras
Resolved 61 packages; rebuilt and installed the local nxt-sim project

uv run --no-sync python -B -m pytest -o addopts='' -q \
  -p no:cacheprovider tests/course_world_model \
  tests/workflow_enablement tests/edge_gateway_live_input
570 passed in 18.08s

uv run --no-sync python -B -m pytest -o addopts='' -q \
  -p no:cacheprovider tests/commissioning tests/site_runtime \
  tests/agent_runtime tests/edge_observation tests/course_world_model \
  tests/workflow_enablement tests/edge_gateway_live_input
1082 passed in 22.91s

# Exact architecture/safety selection from .agent/workflows/testing.md
185 passed in 7.26s

uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider
1650 passed in 43.08s

uv run --no-sync python -B scripts/validate_configs.py
0 errors, 0 warnings; all eight listed config files passed

uv lock --check
Resolved 61 packages

uv pip check
Checked 57 packages; all compatible
```

The CI-equivalent compile command completed with no output. `uv build` produced
one wheel and one source distribution in an isolated temporary directory, and
the repository distribution verifier passed all 13 declared shipped packages,
including `nxt_course_world_model`. The gateway scripts were present in the
388-member source distribution and absent from the 132-member wheel, preserving
their non-shipped composition-root status; the isolated wheel imported all 13
packages and excluded repository-only packages and `scripts`.

At repository root, the CI-policy helper suite passed all 96 tests and the
repository verifier passed 508 tracked/nonignored paths and 64 Markdown files,
including links, anchors, fences, secrets, generated artifacts, and dependency
boundaries.

Standalone Compose 5.5.0 validated the deployment document:

```text
docker-compose -f deploy/edge-gateway-v0/compose.yaml config --quiet
exit 0, no output
```

A native Mosquitto 2.1.2 process using the committed broker config was then
exercised with real Paho 2.1.0 connections and the actual mock publisher. The
diagnostic smoke observed one non-retained QoS 1 message and PUBACK, one
`inventory.dispenser.count` observation with `status=ok`, one accepted
`nxt-edge-observation/adapter-report/v0` report with zero rejections, and
`complete_facility_state=false`. The hybrid smoke independently observed one
live `inventory.dispenser.count` SENSOR channel, 29 explicitly SIMULATION
channels, `complete_facility_state=true`, and an acknowledged `evaluated`
runtime result. Deferred same-process redelivery and retained-message rejection
remain covered by the focused transport regressions; no restart-durability claim
is made.

The local host still had no Docker Engine CLI/daemon, so it did not run the
three-container flow. The dedicated
[`edge-gateway-compose-smoke` job](https://github.com/matthewong1210/nxtektal-systems/actions/runs/33542983779/job/99973552967)
then closed that execution-evidence gap for implementation commit
[`48e839b5d1f89704d2dfa5e8fe6873dc31e26fbe`](https://github.com/matthewong1210/nxtektal-systems/commit/48e839b5d1f89704d2dfa5e8fe6873dc31e26fbe).
GitHub reported the job passed in 57 seconds on an ephemeral Ubuntu 24.04
runner. It rendered the committed Compose model, built and started Mosquitto,
Gateway, and Publisher, and observed the one-shot Publisher in `exited` state
with exit code `0`.

The same job parsed `/healthz`, `/readyz`, and `/api/v0/status`. All three
reported `pilot-course-a` / `pilot-a-edge-v0`,
`HYBRID_RUNTIME_REHEARSAL`, `broker_connected=true`, `sensor_seen=true`,
`adapter_healthy=true`, `runtime_ready=true`, `ready=true`, no last failure,
and the explicit HYBRID/SIMULATION/NOT LIVE CUSTOMER DATA disclaimer. Parsed
Gateway JSONL contained exactly one accepted result: one mock-MQTT-derived
`inventory.dispenser.count` channel labelled `SourceType.SENSOR`, 29
`SourceType.SIMULATION` channels,
`complete_facility_state=true`, and an acknowledged `evaluated` runtime
outcome. `complete_facility_state=true` means complete assembly of this
explicitly hybrid fixture, not a fully sensed physical state. The always-run
evidence step showed Gateway and Mosquitto healthy and Publisher `Exited (0)`;
teardown then removed all three containers and the Compose network.

This CI result exercises only the committed anonymous, nonpersistent local
Mosquitto stack and deterministic mock publisher. It is not customer-broker,
physical-load-cell, sensor-accuracy, persistence, production-deployment,
availability, or uptime evidence. The Compose configuration is hybrid-only;
the separate native broker smoke above remains the diagnostic-mode execution
evidence.
