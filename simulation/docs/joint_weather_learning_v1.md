# Joint weather, range and course batch learning V1

Architecture gate: **Proceed** within existing simulation owners. User explicitly
requested accelerated randomized range/course scenarios, shared robot/personnel
dispatch and continuing background learning. This approves SIMULATION only.
Base is local, unmerged `0288e5d`; branch `feature/joint-weather-learning-v1`.

## Boundary card

| Concern | Owner and boundary |
| --- | --- |
| Rain, assumed soil response, customer demand and sampled course inspection demand | Immutable generated scenario inputs in `scripts/joint_scenarios.py`; all parameters hypothetical |
| Conserved balls, fleet execution, shared personnel occupancy | Existing `RangeSimulation` and `BallLedger`; no second mutable runtime |
| Weather collection-access restrictions | Separate admission condition in existing `SafetyShield`; must not erase customer demand or change whether play is open |
| Course inspection labor | Opaque simulation staff jobs admitted through a new closed directive; same personnel pool as robot recovery; no turf diagnosis or physical repair authority |
| Simulator dispatch choices | Existing `nxt_range_ops.policies`, reusing InventoryThresholdPolicy, with visible-state staff assignment; never Site OS advice or LLM calls |
| Offline learning semantics | `nxt_range_ops.evaluation`: finite candidate comparison, paired exogenous demand, disjoint training/validation/test samples and conservative simulation-only selection |
| Durable batch orchestration | `scripts/joint_learning.py`: bounded batches, episode-boundary resume, exclusive lock, atomic evidence, code/config identity, stop switch and bounded artifacts |
| Presentation | Derived offline report; no authoritative state or browser persistence |
| Recurring execution | Codex thread heartbeat running bounded local batches, with explicit machine/app availability limits; no alternate OS scheduler |

No new package, physical device integration, production publisher, LLM training,
or autonomous production-policy promotion. Existing commissioning/map contracts
remain immutable; existing grounds review evidence is not facility truth. Range
and grounds readiness claims remain unchanged. First shared staff roles are
robot recovery and course inspection/rephotography; manual ball collection and
physical turf repair are not introduced or implied.

## Explicit contracts

Optional `RangeOpsEnv(..., joint_inputs=...)` and corresponding simulator setup
receive `nxt-joint-scenario-inputs/v0` plain data:

- `schema`, `environment: SIMULATION`;
- `demand_by_minute`: integer ball-demand samples, indexed from opening minute;
- `collection_blocks`: zone ID to half-open `{start_minute,end_minute}` windows;
- `staff_jobs`: `{job_id,available_minute,duration_minutes,deadline_minute,
  hole_number,checkpoint_id}` inspection jobs, visible only after observation.

The legacy path has no joint inputs and must retain its random trajectory and
action indices. New staff actions append to the catalog. Job durations and soil
thresholds are simulation assumptions, not field-verified capabilities. A staff
job completion records inspection labor, not proof a defect was repaired.

Future demand samples and unforecast shocks never enter policy inputs. Demand
is sampled independently of served-ball landing draws so two policies face the
same exogenous requests. The policy-facing view excludes true inventory,
internal ledger, future jobs and evaluation metrics. Static policy parameters
contain no future unexpected-event timeline. Existing observations and the
hard action mask remain the simulator admission surface.

Weather is generated as coherent day/half-day episodes. Surface water/soil
response is a finite precomputed scenario timeline, with accumulation and
recovery; it is not a live state duplicate. Customer response to rain is an
explicit configurable assumption. The shared weather generates both range
access constraints and course observation/inspection demand.

Learning means offline parameter search and simulation-candidate selection.
Only training results select the finalist. The finalist is frozen before fresh
validation; held-out test results describe generalization and do not select it.
Retain the fixed baseline and current simulation incumbent, show paired business
metrics, failures and worst regimes. Never modify safety thresholds as a learned
action. Learned configuration is applied only at a new simulation episode.

All scenarios, seed streams, policy parameters, selection criteria and code
identity are frozen in batch evidence. Recovery replays an interrupted episode
from its beginning, never fabricates a saved SimPy runtime. Unknown/mismatched
versions, edited result records, overlapping seed splits and stale source
identities fail closed. No operational-memory output feeds the live Site OS.

Validation requires legacy trajectory parity, paired demand equality, access
denial without demand deletion, worker capacity across roles, delayed release
and starvation exposure, hidden-input isolation, deterministic replay, split
isolation, crash/resume/tamper/lock/stop behavior, full Python suite and config
and repository checks. Reports distinguish simulation improvement from real
field forecasting accuracy.

## Running and stopping

Run from `simulation/` using the project's Python environment:

