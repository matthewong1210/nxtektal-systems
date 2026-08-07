"""Plotly figure builders for the demo viewer — pure functions of bundle data.

Night-ops palette: turf-dark background, range-flag amber for the dispatcher's
decisions, signal green for healthy state, alert red for incidents.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import plotly.graph_objects as go

from nxt_range_demo.bundle import Point, parse_action

TURF_DARK = "#0B1510"
PANEL = "#12211A"
INK = "#E8EDE6"
INK_DIM = "#8FA396"
AMBER = "#F2B93B"
GREEN = "#3FA66A"
RED = "#D6543C"
ROBOT_COLORS = ("#F2B93B", "#6FC2E8", "#E88FD2", "#A8E86F", "#C9A2FF")

_ACTIVITY_INDEX = (
    "idle",
    "traveling",
    "collecting",
    "queued_handoff",
    "unloading",
    "queued_charger",
    "charging",
    "paused",
    "failed",
    "emergency_stopped",
    "awaiting_human",
)
_INCIDENT_ACTIVITIES = {"failed", "emergency_stopped", "awaiting_human"}


def robot_color(index: int) -> str:
    return ROBOT_COLORS[index % len(ROBOT_COLORS)]


def map_bounds(positions: dict[str, Point], pad: float = 25.0) -> tuple[list, list]:
    """Padded ranges, with the y span widened to ~45% of the x span so the
    long, thin driving-range strip still fills the plot at equal aspect."""
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    x_range = [min(xs) - pad, max(xs) + pad]
    y_lo, y_hi = min(ys) - pad, max(ys) + pad
    min_span = 0.45 * (x_range[1] - x_range[0])
    if (y_hi - y_lo) < min_span:
        mid = (y_hi + y_lo) / 2
        y_lo, y_hi = mid - min_span / 2, mid + min_span / 2
    return (x_range, [y_lo, y_hi])


def build_map_figure(
    layout: dict[str, Any],
    frame: dict[str, Any],
    positions: dict[str, Point],
    tracks: list[dict[str, Point]],
    frame_index: int,
    robot_order: list[str],
    trail: int = 25,
    debug_positions: Optional[dict[str, dict[str, float]]] = None,
) -> go.Figure:
    """The facility map at one replay instant."""
    fig = go.Figure()
    x_range, y_range = map_bounds(positions)

    # Distance arcs from the dispenser — driving-range yardage lines.
    dx, dy = positions["dispenser"]
    max_r = max(
        math.hypot(p[0] - dx, p[1] - dy) for p in positions.values()
    )
    radius = 50.0
    while radius <= max_r + 25.0:
        fig.add_shape(
            type="circle",
            x0=dx - radius,
            y0=dy - radius,
            x1=dx + radius,
            y1=dy + radius,
            line={"color": "#1E3327", "width": 1},
            layer="below",
        )
        radius += 50.0

    # Zones: bubbles scaled by current (true) ball count.
    zones = {z["zone_id"]: z for z in frame["zones"]}
    zone_x, zone_y, zone_size, zone_text, zone_line = [], [], [], [], []
    for cfg in layout["zones"]:
        zid = cfg["zone_id"]
        state = zones.get(zid, {})
        balls = state.get("balls", 0)
        is_open = state.get("is_open", True)
        x, y = positions[f"zone:{zid}"]
        zone_x.append(x)
        zone_y.append(y)
        zone_size.append(14 + 2.2 * math.sqrt(max(0, balls)))
        zone_text.append(f"{zid}<br>{balls}")
        zone_line.append(GREEN if is_open else RED)
    fig.add_trace(
        go.Scatter(
            x=zone_x,
            y=zone_y,
            mode="markers+text",
            marker={
                "size": zone_size,
                "color": "rgba(63, 166, 106, 0.18)",
                "line": {"color": zone_line, "width": 1.5},
            },
            text=zone_text,
            textposition="middle center",
            textfont={"color": INK_DIM, "size": 10},
            hovertemplate="zone %{text}<extra>balls on ground</extra>",
            name="zones",
        )
    )

    # Fixed infrastructure.
    stations = {s["station_id"]: s for s in frame["stations"]}
    infra_x, infra_y, infra_text, infra_symbol, infra_color = [], [], [], [], []
    infra_x.append(dx)
    infra_y.append(dy)
    infra_text.append("DISPENSER")
    infra_symbol.append("diamond")
    infra_color.append(AMBER)
    cx, cy = positions["charger"]
    infra_x.append(cx)
    infra_y.append(cy)
    infra_text.append("CHARGER")
    infra_symbol.append("triangle-up")
    infra_color.append(INK_DIM)
    for cfg in layout["stations"]:
        sid = cfg["station_id"]
        x, y = positions[f"station:{sid}"]
        state = stations.get(sid, {})
        infra_x.append(x)
        infra_y.append(y)
        buffer_balls = state.get("buffer_balls", 0)
        infra_text.append(f"{sid} · {buffer_balls}")
        infra_symbol.append("square")
        infra_color.append(GREEN if state.get("is_open", True) else RED)
    fig.add_trace(
        go.Scatter(
            x=infra_x,
            y=infra_y,
            mode="markers+text",
            marker={"size": 13, "symbol": infra_symbol, "color": infra_color},
            text=infra_text,
            textposition="bottom center",
            textfont={"color": INK_DIM, "size": 9},
            hoverinfo="text",
            name="facility",
        )
    )

    # Robot trails then robots on top.
    for idx, rid in enumerate(robot_order):
        color = robot_color(idx)
        start = max(0, frame_index - trail)
        trail_pts = [tracks[i][rid] for i in range(start, frame_index + 1)]
        if len(trail_pts) > 1:
            fig.add_trace(
                go.Scatter(
                    x=[p[0] for p in trail_pts],
                    y=[p[1] for p in trail_pts],
                    mode="lines",
                    line={"color": color, "width": 1.5},
                    opacity=0.35,
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
    robots = {r["robot_id"]: r for r in frame["robots"]}
    for idx, rid in enumerate(robot_order):
        state = robots[rid]
        x, y = tracks[frame_index][rid]
        incident = state["activity"] in _INCIDENT_ACTIVITIES
        fig.add_trace(
            go.Scatter(
                x=[x],
                y=[y],
                mode="markers+text",
                marker={
                    "size": 16,
                    "color": robot_color(idx),
                    "symbol": "circle",
                    "line": {"color": RED if incident else TURF_DARK, "width": 3 if incident else 1},
                },
                text=[rid],
                textposition="top center",
                textfont={"color": INK, "size": 11},
                hovertemplate=(
                    f"{rid} — {state['activity']}<br>"
                    f"battery {state['battery_frac']:.0%} · "
                    f"payload {state['payload_balls']}/{state['payload_capacity_balls']}"
                    "<extra></extra>"
                ),
                showlegend=False,
            )
        )

    # Highlight the dispatcher's current decision on the map.
    action = frame.get("action", {})
    verb, args = parse_action(action.get("name", "wait"))
    target_label = None
    if verb == "assign_collection" and len(args) == 2:
        target_label = f"zone:{args[1]}"
    elif verb == "send_to_charge" and args:
        target_label = "charger"
    elif verb in ("send_to_handoff", "reassign") and args:
        target_label = None  # station picked by the sim; no single target
    if target_label and args and args[0] in robots:
        rx, ry = tracks[frame_index][args[0]]
        tx, ty = positions.get(target_label, (rx, ry))
        dash_color = AMBER if action.get("shield_allowed", True) else RED
        fig.add_trace(
            go.Scatter(
                x=[rx, tx],
                y=[ry, ty],
                mode="lines",
                line={"color": dash_color, "width": 2, "dash": "dot"},
                hoverinfo="skip",
                showlegend=False,
            )
        )

    if debug_positions:
        fig.add_trace(
            go.Scatter(
                x=[p["x_m"] for p in debug_positions.values()],
                y=[p["y_m"] for p in debug_positions.values()],
                mode="markers",
                marker={"size": 8, "symbol": "x", "color": RED},
                name="debug: true positions",
                hovertemplate="hidden sim state<extra>debug</extra>",
            )
        )

    fig.update_layout(
        paper_bgcolor=TURF_DARK,
        plot_bgcolor=TURF_DARK,
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=460,
        showlegend=False,
        xaxis={
            "range": x_range,
            "visible": False,
        },
        yaxis={
            "range": y_range,
            "visible": False,
            "scaleanchor": "x",
            "scaleratio": 1,
        },
    )
    return fig


def build_battery_figure(
    frames: list[dict[str, Any]], robot_order: list[str], current_index: int
) -> go.Figure:
    """Battery per robot across the whole episode, with a now-line."""
    fig = go.Figure()
    steps = [f["step"] for f in frames]
    for idx, rid in enumerate(robot_order):
        series = [
            next(r for r in f["robots"] if r["robot_id"] == rid)["battery_frac"]
            for f in frames
        ]
        fig.add_trace(
            go.Scatter(
                x=steps,
                y=series,
                mode="lines",
                name=rid,
                line={"color": robot_color(idx), "width": 1.5},
            )
        )
    fig.add_vline(x=frames[current_index]["step"], line={"color": INK_DIM, "width": 1, "dash": "dot"})
    fig.update_layout(
        paper_bgcolor=TURF_DARK,
        plot_bgcolor=TURF_DARK,
        font={"color": INK_DIM, "size": 10},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=180,
        yaxis={"range": [0, 1.02], "tickformat": ".0%", "gridcolor": "#1E3327"},
        xaxis={"title": None, "gridcolor": "#1E3327"},
        legend={"orientation": "h", "y": 1.15},
    )
    return fig


def build_activity_figure(
    frames: list[dict[str, Any]], robot_order: list[str], current_index: int
) -> go.Figure:
    """Robot activity bands over the replay timeline (fleet timeline)."""
    z = []
    for rid in robot_order:
        row = []
        for f in frames:
            state = next(r for r in f["robots"] if r["robot_id"] == rid)
            row.append(_ACTIVITY_INDEX.index(state["activity"]))
        z.append(row)
    fig = go.Figure(
        go.Heatmap(
            z=z,
            y=list(robot_order),
            x=[f["step"] for f in frames],
            colorscale=[
                [0.0, "#22352B"],   # idle
                [0.2, "#6FC2E8"],   # traveling
                [0.3, GREEN],       # collecting
                [0.55, "#C9A2FF"],  # handoff/unload/charge queue
                [0.7, AMBER],       # charging/paused
                [1.0, RED],         # failed/estop/awaiting human
            ],
            zmin=0,
            zmax=len(_ACTIVITY_INDEX) - 1,
            showscale=False,
            hovertemplate="step %{x} · %{y}<extra></extra>",
        )
    )
    fig.add_vline(x=frames[current_index]["step"], line={"color": INK, "width": 1})
    fig.update_layout(
        paper_bgcolor=TURF_DARK,
        plot_bgcolor=TURF_DARK,
        font={"color": INK_DIM, "size": 10},
        margin={"l": 10, "r": 10, "t": 10, "b": 10},
        height=120 + 18 * len(robot_order),
        xaxis={"gridcolor": "#1E3327"},
    )
    return fig
