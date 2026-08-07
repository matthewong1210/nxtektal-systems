"""NXTektal Range Operations Demo Replay Exporter.

Read-only consumer of ``nxt_range_ops`` and ``nxt_range_agent``: replays any
benchmark episode deterministically from (scenario, policy, seed) and exports
viewer-ready JSON (layout / episode / benchmark). It never modifies either
protected package, exports only observable state (no hidden simulator
internals unless explicitly requested for debugging), and preserves the
placeholder-provenance disclaimer in every artifact.
"""

from nxt_range_viewer.benchmark_ref import build_benchmark_ref
from nxt_range_viewer.export import export_replay
from nxt_range_viewer.layout import build_layout
from nxt_range_viewer.replay import DEMO_EVENT_KINDS, ReplayResult, replay_episode

VIEWER_VERSION = "0.1.0"

__all__ = [
    "DEMO_EVENT_KINDS",
    "ReplayResult",
    "VIEWER_VERSION",
    "build_benchmark_ref",
    "build_layout",
    "export_replay",
    "replay_episode",
]
