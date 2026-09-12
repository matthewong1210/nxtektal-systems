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
| Persistence | One append-only JSONL journal per writer role with contiguous sequence, content-derived `record_id`, canonical bytes, LOCK_EX verify-before-append, fsync + directory fsync, fail-loud integrity on read, and a sibling high-water anchor (`<journal>.hwm`: record count + last `record_id`, written after each durable append) so a journal rolled back to an older, individually valid prefix is refused as state loss rather than resumed |
| Forbidden reverse path | No `nxt_*` package may import `nxt_edge_task`; the package must not import transport, network, subprocess, threading, wall-clock, UUID, or randomness modules; no message can pause, resume, reset, or clear a device |
| Versioning | `nxt.edge.robot-status/v1`, `nxt.edge.task.request/v1`, `nxt.edge.task.event/v1`, `nxt-edge-task/config/v1`, `nxt-edge-task/journal/v1`; unknown versions, unknown keys, duplicate keys fail closed; no migration exists |
| Environment | `environment.kind` is the single value `SIMULATION`; the config carries only `simulation_env_id`; a different kind is a new protocol version that must pass the architecture gate |
| Determinism | No wall clock, UUID, or randomness inside the package; every timestamp is supplied by the caller; identities are content-derived |
| Safety | Robot local protection is script-driven and never waits for the Edge, a notification, or a human reply; lost contact is not proof of a parked robot; a paused task is not proof that a load is released; nothing here is validated physical behaviour |

## Processes and storage (write authority)

| Process | Writes (only) | Reads | Never |
|---|---|---|---|
| `scripts/edge_task_gateway_v0.py` | `<evidence>/edge/<site>/<deployment>/edge_task_journal.jsonl` (+ its `.hwm` anchor) + `.edge.lock` (origin `EDGE` and robot facts *as received*) | its own journal | a robot or receiver directory; any robot internal state |
| `scripts/edge_task_cli.py` | the same Edge journal (and anchor), origin `SIM_ENTRY` only (`create-task`), under the same lock and admission rules | the Edge journal (`list`, `show`) | device/task state fields; robot directories |
| `scripts/mock_robot_task_device.py --robot-id picker-01` | `<evidence>/robots/picker-01/robot_task_journal.jsonl` (+ `.hwm`) | its own journal | the Edge or carrier directory |
| `scripts/mock_robot_task_device.py --robot-id carrier-01` | `<evidence>/robots/carrier-01/robot_task_journal.jsonl` (+ `.hwm`) | its own journal | the Edge or picker directory |

The Edge learns about a robot only through `task.event` and `robot-status`
messages received on the transport. Tests read the robot journal to assert
execution counts; the Edge never does.

Every process refuses any broker endpoint that is not a loopback IP literal
on a task-specific port (`127.0.0.0/8` or `::1`; never a host name, never
1883/8883). The rule lives in the contract (`assert_local_broker_endpoint`)
and is applied again by the transport adapter before it opens a socket, so
no config can point the rehearsal at a site or shared broker.

Identity continuity: a robot journal exists only because an operator
provisioned that identity (`--initialize`, journaled as `robot_provisioned`,
origin `OPERATOR`). Afterwards a missing, empty, or rolled-back journal
(shorter than its anchor, or differing at the anchored record) is **state
loss**: the device refuses to start (exit 4, no file or directory written,
no socket opened), so a lost dedup store never silently becomes an empty or older
one. Re-provisioning is a new incarnation and requires an explicitly absent
or empty journal: the old anchor is discarded on the operator's say-so, the
stale broker session for the client id is purged first (queued requests for
the dead incarnation are discarded, not executed), and the new `boot_id`
carries a fresh provisioning token. `boot_id` is
`<incarnation>-<boot_sequence>` by contract; the incarnation prefix is fixed
at provisioning and the Edge compares it on every status *and* every task
event, so a new incarnation is recognised whatever its counter says and
even when its events outrun its first heartbeat.

The CLI's `list`/`show` never re-stamp journaled state as current: next to
the Edge's last derived `connectivity` they report `as_read` (the same
receipt thresholds applied to the reader's clock, with `status_age_s`),
`journal_last_record_at_utc`, `journal_age_s`, and `edge_lock_held` (a
read-only probe of `.edge.lock`). A journal nobody ticks reads `OFFLINE`
after `offline_after_s`, not `ONLINE` at "now".

## Wire contracts

All fields are required; unknown or duplicate keys are rejected; payloads
are bounded to 65,536 bytes.

