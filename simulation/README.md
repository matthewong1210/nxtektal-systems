# NXTektal simulation and Site OS stack

> **Repository orientation:** this directory began as the Virtual Handoff Lab
> and the Python distribution is still named `nxt-sim`, but it now contains the
> whole-site operations simulator and downstream Site OS packages as well. See
> the root [product overview](../README.md), [architecture map](../docs/ARCHITECTURE.md),
> and [milestones](../docs/MILESTONES.md) before reading the phase-specific
> details below.

## Virtual Handoff Lab v0.1

Modular simulation and validation environment for the autonomous ball-collection
robot's **docking, lifting, dumping, collision avoidance, and equipment
compatibility** — scoped to a ~10 m x 10 m handoff/docking zone. This is
deliberately **not** a golf-course digital twin.

Phase 0 (this package) runs entirely **without Isaac Sim or ROS 2**: a mock
adapter executes the full handoff state machine so task logic, configs,
metrics, sweeps, and reports are testable today. Isaac Sim (Phase 1) and the
physical robot's ROS 2 stack (Phase 2) plug in behind the same
`RobotTaskInterface` — see [docs/isaac_sim_integration.md](docs/isaac_sim_integration.md)
and [docs/ros2_integration.md](docs/ros2_integration.md).

**Phase 0.5** adds `nxt_range_ops` — the **Range Operations Training
Environment**: a SimPy discrete-event operational digital twin of a whole
driving range (dispenser → demand → zones → collection → handoff → washing →
dispenser) with a Gymnasium `RangeOpsEnv`, a non-bypassable SafetyShield,
four baseline policies, ten scenario generators, Parquet decision logging,
and an evaluation harness. Install with
`uv sync --frozen --extra range-ops`; see
[docs/range_ops.md](docs/range_ops.md). It reuses only Phase 0's pure
vocabulary (`interfaces/types`, `config/models`) and changes nothing in
`nxt_sim`.

## Site OS layers in this directory

| Package | Responsibility |
|---|---|
| `nxt_range_ops` | Mutable whole-site simulation and the guarded simulator directive path |
| `nxt_facility` | Frozen downstream `FacilityState`, analysis, advice, and briefing |
| `nxt_telemetry` | Observation evidence, synthetic input, state assembly, and quality reporting |
| `nxt_memory` | Append-only historical evidence with no live-loop feedback |
| `nxt_range_twin` | Projection-only FacilityState/layout to USD mapping |
| `nxt_pilot_ops` | Shadow policy evaluation, trace, human workflow, and tamper-evident advisory records |
| `nxt_commissioning` | Immutable physical-site static truth and deterministic one-way projections |
| `nxt_site_runtime` | Input sequencing, publication-quality state envelopes, checkpoint/recovery, and idempotent state-publication coordination |
| `nxt_range_viewer`, `nxt_range_demo` | Deterministic replay export and read-only presentation |

