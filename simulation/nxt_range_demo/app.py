"""NXTektal Range Operations — Demo Viewer (Streamlit).

Read-only replay of one exported simulation episode. Launch:

    uv run --no-sync streamlit run nxt_range_demo/app.py -- <bundle-dir>

or set NXT_DEMO_BUNDLE. Every number shown is a SIMULATION RESULT from
placeholder-provenance parameters — never real facility performance.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import streamlit as st

# `streamlit run nxt_range_demo/app.py` puts the script's own directory on
# sys.path, not the project root — make the package importable either way.
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from nxt_range_demo.bundle import (
    compute_robot_tracks,
    load_bundle,
    node_positions,
    parse_action,
    sim_clock,
)
from nxt_range_demo.charts import (
    AMBER,
    GREEN,
    INK_DIM,
    PANEL,
    RED,
    build_activity_figure,
    build_battery_figure,
    build_map_figure,
    robot_color,
)

DEFAULT_BUNDLE = "reports/demo_bundle"
PLAYBACK_SECONDS = {"60 s": 60, "90 s": 90, "30 s": 30}
TICKS_PER_SECOND = 8


def resolve_bundle_dir() -> Path:
    env = os.environ.get("NXT_DEMO_BUNDLE")
    if env:
        return Path(env)
    # `streamlit run app.py -- <dir>` passes the dir through sys.argv; only
    # trust it when it actually looks like a bundle (other harnesses, e.g.
    # pytest, put unrelated values in argv).
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1])
        if (candidate / "episode.json").exists():
            return candidate
    return Path(DEFAULT_BUNDLE)


@st.cache_data(show_spinner="Loading demo bundle…")
def load(bundle_dir: str):
    bundle = load_bundle(bundle_dir)
    positions = node_positions(bundle.layout)
    tracks = compute_robot_tracks(bundle.episode, positions)
    return bundle, positions, tracks


def banner(disclaimer: str) -> None:
    st.markdown(
        f"""<div style="background:{AMBER}; color:#141414; padding:6px 14px;
        border-radius:4px; font-weight:700; font-size:0.82rem;
        letter-spacing:0.06em;">SIMULATION RESULTS — {disclaimer}</div>""",
        unsafe_allow_html=True,
    )


def clock_html(t_s: float) -> str:
    return (
        f'<div style="text-align:right; font-family:ui-monospace,monospace;">'
        f'<span style="font-size:2.6rem; font-weight:700; color:{AMBER};">'
        f"{sim_clock(t_s)}</span>"
        f'<span style="color:{INK_DIM}; font-size:0.8rem;"> sim time</span></div>'
    )