- `robot-status/v1` (robot → Edge, QoS 0, never retained): identity,
  `boot_id` (`<incarnation>-<boot_sequence>`; the prefix is fixed at
  provisioning), `boot_sequence` (monotonic, persisted by the robot),
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
different content → `conflicting_replay` (sticky flag). A same-key replay
that is a *different terminal* is additionally a terminal conflict: the
same key is no exception to the mutual-exclusion check, both raw results
are kept in `terminals`, and the gate below closes. `ACCEPTED` or `REJECTED` after
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

Session ordering: a live status or task event whose incarnation prefix
differs from the one already seen for that robot, or a status with a lower
`boot_sequence` (or the same `boot_sequence` under a different `boot_id`),
is a `session_regression` (recorded with its `source`, `robot_status` or
`task_event`); it is sticky, closes the gate, stops every resend to that
robot, and every task event from the new incarnation is recorded as
`evidence` without moving any task — including events that arrive before
the incarnation's first heartbeat. A differing terminal in that evidence
still makes the recorded result unverifiable (`CONFLICT`, once), so the
terminal-conflict gate closes on top of the regression. The device side no longer re-executes a
queued request after a wipe (provisioning purges the session) and refuses a
rolled-back journal, so the regression is the Edge's independent guard, not
the only one. Task events from an older boot of the *same* incarnation
arriving late are just late evidence. Residual: a request republished by
the Edge before it learned of the regression and delivered to the fresh
incarnation after provisioning is a new task for that incarnation; the
Edge records its events as evidence and closes the gate, but the double
executes it once. The window is one in-flight resend during
re-provisioning; it is documented, not closed.

Evidence conflicts also close the gate: `post_terminal_activity` (a
higher-key non-terminal event after a terminal), `unexpected_acceptance`,
`unexpected_rejection`, and `conflicting_replay` are sticky and, like a
terminal conflict or a session regression, refuse every new authorization
and republication for that robot until PR B's human handling. A robot
reporting activity the Edge's record cannot explain is executing something
the Edge did not authorise as such.

Liveness (Edge receipt clock, never the device clock): `UNKNOWN` after Edge
start or a lost transport session → `OFFLINE` after `restart_grace_s`
without a fresh status; `ONLINE → STALE` after `stale_after_s`; `STALE →
OFFLINE` after `offline_after_s`; any accepted status → `ONLINE`.
Unchanged heartbeats journal a compact `device_heartbeat` receipt at most
once per half stale window, so `last_valid_status_received_at` may lag the
true last heartbeat by up to that amount.

Two clocks per task: the **receipt clock** (`last_event_received_at`, any
robot evidence for the task whatever its disposition) and the **progress
clock** (`last_progress_at`, applied state-advancing events only). Message
receipt is never progress: duplicates, late evidence, and conflicting
replays leave the progress clock, the progress window, and the
progress-type flags untouched.

Reconciliation flags come in two classes. *Presence* reasons ask whether
the robot still holds the task and are answered by any robot evidence for
it: `edge_restart`, `transport_session_lost`, `new_boot_session`,
`expired_unconfirmed`. *Progress* reasons state that no trusted progress
arrived and clear only when an event is applied: `heartbeat_mismatch`
(robot idle with no current task while the Edge holds `ACCEPTED`/`RUNNING`,
one stale window after the last progress), `progress_window_elapsed`
(`ACCEPTED`/`RUNNING` with no applied event inside the request's window,
anchored on the progress clock), `acceptance_window_elapsed` (`CREATED`,
published, no `ACCEPTED` inside the window). Sticky, never cleared:
`conflicting_terminal`, `conflicting_replay`, `post_terminal_activity`,
`unexpected_acceptance`, `unexpected_rejection`, `session_regression`.

Republication (byte-identical request, same `task_id`, never a new id):
only for a non-terminal, non-blocked task whose device is `ONLINE`/`STALE`,
with no authorization block, fewer than `max_republish_attempts` journaled
attempts, at least `min_republish_interval_s` since the last attempt, at
most one attempt after `expires_at_utc`, and only while a clearable
reconciliation flag is **unanswered** — flagged after the last robot
evidence for the task (or no publication was ever confirmed). A robot's
reply to a reconcile resend (typically a duplicate history replay) answers
the flag and stops the resends; the flag itself stays visible until trusted
progress clears it. The
attempt is journaled (`task_publish_attempted`) before the transport call,
so the bound holds even when hop-1 never confirms. The gateway re-derives
each candidate from the live view immediately before publishing, because
an earlier candidate's hop-1 wait pumps the network loop and may have
applied a terminal for a later one. Once a terminal is accepted there is
no republication, not even to recover missing history; naturally late
history only adds evidence.

