# Pilot Dispatch Console V0 — delivery record

Date: 2026-09-16. Scope: a local, SIMULATION-only software integration.

## Delivered behavior

One Manager Console can record a dated collection schedule, show task and device
progress, cancel a schedule before task admission, and record local notification
acknowledgement/resolution. Its task panel refreshes automatically. The existing
facility/advice panels retain their independent fixture data and workflow.

At the due time, scheduling rechecks current device evidence and the existing
task admission. One locked journal write binds the schedule to its task. Repeated
submissions, competing ticks, and restart after the task-created write do not
create a second task. Missed windows and admission refusals remain visible and
are not automatically retried. Human notes cannot clear device authorization or
turn an uncertain/failed task into success.

Start/restart instructions and architecture ownership are in the
[run guide](../simulation/docs/pilot_dispatch_v0.md).

## Integration lineage

- Base main: `2fbccb01c45950b5c4d0221509694f822d410170` (includes PR #17).
- Edge Task input: `0c23f90bea1c294204333b0e745ab7fec4b66b23` (PR #16).
- Site Agent input: `4b86d9fa6a59b1a14cdfc25b019e49a973bf89de` (PR #12).
- Integration branch: `feature/pilot-dispatch-console-v0`.

The branch preserves both input histories. The original canonical worktree,
its uncommitted work, existing worktrees, and remote PRs were not modified.
No main-branch merge, push, deployment, or physical connection was performed.

## Validation observed in this task

The primary task ran the following against the integrated source, using the
locked all-extras Python 3.13.14 environment:

| Check | Result |
|---|---|
| Full Python suite: `.venv/bin/python -B -m pytest -o addopts='' -q -p no:cacheprovider` from `simulation/` | **1,798 passed**, no skips; includes the 11 real local Mosquitto integration tests |
| API/composition focused suite | **32 passed**, including actual HTTP schedule → protocol-double success → restart with one execution, cancellation, missed-window inbox handling, assistance constraints and origin protections |
| Repository helpers: `simulation/.venv/bin/python -B -m unittest discover -s .github/scripts -p 'test_*.py' -q` | **111 passed** |
| Configuration validation | **0 errors, 0 warnings** |
| Python build | Wheel and source distribution built; both sibling packages included in the declared 15-package distribution |
| Repository verifier and whitespace checks | Passed; local links, fences, skill metadata, generated-file and boundary checks included |

The frontend implementation subtask ran typecheck, lint, the full console suite
(**63 tests**), the Next.js static build, and loopback HTTP smoke; all passed.
The console uses the same patched Next.js/eslint-config-next `16.3.5` and Vitest
`4.1.11` versions already selected in PR #17. The primary task's `npm install
--ignore-scripts` completed an audit of this dependency tree with **0 vulnerabilities**.
No dependency changes followed that installation.

Initial sandbox runs could not bind loopback ports; the Python/API/helper suites
above were rerun through the approved local-port execution path. They are local
results, not hosted CI results. No hosted CI was run for this integration branch.

Browser visual/interactive verification was attempted but blocked because the
browser tool could not verify the administrator-enforced policy. No alternative
browser path was used to bypass it. Therefore visual layout, browser interaction,
and browser console-error checks are **not verified** by this delivery.

A supplemental standalone `npm audit --omit=dev` was rejected by automatic
approval review because it would send the dependency inventory to npm's external
advisory service. It was not retried. The successful installation-time audit
above is the available evidence; a second production-only query is not claimed.

## Next integration work

1. Obtain the dated CE82A interface specification and sample status/result
   messages. Confirm vendor task identity, supported actions, cloud dependence,
   disconnection behavior, and real unloading/energy handling.
2. Define the physical admission/adapter contract against those actual
   capabilities; this rehearsal does not supply that contract or authorization.
3. Select and implement an external notification delivery channel, retry and
   escalation, plus a host watchdog. The delivered inbox is local only.
4. Add evidence-based recovery for unresolved tasks without allowing an
   acknowledgement to reopen device authorization.
5. Validate one real picker/zone/complete task before implementing carrier
   transport and handoff. Carrier-01 remains standby-only in this version.

The current scheduler supports one dated task, not recurring daily schedules.
There is no authenticated facility-network service, CE82A adapter, physical
stop/cancel command, automatic unloading/charging, or two-robot execution claim.
