"""Machine-readable decision logging for offline training.

Every decision transition is one row: episode identity (episode id,
simulator version, policy version, git commit, seed), the simulated
timestamp, the observation the decision was made on, the valid-action mask,
the selected action, the reward decomposition, the task (shield) result, the
next-state summary, and the termination reason.

Datasets are written as Parquet (one file per episode) with nested/variable
structures JSON-encoded per cell — a stable, schema-simple layout for later
offline-RL ingestion. Episode summaries are JSON. No wall-clock values are
written, so logs are byte-reproducible from (scenario, policy, seed).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np

from nxt_range_ops import SIMULATOR_VERSION

_GIT_COMMIT: Optional[str] = None


def current_git_commit() -> str:
    global _GIT_COMMIT
    if _GIT_COMMIT is None:
        try:
            _GIT_COMMIT = (
                subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=Path(__file__).resolve().parent,
                    check=True,
                ).stdout.strip()
                or "unknown"
            )
        except Exception:
            _GIT_COMMIT = "unknown"
    return _GIT_COMMIT


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class EpisodeLogger:
    """Buffers one episode's decision transitions, then writes files."""

    def __init__(
        self,
        out_dir: str | Path,
        episode_id: str,
        scenario_name: str,
        policy_name: str,
        policy_version: str,
        seed: int,
    ):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.episode_id = episode_id
        self.scenario_name = scenario_name
        self.policy_name = policy_name
        self.policy_version = policy_version
        self.seed = int(seed)
        self.git_commit = current_git_commit()
        self._rows: list[dict[str, Any]] = []

    def log_step(
        self,
        step: int,
        t_s: float,
        observation: dict[str, Any],
        action_mask: np.ndarray,
        action: int,
        action_name: str,
        task_result: dict[str, Any],
        reward_total: float,
        reward_components: dict[str, float],
        next_state: dict[str, Any],
        terminated: bool,
        truncated: bool,
        termination_reason: Optional[str],
    ) -> None:
        self._rows.append(
            {
                "episode_id": self.episode_id,
                "simulator_version": SIMULATOR_VERSION,
                "policy_name": self.policy_name,
                "policy_version": self.policy_version,
                "git_commit": self.git_commit,
                "seed": self.seed,
                "step": int(step),
                "t_s": float(t_s),
                "observation_json": json.dumps(_jsonable(observation), sort_keys=True),
                "action_mask_json": json.dumps(
                    np.asarray(action_mask, dtype=bool).astype(int).tolist()
                ),
                "action": int(action),
                "action_name": action_name,
                "task_result_json": json.dumps(_jsonable(task_result), sort_keys=True),
                "reward_total": float(reward_total),
                "reward_components_json": json.dumps(
                    _jsonable(reward_components), sort_keys=True
                ),
                "next_state_json": json.dumps(_jsonable(next_state), sort_keys=True),
                "terminated": bool(terminated),
                "truncated": bool(truncated),
                "termination_reason": termination_reason or "",
            }
        )

    @property
    def n_rows(self) -> int:
        return len(self._rows)

    def rows(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def finalize(
        self, final_metrics: dict[str, Any], termination_reason: Optional[str]
    ) -> dict[str, Path]:
        """Write the Parquet decision log and JSON episode summary."""
        paths: dict[str, Path] = {}
        dataset_path = self.out_dir / f"{self.episode_id}.parquet"
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(self._rows)
            pq.write_table(table, dataset_path)
        except ImportError:  # pragma: no cover - pyarrow is an extra
            dataset_path = self.out_dir / f"{self.episode_id}.jsonl"
            with dataset_path.open("w") as fh:
                for row in self._rows:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")
        paths["dataset"] = dataset_path

        summary = {
            "episode_id": self.episode_id,
            "scenario": self.scenario_name,
            "policy_name": self.policy_name,
            "policy_version": self.policy_version,
            "simulator_version": SIMULATOR_VERSION,
            "git_commit": self.git_commit,
            "seed": self.seed,
            "n_steps": len(self._rows),
            "termination_reason": termination_reason,
            "final_metrics": _jsonable(final_metrics),
            "disclaimer": (
                "Placeholder-driven simulation output; does NOT represent "
                "real facility performance."
            ),
        }
        summary_path = self.out_dir / f"{self.episode_id}.summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True))
        paths["summary"] = summary_path
        return paths
