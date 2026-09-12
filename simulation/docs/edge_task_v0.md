# Edge Task Exchange V0 (PR A)

SIMULATION ONLY — a protocol rehearsal between an Edge gateway process and
two protocol doubles over a local, loopback-bound Mosquitto. No physical
robot, sensor, ROS, actuator, or emergency-stop path exists or is reachable
from this code. Human-intervention cases and notifications are PR B and are
not part of this slice.

## Architecture decision (recorded gate outcome)

The repository's architecture table records that physical site-level task
admission/translation has **no implemented owner** and must pause for
design/approval. That row is unchanged. This slice adds a new, approved
leaf package for a different fact class: the **simulated** task-exchange
rehearsal. The user approved the owner after the Phase A review; the gate
outcome is **Proceed** for `nxt_edge_task` with the boundaries below.

Rejected owners (recorded during Phase A): `nxt_sim` (the micro handoff
vocabulary is not a site-level dispatch API); `nxt_range_ops` (directives
only enter the simulator, and this rehearsal simulates no physical world);
`nxt_pilot_ops` (its human workflow is keyed by recommendations and its
ledger event set is closed); `nxt_facility.decisions` (pure advice over
`FacilityState`, no event source, no case lifecycle); `nxt_edge_observation`
(transport-free conversion; the robot-status message is a future seam into
it, not a replacement); `nxt_site_runtime`/`nxt_agent_runtime` (orchestration
and evaluation only, guarded against networking); scripts-only (two
independent processes need one versioned, guarded, importable codec).

The rehearsal is intentional divergence from both advisory owners, not a
third decision engine: it never reads `FacilityState`, emits no
`Recommendation`, applies no battery threshold, and records only what a
double reported or what the Edge observed on the transport. Tasks come from
an explicitly SIMULATION-labelled test entry, never from advice or manager
acceptance.

## Boundary card

| Question | V0 boundary |
|---|---|
| Owner | `nxt_edge_task` (stdlib-only leaf; imports no other `nxt_*` package) |
| Fact class | Simulated task requests, received robot status and task events, the Edge task/device derivation, and the protocol double's own decisions/executions — append-only rehearsal evidence, never truth about a physical act |
| Inputs | `AdmissionFacts` (site/deployment/robot/zone ids + manifest digest projected by `scripts/pilot_course_a_task_fixture.py` from the validated Pilot Course A manifest), the strict JSON config, received wire messages, and an injected clock |
| Outputs | Records in the Edge journal (`nxt-edge-task/journal/v1`) and the robot journal; `task.request` publications; robot status/event publications |
| Consumers | The Edge gateway, the mock robot, the CLI, and tests; no existing package imports it |
| Persistence | One append-only JSONL journal per writer role with contiguous sequence, content-derived `record_id`, canonical bytes, LOCK_EX verify-before-append, fsync + directory fsync, and fail-loud integrity on read |
| Forbidden reverse path | No `nxt_*` package may import `nxt_edge_task`; the package must not import transport, network, subprocess, threading, wall-clock, UUID, or randomness modules; no message can pause, resume, reset, or clear a device |
| Versioning | `nxt.edge.robot-status/v1`, `nxt.edge.task.request/v1`, `nxt.edge.task.event/v1`, `nxt-edge-task/config/v1`, `nxt-edge-task/journal/v1`; unknown versions, unknown keys, duplicate keys fail closed; no migration exists |
| Environment | `environment.kind` is the single value `SIMULATION`; the config carries only `simulation_env_id`; a different kind is a new protocol version that must pass the architecture gate |
| Determinism | No wall clock, UUID, or randomness inside the package; every timestamp is supplied by the caller; identities are content-derived |
| Safety | Robot local protection is script-driven and never waits for the Edge, a notification, or a human reply; lost contact is not proof of a parked robot; a paused task is not proof that a load is released; nothing here is validated physical behaviour |

## Processes and storage (write authority)

| Process | Writes (only) | Reads | Never |
|---|---|---|---|
| `scripts/edge_task_gateway_v0.py` | `<evidence>/edge/<site>/<deployment>/edge_task_journal.jsonl` + `.edge.lock` (origin `EDGE` and robot facts *as received*) | its own journal | a robot or receiver directory; any robot internal state |
| `scripts/edge_task_cli.py` | the same Edge journal, origin `SIM_ENTRY` only (`create-task`), under the same lock and admission rules | the Edge journal (`list`, `show`) | device/task state fields; robot directories |
| `scripts/mock_robot_task_device.py --robot-id picker-01` | `<evidence>/robots/picker-01/robot_task_journal.jsonl` | its own journal | the Edge or carrier directory |
| `scripts/mock_robot_task_device.py --robot-id carrier-01` | `<evidence>/robots/carrier-01/robot_task_journal.jsonl` | its own journal | the Edge or picker directory |