```sh
python -B -m scripts.joint_learning run --state-dir /tmp/joint-learning
python -B -m scripts.joint_learning status --state-dir /tmp/joint-learning
python -B -m scripts.joint_learning pause --state-dir /tmp/joint-learning
python -B -m scripts.joint_learning resume --state-dir /tmp/joint-learning
```

`run` executes at most one batch. A repeated invocation resumes unfinished work
or creates the next batch with fresh seeds. `resume` clears the stop switch;
the next `run` does the work. Pausing takes effect between simulated days, including
during a running batch. Reports update when a worker publishes its next result;
the HTML is a static snapshot, without an editable manager interface.

Outputs include `report.html`, `report.md`, immutable `config.json`, the stop
switch `control.json`, checksummed `state.json`, and per-batch manifests,
episode results, frozen training selections, validation decisions and compact
completion summaries. Manifests include compiled scenario and joint inputs,
not just seed references. Missing controls, incompatible code/dependencies,
changed configuration or mismatched evidence stop execution. Checksums detect
accidental edits; they do not provide an external trust anchor against a complete
rewrite or rollback of the experiment directory.

Defaults use five weather/demand regimes, ten training days, five validation
days and five held-out test days per batch. Six candidates share each training
day. Validation and test compare the frozen finalist, incumbent and fixed
baseline, deduplicating identical candidates. That produces 70–90 policy-days
per complete default batch. An 8-hour simulated day does not wait eight hours.
Every day starts independently; there is no overnight inventory carry-over.

The worker defaults to 100 episode evaluations and 240 seconds per invocation.
Limits and pause are checked between episodes, after bounded scenario compilation;
they are not hard process deadlines. Disk checks can overshoot by the current
episode and publication artifacts. The newest twelve completed batches retain
episode details. Older completed batches retain manifests and compact summaries;
pending evidence is never removed by retention. A 512 MiB experiment limit
eventually stops further work, rather than discarding the remaining audit history.
Failures are recorded and receive at most two attempts per episode.

## Manager assumptions and learning limits

Managers can provide a full configuration via `--config` when creating a new
experiment directory. Copy `DEFAULT_CONFIG` from `scripts/joint_learning.py`;
only documented fields are accepted. Existing experiments cannot be retuned
in place. Supported `assumptions` in `scripts/joint_scenarios.py` include rain
intensity and demand reduction, surprise-demand multiplier, baseline demand,
opening ball stock, staff capacity, robot failures, soil drying, inspection
duration/deadline and observation quality. Values remain explicit assumptions
until calibrated using actual operations. This is file-based configuration;
a manager-facing editing screen is not part of V1.

“Customer demand” currently means requested balls per minute, not measured
people or tee-time bookings. Demand during rain falls under the assumed response
factor. Half-day rain persists through a coherent time window, and the simplified
soil state recovers gradually afterward. Unexpected surges are withheld from
the policy's forecast; the policy responds to current observations and inventory
trends. Changing staff capacity or strategy does not change the sampled demand
the candidates are compared against.

The course has 54 synthetic point checkpoints. Six greens are unobserved by
default; missed, blurred or occluded observations are not reported as healthy.
Each checkpoint releases at most one inspection/rephotography job per day.
This caps the queue for this first experiment. It is not continuous visual
coverage or a validated agronomic model. Soil recovery is independent of job
completion. Jobs are nonpreemptive; unfinished or overdue work remains visible
at closing. Human roles are robot recovery and inspection/rephotography, not
manual ball collection or physical grounds repair.

The six dispatch configurations are fixed, vetted implementations. Learning
selects parameters, not new code, neural-network weights or safety thresholds.
Training chooses a finalist; independent validation can admit it as the next
simulation incumbent. Test results never choose or admit a strategy. The fixed
baseline remains in comparisons, and failed/truncated evaluations are shown as
failures rather than averaged into successful-day performance. A rejected
candidate leaves the incumbent unchanged. More simulated days test these
assumptions more thoroughly; they do not establish accuracy on a real course.

## Recurring operation

The local Codex thread heartbeat runs the same bounded command hourly. It should
honor `control.json`, remain quiet when no meaningful result changes, and surface
a validated incumbent change, failure, version mismatch or exhausted disk budget.
Execution depends on the local host and Codex scheduler being available; it is
not a cloud service or an uninterrupted real-time simulation. No model API calls,
device commands, production configuration changes or package installation are
part of a batch. Pausing the experiment or its heartbeat stops new work.

Verification includes deterministic replay, identical exogenous demand across
strategies, shared staff capacity, weather admission, hidden-future isolation,
failed-day accounting and interruption/resume behavior. The full Python suite,
configuration validator and repository hygiene checker are required before
enabling the recurring run. Visual browser verification is unavailable in this
environment; offline report content and escaping are covered by tests.
