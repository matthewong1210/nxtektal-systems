# Edge Task Interventions V0 (PR B)

SIMULATION ONLY — the minimal human-handling rehearsal on top of the Edge
Task Exchange (`edge_task_v0.md`): evidence the Edge already journals becomes
a persisted human-handling case, a persisted notification intent, a bounded
delivery to a **local loopback test receiver**, and a human `ack`/`resolve`
record. No e-mail, SMS, push, webhook, phone number, contact list, or
credential exists in this code. A receipt from the test receiver proves that
the receiver persisted the notification; it proves nothing about a phone,
and nothing about a person.

## Architecture decision (recorded gate outcome)

`nxt_edge_task` is registered as owning no human workflow and no
notification, and no existing package may import it. Rejected owners for
this fact class: `nxt_edge_task` itself (its registration forbids the
responsibility and the journal must stay single-writer); `nxt_pilot_ops`
(its human workflow is keyed by recommendations and its ledger vocabulary is
closed; it has no case source in a device journal); `nxt_facility.decisions`
(advice over `FacilityState`, no event source, no case lifecycle);
scripts-only (a service, a receiver, and a CLI in three processes need one
versioned, guarded, importable codec). Gate outcome: **Proceed** with a new
stdlib-only leaf, `nxt_edge_interventions`, that receives the Edge view as
plain data from composition roots and imports no other `nxt_*` package.

It is not a third decision engine: it emits no `Recommendation`, ranks
nothing, reconciles nothing with advisory output, and reads no
`FacilityState`. It is the same intentional divergence recorded for the
rehearsal in `.agent/context/source-of-truth.md`.

## Boundary card

| Item | Value |
| --- | --- |
| Owner | `nxt_edge_interventions` (stdlib-only leaf; imports no `nxt_*` package) |
| Input | `EdgeSnapshot`: the Edge task and device summaries plus `last_record_id` and `max_republish_attempts`, built by the service from `derive_edge_view` over the Edge journal. Missing fields are refused, never defaulted, so an incomplete snapshot can never read as healthy, idle, zero-battery, or parked |
| Output | Record specs for its own journal (`nxt-edge-interventions/journal/v1`), notification payloads (`nxt.edge.intervention.notification/v1`), receiver record specs (`nxt-edge-interventions/receiver-journal/v1`), derived views |
| Composition roots | `scripts/edge_intervention_service_v0.py` (reads the Edge journal, writes the interventions journal, POSTs to the receiver), `scripts/edge_notification_receiver_v0.py` (loopback HTTP, writes the receiver journal), `scripts/edge_intervention_cli.py` (list/show/ack/resolve, writes the interventions journal) |
| Never | Writes to the Edge journal or a robot journal; publishes task traffic; creates tasks; clears a gate or a conflict marker; sets a device idle, available, or parked; releases an e-stop; resends a task; talks to anything but a loopback literal |
| Forbidden imports (guarded) | any `nxt_*`; `socket`, `http`, `urllib`, `ssl`, `asyncio`, `subprocess`, `threading`, `multiprocessing`, `os`, `pathlib`, `shutil`, `time`, `random`, `secrets`, `uuid`, `tempfile`, `signal`, `paho`, `requests`, `importlib`; no wall clock |
| Guards | `tests/edge_interventions/test_architecture.py`: stdlib-only, no `nxt_*` import, no existing package imports the leaf, exactly the three scripts import it, the scripts never import the executor or forge robot evidence or create tasks, the service writes only its own journal |

## Write authority (three journals, three writers)

