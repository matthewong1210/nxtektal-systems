# NXTektal Virtual Handoff Lab v0.1

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

## Honest-scope disclaimers

* **Every physical value is a placeholder.** No AgileX, basket, or equipment
  specs exist yet. Configs tag each quantity `source: placeholder`; reports
  count them. Results validate the *pipeline*, never the *design*.
  See [docs/assumptions.md](docs/assumptions.md) and
  [docs/missing_inputs.md](docs/missing_inputs.md).
* **Ball flow is not simulated.** Granular flow, ball-to-ball friction,
  bridging, and jamming are physical-test risks. The mock's unloading model is
  a configured probability stand-in and is labeled as such everywhere.
* The tipping indicator is a static heuristic (`static_margin_heuristic_v0`),
  not validated dynamics.

## Environment requirements (documented before anything installs)

* Any machine with [uv](https://docs.astral.sh/uv/). `uv sync` creates a
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
uv sync                                     # one-time: project-local .venv

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
