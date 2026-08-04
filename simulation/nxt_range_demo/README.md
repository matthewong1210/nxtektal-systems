# NXTektal Range Ops — Demo Viewer

A read-only Streamlit replay of one simulated operating day: the AI
dispatcher's decisions, the robot fleet on a 2D facility map, and the
resulting KPIs — built for a 60–90 second YC application demonstration.

> **All numbers shown are SIMULATION RESULTS** from placeholder-provenance
> parameters. They exercise the decision problem and the pipeline only and
> must not be presented as real facility performance.

No backend, no database, no authentication. The app only reads a demo
bundle exported by `nxt_range_viewer`; it never imports the simulator.

## Architecture

```
nxt_range_ops                      nxt_range_agent
(SimPy + Gymnasium simulator)      (E1 baseline benchmark)
        │                                  │
        │  RangeOpsEnv step/info dicts     │  report.json (rankings)
        ▼                                  │
nxt_range_viewer  ◄────────────────────────┘
(deterministic replay exporter — python -m nxt_range_viewer)
        │
        ▼
demo bundle (three static JSON files)
  ├── layout.json     static facility geometry
  ├── episode.json    per-step frames, actions, rewards, events, KPI ticks
  └── benchmark.json  published benchmark rankings, reused verbatim
        │
        ▼
nxt_range_demo (this package — Streamlit, read-only)
```

Replays are deterministic: the same (scenario, policy, seed) always
produces a byte-identical bundle, and seed 101 of `demand_spike ×
demand_forecast_dispatch` reproduces the exact episode published in the E1
benchmark artifacts.

## Quick start

From the `simulation/` directory:

```bash
# 1. Install (one-time): simulator + demo extras
uv sync --extra range-ops --extra demo

# 2. Export a demo bundle (deterministic; replays a published E1 episode)
uv run --no-sync python -m nxt_range_viewer \
  --out reports/demo_bundle \
  --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \
  --benchmark-report reports/range_agent_e1/report.json

# 3. Launch the viewer
uv run --no-sync streamlit run nxt_range_demo/app.py
```

Streamlit opens at <http://localhost:8501>. Press **Play** — the default
playback compresses the 06:00–22:00 operating day (960 control steps) into
about 60 seconds.

To view a different bundle:

```bash
uv run --no-sync streamlit run nxt_range_demo/app.py -- path/to/bundle
```

or set `NXT_DEMO_BUNDLE=path/to/bundle`.

## Suggested 60–90 s demo script

1. **Facility overview** (sidebar): zones, robots, stations, one day, one
   AI dispatcher.
2. Press **Play**: robots fan out on the map; the sim clock races through
   the day; the dispatcher panel narrates each decision and its safety-shield
   verdict; the KPI ticker climbs.
3. Pause on an incident (Events tab lists them with timestamps — robot
   failure → human intervention → recovery).
4. **Final summary** tab: full-day KPIs (availability, stockouts,
   interventions, cost).
5. **Benchmark** tab: the published 400-episode baseline ranking this
   policy came from.

## What's in a bundle

| File | Contents |
|---|---|
| `layout.json` | Static facility geometry (zones, stations, charger, dispenser, robot roster) |
| `episode.json` | Per-step frames: robot states, zone/inventory state, actions + shield verdicts, rewards, KPI ticks, incident events |
| `benchmark.json` | Ranking data reused verbatim from the E1 benchmark report |

Hidden simulator internals are excluded from bundles by default; if a bundle
was exported with `--debug`, the viewer keeps that overlay off unless the
sidebar toggle is explicitly enabled.

## Known limitations

- **Simulation only.** Every number is a SIMULATION RESULT from
  placeholder-provenance parameters (26 per scenario, all tagged) — no real
  facility data exists anywhere in this pipeline.
- Robot map positions are interpolated from symbolic node labels; the
  viewer's straight-line travel matches the simulator's own linear travel
  model but is not a physical trajectory.
- During playback the map re-renders ~8×/s server-side; on slow machines
  this can flash. Paused frames are always fully rendered; lower
  `TICKS_PER_SECOND` in `app.py` if needed.
- The play loop holds one Streamlit script run open; it is a single-viewer
  local demo tool by design, not a hosted dashboard.
- Editing `charts.py`/`bundle.py` requires a server restart — Streamlit
  hot-reloads only the entry script.

## Tests

```bash
uv run --no-sync python -m pytest tests/range_demo -q
```