The Edge learns about a robot only through `task.event` and `robot-status`
messages received on the transport. Tests read the robot journal to assert
execution counts; the Edge never does.

## Wire contracts

All fields are required; unknown or duplicate keys are rejected; payloads
are bounded to 65,536 bytes.

- `robot-status/v1` (robot → Edge, QoS 0, never retained): identity,
  `boot_id`, `boot_sequence` (monotonic, persisted by the robot),
  `status_sequence`, `availability` (the Shadow Ops `RobotStatus` literals
  minus `offline`), `current_task`, declared `capabilities.task_types`
  (evidence only; the Edge never uses it for admission), `energy`
  (`level_fraction`, `can_continue`, `needs_manual_recharge`), `safety`
  (`estop_latched`, `awaiting_human`, `safe_return_confirmed`), `fault_code`,
  `location` (`zone_id`, `x_m`, `y_m`, `coordinate_frame`). `null` means
  unknown and is never filled with a healthy default. The mock emits
  `level_fraction`, `x_m`, `y_m` as `null`: it has no battery model or
  position.
- `task.request/v1` (Edge → robot, QoS 1, never retained): identity,
  `task_id`, `target_robot_id`, `task_type` (`COLLECT_BALLS_ZONE`),
  `parameters.zone_id`, `issued_at_utc`, `expires_at_utc`,
  `progress_window_s`, `issued_by` (`SIMULATION_TEST_ENTRY:<operator>`).
  `task_id = "task_" + sha256(all other fields)[:24]`; both ends verify it.
- `task.event/v1` (robot → Edge, QoS 1, never retained): identity,
  `task_id`, `robot_id`, `boot_id`, `boot_sequence`, `event_sequence`
  (per boot and task, from 1; `0` is reserved for request-level rejections
  that never touch task history), `kind` (`ACCEPTED`, `REJECTED`,
  `PROGRESS`, `ASSISTANCE_REQUIRED`, `SUCCEEDED`, `FAILED`,
  `INCONCLUSIVE`), `reason_code` (closed vocabulary; unknown codes preserved
  as `unknown:<token>`), `detail`, `reported_at_utc`, `progress`.

Topics: `nxt/v1/sites/{site_id}/robots/{robot_id}/status`,
`.../task/request`, `.../task/event`. The Edge → robot vocabulary is exactly
`task.request`; there is no pause, resume, cancel, reset, or e-stop message.

## Acknowledgement semantics

| Confirmation | Hop / client | Evidence | Gated by journal fsync? |
|---|---|---|---|
| Transport hop-1 | publisher → broker PUBACK (`is_published()` while pumping the network loop) | `task_publish_attempted` (persisted before the call) then `task_publish_confirmed` / `event_publish_confirmed` | No; broker-controlled; proves only that the broker took the packet. "Unconfirmed" means not confirmed: a copy may still be in flight and is deduplicated by the receiver; a publish while the socket is known down is refused locally and never queued behind the journal's back |
| Transport hop-2 | broker → subscriber manual PUBACK (`manual_ack_set(True)` + `ack(mid, qos)`) | implied by the subscriber's journal append | **Yes**: sent only after append + file fsync + directory fsync; an append failure means no ack, disconnect, fail-stop |
| Status heartbeat | QoS 0, no PUBACK | `device_status_changed` / `device_heartbeat` | n/a; received means live |
| Robot acceptance | `task.event ACCEPTED` (seq ≥ 1) | `task_event_received` | task-level, not transport |
| Task result | terminal `task.event` | `task_event_received` | task-level |

QoS 1 is at-least-once delivery to the broker. It proves neither robot
receipt, acceptance, nor execution, and it is not exactly-once: two-sided
dedup on `task_id` and `(boot_sequence, event_sequence)` is the only
execution-idempotency mechanism.

## Edge task lifecycle

States: `CREATED → ACCEPTED → RUNNING → {SUCCEEDED | FAILED | INCONCLUSIVE}`,
`CREATED → REJECTED`, any non-terminal `→ BLOCKED_AWAITING_HUMAN`
(`ASSISTANCE_REQUIRED`), `BLOCKED_AWAITING_HUMAN → RUNNING` (`PROGRESS`).
Terminals absorb; the displayed state is the first terminal the Edge
accepted.