## Protocol double (executor) rules

Validation order for a request: topic → schema (including
`task_id == f(content)`) → site/deployment/environment/target identity →
known `task_id` (identical
content: replay the task's full persisted history, no execution; different
content is unreachable and rejected at sequence 0) → robot condition
(`awaiting_human`, `faulted`, `estopped` → task-level `REJECTED` with
`robot_faulted` / `estop_latched` / `energy_insufficient`) → expiry
(`task_expired`) → busy (`robot_busy`) → task type
(`unsupported_task_type`) → `ACCEPTED`.

Evidence: `task_decision` (intent), `execution_started` (the simulated
action began), `execution_completed` (it ended, with its outcome). **Execution
count is the number of `execution_started` records for a task.** Every
decision and event is journaled before it is published; the robot journals
`event_publish_confirmed` on hop-1 and republishes unconfirmed events after a
restart. Sequence-0 rejections (identity or content failures, whether or not
the `task_id` is known) are persisted and published once, keyed by their own
journal record, and are never part of any task's replayed history.

The robot's protective condition (`awaiting_human`, availability, energy
and fault facts) is a pure function of the persisted task events and the
operator reset record — there is no separate trailing "condition" record.
`INCONCLUSIVE`, `FAILED`, and `ASSISTANCE_REQUIRED` protect (with
`safe_return_confirmed = null`; `unknown:<code>` → `faulted` with that
`fault_code`; `energy_insufficient` → `needs_manual_recharge`; protection
under `cannot_continue` and the restart reasons never downgrades an existing
`faulted` availability or erases an energy fact, while a fresh
`unknown:<code>` fault marks the energy facts unknown and a fresh recharge
report marks the robot `awaiting_human`); `SUCCEEDED` clears; `REJECTED`
changes nothing; only the OPERATOR-origin
`simulate_reset` record clears protection, and it is always the last line
of its batch. A batch torn at any complete-line boundary can therefore only
leave the robot *more* protected, never less.

Restart branches (decided against persisted evidence only; accepted tasks
first so protection is derived before anything else is decided):
`execution_completed` persisted but its terminal event lost → the persisted
outcome is reported from the new boot (`SUCCEEDED`, or `FAILED
cannot_continue`); accepted-but-never-started → `FAILED
not_started_after_restart`; accepted-and-started-but-not-completed →
`INCONCLUSIVE interrupted_execution_unknown_outcome`; a rejected decision whose
`REJECTED` event was lost → that rejection is persisted and published; a
request persisted without any decision → decided at restart exactly as if
it had just arrived (so a duplicate request never replays an empty
history); completed-and-persisted-but-unpublished → republish the original
terminal. Nothing is re-executed. Only the explicit SIMULATION
`--simulate-reset` returns the double to `available` (and reports `FAILED
cannot_continue` for an abandoned task).

Behaviors (`--behavior`): `accept_and_succeed`, `fail_cannot_continue`,
`help_needs_manual_recharge`, `help_unknown_fault:<code>`,
`silent_after_accept`, `crash_after_accept` (decision and `ACCEPTED`
persisted, exit before publishing them and before execution starts),
`crash_after_execution_started`, `crash_after_result_persisted`. The carrier
always runs `standby`, which advertises and accepts no task type whatever
the config lists, so every request to it is `REJECTED unsupported_task_type`
with zero executions. `--step-interval-s` paces the simulated execution
steps (a test fixture for real-broker ordering scenarios; the default
advances one step per tick).

## Running it

From `simulation/` (four terminals, task-specific port 18830):

```bash
mosquitto -c deploy/edge-task-v0/mosquitto.loopback.conf
uv run --no-sync python -B scripts/edge_task_gateway_v0.py --config configs/edge_task/pilot-course-a.sim.example.json
uv run --no-sync python -B scripts/mock_robot_task_device.py --config configs/edge_task/pilot-course-a.sim.example.json --robot-id picker-01 --behavior accept_and_succeed --initialize
uv run --no-sync python -B scripts/mock_robot_task_device.py --config configs/edge_task/pilot-course-a.sim.example.json --robot-id carrier-01 --initialize
issued="$(date -u +%Y-%m-%dT%H:%M:%SZ)"; expires="$(date -u -v+10M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '+10 minutes' +%Y-%m-%dT%H:%M:%SZ)"
uv run --no-sync python -B scripts/edge_task_cli.py --config configs/edge_task/pilot-course-a.sim.example.json create-task --robot picker-01 --zone Z1 --issued-at-utc "$issued" --expires-at-utc "$expires"
uv run --no-sync python -B scripts/edge_task_cli.py --config configs/edge_task/pilot-course-a.sim.example.json list
```

