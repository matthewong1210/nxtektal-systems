"""NXTektal Range Operations Agent layer (Phase 1).

The intelligence layer above the Phase 0.5 Range Operations Training
Environment: benchmarking, policy tuning, training, and evaluation of
*operational* decisions (dispatch, zoning, unload, charge, wait, human
assistance). This package consumes ``nxt_range_ops`` strictly read-only —
it never modifies the simulator, the environment, or the baseline policies,
and it must never import from robot execution code (``nxt_sim``) directly.

Everything computed here inherits the Phase 0/0.5 placeholder-provenance
policy: results exercise the decision problem and the pipeline only and must
never be presented as real facility performance.
"""

AGENT_VERSION = "0.1.0"
