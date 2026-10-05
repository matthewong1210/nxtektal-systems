# Whole-course read-only console integration V1

Architecture gate: **Proceed**, restricted to saved SIMULATION observations.
The user approved phase two of the console/course integration: inspect evidence,
coverage and recorded work without a new execution path.

## Status and ownership

The baseline is the local integration commit `cac34f0`, combining the planning
admission-time read projection and the shared scheduler health console. The V2
session implementation is copied byte-for-byte from its separate development
checkout before adding this consumer; its original experiments are unchanged.

`RangeSimulation` and `BallLedger` retain runtime truth. Existing course frames
and `CourseConditionObservation` retain observation meaning. `GroundsWorkflow`
retains inspection/maintenance records. This change owns only a disposable,
versioned, read-only projection in `scripts/course_operations.py`. It is not a
new FacilityState, policy, journal, task controller or observation assembler.

The Manager API transports injected callbacks and image bytes. Its package must
not import the simulator, course scripts, filesystem report readers or policies.
The console consumes the versioned HTTP contract only. Code and tests remain
within the existing Python/console surfaces; there is no new package/runtime.

## Data and failure boundary

- Read a configured series root, verified series/child state and report
  envelopes. Reject malformed, changing, inconsistent or corrupt evidence.
- Whitelist observed camera locations (including error), image-derived
  detections, point coverage and recorded maintenance work. Raw routes,
  inventory ledger, evaluation labels, future events and renderer assumptions
  are not observation inputs.
- Historical reports missing range observations remain explicitly unavailable.
  New reports may export the existing policy-input observation projection; no
  accounting-truth fallback or new sensor inference is allowed.
- Keep report/session identity and map identity explicit. Distinguish simulation
  time, API response time, source publication time and pause state. Source wall
  publication time is unknown for historical artifacts. Successful HTTP reads
  never make saved evidence live or recently observed.
- Media is limited to declared PNG frames in the selected verified round;
  validate identifiers, paths, symlinks, bounded sizes and image hashes. Do not
  expose arbitrary source files, reference labels or directory browsing.
- A missing optional route is 404; configured unreadable evidence is 503.
  POST requests in the course namespace return 405; other unsupported methods
  retain the server's rejection behavior. The same-origin/loopback rules
  remain in force. Requests never advance, resume or alter a session.
- The UI keeps source status separate from service transport failure, and
  resets selection on round change. Maps show checkpoint coverage, not an
  assertion that every square metre has been inspected.

## Interfaces and composition

`CourseOpsReader(series_root).snapshot()` returns `nxt-course-ops/v1` data;
`media(round_id, frame_id, expected_sha)` returns verified PNG bytes bound to
the requested image hash. Composition translates
reader errors to transport errors. Optional `--course-series` on the existing
pilot runner binds these callbacks; it never starts a second simulation loop.

`GET /api/v1/course-ops` uses the existing Manager API envelope. Images use
`GET /api/v1/course-ops/media/{round_id}/{frame_id}.png?sha256={image_sha256}`.
New fields and fixtures
are defined by the course-ops contract, not by components reading report files.

The existing pilot scheduling and whole-course simulation have different
identities. This read-only surface does not claim they execute in one runtime;
no planning acceptance or task receipt changes a V2 robot, ball or staff state.
Execution integration remains a separate reviewed phase.

## Implementation and verification sequence

1. Preserve and test the V2 source snapshot with its existing focused tests.
2. Define one course-ops contract/example and strict TypeScript parser.
3. Add failing projection tests for source integrity, missing legacy fields,
   white-listed output, pause/time handling, snapshot changes and image access.
4. Add failing HTTP tests for optional callbacks, envelopes, media, forbidden
   methods, same-origin restrictions and error classification; bind the runner.
5. Add mounted UI tests for saved/paused evidence, selection, image identity,
   unavailable/stale readings, recovery and teardown; reuse existing styles.
6. Verify against saved artifacts without mutating them. Use a separate short
   test session for any new report fields; never overwrite an old fingerprint.
7. Run full Python/console suites, architecture guards included by the Python
   suite, configuration/build/HTTP checks and repository hygiene. Record browser
   or platform checks not performed without substituting claims.
