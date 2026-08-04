# NXTektal — YC Demo Configuration

> **SIMULATION RESULTS.** Every number in this demo comes from
> placeholder-provenance simulation parameters. Nothing here is, or may be
> presented as, real customer or facility performance.

Fixed, reproducible demo episode (see [`yc_demo.json`](yc_demo.json)):

| | |
|---|---|
| Scenario | `demand_spike` — normal weekday plus an unforecast 2.5× walk-in surge |
| Policy | `demand_forecast_dispatch` — the E1 benchmark's #1-ranked baseline |
| Seed | `101` — replays the exact episode published in the E1 artifacts |
| Playback | 960 simulated steps (06:00–22:00) compressed to ~60 s |

## Regenerate the demo (exact commands, from `simulation/`)

```bash
# 1. Install
uv sync --extra range-ops --extra demo

# 2. (only if reports/range_agent_e1/ is absent) regenerate the benchmark report
uv run --no-sync python -m nxt_range_agent.benchmark --out reports/range_agent_e1

# 3. Export the fixed demo bundle
uv run --no-sync python -m nxt_range_viewer \
  --out reports/demo_bundle \
  --scenario demand_spike --policy demand_forecast_dispatch --seed 101 \
  --benchmark-report reports/range_agent_e1/report.json

# 4. Launch the viewer
uv run --no-sync streamlit run nxt_range_demo/app.py
```

Determinism: the same (scenario, policy, seed) regenerates identical
frames, events, and KPIs every time, on any machine — the only
environment-dependent field is the `git_commit` provenance stamp.

## 90-second demo script

Times are wall-clock from pressing **Play** (60 s playback), with sim times
in parentheses. The five beats: spike → decision → dispatch → recovery →
no stockout.

**0:00–0:15 — Setup.** Sidebar: one facility, 6 zones, 3 robots, 8,000
balls, one operating day. Point at the amber banner: *"everything you'll
see is a simulation result — placeholder parameters, real decision
problem."* Press **Play**.

**0:15–0:31 — Morning (06:00→14:00).** Robots fan out; the dispatcher
panel narrates every decision with its safety-shield verdict; the KPI
ticker climbs; demand is served at 100%.

**0:31–0:36 — THE SPIKE (14:15–15:45). Pause around sim time 15:00.**
An unforecast walk-in group more than doubles demand — ~20 to ~50
balls/min, peaking at 52.8/min at 15:07. The forecast the AI sees
deliberately excludes spikes: it must detect the surge through the
inventory pipeline and react. Watch the dispatcher issue collection
assignments; both robots cycle collect→handoff on the map (a dock failure
at 15:26 is retried and absorbed). Resume.

**0:36–0:45 — Recovery.** Dispenser inventory bottoms out at **1,578
balls at 15:28** — then climbs back above 2,200 by 16:00 and stays
positive to close. The washer loop never starves.

**0:45–0:58 — Optional resilience beat (18:33).** R1 hard-fails mid-zone
(red ring). The dispatcher requests human assistance; R1 is recovered in
12 simulated minutes. One assist all day.

**0:58–1:30 — The receipts.** Let playback finish at 22:00, then:
- **Final summary** tab: **0.0 stockout minutes, 20,751/20,751 balls
  served, 100% service availability**, 15,311 balls processed, one human
  intervention — all labeled SIMULATION RESULTS.
- **Benchmark** tab: this policy ranked #1 across the published 400-episode
  baseline benchmark (10 scenarios × 4 policies × 10 paired seeds), and
  this exact episode is one of those 400 — the animation and the published
  numbers agree to the last digit.

## Claims discipline

- Say: *"simulation results", "placeholder-provenance parameters",
  "reproducible from a seed", "the decision problem is real."*
- Never say or imply: real customer data, real facility throughput,
  validated hardware performance, or field-measured costs.
