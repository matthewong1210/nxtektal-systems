"""Proof that the replay/export layer never touches the protected packages.

The exporter is a read-only consumer of nxt_range_ops and nxt_range_agent:
running a full export must leave both source trees byte-identical.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nxt_range_viewer.export import export_replay

SIMULATION_ROOT = Path(__file__).resolve().parents[2]
PROTECTED = (
    SIMULATION_ROOT / "nxt_range_ops",
    SIMULATION_ROOT / "nxt_range_agent",
)


def _tree_digest(root: Path) -> dict[str, str]:
    digests = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digests[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return digests


def test_export_does_not_modify_protected_packages(tmp_path, short_scenario):
    before = {str(root): _tree_digest(root) for root in PROTECTED}

    export_replay(tmp_path / "out", short_scenario, "random_valid", seed=101)

    after = {str(root): _tree_digest(root) for root in PROTECTED}
    assert after == before