Both timestamps must lie in the future relative to creation: an already
expired request is refused at the entry, and a byte-identical re-run of the
same `create-task` (same timestamps) is idempotent even after expiry.

`--initialize` is the explicit first boot of each robot identity; restart a
robot without it. A robot whose journal is missing, empty, or rolled back
and that is started without `--initialize` exits 4 (`robot_state_lost`)
before it can read a request; `--initialize` on an already provisioned
identity exits 4 as well (`robot_already_provisioned`). A `crash_*`
behavior exits 3 after persisting its evidence; a journal integrity or ack
failure at runtime fail-stops with exit 2.

Evidence lands under `reports/edge-task-v0/` (git-ignored). Stop each
process with Ctrl-C; the journals are the only state and every process
resumes from them. A second Edge instance on the same journal is refused by
`.edge.lock`. `list` and `show` print the journaled state together with the
read-time `as_read` freshness fields described above; they do not require a
running Edge and never write.

## Verification

```bash
cd simulation
uv lock --check && uv sync --locked --all-extras
uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/edge_task
uv run --no-sync python -B -m pytest -o addopts='' -q -rs -p no:cacheprovider tests/edge_task/test_integration_mosquitto.py
```

The integration module needs a local `mosquitto`; it skips with an explicit
reason otherwise, and a skip is not delivery evidence. It starts its own
broker on a random loopback port per scenario (the manual run above uses
the task-specific port 18830 from the checked-in conf), with one temporary
evidence root per scenario and a separate evidence directory per process
role. The delivered head's observed results
are recorded in the PR A hand-off, not here.

## Fault-recovery matrix coverage (PR A)