| Journal | Path | Writer | Readers |
| --- | --- | --- | --- |
| Edge journal `nxt-edge-task/journal/v1` | `<root>/<edge.evidence_dir>/<site>/<deployment>/edge_task_journal.jsonl` | PR A gateway and its test-entry CLI only | the intervention service (anchored read, plain data) |
| Interventions journal `nxt-edge-interventions/journal/v1` | `<root>/<evidence_dir>/<site>/<deployment>/edge_interventions_journal.jsonl` | the intervention service (cases, intents, attempts, results) and the operator CLI (`case_acknowledged`, `case_resolved`, `operator_action_rejected`), both through the shared append-only `JsonlJournal` with its high-water anchor and journal-shrank/diverged preconditions | the CLI views, tests |
| Receiver journal `nxt-edge-interventions/receiver-journal/v1` | `<root>/<receiver.evidence_dir>/receiver_journal.jsonl` | the local test receiver only | tests |

The receiver never reads Edge or intervention storage; its duplicate
detection is rebuilt from its own journal on restart.

The service also holds a process-lifetime, non-blocking POSIX lock in
`edge_interventions_service.lock` beside its journal. The lock is runtime
qualification, not evidence and not the journal's per-append lock: only one
dispatcher may use one persistent interventions journal, while `list`,
`show`, `ack`, and `resolve` continue to use the journal normally. A second
service exits non-zero before `intervention_service_started`, an attempt, or
a send. Process exit or abrupt loss releases the kernel lock; the next
service resumes from the unchanged journal without deleting the lock file,
resetting a budget, or generating replacement ids.

## Three state families, kept apart

| Family | Owner | Values | Changed by |
| --- | --- | --- | --- |
| Task and device state | PR A Edge journal | unchanged by this slice | robot messages and the gateway only |
| Human handling | interventions journal | `OPEN` → `ACKNOWLEDGED` → `RESOLVED`, each with operator label, time, and note | `ack`, `resolve` |
| Notification | interventions journal | `PENDING` → `ATTEMPTING` → `DELIVERED` / `UNKNOWN` / `FAILED` → `EXHAUSTED` | the service's attempts and results |

A `DELIVERED` state means the local receiver answered with a receipt. It is
not phone delivery and not "a person saw it"; neither of those exists here.
`RESOLVED` is a human record: every case summary carries
`authorization_effect: none` and `device_state_effect: none`.

## Cases

| Kind | Opened from | Severity | Evidence kept |
| --- | --- | --- | --- |
| `ASSISTANCE_REQUIRED` | a task in `BLOCKED_AWAITING_HUMAN` with its `blocking_event` (`ASSISTANCE_REQUIRED` event keyed by `(boot_sequence, event_sequence)`) | `WARNING`; `CRITICAL` when the reason code starts with `unknown:` | task id, robot id, reason code, event key, record id |
| `DEVICE_UNREACHABLE` | a device the Edge marks `OFFLINE` | `WARNING`; `CRITICAL` when the robot holds an open task | the last valid status time and availability the Edge recorded, the open task if any, and the explicit markers `state_unknown: true`, `stopped_confirmed: false` |
| `RESULT_UNCONFIRMED` | an open task whose republish budget is exhausted, or an expired task without a confirmed result | `WARNING` | task id, state, publish attempts, detail |
| `EVIDENCE_CONFLICT` | `effective_result = CONFLICT`, a sticky conflict reason, or a device `session_regression` | `CRITICAL` | task or device subject, the conflict reasons |

The message for a lost robot says `current state UNKNOWN — not confirmed
stopped or parked` and repeats the last valid data verbatim; unknown values
render as the literal `unknown`. Nothing in this slice ever renders a lost
device as healthy, parked, idle, or at zero battery.

## Identity, deduplication, recurrence, escalation

- `case_id = case_<digest(kind, subject_kind, subject_id, evidence_key)>`.
  The same evidence observed again on every tick adds nothing: one case, one
  `OPENED` notification, however often the robot repeats itself.
- New evidence on an open case (a different event key, a new conflict
  reason) is journaled as `case_evidence_added`; it does not notify.
- A severity rise is `case_escalated` plus one `ESCALATION` notification,
  delivered even when the case is acknowledged, never deduplicated away.
- When the underlying condition disappears the case records
  `case_condition_cleared` and stays open for the human; if it comes back,
  `case_condition_returned`. Only a human closes a case.
