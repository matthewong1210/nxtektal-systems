"""Lockstep contract revision: the pinned value is recomputed from the staffing
source set, carried on every wire document, and enforced in both mixed
directions over an isolated synthetic ledger (no demo or persistent ledger is
touched)."""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest
from nxt_pilot_ops.staffing.contracts import StaffingError
from nxt_site_agent import SiteAgentError

from scripts import staffing_contract_revision as revision_module
from scripts.staffing_contract_revision import STAFFING_CONTRACT_REVISION
from scripts.staffing_operations import (
    admit_contract_revision,
    public_domain_error,
)
from tests.site_agent.test_staffing_composition import (
    FLOW_SERVICE_DATE,
    exchange_request,
    flow_generation,
    flow_leave,
    validator,
)
from tests.site_agent.test_staffing_review_wire import (
    MORNING,
    MovableClock,
    _evening_roster,
    _flow,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONSOLE_PIN = REPOSITORY_ROOT / "apps/site-agent-console/lib/staffing-revision.ts"
LOCKSTEP = REPOSITORY_ROOT / "simulation/docs/contracts/staffing-v1/lockstep.json"
FOREIGN_REVISION = "f" * 64


def _ledger_bytes(ledger_root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(ledger_root).as_posix(): path.read_bytes()
        for path in sorted(ledger_root.rglob("*"))
        if path.is_file()
    }


def _ledger_root(tmp_path: Path) -> Path:
    """The isolated synthetic ledger ``_flow`` opens under the test's own directory."""

    root = tmp_path / "flow"
    assert root.is_dir()
    return root


def test_pinned_revision_is_the_digest_of_the_lockstep_source_set() -> None:
    computed = revision_module.compute_contract_revision(REPOSITORY_ROOT)
    assert re.fullmatch(r"[0-9a-f]{64}", computed)
    assert STAFFING_CONTRACT_REVISION == computed, (
        "the staffing implementation changed; regenerate every pin with "
        "`uv run --no-sync python -B scripts/staffing_contract_revision.py --write`"
    )
    pins = revision_module.pinned_values(REPOSITORY_ROOT)
    assert pins == {"python": computed, "console": computed, "manifest": computed}
    assert json.loads(LOCKSTEP.read_text(encoding="utf-8"))["contract_revision"] == computed
    assert re.search(
        r'^export const STAFFING_CONTRACT_REVISION = "%s";$' % computed,
        CONSOLE_PIN.read_text(encoding="utf-8"),
        re.M,
    )
    assert revision_module.example_revisions(REPOSITORY_ROOT) == {computed}
    assert revision_module.check(REPOSITORY_ROOT) == []
    fingerprint = revision_module.contract_fingerprint(REPOSITORY_ROOT)
    assert revision_module.pinned_fingerprints(REPOSITORY_ROOT) == {
        "python": fingerprint,
        "console": fingerprint,
    }


def test_lockstep_source_set_covers_both_sides_and_excludes_every_pin() -> None:
    sources = [
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in revision_module.lockstep_sources(REPOSITORY_ROOT)
    ]
    assert sources == sorted(sources)
    for required in (
        "simulation/nxt_pilot_ops/staffing/operations.py",
        "simulation/nxt_pilot_ops/staffing/review.py",
        "simulation/nxt_pilot_ops/staffing/time.py",
        "simulation/scripts/staffing_operations.py",
        "simulation/nxt_site_agent/api.py",
        "simulation/docs/contracts/staffing-v1/schema.json",
        "apps/site-agent-console/lib/staffing.ts",
        "apps/site-agent-console/lib/staffing-state.ts",
        "apps/site-agent-console/lib/staffing-actions.ts",
        "apps/site-agent-console/lib/staffing-labels.ts",
        "apps/site-agent-console/lib/site-time.ts",
        "apps/site-agent-console/components/StaffingPanel.tsx",
        "apps/site-agent-console/components/staffing/CandidateCards.tsx",
    ):
        assert required in sources, required
    for excluded in (
        "simulation/scripts/staffing_contract_revision.py",
        "apps/site-agent-console/lib/staffing-revision.ts",
        "simulation/docs/contracts/staffing-v1/lockstep.json",
    ):
        assert excluded not in sources, excluded
    assert not any("/tests/" in item or "/examples/" in item for item in sources)
    # Any byte of any source moves the value.
    manifest = revision_module.lockstep_manifest_text(REPOSITORY_ROOT)
    assert manifest.count("\n") == len(sources)
    assert all(line.split(" ")[0] in sources for line in manifest.splitlines())


def test_lockstep_manifest_rejects_a_missing_or_malformed_source(tmp_path: Path) -> None:
    (tmp_path / "simulation/docs/contracts/staffing-v1").mkdir(parents=True)
    manifest = tmp_path / revision_module.LOCKSTEP_MANIFEST
    manifest.write_text(
        json.dumps(
            {
                "schema": "nxt-staffing-lockstep/v1",
                "contract_revision": "0" * 64,
                "sources": {"files": ["missing.py"], "directories": []},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(tmp_path)
    manifest.write_text('{"schema": "other"}', encoding="utf-8")
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(tmp_path)


def test_admission_strips_only_the_pinned_revision() -> None:
    body = {"schema": "nxt-staffing-exception/v1", "contract_revision": STAFFING_CONTRACT_REVISION, "x": 1}
    assert admit_contract_revision(body) == {"schema": "nxt-staffing-exception/v1", "x": 1}
    assert "contract_revision" in body, "the caller's body is not mutated"
    for missing in ({"schema": "nxt-staffing-exception/v1"}, {}, {"contract_revision": None}, {"contract_revision": 7}, {"contract_revision": "abc"}):
        with pytest.raises(SiteAgentError) as refused:
            admit_contract_revision(missing)
        assert refused.value.code == "staffing_invalid_request"
    with pytest.raises(SiteAgentError) as mismatch:
        admit_contract_revision({"schema": "nxt-staffing-exception/v1", "contract_revision": FOREIGN_REVISION})
    assert mismatch.value.code == "staffing_contract_mismatch"
    assert FOREIGN_REVISION not in mismatch.value.detail


def test_new_service_rejects_an_old_or_foreign_write_without_appending(tmp_path: Path) -> None:
    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        root = _ledger_root(tmp_path)
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        before = ledger.verify()
        bytes_before = _ledger_bytes(root)

        # A body from a console built before the field existed (02e1d7d and earlier).
        old_write = flow_leave("leave-old", roster=1, exception_set=0)
        del old_write["contract_revision"]
        with pytest.raises(SiteAgentError) as refused_old:
            api.route("POST", "/api/v1/staffing/exceptions", old_write)
        assert refused_old.value.code == "staffing_invalid_request"

        # A well-formed body from a console built with another checkout.
        foreign_write = flow_leave("leave-foreign", roster=1, exception_set=0)
        foreign_write["contract_revision"] = FOREIGN_REVISION
        for _ in range(2):
            with pytest.raises(SiteAgentError) as refused_foreign:
                api.route("POST", "/api/v1/staffing/exceptions", foreign_write)
            assert refused_foreign.value.code == "staffing_contract_mismatch"
            assert refused_foreign.value.detail == "console and service contract revisions differ"

        # Every POST route admits the revision before anything else.
        for path in (
            "/api/v1/staffing/roster-imports",
            "/api/v1/staffing/exceptions/exception-1/cancel",
            "/api/v1/staffing/exceptions/exception-1/correct",
            "/api/v1/staffing/suggestions",
            "/api/v1/staffing/suggestions/generation-1/accept",
            "/api/v1/staffing/suggestions/generation-1/modify",
            "/api/v1/staffing/suggestions/generation-1/reject",
        ):
            with pytest.raises(SiteAgentError) as refused_route:
                api.route("POST", path, {**foreign_write, "contract_revision": FOREIGN_REVISION})
            assert refused_route.value.code == "staffing_contract_mismatch", path
            with pytest.raises(SiteAgentError) as refused_missing:
                api.route("POST", path, {key: value for key, value in foreign_write.items() if key != "contract_revision"})
            assert refused_missing.value.code == "staffing_invalid_request", path

        assert ledger.verify() == before
        assert _ledger_bytes(root) == bytes_before
        for request_id in ("leave-old", "leave-foreign"):
            with pytest.raises(SiteAgentError) as missing:
                api.route("GET", f"/api/v1/staffing/requests/exception-record/{request_id}", {})
            assert missing.value.code == "staffing_request_not_found"
    finally:
        api.close()


def test_old_service_rejects_a_new_write_by_its_closed_key_rule(tmp_path: Path) -> None:
    """The service of 02e1d7d hands every POST body straight to the domain, whose
    request admission has always rejected a key outside its closed set. That
    unchanged rule is what refuses a body carrying ``contract_revision``."""

    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        owner = api._router._owner  # noqa: SLF001 - the domain the older route called directly
        before = _ledger_bytes(_ledger_root(tmp_path))
        with pytest.raises(StaffingError) as refused_roster:
            owner.import_roster(_evening_roster("roster-new"), recorded_at=MORNING)
        assert refused_roster.value.code == "staffing_unknown_field"
        assert public_domain_error(refused_roster.value).code == "staffing_invalid_request"
        assert _ledger_bytes(_ledger_root(tmp_path)) == before

        # With a roster in place the same rule refuses every other new body at
        # every domain write entry the older route called, before any lookup.
        api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        verified = ledger.verify()
        cancel = {**exchange_request("exception-correction.json", "exception-cancelled"), "exception_id": "exception-1"}
        correct = {**exchange_request("exception-correction.json", "exception-corrected"), "exception_id": "exception-1"}
        entries = [
            ("record_exception", lambda: owner.record_exception(flow_leave("leave-new", roster=1, exception_set=0), recorded_at=MORNING)),
            ("cancel_exception", lambda: owner.cancel_exception(cancel, recorded_at=MORNING)),
            ("correct_exception", lambda: owner.correct_exception(correct, recorded_at=MORNING)),
            ("commit_manager_response", lambda: owner.commit_manager_response(
                exchange_request("manager-response.json", "manager-accepted"), suggestion_id="generation-1", recorded_at=MORNING,
            )),
            ("worker.submit", lambda: api._worker.submit(flow_generation("generate-new", roster=1, exception_set=0, effective_plan=0))),  # noqa: SLF001
        ]
        for label, call in entries:
            with pytest.raises(StaffingError) as refused:
                call()
            assert refused.value.code in {"staffing_unknown_field", "staffing_invalid_request"}, label
            assert public_domain_error(refused.value).code == "staffing_invalid_request", label
        assert ledger.verify() == verified
    finally:
        api.close()


def test_pin_module_is_stdlib_only_and_imports_no_package() -> None:
    module = REPOSITORY_ROOT / revision_module.PYTHON_PIN
    tree = ast.parse(module.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "argparse", "hashlib", "json", "pathlib", "re", "sys"}, sorted(imported)
    assert not any(name.startswith("nxt_") or name in {"scripts", "socket", "urllib", "http", "subprocess", "os"} for name in imported)


def _layout(tmp_path: Path, *, nested_bytes: bytes = b"export const x = 1;\n") -> Path:
    """A scratch repository with a two-file source set and every pin file."""

    (tmp_path / "src/dir/nested").mkdir(parents=True)
    (tmp_path / "src/a.py").write_bytes(b"VALUE = 1\n")
    (tmp_path / "src/dir/top.ts").write_bytes(b"export const top = 1;\n")
    (tmp_path / "src/dir/nested/b.ts").write_bytes(nested_bytes)
    (tmp_path / "src/dir/nested/.hidden.ts").write_bytes(b"ignored\n")
    (tmp_path / "src/dir/__pycache__").mkdir()
    (tmp_path / "src/dir/__pycache__/c.ts").write_bytes(b"ignored\n")
    (tmp_path / "src/dir/other.txt").write_bytes(b"ignored\n")
    contract = tmp_path / revision_module.CONTRACT_DIRECTORY
    (contract / "examples").mkdir(parents=True)
    (contract / "schema.json").write_text("{}\n", encoding="utf-8")
    (contract / "examples/success.json").write_text(
        '{"request":{"contract_revision":"%s"},\n "data": {"contract_revision": "%s"}}\n' % ("0" * 64, "0" * 64),
        encoding="utf-8",
    )
    (contract / "examples/errors.json").write_text('{"exchanges":[]}\n', encoding="utf-8")
    (contract / "lockstep.json").write_text(
        json.dumps(
            {
                "schema": "nxt-staffing-lockstep/v1",
                "contract_revision": "0" * 64,
                "sources": {"files": ["src/a.py"], "directories": [{"path": "src/dir", "suffixes": [".ts"]}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for relative, text in (
        (revision_module.PYTHON_PIN, 'STAFFING_CONTRACT_REVISION = "%s"\n' % ("0" * 64)),
        (revision_module.CONSOLE_PIN, 'export const STAFFING_CONTRACT_REVISION = "%s";\n' % ("0" * 64)),
        (revision_module.PYTHON_FINGERPRINT_PIN, 'STAFFING_CONTRACT_FINGERPRINT = "%s"\n' % ("0" * 64)),
        (revision_module.CONSOLE_FINGERPRINT_PIN, 'const STAFFING_CONTRACT_FINGERPRINT = "%s";\n' % ("0" * 64)),
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_write_regenerates_every_pin_and_check_detects_any_source_drift(tmp_path: Path) -> None:
    root = _layout(tmp_path)
    sources = [path.relative_to(root).as_posix() for path in revision_module.lockstep_sources(root)]
    assert sources == ["src/a.py", "src/dir/nested/b.ts", "src/dir/top.ts"], "recursive, suffix-filtered, dotfiles and __pycache__ skipped"
    assert len(revision_module.check(root)) >= 3, "placeholder pins disagree with the computed value"

    result = revision_module.write(root)
    revision = result["contract_revision"]
    assert re.fullmatch(r"[0-9a-f]{64}", revision) and revision != "0" * 64
    assert revision_module.check(root) == []
    assert revision_module.pinned_values(root) == {"python": revision, "console": revision, "manifest": revision}
    assert revision_module.example_revisions(root) == {revision}
    example = (root / revision_module.CONTRACT_DIRECTORY / "examples/success.json").read_text(encoding="utf-8")
    assert example.count(revision) == 2 and '"contract_revision": "%s"' % revision in example, "spacing preserved"
    assert (root / revision_module.CONTRACT_DIRECTORY / "examples/errors.json").read_text(encoding="utf-8") == '{"exchanges":[]}\n'
    fingerprint = revision_module.contract_fingerprint(root)
    assert revision_module.pinned_fingerprints(root) == {"python": fingerprint, "console": fingerprint}
    assert revision_module.write(root)["changed"] == [], "a second write is a no-op"

    # One byte in a nested source moves the revision and every check notices.
    (root / "src/dir/nested/b.ts").write_bytes(b"export const x = 2;\n")
    drift = revision_module.check(root)
    assert drift and all("contract revision" in line for line in drift)
    moved = revision_module.write(root)["contract_revision"]
    assert moved != revision and revision_module.check(root) == []

    # CRLF is folded before hashing, so a line-ending setting cannot move it.
    (root / "src/dir/nested/b.ts").write_bytes(b"export const x = 2;\r\n")
    assert revision_module.compute_contract_revision(root) == moved
    # A missing source fails loud rather than silently shrinking the set.
    (root / "src/a.py").unlink()
    with pytest.raises(revision_module.LockstepError):
        revision_module.compute_contract_revision(root)


def test_snapshot_and_receipts_carry_the_pinned_revision_and_the_domain_never_sees_it(tmp_path: Path) -> None:
    api, ledger, _ = _flow(tmp_path, MovableClock(MORNING))
    try:
        receipt = api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        validator("#/$defs/StaffingReceipt").validate(receipt)
        assert receipt["contract_revision"] == STAFFING_CONTRACT_REVISION
        replay = api.route("GET", "/api/v1/staffing/requests/roster-import/roster-1", {})
        assert replay["contract_revision"] == STAFFING_CONTRACT_REVISION
        assert replay["disposition"] == "duplicate"
        duplicate = api.route("POST", "/api/v1/staffing/roster-imports", _evening_roster("roster-1"))
        assert duplicate["disposition"] == "duplicate"
        assert duplicate["contract_revision"] == STAFFING_CONTRACT_REVISION
        for path in ("/api/v1/staffing", f"/api/v1/staffing/dates/{FLOW_SERVICE_DATE}"):
            snapshot = api.route("GET", path, {})
            validator("#/$defs/StaffingDateSnapshot").validate(snapshot)
            assert snapshot["contract_revision"] == STAFFING_CONTRACT_REVISION
        exception = api.route("POST", "/api/v1/staffing/exceptions", flow_leave("leave-1", roster=1, exception_set=0))
        assert exception["contract_revision"] == STAFFING_CONTRACT_REVISION
        ledger.verify()
        text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in _ledger_root(tmp_path).rglob("*")
            if path.is_file()
        )
        assert "contract_revision" not in text, "the revision is a wire fact, never a ledger fact"
        assert STAFFING_CONTRACT_REVISION not in text
    finally:
        api.close()
