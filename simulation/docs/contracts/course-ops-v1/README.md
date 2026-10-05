# Course Ops v1: saved synthetic evidence

`GET /api/v1/course-ops` returns the existing Manager API envelope with a
`nxt-course-ops/v1` data projection. `snapshot.schema.json` is normative for the
projected data; the fixtures in `examples/` are complete example envelopes.
The entire namespace is read-only. No task, schedule, pause, resume, or physical
control is available. The simulation remains the sole owner of execution.

## Ownership and provenance

The series config/state, selected child config/state, and saved report are read
without modification. JSON records use the existing canonical payload/SHA-256
format. The reader bounds file sizes before parsing, rejects duplicate keys,
nonfinite numbers, symlinks and inconsistent config/cursor/report identities,
and re-reads every source to reject concurrent publication or round switches.
A partially published report returns 503; it never combines two rounds.

`source.series_id` and `source.session_id` are content identities made from the
respective config digest and source engine digest. They are not path identities:
copies of the same experiment deliberately retain the same identity.
`source.report_sha256` identifies the exact saved report payload. An engine
change produces another identity; a reader does not require the old engine to
match its current runtime, and never replays the report to read it.

`generated_at_utc` is when this API projection was read. It is **not** a capture
or publication timestamp. `clock` is the source's simulation clock.
`source.published_at_utc` is null because legacy artifacts do not record wall
publication time. File mtime is never a freshness measurement. `source.live`
is always false; parent and child pause flags are shown separately. A saved
status such as CHUNK_COMPLETE does not imply a process is running now.

## Visible evidence

The 18 holes and 16 carts belong to the fixed synthetic scenario. The map shows
54 declared inspection points, not surveyed geometry or complete camera
coverage; `geometry` is null. A reported map revision must match the fixed
synthetic map contract. Without a bound condition observation the revision is
unknown (null). Cart positions come only from each cart's latest saved frame,
including its simulation minute and accuracy radius; unobserved carts have no
position. Never substitute the exact scenario route or assume a cart at a
checkpoint photographed the entire surrounding area. Checkpoint coverage uses
its latest frame and becomes STALE after 90 simulation minutes.

Only declared frame metadata, pixel detections, validated/bound condition
observations, GroundsWorkflow cases/tasks and simulated staff-job records are
projected. Observation schema/content identity is validated by its existing
telemetry owner; checkpoint/cart/time/image hash must match its referenced
frame. A staff-job record is simulated workflow history, not a named employee
or a promise of current staff capacity. Scores are `NOT_CALIBRATED`, not
probabilities or real-world accuracy. Bounding boxes are pixel `[left, top,
right, bottom]` coordinates.

The new report's optional `observed_range` field exports the existing
`policy_inputs` whitelist: sensed inventory fraction, sensed zone counts,
reported robot activity and sensed batteries, and published joint-operations
staff capacity. The fraction's denominator is the scenario total ball count;
it is not hopper fill percentage. The existing noisy sensor can report a
fraction up to 2. These are synthetic observations. Legacy reports without this
field produce UNKNOWN with null/empty values; there is no fallback to true
`summary.inventory`, ledger, raw robots or zones. Weather is UNKNOWN because
reports currently contain scenario weather truth, not an observed weather feed.
Compiled inputs, future schedules, reference labels, surface assumptions,
weather truth and vision evaluation are excluded.

## Media

`GET /api/v1/course-ops/media/{round_id}/{frame_id}.png?sha256={image_sha256}`
requires exact names and a lowercase 64-digit hash. Only a frame declared by the
current verified report is served. The expected hash must match its declaration
and the actual PNG bytes; older round names return 404. The PNG is limited to
4 MiB and 1024 by 768, must match report dimensions, and its chunks/CRCs are
validated. Paths are descriptor-walked without following symlinks. No compiled
inputs or reference file can be requested. Responses are non-cacheable.

## Errors and limits

Unavailable/invalid/mid-publication sources: 503 `course_ops_unavailable`.
Wrong round/frame/hash: 404 `course_ops_not_found`.
Unconfigured optional endpoint: 404. POST requests: 405; other unsupported
methods retain the server's rejection behavior. JSON files are bounded to
32 MiB; 10,000 frames, 20,000 observation/workflow rows, 100 candidate boxes per
frame, 16 carts, 100 range robots/zones. These are read bounds, not truncation:
over-limit evidence is unavailable rather than silently incomplete.