def action_panel(frame: dict) -> None:
    action = frame.get("action", {})
    name = action.get("name", "wait")
    verb, args = parse_action(name)
    allowed = action.get("shield_allowed", True)
    verdict_color = GREEN if allowed else RED
    verdict = "allowed" if allowed else f"blocked — {action.get('shield_reason', '')}"
    pretty = verb.replace("_", " ")
    detail = f" · {', '.join(args)}" if args else ""
    st.markdown(
        f"""<div style="background:{PANEL}; border-left:3px solid {verdict_color};
        padding:10px 14px; border-radius:4px;">
        <div style="color:{INK_DIM}; font-size:0.7rem; letter-spacing:0.1em;
        text-transform:uppercase;">dispatcher decision</div>
        <div style="font-size:1.05rem; font-weight:600;">{pretty}{detail}</div>
        <div style="color:{verdict_color}; font-size:0.8rem;">shield: {verdict}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def kpi_ticker(frame: dict) -> None:
    kpi = frame["kpi"]
    served = kpi["demand_balls_served"]
    total = max(1, kpi["demand_balls_total"])
    inventory = frame["inventory"]["dispenser_balls"]
    a, b = st.columns(2)
    a.metric("Dispenser", f"{inventory:,}")
    b.metric("Served", f"{served / total:.0%}")
    c, d = st.columns(2)
    c.metric("Collected", f"{kpi['balls_collected']:,}")
    d.metric("Processed", f"{kpi['balls_processed']:,}")
    e, f = st.columns(2)
    e.metric("Stockout min", f"{kpi['stockout_minutes']:.0f}")
    f.metric("Assists", f"{kpi['human_interventions']}")
    st.caption("SIMULATION RESULTS — cumulative since 06:00")


def fleet_strip(frame: dict, robot_order: list[str]) -> None:
    for idx, rid in enumerate(robot_order):
        state = next(r for r in frame["robots"] if r["robot_id"] == rid)
        color = robot_color(idx)
        payload = state["payload_balls"] / state["payload_capacity_balls"]
        st.markdown(
            f'<span style="color:{color}; font-weight:700;">{rid}</span> '
            f'<span style="color:{INK_DIM}; font-size:0.8rem;">'
            f"{state['activity']} · battery {state['battery_frac']:.0%} · "
            f"payload {payload:.0%}</span>",
            unsafe_allow_html=True,
        )


def recent_events(events: list[dict], t_s: float, window_s: float = 1800.0) -> list[dict]:
    return [e for e in events if t_s - window_s <= e["t_s"] <= t_s]


def main() -> None:
    st.set_page_config(
        page_title="NXTektal Range Ops — Simulation Replay",
        page_icon="⛳",
        layout="wide",
    )
    bundle_dir = resolve_bundle_dir()
    if not (Path(bundle_dir) / "episode.json").exists():
        st.error(
            f"No demo bundle at `{bundle_dir}`. Export one first:\n\n"
            "```\nuv run --no-sync python -m nxt_range_viewer "
            "--out reports/demo_bundle --scenario demand_spike "
            "--policy demand_forecast_dispatch --seed 101 "
            "--benchmark-report reports/range_agent_e1/report.json\n```"
        )
        st.stop()
    bundle, positions, tracks = load(str(bundle_dir))
    layout, episode = bundle.layout, bundle.episode
    meta, frames, events = episode["meta"], episode["frames"], episode["events"]
    robot_order = [r["robot_id"] for r in layout["robots"]]
    n = len(frames)

    if "frame_idx" not in st.session_state:
        st.session_state.frame_idx = 0
    if "playing" not in st.session_state:
        st.session_state.playing = False

    banner(meta["disclaimer"])

    # Header: identity left, sim clock right.
    head_l, head_r = st.columns([3, 1])
    with head_l:
        st.title("NXTektal Range Operations")
        st.caption(
            f"AI dispatcher replay — scenario **{meta['scenario']}** · policy "
            f"**{meta['policy']}** · seed {meta['seed']} · simulator "
            f"v{meta['simulator_version']} @ {meta['git_commit']}"
        )
    clock_slot = head_r.empty()

    # Sidebar: facility overview + options.
    with st.sidebar:
        st.subheader("Facility")
        hours = layout["hours"]
        st.markdown(
            f"- **{len(layout['zones'])}** collection zones\n"
            f"- **{len(robot_order)}** robots · "
            f"**{len(layout['stations'])}** handoff station(s)\n"
            f"- **{layout['total_balls']:,}** balls in circulation\n"
            f"- open {sim_clock(hours['open_minute'] * 60)}"
            f"–{sim_clock(hours['close_minute'] * 60)}"
        )
        st.caption(layout.get("description", ""))
        duration_label = st.radio("Playback length", list(PLAYBACK_SECONDS), index=0)
        show_debug = False
        if meta.get("includes_debug_state"):
            show_debug = st.toggle(
                "Show debug state (hidden simulator internals)", value=False
            )
        st.caption(
            "SIMULATION RESULTS from placeholder-provenance parameters — "
            "not real facility performance."
        )

    stride = max(1, round(n / (PLAYBACK_SECONDS[duration_label] * TICKS_PER_SECOND)))

    # Playback controls.
    c_play, c_slider = st.columns([1, 6])
    with c_play:
        if st.session_state.playing:
            if st.button("Pause", use_container_width=True):
                st.session_state.playing = False
                st.rerun()
        else:
            label = "Replay" if st.session_state.frame_idx >= n - 1 else "Play"
            if st.button(label, type="primary", use_container_width=True):
                if st.session_state.frame_idx >= n - 1:
                    st.session_state.frame_idx = 0
                st.session_state.playing = True
                st.rerun()
    with c_slider:
        st.session_state.frame_idx = st.slider(
            "timeline",
            0,
            n - 1,
            st.session_state.frame_idx,
            label_visibility="collapsed",
        )

    # Live panels (placeholders so the play loop can update them in place).
    map_col, rail_col = st.columns([5, 2])
    map_slot = map_col.empty()
    with rail_col:
        action_slot = st.empty()
        kpi_slot = st.container()
    ticker_slot = kpi_slot.empty()
    fleet_slot = rail_col.empty()

    def render(idx: int) -> None:
        frame = frames[idx]
        clock_slot.markdown(clock_html(frame["t_s"]), unsafe_allow_html=True)
        debug_positions = (
            frame.get("debug", {}).get("robot_positions") if show_debug else None
        )
        map_slot.plotly_chart(
            build_map_figure(
                layout,
                frame,
                positions,
                tracks,
                idx,
                robot_order,
                debug_positions=debug_positions,
            ),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        with action_slot.container():
            action_panel(frame)
        with ticker_slot.container():
            kpi_ticker(frame)
        with fleet_slot.container():
            fleet_strip(frame, robot_order)

    if st.session_state.playing:
        idx = st.session_state.frame_idx
        tick = 1.0 / TICKS_PER_SECOND
        while st.session_state.playing and idx < n:
            start = time.monotonic()
            render(idx)
            st.session_state.frame_idx = idx
            idx += stride
            elapsed = time.monotonic() - start
            if elapsed < tick:
                time.sleep(tick - elapsed)
        st.session_state.frame_idx = n - 1
        st.session_state.playing = False
        st.rerun()
    else:
        render(st.session_state.frame_idx)

    # Below the fold: timeline analytics and wrap-up.
    tab_fleet, tab_events, tab_summary, tab_benchmark = st.tabs(
        ["Fleet timeline", "Events", "Final summary", "Benchmark"]
    )
    with tab_fleet:
        st.caption("Robot activity across the operating day — SIMULATION RESULTS")
        st.plotly_chart(
            build_activity_figure(frames, robot_order, st.session_state.frame_idx),
            use_container_width=True,
            config={"displayModeBar": False},
        )
        st.plotly_chart(
            build_battery_figure(frames, robot_order, st.session_state.frame_idx),
            use_container_width=True,
            config={"displayModeBar": False},
        )
    with tab_events:
        st.caption("Operational callouts captured by the simulator")
        if not events:
            st.markdown("No incident events in this episode.")
        for event in events:
            payload = ", ".join(f"{k}={v}" for k, v in event["payload"].items())
            st.markdown(
                f"`{sim_clock(event['t_s'])}` **{event['kind']}**"
                + (f" — {payload}" if payload else "")
            )
    with tab_summary:
        st.subheader("Final summary")
        st.caption(
            "SIMULATION RESULTS — one full operating day, "
            f"terminated: {meta['termination_reason']}"
        )
        kpis = meta["kpis"]
        rows = [
            ("Service availability", f"{kpis['service_availability']:.1%}"),
            ("Stockout minutes", f"{kpis['stockout_minutes']:.1f}"),
            ("Balls processed", f"{kpis['balls_processed']:,.0f}"),
            ("Human interventions", f"{kpis['human_interventions']:.0f}"),
            ("Energy (Wh)", f"{kpis['energy_wh']:,.0f}"),
            ("Robot utilization", f"{kpis['robot_utilization']:.1%}"),
            ("Est. operating cost", f"{kpis['estimated_operating_cost']:,.2f}"),
            ("Episode reward", f"{meta['reward_total']:,.1f}"),
        ]
        left, right = st.columns(2)
        for i, (label, value) in enumerate(rows):
            (left if i % 2 == 0 else right).metric(label, value)
    with tab_benchmark:
        if bundle.benchmark is None:
            st.markdown("No benchmark.json in this bundle.")
        else:
            bm = bundle.benchmark
            st.caption(
                "Published baseline benchmark (SIMULATION RESULTS) — "
                f"{len(bm['scenarios'])} scenarios × {len(bm['policies'])} "
                f"policies × {len(bm['seeds'])} paired seeds"
            )
            st.dataframe(
                bm["rankings"]["overall"],
                use_container_width=True,
                hide_index=True,
            )
            for row in bm["rankings"].get("per_scenario", []):
                if row.get("scenario") == meta["scenario"]:
                    st.markdown(
                        f"**{meta['scenario']}** winner: `{row['winner']}`"
                    )

    st.caption(
        f"Bundle: `{bundle.path}` · viewer v{meta['viewer_version']} · "
        "All metrics on this page are SIMULATION RESULTS."
    )


main()