| Scenario | Test |
|---|---|
| Normal flow, Picker once, Carrier zero (standby rule), restart both, robot journal truncation fail-stop | `test_closed_loop_inmemory.py::test_normal_flow_contract`, `::test_restart_of_edge_and_picker_preserves_contract`, `::test_carrier_rejects_unsupported_task_and_never_executes`, `::test_standby_double_with_supported_task_type_still_rejects_and_never_executes`, `test_recovery_robot.py::test_truncated_robot_journal_fails_stop_without_second_execution_started`, `test_integration_mosquitto.py::test_normal_flow_over_real_broker` |
| A1 terminal before acceptance, no resend, natural late history | `test_closed_loop_inmemory.py::test_terminal_before_acceptance_preserves_gaps_without_resend`, `::test_naturally_late_history_fills_gaps_without_resend_or_state_regression`, `test_integration_mosquitto.py::test_terminal_before_acceptance_over_real_broker_has_zero_resend_and_natural_late_history` |
| A2 duplicate terminal | `test_closed_loop_inmemory.py::test_duplicate_terminal_is_idempotent` |
| B1 old-boot progress after new-boot terminal | `test_cases.py::test_old_boot_progress_after_new_boot_terminal_is_late_evidence` |
| B2 forged / foreign requests: sequence-0 rejections, repeated and for unknown ids | `test_recovery_robot.py::test_forged_same_id_request_is_rejected_at_sequence_zero_without_touching_task`, `::test_repeated_forged_same_id_requests_each_publish_a_sequence_zero_rejection`, `::test_legit_resend_after_forged_same_id_request_replays_no_sequence_zero`, `::test_unknown_task_identity_rejection_is_acked_published_and_survives_restart`, `test_cases.py::test_sequence_zero_rejection_never_transitions` |
| B3 late legitimate terminal | `test_closed_loop_inmemory.py::test_late_legitimate_terminal_is_applied_after_resend` |
| B3′a / B3′b terminal conflict, both orders, same-key conflict, prior facts preserved, torn batch | `test_cases.py::test_inconclusive_then_success_exposes_conflict_and_blocks_authorization`, `::test_success_then_conflicting_terminal_blocks_future_authorization`, `::test_conflicting_terminal_detected_regardless_of_arrival_order`, `::test_conflict_preserves_prior_release_and_dispatch_facts`, `::test_same_key_conflicting_terminal_closes_the_authorization_gate`, `test_recovery_edge.py::test_torn_conflict_batch_restores_gate_from_evidence_record_on_restart`, `test_integration_mosquitto.py::test_conflict_gate_holds_when_inconclusive_arrives_first_over_real_broker`, `::test_conflict_gate_holds_when_success_arrives_first_over_real_broker` (later-boot INCONCLUSIVE, later-boot FAILED, same-key FAILED), `test_cases.py::test_two_differing_late_terminals_on_an_open_task_conflict`, `::test_same_key_conflict_against_a_late_evidence_terminal_is_kept_without_a_second_conflict_record` |
| B4 session regression and identity continuity (state loss refused, rolled-back journal refused, re-provisioning purges the queued request, same-boot and later-boot re-provisioning detected, events outrunning the first heartbeat, resend ban, event ban) | `test_sessions.py::test_lost_robot_journal_is_not_a_first_boot_and_never_re_executes_a_queued_request`, `::test_rolled_back_robot_journal_is_refused_even_though_the_prefix_is_valid`, `::test_reprovisioned_robot_at_the_same_boot_number_is_a_session_regression`, `::test_reprovisioned_incarnation_is_detected_even_after_its_counter_passes_the_seen_boot`, `::test_events_of_a_new_incarnation_arriving_before_its_heartbeat_are_evidence`, `::test_differing_terminal_from_a_regressed_session_marks_the_result_conflicting`, `::test_boot_sequence_regression_blocks_resend_and_opens_conflict`, `::test_events_from_regressed_session_are_evidence_not_applied`, `test_integration_mosquitto.py::test_lost_robot_journal_is_refused_and_never_re_executes_over_real_broker` |
| Evidence-conflict gate (post-terminal activity, unexpected acceptance/rejection) | `test_cases.py::test_evidence_conflicts_close_the_authorization_gate` |
| Journal anchor (rollback, rewritten record, missing anchor, torn batch tolerance) | `test_journal.py::test_anchor_advances_with_every_append_and_lags_never_leads`, `::test_rollback_to_a_valid_prefix_fails_loud_on_read_and_append`, `::test_rewritten_record_at_the_anchor_fails_loud`, `::test_missing_journal_with_an_anchor_and_journal_without_anchor_both_fail`, `::test_discard_anchor_only_for_a_missing_or_empty_journal`, `::test_torn_batch_is_not_a_rollback` |
| C1 Edge crash before publish confirmation; unconfirmed publish bounds | `test_recovery_edge.py::test_crash_after_task_created_before_publish_confirmed_republishes_same_bytes`, `::test_publish_without_hop1_confirmation_is_retried_and_deduplicated_by_the_robot`, `::test_unconfirmed_initial_publish_is_bounded_by_interval_and_attempt_cap` |
| C2 accept persisted, crash before start | `test_recovery_robot.py::test_crash_after_accept_persist_before_start_reports_failed_not_started` |
| C3 crash during execution | `test_recovery_robot.py::test_crash_during_execution_reports_inconclusive_and_never_reruns`, `test_integration_mosquitto.py::test_crash_after_execution_started_reports_inconclusive_and_never_reruns` |
| Torn robot batches at every complete-line boundary (recovery, request, rejection, completion, operator reset) | `test_recovery_robot.py::test_torn_restart_recovery_batch_still_protects_the_robot` (every boundary of the recovery batch), `::test_torn_request_batch_is_resolved_at_restart` (×2), `::test_torn_rejection_batch_publishes_the_rejection_at_restart`, `::test_torn_completion_batch_reports_the_persisted_outcome_at_restart`, `::test_torn_operator_reset_batch_leaves_the_robot_protected` (×4) |
| C4 result persisted, crash before publish | `test_recovery_robot.py::test_crash_after_result_persisted_before_publish_republishes_original_boot_event` |
| C5 / C6 Edge crash around the journal append and PUBACK; terminal lost with the broker session | `test_recovery_edge.py::test_crash_before_journaling_result_recovers_by_redelivery_or_resend`, `::test_crash_after_journal_before_puback_yields_single_duplicate_record`, `::test_terminal_lost_with_broker_session_triggers_single_resend_and_replay` |
| Edge downtime (C5 persistent-session branch): broker-queued history applied in order, no resend | `test_integration_mosquitto.py::test_late_history_after_edge_downtime_is_applied_over_real_broker` |
| D1 resend executes once within validity | `test_closed_loop_inmemory.py::test_resend_to_robot_that_never_received_request_executes_once_within_validity` |
| D2 known task replays history | `test_recovery_robot.py::test_known_task_resend_replays_history_without_execution` |
| D3 expired unknown task | `test_closed_loop_inmemory.py::test_expired_unknown_task_is_rejected_and_never_executed` |
| D4 no republish after terminal/blocked/offline; candidate re-derived after a hop-1 wait | `test_closed_loop_inmemory.py::test_terminal_and_blocked_tasks_are_not_republished`, `::test_terminal_applied_during_hop1_wait_is_not_republished`, `test_sessions.py::test_offline_device_gets_no_republication` |
| Progress window (receipt ≠ progress), liveness, restart grace | `test_sessions.py` (incl. `::test_duplicate_history_replays_do_not_reset_the_progress_window`), `test_integration_mosquitto.py::test_silent_after_accept_leaves_persistent_unverified_progress_evidence` |
| Read-time freshness of the CLI views | `test_cli_views.py` |
| Local-broker boundary (config and transport, no network) | `test_contracts.py::test_config_refuses_non_loopback_or_conventional_broker_endpoints`, `::test_config_accepts_loopback_task_specific_endpoints`, `test_transport.py::test_paho_client_refuses_a_non_loopback_endpoint_before_any_socket` |
| Broker restart | `test_recovery_edge.py::test_broker_restart_mid_task_converges_without_duplicate_execution`, `test_integration_mosquitto.py::test_broker_restart_mid_task_converges_without_duplicate_execution` |