Ordering key per task: `(boot_sequence, event_sequence)`. Above the applied
maximum → applied; below → `late_evidence` (fills `missing_sequences`,
never regresses state); same key, same content → `duplicate`; same key,
different content → `conflicting_replay`. `ACCEPTED` or `REJECTED` after
progress → `unexpected_acceptance` / `unexpected_rejection` evidence plus a
sticky flag, no transition. `acceptance_observed` counts only an applied or
genuinely late `ACCEPTED`, never a post-terminal or unexpected one. Every
`task_event_received` record carries `missing_sequences_after` and
`acceptance_observed_after` so the gap is visible at the moment of application.

Terminal conflict: a second, different terminal is preserved as evidence,
a `conflicting_terminal` record is written once, `effective_result` becomes
`CONFLICT` with `result_verification = conflicting`, and the device's
authorization gate closes: no new task for that robot and no republication
until PR B's human handling. The conflict is detected in every arrival
order — a differing terminal that arrived earlier as late or unexpected
evidence conflicts with the terminal applied later — and the
`task_event_received` line that carries `conflict: true` closes the gate by
itself, so a torn batch (evidence line persisted, companions not) still
derives the gate after a restart. Prior release and dispatch facts are
never rewritten.

Session ordering: a live status with a lower `boot_sequence` than the one
already seen is a `session_regression` (robot journal wiped); it is sticky,
closes the gate, and every later task event from that robot is recorded as
`evidence` without moving any task (a wiped robot may re-execute a queued
request). Task events from an older boot arriving late are just late
evidence. Over a real broker, one queued event can reach the Edge before
the regressed heartbeat; that single pre-regression event is applied
normally and documented as a known ordering residual.

Liveness (Edge receipt clock, never the device clock): `UNKNOWN` after Edge
start or a lost transport session → `OFFLINE` after `restart_grace_s`
without a fresh status; `ONLINE → STALE` after `stale_after_s`; `STALE →
OFFLINE` after `offline_after_s`; any accepted status → `ONLINE`.
Unchanged heartbeats journal a compact `device_heartbeat` receipt at most
once per half stale window, so `last_valid_status_received_at` may lag the
true last heartbeat by up to that amount.

