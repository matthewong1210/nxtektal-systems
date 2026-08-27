# Investor demo guide

The recommended demo shows one reproducible facility operating day and keeps
the product layers visible:

1. the operations simulator produces a changing site;
2. `FacilityState` captures the whole facility at each step;
3. deterministic AI-operations logic produces a synchronized manager briefing;
4. the viewer presents the operating day; and
5. the digital-twin builder can project the same state stream into USD.

Every number is a **simulation result from placeholder-provenance parameters**.
The demo proves system architecture, reproducibility, and decision trace—not
real-site performance.

Digital Twin Phase 0, Shadow Ops, Commissioning, and Site Runtime are merged,
but this verified investor path remains intentionally simulation-first. It does
not connect Site Runtime to physical sensors, exercise a vendor integration,
admit a physical command, or execute a robot automatically. Shadow Ops remains
advisory; its native `FacilityState` adapter lacks collector ETA, yield,
capabilities, collection permission, current demand, and live washer
availability, so it cannot support autonomous collector dispatch without new
provenance-bearing inputs.

## Quick start

From the repository root:

```bash
cd simulation
uv sync --frozen --extra range-ops --extra demo

# Generate the benchmark used by the viewer if it is not already present.
uv run --no-sync python -m nxt_range_agent.benchmark \
  --out reports/range_agent_e1

# Capture canonical state and the synchronized briefing sidecar.
uv run --no-sync python scripts/facility_twin_capture.py \
  --scenario demand_spike --policy demand_forecast_dispatch --seed 101

# Export the read-only viewer bundle from the same seeded episode.
uv run --no-sync python -m nxt_range_viewer \
  --out reports/demo_bundle \
  --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \
  --benchmark-report reports/range_agent_e1/report.json

# Launch locally at http://localhost:8501.
uv run --no-sync streamlit run nxt_range_demo/app.py
```

The viewer automatically looks for the generated briefing sidecar at
`reports/demo/demand_spike-seed101/briefings.jsonl`. All generated reports are
gitignored.

## Optional browser story

The recovered Operational Replay app is a separate read-only presentation over
the facility-twin capture files and synchronized briefing sidecar. This is not
the `nxt_range_viewer` bundle: the viewer exports `episode.json`, `layout.json`,
and optional `benchmark.json`. The browser app does not replace that viewer,
run policy logic, or mutate the selected files.

```bash
cd apps/operational-replay
npm ci
npm run dev
```

Open `http://localhost:3000`, choose **Load artifacts**, and first select the
capture's `events.jsonl`, `facility_states.jsonl`, and `layout.json`. Choose
**Load artifacts** again to add the separately stored
`reports/demo/<episode>/briefings.jsonl` sidecar in page memory. Selecting a new
`events.jsonl` starts a new bundle; no source file is copied or modified. The
app reports missing or contract-invalid inputs, keeps advisory outputs
separate, and never interpolates robot movement. `stream.meta.json` is outside
the v1 input contract; selected-file scenario and seed checks are not
cryptographic proof that the separately selected files form one episode.

For the prepared 60–90 second narration and exact event timestamps, use
[`simulation/nxt_range_demo/YC_DEMO.md`](../simulation/nxt_range_demo/YC_DEMO.md).

## What to point out

- **One site state:** demand, inventory, washer, fleet, charging, staff, zones,
  and stations appear in one operational model.
- **Intelligence, not teleoperation:** the briefing explains current conditions
  and recommended priorities; it is not a robot command console.
- **Safety stays in execution:** simulator actions are checked again by the
  non-bypassable `SafetyShield`.
- **One episode, bounded consumers:** briefing, memory, and twin consume
  `FacilityState`-derived artifacts, while the viewer independently replays the
  same seeded public environment. None becomes a competing truth store.
- **Reproducible evidence:** scenario, policy, seed, simulator version, and Git
  commit are recorded with the artifacts.

## Optional USD projection

The twin package uses the locked `usd-core==26.8` optional dependency. Install
the complete lock-consistent environment before running this projection:

```bash
uv sync --locked --all-extras

uv run --no-sync python -m nxt_range_twin \
  --episode-dir reports/digital_twin/sim-baseline/dev/demand_spike-seed101
```

The output is
`reports/digital_twin/sim-baseline/dev/demand_spike-seed101/usd/episode.usda`.
It is a projection of `facility_states.jsonl` and `layout.json`, never a source
of facility state.

## Approved claims

- “The unit of autonomy is the site, not the robot.”
- “NXTektal coordinates demand, inventory, collection, handoff, washing,
  charging, staffing, and safety as one operating system.”
- “The policy responds to changing simulated facility state.”
- “The same versioned state drives the operational briefing and spatial
  projection.”
- “The episode and its decision evidence are reproducible from a seed.”

Do not claim real customer data, field-measured performance, deployed robot
control, physical Site Runtime connectivity, a trained demo policy, spike
prediction, or causal “avoided X” results. Do not imply that USD, the viewer,
or a briefing is operational truth.