E and F (notifications, human handling) are PR B.

## Non-goals and known gaps

Scope note versus the approved plan's file list: the protocol double's pure
rules live in `nxt_edge_task/executor.py` (same stdlib-only guards as the
rest of the package) and the transport port with its two implementations in
`scripts/edge_task_transport.py`; both are composition-root or leaf files,
not new owners.

Evidence written before the identity-continuity, derived-condition, and
anchor changes is refused on read: robot journals without a
`robot_provisioned` record or with `condition_changed` lines, and any
journal (Edge or robot) without its `.hwm` anchor. The record vocabulary
changed inside `nxt-edge-task/journal/v1` because the package is unmerged
and no consumer exists; the only recovery for old evidence directories is
deleting them and provisioning again. No migration exists for pre-merge V0
evidence.

## Deviations from plan v3.1 (errata candidates)

Recorded so the plan can be amended rather than silently diverged from:

1. Robot identity continuity is explicit: `robot_provisioned` (OPERATOR),
   `--initialize`, state-loss refusal (exit 4), broker-session purge on
   provisioning, and the `<incarnation>-<boot_sequence>` `boot_id`
   convention. B4's "journal deleted then started at `boot_sequence` 1" now
   requires the operator flag; the Edge detects the new incarnation by
   prefix on status and events, not only by a lower boot number.
2. Journals carry a high-water anchor; a rolled-back journal is state loss.
3. The robot's protective condition is derived from task events; there is
   no `condition_changed` record.
4. The authorization gate also closes on the sticky evidence conflicts
   (`post_terminal_activity`, `unexpected_acceptance`,
   `unexpected_rejection`, `conflicting_replay`), not only on terminal
   conflicts and session regression. PR B's human handling must decide
   which of these a `resolve` may clear.
5. Receipt and progress clocks are distinct; presence reasons are answered
   by any robot evidence, progress reasons only by applied events, and a
   reconcile resend is repeated only while its flag is unanswered.
6. A restart decides a request that was persisted without a decision as if
   it had just arrived (a first execution, never a re-execution).

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
built here; the CLI's `as_read` fields make a dead Edge visible to a reader
but nothing pushes that fact anywhere. `last_valid_status_received_at`
after an Edge restart reflects the last journaled receipt, not the last
heartbeat (rate-limited `device_heartbeat` records lag by up to
`stale_after_s / 2`). Identity continuity is journal-based: a wiped or
rolled-back journal is refused, but a journal and its anchor rolled back
*together* to a consistent older prefix are indistinguishable from a crash
at that point (no external attestation exists in V0); the Edge still
detects a new incarnation from the `boot_id` prefix, and an unexplained
re-execution by the same incarnation surfaces as an evidence conflict that
closes the gate. A resend already in flight during re-provisioning is the
documented residual above. Journal growth is unbounded (heartbeat receipts
dominate); no rotation exists in PR A.
