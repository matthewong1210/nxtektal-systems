# NXTektal — YC Demo Configuration

> **SIMULATION RESULTS.** Every number in this demo comes from
> placeholder-provenance simulation parameters. Nothing here is, or may be
> presented as, real customer or facility performance.

Fixed, reproducible demo episode (see [`yc_demo.json`](yc_demo.json)):

| | |
|---|---|
| Scenario | `demand_spike` — normal weekday plus an unforecast 2.5× surge, 14:00–15:30 |
| Policy | `demand_forecast_dispatch` — a **forecast-based autonomous dispatch policy** (rule-based baseline, not a learned or trained model); #1-ranked in the E1 baseline benchmark |
| Seed | `101` — replays the exact episode published in the E1 artifacts |
| Playback | 960 simulated steps (06:00–22:00) at 6 ticks/s — the "60 s" setting completes in ~53 s (6 ticks/s is the recording-stable rate; higher rates can intermittently blank Plotly frames) |

The `nxt_range_ops` environment is built to **train and evaluate learned
policies**; this demo replays its strongest rule-based baseline.

## Regenerate the demo (exact commands, from `simulation/`)

```bash
# 1. Install
uv sync --frozen --extra range-ops --extra demo

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

## Recording timestamps (exact, from episode.json)

Wall-clock times assume the "60 s" playback setting (6 ticks/s, frame
stride 3, ~53 s total).

| Beat | Sim time | Frame | Wall clock |
|---|---|---|---|
| Configured demand spike begins (2.5×, config window 14:00–15:30) | 14:00 | 479 | ~26.6 s |
| Post-spike autonomous dispatch action (`assign_collection(R2,Z3)`, R2 idle→traveling) | 14:20 | 499 | ~27.7 s |
| Docking failure (R2 at H1, retried and absorbed) | 15:26 | 566 | ~31.4 s |
| Inventory trough (1,578 balls) | 15:28 | 567 | ~31.5 s |
| Inventory recovery (back to 2,200) | 15:54 | 593 | ~32.9 s |
| Final summary (facility closes) | 22:00 | 959 | ~53.3 s |

The spike window comes from the scenario configuration
(`start_minute=840, end_minute=930` → 14:00–15:30); it is exogenous
configuration, not a value smoothed or detected from episode data. No
individual dispatch action is narrated as a response to the spike; the
approved claim is that the policy responds to changing facility state.

## 90-second demo script

**0:00–0:15 — Setup.** Sidebar: one facility, 6 zones, 3 robots, 8,000
balls, one operating day, one forecast-based autonomous dispatch policy.
Point at the amber banner: *"everything you'll see is a simulation result —
placeholder parameters, real decision problem."* Press **Play**.

**0:15–0:27 — Morning (06:00→14:00).** Robots fan out; the decision panel
narrates each autonomous dispatch decision with its safety-shield verdict;
the KPI ticker climbs; demand is served at 100%.

**0:27–0:32 — THE SPIKE (14:00–15:30). Pause around sim time 14:20.**
*"The unforecast demand spike begins at 2:00 PM"* — demand multiplies
2.5×, ~20 to ~50 balls/min, peaking at 52.8/min at 15:07. The forecast
the policy sees deliberately excludes spikes. *"The forecast-based
autonomous dispatch policy responds to changing facility state"*: at
14:20 its own public observation showed a sensed pipeline of ~2,400 balls
against a ~2,562-ball forecast — a shortfall — and it assigned R2 to
collect in Z3 (`assign_collection(R2,Z3)`, safety shield: allowed).
Both robots cycle collect→handoff on the map (a docking failure at 15:26
is retried and absorbed). Resume.

**0:32–0:40 — Recovery.** Dispenser inventory bottoms out at **1,578
balls at 15:28** — then climbs back to 2,200 by 15:54 and stays positive
to close. *"The policy maintains service through the simulated
disruption."*

**0:40–0:50 — Optional resilience beat (18:33).** R1 hard-fails mid-zone
(red ring). The policy requests human assistance; R1 is recovered in 12
simulated minutes — the episode's single human assist.

**0:50–1:30 — The receipts.** Let playback finish at 22:00, then:
- **Final summary** tab: *"The episode ends with zero simulated stockout
  minutes"* — 20,751/20,751 demanded balls served, 100% service
  availability, 15,311 balls processed, exactly one human assist — all
  labeled SIMULATION RESULTS.
- **Benchmark** tab: this policy ranked #1 across the published 400-episode
  baseline benchmark (10 scenarios × 4 policies × 10 paired seeds), and
  this exact episode is one of those 400 — the animation and the published
  numbers agree to the last digit.

## Claims discipline

- Approved claim forms (use these, verbatim or near-verbatim):
  - *"The unforecast demand spike begins at 2:00 PM."* (config window
    14:00–15:30)
  - *"The forecast-based autonomous dispatch policy responds to changing
    facility state."*
  - *"The policy maintains service through the simulated disruption."*
  - *"The episode ends with zero simulated stockout minutes."*
  Plus factual framing: *"simulation results", "placeholder-provenance
  parameters", "reproducible from a seed", "the environment is built to
  train and evaluate learned policies."*
- Never say or imply: real customer data, real facility throughput,
  validated hardware performance, field-measured costs, that this policy
  is a learned/trained model, that the policy predicted the unforecast
  spike, or that any single dispatch action was caused by the spike.
- **No "avoided X" claims.** Same-seed runs of different policies do not
  share an identical exogenous event schedule (demand realization and
  failure/docking events diverge with policy execution), so counterfactual
  "this policy avoided a stockout" statements are not supported. For
  cross-policy comparisons, cite the paired-seed E1 benchmark statistics.
