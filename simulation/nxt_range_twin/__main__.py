# nxt_range_twin/__main__.py
"""CLI: build USD from a captured episode dir (layout.json + facility_states.jsonl)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from nxt_range_twin.overlay import build_episode_layer
from nxt_range_twin.stream import read_jsonl, validate_stream_meta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="default: <episode-dir>/usd")
    args = parser.parse_args()
    layout = json.loads((args.episode_dir / "layout.json").read_text())
    meta = json.loads((args.episode_dir / "stream.meta.json").read_text())
    validate_stream_meta(meta)
    states = read_jsonl(args.episode_dir / "facility_states.jsonl")
    # Manifest input hashes (design §4.4): hand-patched artifacts are detectable
    # because rebuilt customLayerData hashes stop matching the inputs.
    meta = dict(meta)
    meta["input_sha256"] = {
        name: hashlib.sha256((args.episode_dir / name).read_bytes()).hexdigest()
        for name in ("layout.json", "facility_states.jsonl")
    }
    out_dir = args.out or (args.episode_dir / "usd")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(build_episode_layer(layout, states, meta, out_dir))


if __name__ == "__main__":
    main()
