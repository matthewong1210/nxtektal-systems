# Human-led planning v1 validation record

Base: `4f1fa803e5ea1f9dc5f955e85823a436c3bc86bc`.
All results below are local observed results, not CI or field evidence.
Commands run from `simulation/` unless stated otherwise. The environment was
provisioned with `uv sync --locked --all-extras` (including USD and MQTT).
`UV_CACHE_DIR=/private/tmp/nxtektal-uv-cache` was supplied locally because the
host's default cache is outside the sandbox. Loopback/MQTT tests require host
permission; an initial sandbox-only run had socket permission errors, then the
same suite passed with local test socket access.

## Stage 1: manager response commit receipt

- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider tests/site_agent`
  — **126 passed** in 15.81s.
- `uv run --no-sync python -B -m pytest -o addopts='' -q -p no:cacheprovider`
  — **1815 passed**, no skips, in 211.58s.
- `uv run --no-sync python -B scripts/validate_configs.py`
  — **0 errors, 0 warnings**, all configuration inputs passed.
- Regression negative control temporarily loaded the baseline `respond`
  implementation against the new post-commit queue fault test: **1 expected
  failure** at the reproduced post-commit read; working files were unchanged.
- `git diff --check` — clean. Changes confined to service/API, their tests,
  and supporting documentation. Existing unrelated worktrees were untouched.

Faults cover accept/reject/modify commits followed by queue/journal/ledger
read failure, disappeared projection entry, reliable ledger receipt, duplicate
responses after recovery, and append failures before/after durability. HTTP
coverage distinguishes successful-but-unavailable projection, conflict, and
unknown result. Manager acceptance still does not create a task.
