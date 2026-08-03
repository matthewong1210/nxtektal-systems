"""Experiment E1: reproducible benchmark of the existing baseline policies.

Runs every baseline over every scenario with a shared, paired seed list;
aggregates KPIs; ranks policies (direction-aware); identifies failure
scenarios; and writes JSON / Parquet / CSV / Markdown outputs, all
byte-reproducible from the configuration.
"""

from nxt_range_agent.benchmark.aggregate import build_report
from nxt_range_agent.benchmark.config import BenchmarkConfig
from nxt_range_agent.benchmark.outputs import write_outputs
from nxt_range_agent.benchmark.runner import run_benchmark

__all__ = ["BenchmarkConfig", "build_report", "run_benchmark", "write_outputs"]