- After `RESOLVED`, identical evidence never reopens the case. Different
  evidence for the same subject opens a new case that names the old one in
  `recurrence_of` and notifies again.
  For an offline device, identity includes the offline transition and any
  open task id: a new task after a resolved task-free warning raises a new
  critical case. Within the same offline transition, a task ending after a
  resolved critical case does not create a warning recurrence. For a pre-fix
  time-only offline case, that terminal-task rule is compatibility behavior:
  removing task occupancy within the same offline episode is not new warning
  evidence. A different new task or a new offline transition can create a
  recurrence. Pre-fix PR B records retain their case ids; the recorded offline
  time and open task id distinguish unchanged evidence from recurrence.
- `notification_id = ntf_<digest(case_id, intent, ordinal)>` is stable
  across retries and restarts; every attempt of one intent carries the same
  id, and the receiver deduplicates on it. When replay recovers a torn
  escalation from an old journal after later reminders were already recorded,
  the reconstructed escalation may reuse a reminder ordinal; `intent` remains
  part of the digest, so notification ids stay unique and their delivery
  budgets stay separate.

## Delivery

1. Opening/escalation events and their notification intents are appended in
   one batch. If only the event line survives a crash, replay reconstructs
   its missing intent with the original id, ordinal, time, and payload.
   The service persists these intents before reading the Edge snapshot or
   scheduling reminders, including when the snapshot is temporarily
   unreadable. Repeated restarts do not duplicate them. Later `ack`/`resolve`
   records do not cancel notification obligations already committed by a
   case event. The payload's `human_state` is the state at the event time,
   not the current human disposition or any task/authorization state.
2. `notification_attempted` is journaled before the HTTP call;
   `notification_result` (`delivered`, `unknown`, `failed`) after it. A
   crash between the two leaves the notification `ATTEMPTING`; after the
   retry interval it is retried under the same id. If the receiver had
   already persisted it, the retry gets the original receipt back
   (`duplicate: true`) and the sender records `DELIVERED` once.
3. `unknown` (no answer, connection refused) and `failed` (refused with
   4xx/5xx) are retried every `notify_retry_interval_s`, at most
   `notify_max_attempts` per notification, then `notification_exhausted` is
   journaled and the notification is `EXHAUSTED`. Exhaustion is never
   recorded as success, and the case stays visible and `OPEN`.
4. Attempts are counted in the interventions journal; unique receipts,
   duplicate attempts, and refusals are counted in the receiver journal.
   The two counts are separate facts and are asserted separately.
5. The receiver answers `200` with `{receipt_id, duplicate}` for a valid
   envelope (exact keys, schema `nxt.edge.intervention.notification/v1`,
   `environment.kind = SIMULATION`, matching site and deployment), or `400`
   with an error and a `notification_refused` line. An invalid message never
   becomes a receipt.
6. The sender validates the actual URL against the configured loopback
   literal, disables environment proxies, and refuses HTTP redirects.
   Redirect responses are failed attempts under the existing retry budget;
   no request is sent to the redirect target.
7. An expected interventions-journal append failure is a fail-stop: the
   service reports the original error, exits 2, and performs no later append
   or send. A receiver record already persisted before a local result-write
   failure remains valid evidence; it is not treated as revoked or erased.

## Reminders

While a case is `OPEN` and unacknowledged, a `REMINDER` notification is
intended every `reminder_interval_s`, at most `max_reminders` times, then
`reminders_exhausted`. An `ACKNOWLEDGED` case is not reminded but stays in
`list`/`show` with its condition. A `RESOLVED` case gets no new reminders;
all already committed notification intents—including `OPENED`, `ESCALATION`,
and `REMINDER`, whether originally complete or reconstructed during recovery—
still follow their bounded delivery attempts. Recovering an older intent does
not move the reminder clock backwards past a later recorded intent.
Reminders and escalations are separate notification ids with their own attempt budgets;
the total attempt bound for one case is
`(1 + reminders + escalations) × notify_max_attempts`.

## Human entry (operator CLI)