Reconciliation flags (cleared by the next robot evidence unless sticky):
`edge_restart`, `transport_session_lost`, `new_boot_session`,
`heartbeat_mismatch` (robot idle with no current task while the Edge holds
`ACCEPTED`/`RUNNING`, after one stale window), `progress_window_elapsed`
(`ACCEPTED`/`RUNNING` with no event inside the request's window),
`acceptance_window_elapsed` (`CREATED`, published, no `ACCEPTED` inside the
window), `expired_unconfirmed`. Sticky: `conflicting_terminal`,
`conflicting_replay`, `post_terminal_activity`, `session_regression`.

Republication (byte-identical request, same `task_id`, never a new id):
only for a non-terminal, non-blocked task whose device is `ONLINE`/`STALE`,
with no authorization block, fewer than `max_republish_attempts` journaled
attempts, at least `min_republish_interval_s` since the last attempt, at
most one attempt after `expires_at_utc`, and only while a clearable
reconciliation flag is pending (or no publication was ever confirmed). The
attempt is journaled (`task_publish_attempted`) before the transport call,
so the bound holds even when hop-1 never confirms. The gateway re-derives
each candidate from the live view immediately before publishing, because
an earlier candidate's hop-1 wait pumps the network loop and may have
applied a terminal for a later one. Once a terminal is accepted there is
no republication, not even to recover missing history; naturally late
history only adds evidence.

## Protocol double (executor) rules

Validation order for a request: topic/schema/site/deployment/environment/
target identity → `task_id == f(content)` → known `task_id` (identical
content: replay the task's full persisted history, no execution; different
content is unreachable and rejected at sequence 0) → robot condition
(`awaiting_human`, `faulted`, `estopped` → task-level `REJECTED` with
`robot_faulted` / `estop_latched` / `energy_insufficient`) → expiry
(`task_expired`) → busy (`robot_busy`) → task type
(`unsupported_task_type`) → `ACCEPTED`.

Evidence: `task_decision` (intent), `execution_started` (the simulated
action began), `execution_completed` (it ended). **Execution count is the
number of `execution_started` records for a task.** Every decision and event
is journaled before it is published; the robot journals
`event_publish_confirmed` on hop-1 and republishes unconfirmed events after a
restart. Sequence-0 rejections (identity or content failures, whether or not
the `task_id` is known) are persisted and published once, keyed by their own
journal record, and are never part of any task's replayed history.

Restart branches: accepted-but-never-started → `FAILED
not_started_after_restart`; started-but-not-completed → `INCONCLUSIVE
interrupted_execution_unknown_outcome` and the robot stays
`awaiting_human` with `safe_return_confirmed = null`; completed-but-
unpublished → republish the original terminal. Nothing is re-executed. Only
the explicit SIMULATION `--simulate-reset` returns the double to
`available` (and reports `FAILED cannot_continue` for an abandoned task).

Behaviors (`--behavior`): `accept_and_succeed`, `fail_cannot_continue`,
`help_needs_manual_recharge`, `help_unknown_fault:<code>`,
`silent_after_accept`, `crash_after_accept` (decision and `ACCEPTED`
persisted, exit before publishing them and before execution starts),
`crash_after_execution_started`, `crash_after_result_persisted`. The carrier
always runs `standby`, which advertises and accepts no task type whatever
the config lists, so every request to it is `REJECTED unsupported_task_type`
with zero executions.

## Running it

From `simulation/` (four terminals, task-specific port 18830):

```bash
mosquitto -c deploy/edge-task-v0/mosquitto.loopback.conf
uv run --no-sync python -B scripts/edge_task_gateway_v0.py --config configs/edge_task/pilot-course-a.sim.example.json
uv run --no-sync python -B scripts/mock_robot_task_device.py --config configs/edge_task/pilot-course-a.sim.example.json --robot-id picker-01 --behavior accept_and_succeed
uv run --no-sync python -B scripts/mock_robot_task_device.py --config configs/edge_task/pilot-course-a.sim.example.json --robot-id carrier-01
issued="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; expires="$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)"
uv run --no-sync python -B scripts/edge_task_cli.py --config configs/edge_task/pilot-course-a.sim.example.json create-task --robot picker-01 --zone Z1 --issued-at-utc "$issued" --expires-at-utc "$expires"
uv run --no-sync python -B scripts/edge_task_cli.py --config configs/edge_task/pilot-course-a.sim.example.json list
```

Both timestamps must lie in the future relative to creation: an already
expired request is refused at the entry, and a byte-identical re-run of the
same `create-task` (same timestamps) is idempotent even after expiry.

Evidence lands under `reports/edge-task-v0/` (git-ignored). Stop each
process with Ctrl-C; the journals are the only state and every process
resumes from them. A second Edge instance on the same journal is refused by
`.edge.lock`.

## Verification

```bash
cd simulation
uv lock --check && uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/edge_task
uv run --no-sync python -B -m pytest -o addopts='' -q -rs -p no:cacheprovider tests/edge_task/test_integration_mosquitto.py
```

The integration module needs a local `mosquitto`; it skips with an explicit
reason otherwise, and a skip is not delivery evidence. The delivered head's
observed results are recorded in the PR A hand-off, not here.

## Fault-recovery matrix coverage (PR A)

| Scenario | Test |
|---|---|
| Normal flow, Picker once, Carrier zero (standby rule), restart both, robot journal truncation fail-stop | `test_closed_loop_inmemory.py::test_normal_flow_contract`, `::test_restart_of_edge_and_picker_preserves_contract`, `::test_carrier_rejects_unsupported_task_and_never_executes`, `::test_standby_double_with_supported_task_type_still_rejects_and_never_executes`, `test_recovery_robot.py::test_truncated_robot_journal_fails_stop_without_second_execution_started`, `test_integration_mosquitto.py::test_normal_flow_over_real_broker` |
| A1 terminal before acceptance, no resend, natural late history | `test_closed_loop_inmemory.py::test_terminal_before_acceptance_preserves_gaps_without_resend`, `::test_naturally_late_history_fills_gaps_without_resend_or_state_regression` |
| A2 duplicate terminal | `test_closed_loop_inmemory.py::test_duplicate_terminal_is_idempotent` |
| B1 old-boot progress after new-boot terminal | `test_cases.py::test_old_boot_progress_after_new_boot_terminal_is_late_evidence` |
| B2 forged / foreign requests: sequence-0 rejections, repeated and for unknown ids | `test_recovery_robot.py::test_forged_same_id_request_is_rejected_at_sequence_zero_without_touching_task`, `::test_repeated_forged_same_id_requests_each_publish_a_sequence_zero_rejection`, `::test_legit_resend_after_forged_same_id_request_replays_no_sequence_zero`, `::test_unknown_task_identity_rejection_is_acked_published_and_survives_restart`, `test_cases.py::test_sequence_zero_rejection_never_transitions` |
| B3 late legitimate terminal | `test_closed_loop_inmemory.py::test_late_legitimate_terminal_is_applied_after_resend` |
| B3′a / B3′b terminal conflict, both orders, prior facts preserved, torn batch | `test_cases.py::test_inconclusive_then_success_exposes_conflict_and_blocks_authorization`, `::test_success_then_conflicting_terminal_blocks_future_authorization`, `::test_conflicting_terminal_detected_regardless_of_arrival_order`, `::test_conflict_preserves_prior_release_and_dispatch_facts`, `test_recovery_edge.py::test_torn_conflict_batch_restores_gate_from_evidence_record_on_restart`, `test_integration_mosquitto.py::test_late_terminal_history_is_applied_and_conflict_gate_holds` |
| B4 session regression (resend ban and event ban) | `test_sessions.py::test_boot_sequence_regression_blocks_resend_and_opens_conflict`, `::test_events_from_regressed_session_are_evidence_not_applied` |
| C1 Edge crash before publish confirmation; unconfirmed publish bounds | `test_recovery_edge.py::test_crash_after_task_created_before_publish_confirmed_republishes_same_bytes`, `::test_publish_without_hop1_confirmation_is_retried_and_deduplicated_by_the_robot`, `::test_unconfirmed_initial_publish_is_bounded_by_interval_and_attempt_cap` |
| C2 accept persisted, crash before start | `test_recovery_robot.py::test_crash_after_accept_persist_before_start_reports_failed_not_started` |
| C3 crash during execution | `test_recovery_robot.py::test_crash_during_execution_reports_inconclusive_and_never_reruns`, `test_integration_mosquitto.py::test_crash_after_execution_started_reports_inconclusive_and_never_reruns` |
| C4 result persisted, crash before publish | `test_recovery_robot.py::test_crash_after_result_persisted_before_publish_republishes_original_boot_event` |
| C5 / C6 Edge crash around the journal append and PUBACK; terminal lost with the broker session | `test_recovery_edge.py::test_crash_before_journaling_result_recovers_by_redelivery_or_resend`, `::test_crash_after_journal_before_puback_yields_single_duplicate_record`, `::test_terminal_lost_with_broker_session_triggers_single_resend_and_replay` |
| D1 resend executes once within validity | `test_closed_loop_inmemory.py::test_resend_to_robot_that_never_received_request_executes_once_within_validity` |
| D2 known task replays history | `test_recovery_robot.py::test_known_task_resend_replays_history_without_execution` |
| D3 expired unknown task | `test_closed_loop_inmemory.py::test_expired_unknown_task_is_rejected_and_never_executed` |
| D4 no republish after terminal/blocked/offline; candidate re-derived after a hop-1 wait | `test_closed_loop_inmemory.py::test_terminal_and_blocked_tasks_are_not_republished`, `::test_terminal_applied_during_hop1_wait_is_not_republished`, `test_sessions.py::test_offline_device_gets_no_republication` |
| Progress window, liveness, restart grace | `test_sessions.py`, `test_integration_mosquitto.py::test_silent_after_accept_leaves_persistent_unverified_progress_evidence` |
| Broker restart | `test_recovery_edge.py::test_broker_restart_mid_task_converges_without_duplicate_execution`, `test_integration_mosquitto.py::test_broker_restart_mid_task_converges_without_duplicate_execution` |

E and F (notifications, human handling) are PR B.

## Non-goals and known gaps

Scope note versus the approved plan's file list: the protocol double's pure
rules live in `nxt_edge_task/executor.py` (same stdlib-only guards as the
rest of the package) and the transport port with its two implementations in
`scripts/edge_task_transport.py`; both are composition-root or leaf files,
not new owners.

No physical robot, sensor, CAN, serial, ROS, arm, charger, or site broker;
no automatic zone selection, density learning, or scheduling; no carrier
transport, rescue, or takeover; no automatic charging or return; no
dashboard, cloud, or mobile surface; no conversion of the status message
into an `Observation` (the status message carries the same semantic facts
as the edge adapter's `RobotStatusSample` under different names and nesting,
so a future composition root must map them explicitly); no advice-to-task
conversion; no
`task_closed_by_operator` exit. The Edge cannot notify anyone of its own
death: a host watchdog is a prerequisite for any site acceptance and is not
built here. `last_valid_status_received_at` after an Edge restart reflects
the last journaled receipt, not the last heartbeat.