The robot handoff packages and Site OS packages remain separate layers. The
full dependency and truth map is in
[`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Honest-scope disclaimers

* **Every physical value is a placeholder.** No AgileX, basket, or equipment
  specs exist yet. Configs tag each quantity `source: placeholder`; reports
  count them. Results validate the *pipeline*, never the *design*.
  See [docs/assumptions.md](docs/assumptions.md) and
  [docs/missing_inputs.md](docs/missing_inputs.md).
* **Granular physical flow is not simulated.** `nxt_range_ops` conserves integer
  ball inventory around the facility, but ball-to-ball friction, bridging, and
  jamming remain physical-test risks. The mock's unloading model is a configured
  probability stand-in and is labeled as such everywhere.
* **Merged contracts are not live physical integrations.** Commissioning owns
  static facility declarations, and Site Runtime orchestrates sequenced state
  assembly/publication, but only synthetic sources and abstract/test ports exist.
  No physical telemetry transport, vendor adapter, production publisher, command
  admission, automatic robot execution, or real-site service is implemented.
* **Advice is not execution.** Facility recommendations and Shadow Ops records
  are advisory. Native `FacilityState` lacks the ETA, yield, capabilities,
  permission, current demand, and live washer availability needed for autonomous
  collector dispatch, and no LLM participates in command or safety loops.
* **USD is downstream projection only.** Digital-twin output is regenerated from
  declared layout and the FacilityState stream; it is never operational truth or
  policy input, and live Omniverse/Nucleus delivery is not implemented.
* The tipping indicator is a static heuristic (`static_margin_heuristic_v0`),
  not validated dynamics.

## Environment requirements (documented before anything installs)

* Any machine with [uv](https://docs.astral.sh/uv/). `uv sync --frozen` creates a
  **project-local** `simulation/.venv` and downloads three wheels (pydantic,
  PyYAML, pytest — a few MB). If no Python >= 3.11 is present, uv downloads a
  managed CPython (~35 MB) into `~/.local/share/uv`. **No system-level
  software is installed and nothing outside those two directories changes.**
* Isaac Sim / ROS 2 / Docker / NVIDIA drivers are **not** required for
  Phase 0. Check what your machine has (read-only):

```bash
python3 scripts/inspect_environment.py
```

## Quickstart

```bash
cd simulation
uv sync --frozen                            # one-time: project-local .venv

uv run pytest                               # full unit-test suite
uv run python scripts/validate_configs.py   # validate every config
uv run python scripts/run_mock_scenario.py  # one nominal handoff cycle
uv run python scripts/run_mock_scenario.py --scenario configs/scenarios/emergency_stop_demo.yaml
uv run python scripts/run_mock_scenario.py --scenario configs/scenarios/failed_docking_retry_demo.yaml
uv run python scripts/run_docking_sweep.py  # 294-run error sweep -> JSON + CSV
```

Reports land in `reports/` (gitignored).

## Layout

```
configs/            YAML: robot / equipment / safety / scenarios (all placeholder-tagged)
assets/             placeholder slots for supplier CAD/URDF/USD (see per-dir READMEs)
nxt_sim/
  interfaces/       RobotTaskInterface + shared types (no sim, no ROS)
  controllers/      handoff state machine — task logic, simulator-independent
  metrics/          structured metric logging
  reporting/        JSON + CSV writers
  scenarios/        runner + parameter sweeps
  adapters/
    mock/           Phase 0 backend (kinematic, deterministic, seeded)
    isaac_sim/      Phase 1 stub with the planned Isaac mapping
    ros2/           Phase 2 stub with the planned hardware mapping
scripts/            inspect_environment / run_mock_scenario / run_docking_sweep / validate_configs
tests/              unit tests (state machine, configs, retries, e-stop, sweeps)
docs/               architecture, integration plans, assumptions, missing inputs
```

## Architecture rule

`controllers/` (task logic) never imports `adapters/` (simulation). This is
enforced by `tests/test_architecture.py`. The high-level interface —
`navigate_to_pose, approach_handoff_station, dock, verify_docking, lift, dump,
verify_unloading, lower, undock, return_to_charge, emergency_stop,
recover_from_failed_docking` — is the only seam between them, so the same task
code will drive the simulated and the physical robot.

## Equipment compatibility

Each washer/hopper model gets one profile file
(`configs/equipment/*.yaml`) following the schema in
[nxt_sim/config/models.py](nxt_sim/config/models.py) (`EquipmentProfile`):
inlet geometry, approach direction, required dump angle, alignment tolerances,
marker pose, docking guide, safety zone, unloading cycle. Scenarios and sweeps
run unchanged against any profile.

## Parameter sweeps

`configs/scenarios/docking_error_sweep.yaml` sweeps initial lateral /
longitudinal / yaw error. Any dotted config path can be an axis (inlet
height/width, guide geometry, payload, CoM height, ground slope, lift height,
dump angle/speed, sensor mounts, e-stop distance) — examples in that file's
header. Outputs: per-run CSV + full JSON report with docking success rate,
max tolerated lateral/yaw error, collision counts, minimum clearance, cycle
times, recovery rate, e-stop stats, and a failure-reason histogram.