`list` and `show` render the derived view; `ack --case --operator --note`
moves `OPEN` to `ACKNOWLEDGED`; `resolve --case --operator --resolution`
moves `ACKNOWLEDGED` to `RESOLVED` and records whether the condition was
still active at resolution. A resolve on an unacknowledged case, an unknown
case, a repeated ack, an empty operator, or an empty note is refused with
exit 1 and journaled as `operator_action_rejected`. The operator label is a
recorded string, not an authenticated identity; nothing here is production
authorization. The CLI has no path to forge a robot event, edit evidence,
clear a conflict, reopen a gate, write a task result, or create a task; the
architecture guard pins that the three scripts never import the executor,
never mention task creation or task-traffic publication, and the service
writes only its own journal.

For each case, `list` and `show` also report a `processing` object scoped to
notification-intent persistence. It lists the case records already persisted
and, when replay can derive it exactly, the missing intent still awaiting
service recovery. An indeterminate derivation is shown as `unknown` with its
reason. These view commands never repair the journal, send a notification, or
append an operator action.

## Configuration

`configs/edge_task/pilot-course-a.interventions.sim.example.json`, schema
`nxt-edge-interventions/config/v1`, exact keys: `site_id`, `deployment_id`,
`simulation_env_id` (all three must equal the Edge config or the service
refuses to start), `evidence_dir`, `receiver` (`receiver_id`, `host` — a
loopback IP literal or the receiver refuses to bind and the service refuses
to send, `port` 1024–65535, `timeout_s`, `evidence_dir`),
`reminder_interval_s`, `max_reminders`, `notify_max_attempts`,
`notify_retry_interval_s`, `tick_interval_s`. Unknown keys and out-of-range
values are refused.

Compatibility statement: every record and payload this slice writes carries
its own schema id above; nothing was added to or changed inside
`nxt-edge-task/journal/v1`. PR A's code gained one additive field on the
task summary (`blocking_event`, the `ASSISTANCE_REQUIRED` event that blocked
the task) and a `schema` constructor parameter on the shared `JsonlJournal`
class whose default is unchanged; PR A evidence written before PR B reads
identically. Older readers of the task summary ignore the new field.

The offline-identity and intent-recovery fixes are pre-merge changes to
PR B's unreleased v1 journal semantics. The fixed code reads pre-fix PR B
journals without rewriting their records or case ids. On its first tick,
an open critical offline case with the old time-only key gains one
`case_evidence_added` record to include its already recorded task id; it
gets no extra notification, and subsequent ticks/restarts add nothing for
unchanged evidence. A case already resolved before the fixed service starts
stays quiet for unchanged evidence without that normalization record.
Pre-fix PR B code is not a supported reader of journals written by the fixed
code. These compatibility limits do not change PR A's journal contract.

## Commands (run from `simulation/`, SIMULATION only)

Start the PR A stack first (broker, Edge gateway, mock robots) exactly as in
`edge_task_v0.md`. Then, in separate terminals:

```bash
uv run --no-sync python -B scripts/edge_notification_receiver_v0.py --config configs/edge_task/pilot-course-a.interventions.sim.example.json
```

```bash
uv run --no-sync python -B scripts/edge_intervention_service_v0.py --edge-config configs/edge_task/pilot-course-a.sim.example.json --config configs/edge_task/pilot-course-a.interventions.sim.example.json
```

View, acknowledge, resolve:

```bash
uv run --no-sync python -B scripts/edge_intervention_cli.py --config configs/edge_task/pilot-course-a.interventions.sim.example.json list
```

```bash
uv run --no-sync python -B scripts/edge_intervention_cli.py --config configs/edge_task/pilot-course-a.interventions.sim.example.json show <case_id>
```

```bash
uv run --no-sync python -B scripts/edge_intervention_cli.py --config configs/edge_task/pilot-course-a.interventions.sim.example.json ack --case <case_id> --operator <label> --note "<text>"
```

```bash
uv run --no-sync python -B scripts/edge_intervention_cli.py --config configs/edge_task/pilot-course-a.interventions.sim.example.json resolve --case <case_id> --operator <label> --resolution "<text>"
```

Stop: `Ctrl-C` (SIGINT/SIGTERM) in the service and receiver terminals; both
journal nothing on stop beyond what was already appended, and a restart
re-derives every case and notification from the journal. Evidence lands
under `reports/edge-task-v0/interventions/` and
`reports/edge-task-v0/notifications/local-test-receiver/` by default;
point `--evidence-root` at a fresh directory for a clean rehearsal.

## Scenario → test map

| Plan scenario | Test |
| --- | --- |
| 1. Normal task: no case, no notification | `test_flow.py::test_normal_task_produces_no_human_case_or_notification`; `test_integration_e2e.py::test_normal_task_then_explicit_request_ack_and_resolve_over_real_broker_and_receiver` |
| 2. Explicit request → persisted case, one unique notification | `test_flow.py::test_explicit_assistance_request_opens_one_case_and_one_unique_notification`; e2e as above |
| 3. Repeated evidence does not multiply | `test_flow.py::test_repeated_assistance_evidence_does_not_reopen_or_renotify`, `::test_case_survives_service_restart_and_is_not_reopened`; e2e as above |
| 4. Lost receipt retried under one id, received once | `test_delivery.py::test_lost_receipt_is_retried_under_the_same_id_and_received_once`; `test_integration_e2e.py::test_lost_receipt_service_restart_and_receiver_restart_keep_one_unique_receipt` |
| 5. Event persisted, intent/delivery interrupted by a restart: no loss, no fake success | `test_delivery.py::test_torn_batch_between_case_and_intent_is_repaired_on_restart`, `::test_crash_after_attempt_before_send_retries_same_id_once_delivered`, `::test_crash_after_receiver_persisted_before_result_is_journaled_never_fakes_or_loses_delivery` |
| Torn escalation (including later resolve) and opening repair before new escalation | `test_delivery.py::test_durable_escalation_repairs_its_missing_intent_once` (both resolve parameters), `::test_missing_opening_intent_is_repaired_before_new_escalation` |
| Recovered intent does not move the reminder clock backwards; unreadable Edge snapshot cannot delay repair behind reminders | `test_delivery.py::test_recovered_older_escalation_does_not_move_the_reminder_clock_backwards`, `::test_pending_escalation_is_persisted_before_reminders_when_edge_snapshot_is_unreadable` |
| Unreachable / refusing receiver: bounded retries, never delivery | `test_delivery.py::test_unreachable_receiver_bounds_retries_and_never_reports_delivery`, `::test_refusing_receiver_records_failed_attempts` |
| HTTP destination remains loopback: actual URL validation, no environment proxy or redirect, valid receipts still accepted | `test_http_transport.py::test_transport_validates_the_url_it_will_send_to`, `::test_environment_proxy_cannot_change_the_socket_destination`, `::test_redirect_is_refused_without_a_second_request`, `::test_loopback_receipt_still_means_delivered` |
| One dispatcher per persistent service identity; CLI remains available; crash releases qualification | `test_integration_e2e.py::test_one_dispatcher_per_persistent_service_identity_with_crash_recovery` |
| Journal failure exits 2 with no later side effect; an already received notification is not undone | `test_integration_e2e.py::test_service_process_exits_two_on_journal_failure_without_delivery_side_effects`; `test_delivery.py::test_result_persistence_failure_fail_stops_without_undoing_the_receiver_record` |
| CLI exposes a torn case/intent batch without repairing or acting | `test_cli_recovery.py::test_list_and_show_expose_incomplete_batch_without_writing_or_acting`, `::test_processing_summary_marks_unreliable_pending_derivation_unknown` |
| 6. Bounded reminders, acknowledged/resolved silence, committed reminder delivery, escalation with synchronized evidence | `test_reminders.py::test_unacknowledged_case_is_reminded_at_the_interval_then_stops_at_the_cap`, `::test_acknowledged_case_gets_no_reminders_but_stays_visible`, `::test_resolved_case_with_unchanged_evidence_is_not_reopened`, `::test_resolve_does_not_cancel_an_already_committed_reminder`, `::test_escalation_notifies_even_when_acknowledged_and_is_not_deduplicated` |
| Condition clears and returns on an unresolved case | `test_cases.py::test_unresolved_case_records_when_its_condition_returns` |
| New offline episode or new task after resolve notifies; unchanged evidence and task completion stay quiet | `test_reminders.py::test_real_recurrence_after_resolution_opens_a_new_case_naming_the_old_one`, `::test_resolved_case_with_unchanged_evidence_is_not_reopened`, `::test_new_task_on_resolved_offline_warning_opens_a_critical_recurrence`, `::test_resolved_critical_offline_case_stays_quiet_when_task_ends_but_not_for_a_new_task` |
| Pre-fix PR B journals: resolve before first fixed-service tick stays quiet; open critical key normalizes once without notifying | `test_reminders.py::test_legacy_resolved_offline_identity_stays_quiet_on_restart` (both task parameters), `::test_legacy_open_critical_case_normalizes_its_key_once_without_notifying` |
| 7. Ack/resolve leave task result, gate, execution count unchanged | `test_flow.py::test_acknowledgement_is_recorded_without_touching_task_or_device_state`; `test_human_actions.py::test_ack_then_resolve_change_only_human_state`, `::test_resolve_requires_acknowledgement_and_valid_operator`, `::test_cli_entry_points_round_trip_and_never_touch_the_edge_journal`; `test_cases.py::test_evidence_conflict_opens_a_critical_case_and_resolution_does_not_reopen_authorization`; e2e as above |
| 8. Lost device keeps last valid data plus an unknown marker, never "parked" | `test_cases.py::test_lost_robot_with_open_task_opens_a_critical_case_with_last_valid_data_and_unknown_markers`; `test_integration_e2e.py::test_lost_device_with_open_task_over_real_broker_keeps_last_valid_data_and_unknown_marker`; `test_contracts.py::test_templates_mark_unknown_values_and_never_claim_a_stop` |
| Retry exhaustion and evidence conflicts open cases | `test_cases.py::test_exhausted_republish_budget_opens_a_result_unconfirmed_case`, `::test_session_regression_opens_a_device_conflict_case` |
| Invalid messages refused; strict config and snapshot | `test_contracts.py` (all six) |
| 9. PR A behaviour unchanged | the full `tests/edge_task/` suite, unchanged, plus `tests/edge_task/test_integration_mosquitto.py` locally |
| Boundaries | `test_architecture.py` (seven guards) plus the eleven existing guard tuples that now list `nxt_edge_interventions` |

All logic tests run on the injected clock through the PR A in-memory
harness composed with an in-process receiver double that persists through
the same `receipt_specs` decision as the real receiver.

## Local versus hosted CI

Hosted CI runs `tests/edge_interventions/` with the rest of the suite but
has no Mosquitto: `test_integration_e2e.py` skips there with an explicit
reason, exactly like PR A's broker test. A skip is not acceptance evidence;
the PR B hand-off attaches a local run of that module on its exact head,
with the broker, the Edge gateway, two mock robots, the intervention
service, the receiver, and the CLI as separate processes over separate
evidence directories.

## Not implemented (and not claimed)

- Real notification of any kind: no e-mail, SMS, push, webhook, pager,
  contact, credential, or non-loopback endpoint.
- Recovery or re-authorization after a conflict, a state loss, or an
  exhausted retry budget: `ack`/`resolve` clear nothing (see the recorded
  errata decision in `edge_task_v0.md`); a separate evidence condition,
  recovery protocol, and acceptance contract are required first.
- Real devices, physical robots, ROS, actuators, e-stop, or any command.
- A host watchdog: nothing restarts a crashed service or receiver; the
  journals make a restart safe, they do not perform it.
- Authenticated operators, roles, or an audit of who a label is.
