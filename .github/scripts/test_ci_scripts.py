#!/usr/bin/env python3
"""Focused standard-library tests for CI policy scripts."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import verify_npm_audit
from verify_npm_audit import ACCEPTED_DEV_ADVISORIES, parse_report, validate

CHECKER = Path(__file__).with_name("verify_npm_audit.py")

# A complete, real-shaped zero-vulnerability report: the exact structure npm
# 11.11.0 (CI) and 11.11.1 write for `npm audit --json`, including the
# `dependencies` block the verifier does not otherwise inspect. Negative
# samples below change exactly one field of this report.
FULL_ZERO_REPORT = {
    "auditReportVersion": 2,
    "vulnerabilities": {},
    "metadata": {
        "vulnerabilities": {
            "info": 0,
            "low": 0,
            "moderate": 0,
            "high": 0,
            "critical": 0,
            "total": 0,
        },
        "dependencies": {
            "prod": 3,
            "dev": 72,
            "optional": 27,
            "peer": 0,
            "peerOptional": 0,
            "total": 74,
        },
    },
}

# What `npm audit --json` (11.11.1) actually prints when the registry cannot be
# reached: an error envelope with no report at all.
REAL_ERROR_RESPONSE = {
    "message": (
        "request to http://127.0.0.1:9/-/npm/v1/security/advisories/bulk "
        "failed, reason: connect ECONNREFUSED 127.0.0.1:9"
    ),
    "error": {"summary": "", "detail": ""},
}


def full_zero_report() -> dict:
    return copy.deepcopy(FULL_ZERO_REPORT)

# The live baseline accepts no development advisory. The graph-shape rules are
# exercised against this historical baseline (the ROI lockfile before the
# vitest 4.1.11 upgrade) so the mechanism stays tested while no real exception
# exists.
HISTORICAL_DEV_ADVISORIES = {
    "GHSA-67mh-4wv8-2f99": "moderate",
    "GHSA-2v37-7h3g-55p8": "high",
    "GHSA-fxqj-rqcc-2cmp": "moderate",
    "GHSA-4w7w-66w2-5vf9": "moderate",
    "GHSA-v6wh-96g9-6wx3": "moderate",
    "GHSA-fx2h-pf6j-xcff": "high",
    "GHSA-5xrq-8626-4rwp": "critical",
}
HISTORICAL_DEV_SEVERITY_COUNTS = {
    "info": 0,
    "low": 0,
    "moderate": 4,
    "high": 2,
    "critical": 1,
}
HISTORICAL_DEV_NODES = {
    "@vitest/mocker": frozenset(
        {
            "GHSA-67mh-4wv8-2f99",
            "GHSA-4w7w-66w2-5vf9",
            "GHSA-v6wh-96g9-6wx3",
            "GHSA-fx2h-pf6j-xcff",
        }
    ),
    "esbuild": frozenset({"GHSA-67mh-4wv8-2f99"}),
    "nanoid": frozenset({"GHSA-2v37-7h3g-55p8"}),
    "postcss": frozenset({"GHSA-fxqj-rqcc-2cmp"}),
    "vite": frozenset(
        {
            "GHSA-67mh-4wv8-2f99",
            "GHSA-4w7w-66w2-5vf9",
            "GHSA-v6wh-96g9-6wx3",
            "GHSA-fx2h-pf6j-xcff",
        }
    ),
    "vite-node": frozenset(
        {
            "GHSA-67mh-4wv8-2f99",
            "GHSA-4w7w-66w2-5vf9",
            "GHSA-v6wh-96g9-6wx3",
            "GHSA-fx2h-pf6j-xcff",
        }
    ),
    "vitest": frozenset(
        {
            "GHSA-67mh-4wv8-2f99",
            "GHSA-4w7w-66w2-5vf9",
            "GHSA-v6wh-96g9-6wx3",
            "GHSA-fx2h-pf6j-xcff",
            "GHSA-5xrq-8626-4rwp",
        }
    ),
}


def historical_baseline():
    """Patch the verifier's accepted baseline with the historical ROI baseline."""

    return mock.patch.multiple(
        verify_npm_audit,
        ACCEPTED_DEV_ADVISORIES=HISTORICAL_DEV_ADVISORIES,
        ACCEPTED_DEV_SEVERITY_COUNTS=HISTORICAL_DEV_SEVERITY_COUNTS,
        ACCEPTED_DEV_NODES=HISTORICAL_DEV_NODES,
    )


def report_from_vulnerabilities(vulnerabilities: dict) -> dict:
    counts = {
        "info": 0,
        "low": 0,
        "moderate": 0,
        "high": 0,
        "critical": 0,
    }
    for vulnerability in vulnerabilities.values():
        if isinstance(vulnerability, dict):
            severity = vulnerability.get("severity")
            if isinstance(severity, str) and severity in counts:
                counts[severity] += 1
    # Same envelope as a real report so every graph test exercises the policy,
    # not the report-shape gate.
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulnerabilities,
        "metadata": {
            "vulnerabilities": {**counts, "total": len(vulnerabilities)},
            "dependencies": copy.deepcopy(FULL_ZERO_REPORT["metadata"]["dependencies"]),
        },
    }


def report(*advisories: tuple[str, str]) -> dict:
    vulnerabilities = {}
    for index, (identifier, severity) in enumerate(advisories):
        vulnerabilities[f"package-{index}"] = {
            "severity": severity,
            "via": [
                {
                    "url": f"https://github.com/advisories/{identifier}",
                    "severity": severity,
                }
            ]
        }
    return report_from_vulnerabilities(vulnerabilities)


def accepted_development_report() -> dict:
    def advisory(identifier: str) -> dict:
        return {
            "url": f"https://github.com/advisories/{identifier}",
            "severity": HISTORICAL_DEV_ADVISORIES[identifier],
        }

    return report_from_vulnerabilities(
        {
            "@vitest/mocker": {"severity": "moderate", "via": ["vite"]},
            "esbuild": {
                "severity": "moderate",
                "via": [advisory("GHSA-67mh-4wv8-2f99")],
            },
            "nanoid": {
                "severity": "high",
                "via": [advisory("GHSA-2v37-7h3g-55p8")],
            },
            "postcss": {
                "severity": "moderate",
                "via": [advisory("GHSA-fxqj-rqcc-2cmp")],
            },
            "vite": {
                "severity": "high",
                "via": [
                    advisory("GHSA-4w7w-66w2-5vf9"),
                    advisory("GHSA-v6wh-96g9-6wx3"),
                    advisory("GHSA-fx2h-pf6j-xcff"),
                    "esbuild",
                ],
            },
            "vite-node": {"severity": "moderate", "via": ["vite"]},
            "vitest": {
                "severity": "critical",
                "via": [
                    "@vitest/mocker",
                    advisory("GHSA-5xrq-8626-4rwp"),
                    "vite",
                    "vite-node",
                ],
            },
        }
    )


class ReportShapeTests(unittest.TestCase):
    """The verifier refuses anything that is not a supported npm audit report."""

    def test_full_zero_report_passes_both_policies(self) -> None:
        for policy in ("production", "development"):
            errors, notes = validate(full_zero_report(), policy)
            self.assertEqual(errors, [], policy)
            self.assertTrue(notes, policy)

    def test_top_level_error_is_refused_even_with_zero_counts(self) -> None:
        # Exactly one change: an `error` member next to an otherwise clean report.
        report = full_zero_report()
        report["error"] = {"code": "ENOTFOUND", "summary": "registry unreachable", "detail": ""}
        for policy in ("production", "development"):
            errors, _ = validate(report, policy)
            self.assertEqual(len(errors), 1, errors)
            self.assertIn("error response, not a report", errors[0])
            self.assertIn("registry unreachable", errors[0])

    def test_real_error_response_is_refused(self) -> None:
        errors, _ = validate(copy.deepcopy(REAL_ERROR_RESPONSE), "production")
        self.assertEqual(len(errors), 1, errors)
        self.assertIn("error response, not a report", errors[0])
        self.assertIn("ECONNREFUSED", errors[0])

    def test_missing_audit_report_version_is_refused(self) -> None:
        report = full_zero_report()
        del report["auditReportVersion"]
        errors, _ = validate(report, "production")
        self.assertEqual(errors, [
            "npm audit JSON is missing auditReportVersion; the report format "
            "cannot be verified"
        ])

    def test_unknown_audit_report_version_is_refused(self) -> None:
        for version in (1, 3, 0, -2):
            report = full_zero_report()
            report["auditReportVersion"] = version
            errors, _ = validate(report, "production")
            self.assertEqual(len(errors), 1, (version, errors))
            self.assertIn(f"unsupported npm audit report version {version}", errors[0])

    def test_wrongly_typed_audit_report_version_is_refused(self) -> None:
        for version in (None, "2", 2.0, True, [2], {"v": 2}):
            report = full_zero_report()
            report["auditReportVersion"] = version
            errors, _ = validate(report, "development")
            self.assertEqual(len(errors), 1, (version, errors))
            self.assertIn("auditReportVersion must be an integer", errors[0])

    def test_shape_gate_runs_before_counting(self) -> None:
        # Both the error member and inconsistent counts are present; only the
        # shape error is reported, so a failure is never attributed to counts.
        report = full_zero_report()
        report["error"] = {"summary": "boom"}
        report["metadata"]["vulnerabilities"]["total"] = 5
        errors, _ = validate(report, "production")
        self.assertEqual(len(errors), 1)
        self.assertIn("error response", errors[0])


class CheckerCliTests(unittest.TestCase):
    """Exit codes and messages of the real checker entry point."""

    def run_checker(self, policy: str, report: object) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            if isinstance(report, str):
                path.write_text(report, encoding="utf-8")
            else:
                path.write_text(json.dumps(report), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-B", str(CHECKER), policy, str(path)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_full_zero_report_exits_zero_in_both_policies(self) -> None:
        for policy in ("production", "development"):
            result = self.run_checker(policy, full_zero_report())
            self.assertEqual(result.returncode, 0, (policy, result.stderr))
            self.assertEqual(result.stderr, "")
            self.assertIn("0 vulnerabilities" if policy == "production" else "0 present", result.stdout)

    def test_error_response_exits_one_with_reason(self) -> None:
        report = full_zero_report()
        report["error"] = {"summary": "registry unreachable"}
        for policy in ("production", "development"):
            result = self.run_checker(policy, report)
            self.assertEqual(result.returncode, 1, policy)
            self.assertIn("ERROR: npm audit returned an error response", result.stderr)
            self.assertIn("registry unreachable", result.stderr)
        real = self.run_checker("production", copy.deepcopy(REAL_ERROR_RESPONSE))
        self.assertEqual(real.returncode, 1)
        self.assertIn("ECONNREFUSED", real.stderr)

    def test_missing_wrong_or_unknown_version_exits_one(self) -> None:
        missing = full_zero_report()
        del missing["auditReportVersion"]
        samples = [
            (missing, "missing auditReportVersion"),
            ({**full_zero_report(), "auditReportVersion": None}, "must be an integer"),
            ({**full_zero_report(), "auditReportVersion": "2"}, "must be an integer"),
            ({**full_zero_report(), "auditReportVersion": True}, "must be an integer"),
            ({**full_zero_report(), "auditReportVersion": 3}, "unsupported npm audit report version 3"),
        ]
        for report, expected in samples:
            result = self.run_checker("production", report)
            self.assertEqual(result.returncode, 1, (expected, result.stderr))
            self.assertIn(expected, result.stderr)

    def test_vulnerability_reports_still_exit_one(self) -> None:
        production = self.run_checker("production", report(("GHSA-new1-new2-new3", "high")))
        self.assertEqual(production.returncode, 1)
        self.assertIn("production audit must be zero", production.stderr)
        development = self.run_checker("development", report(("GHSA-82fw-gwwq-j7x9", "moderate")))
        self.assertEqual(development.returncode, 1)
        self.assertIn("new development advisory: GHSA-82fw-gwwq-j7x9", development.stderr)

    def test_unreadable_or_duplicate_key_input_exits_two(self) -> None:
        duplicate = self.run_checker("production", '{"auditReportVersion": 2, "auditReportVersion": 2}')
        self.assertEqual(duplicate.returncode, 2)
        self.assertIn("duplicate JSON key", duplicate.stderr)
        broken = self.run_checker("production", "{not json")
        self.assertEqual(broken.returncode, 2)
        self.assertIn("cannot read npm audit evidence", broken.stderr)


class LiveBaselineTests(unittest.TestCase):
    """The committed baseline accepts no development advisory at all."""

    def test_live_baseline_accepts_nothing(self) -> None:
        self.assertEqual(ACCEPTED_DEV_ADVISORIES, {})
        self.assertEqual(verify_npm_audit.ACCEPTED_DEV_NODES, {})
        self.assertEqual(
            set(verify_npm_audit.ACCEPTED_DEV_SEVERITY_COUNTS.values()), {0}
        )

    def test_clean_development_audit_passes(self) -> None:
        errors, notes = validate(report(), "development")
        self.assertEqual(errors, [])
        self.assertIn("development advisory evidence: 0 present, 0 no longer present", notes)

    def test_any_development_advisory_fails_live_baseline(self) -> None:
        for severity in ("info", "low", "moderate", "high", "critical"):
            errors, _ = validate(report(("GHSA-82fw-gwwq-j7x9", severity)), "development")
            self.assertTrue(
                any("new development advisory: GHSA-82fw-gwwq-j7x9" in error for error in errors),
                (severity, errors),
            )
            self.assertTrue(
                any("new development vulnerability node: package-0" in error for error in errors),
                (severity, errors),
            )


class AuditPolicyTests(unittest.TestCase):
    """Graph-shape rules, exercised against the historical accepted baseline."""

    def setUp(self) -> None:
        patcher = historical_baseline()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_production_zero_passes(self) -> None:
        errors, notes = validate(report(), "production")
        self.assertEqual(errors, [])
        self.assertIn("production dependency audit: 0 vulnerabilities", notes)

    def test_production_requires_zero(self) -> None:
        errors, _ = validate(report(("GHSA-new1-new2-new3", "high")), "production")
        self.assertTrue(errors)

    def test_resolved_development_advisories_do_not_fail(self) -> None:
        errors, notes = validate(report(), "development")
        self.assertEqual(errors, [])
        self.assertTrue(any("no longer present" in note for note in notes))

    def test_new_development_advisory_fails(self) -> None:
        errors, _ = validate(report(("GHSA-new1-new2-new3", "moderate")), "development")
        self.assertTrue(any("new development advisory" in error for error in errors))

    def test_severity_increase_fails(self) -> None:
        identifier = next(
            item
            for item, severity in HISTORICAL_DEV_ADVISORIES.items()
            if severity == "moderate"
        )
        errors, _ = validate(report((identifier, "high")), "development")
        self.assertTrue(any("severity increased" in error for error in errors))

    def test_exact_accepted_development_baseline_passes(self) -> None:
        errors, _ = validate(accepted_development_report(), "development")

        self.assertEqual(errors, [])

    def test_extra_moderate_graph_node_exceeds_accepted_baseline(self) -> None:
        vulnerabilities = accepted_development_report()["vulnerabilities"]
        moderate_package = next(
            package_name
            for package_name, vulnerability in vulnerabilities.items()
            if vulnerability["severity"] == "moderate"
        )
        vulnerabilities["extra-wrapper-package"] = {
            "severity": "moderate",
            "via": [moderate_package],
        }
        audit_report = report_from_vulnerabilities(vulnerabilities)

        errors, _ = validate(audit_report, "development")

        self.assertTrue(
            any(
                "moderate vulnerability count exceeds accepted baseline: 5 > 4"
                in error
                for error in errors
            )
        )

    def test_replacement_wrapper_cannot_reuse_accepted_advisory(self) -> None:
        vulnerabilities = accepted_development_report()["vulnerabilities"]
        del vulnerabilities["postcss"]
        vulnerabilities["replacement-wrapper"] = {
            "severity": "moderate",
            "via": ["esbuild"],
        }

        errors, _ = validate(
            report_from_vulnerabilities(vulnerabilities), "development"
        )

        self.assertTrue(
            any(
                "new development vulnerability node: replacement-wrapper" in error
                for error in errors
            )
        )

    def test_direct_node_severity_requires_reachable_advisory_evidence(self) -> None:
        vulnerabilities = accepted_development_report()["vulnerabilities"]
        del vulnerabilities["vitest"]
        vulnerabilities["vite"]["severity"] = "critical"

        errors, _ = validate(
            report_from_vulnerabilities(vulnerabilities), "development"
        )

        self.assertTrue(
            any(
                "vulnerability node vite claims critical severity but reaches at "
                "most high accepted advisory severity" in error
                for error in errors
            )
        )

    def test_transitive_cycle_severity_cannot_exceed_reachable_advisory(self) -> None:
        identifier = next(
            item
            for item, severity in HISTORICAL_DEV_ADVISORIES.items()
            if severity == "moderate"
        )
        audit_report = report_from_vulnerabilities(
            {
                "advisory-package": {
                    "severity": "moderate",
                    "via": [
                        {
                            "url": f"https://github.com/advisories/{identifier}",
                            "severity": "moderate",
                        },
                        "critical-wrapper-package",
                    ],
                },
                "critical-wrapper-package": {
                    "severity": "critical",
                    "via": ["advisory-package"],
                },
            }
        )

        errors, _ = validate(audit_report, "development")

        self.assertTrue(
            any(
                "claims critical severity but reaches at most moderate accepted "
                "advisory severity" in error
                for error in errors
            )
        )

    def test_reduced_advisory_preserves_accepted_transitive_ceiling(self) -> None:
        identifier = "GHSA-fx2h-pf6j-xcff"
        audit_report = report_from_vulnerabilities(
            {
                "vite": {
                    "severity": "moderate",
                    "via": [
                        {
                            "url": f"https://github.com/advisories/{identifier}",
                            "severity": "moderate",
                        }
                    ],
                },
                "vite-node": {
                    "severity": "high",
                    "via": ["vite"],
                },
            }
        )

        errors, _ = validate(audit_report, "development")

        self.assertEqual(errors, [])

    def test_transitive_via_resolves_to_concrete_advisory(self) -> None:
        identifier = "GHSA-67mh-4wv8-2f99"
        severity = HISTORICAL_DEV_ADVISORIES[identifier]
        audit_report = report_from_vulnerabilities(
            {
                "esbuild": {
                    "severity": severity,
                    "via": [
                        {
                            "url": f"https://github.com/advisories/{identifier}",
                            "severity": severity,
                        }
                    ],
                },
                "@vitest/mocker": {
                    "severity": severity,
                    "via": ["esbuild"],
                },
            }
        )

        errors, _ = validate(audit_report, "development")

        self.assertEqual(errors, [])

    def test_dangling_string_via_fails(self) -> None:
        audit_report = report_from_vulnerabilities(
            {
                "wrapper-package": {
                    "severity": "high",
                    "via": ["missing-package"],
                }
            }
        )

        errors, _ = validate(audit_report, "development")

        self.assertTrue(any("dangling via reference" in error for error in errors))
        self.assertTrue(
            any("does not resolve to a classified advisory" in error for error in errors)
        )

    def test_string_via_cycle_without_advisory_fails(self) -> None:
        audit_report = report_from_vulnerabilities(
            {
                "package-a": {"severity": "high", "via": ["package-b"]},
                "package-b": {"severity": "high", "via": ["package-a"]},
            }
        )

        errors, _ = validate(audit_report, "development")

        unresolved = [
            error
            for error in errors
            if "does not resolve to a classified advisory" in error
        ]
        self.assertEqual(len(unresolved), 2)

    def test_malformed_via_containers_and_entries_fail(self) -> None:
        for via in ({}, [42], [""], []):
            with self.subTest(via=via):
                audit_report = report_from_vulnerabilities(
                    {"package": {"severity": "moderate", "via": via}}
                )

                errors, _ = validate(audit_report, "development")

                self.assertTrue(errors)
                self.assertTrue(
                    any(
                        "via" in error
                        or "does not resolve to a classified advisory" in error
                        for error in errors
                    )
                )

    def test_unclassified_advisory_fails(self) -> None:
        for advisory in (
            {"url": 42, "severity": "moderate"},
            {
                "url": "https://github.com/advisories/GHSA-new1-new2-new3",
                "severity": [],
            },
        ):
            with self.subTest(advisory=advisory):
                audit_report = report_from_vulnerabilities(
                    {
                        "package": {
                            "severity": "moderate",
                            "via": [advisory],
                        }
                    }
                )

                errors, _ = validate(audit_report, "development")

                self.assertTrue(
                    any("unclassified advisory" in error for error in errors)
                )

    def test_unhashable_node_severity_fails_without_crashing(self) -> None:
        audit_report = report_from_vulnerabilities(
            {
                "package": {
                    "severity": [],
                    "via": [
                        {
                            "url": "https://github.com/advisories/"
                            "GHSA-new1-new2-new3",
                            "severity": "moderate",
                        }
                    ],
                }
            }
        )

        errors, _ = validate(audit_report, "development")

        self.assertTrue(any("invalid severity" in error for error in errors))

    def test_root_array_fails_as_non_object(self) -> None:
        errors, _ = validate([], "development")
        self.assertEqual(errors, ["npm audit JSON root must be an object"])

    def test_malformed_json_containers_fail(self) -> None:
        # Each case starts from the complete, valid zero report (auditReportVersion
        # stays the integer 2) and breaks exactly one container, so the only
        # possible error is the one naming that container.
        vulnerabilities_broken = full_zero_report()
        vulnerabilities_broken["vulnerabilities"] = []
        metadata_broken = full_zero_report()
        metadata_broken["metadata"] = []
        metadata_counts_broken = full_zero_report()
        metadata_counts_broken["metadata"]["vulnerabilities"] = []
        cases = (
            (
                "vulnerabilities",
                vulnerabilities_broken,
                "npm audit JSON vulnerabilities must be an object",
            ),
            (
                "metadata",
                metadata_broken,
                "npm audit JSON metadata must be an object",
            ),
            (
                "metadata.vulnerabilities",
                metadata_counts_broken,
                "npm audit JSON metadata.vulnerabilities must be an object",
            ),
        )
        for field, audit_report, expected in cases:
            with self.subTest(field=field):
                self.assertEqual(audit_report["auditReportVersion"], 2)
                for policy in ("production", "development"):
                    errors, _ = validate(audit_report, policy)
                    self.assertEqual(errors, [expected], (field, policy, errors))

    def test_duplicate_json_keys_fail_closed(self) -> None:
        evidence = (
            '{"vulnerabilities":{"danger":{"severity":"critical","via":[]}},'
            '"vulnerabilities":{},"metadata":{"vulnerabilities":'
            '{"info":0,"low":0,"moderate":0,"high":0,"critical":0,"total":0}}}'
        )

        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            parse_report(evidence)

    def test_metadata_total_must_match_entries(self) -> None:
        audit_report = report(("GHSA-new1-new2-new3", "high"))
        audit_report["metadata"]["vulnerabilities"]["total"] = 0

        errors, _ = validate(audit_report, "development")

        self.assertTrue(
            any("total does not match vulnerability entries" in error for error in errors)
        )

    def test_metadata_severity_counts_must_match_entries(self) -> None:
        audit_report = report(("GHSA-new1-new2-new3", "high"))
        audit_report["metadata"]["vulnerabilities"]["high"] = 0

        errors, _ = validate(audit_report, "development")

        self.assertTrue(
            any("high count does not match vulnerability entries" in error for error in errors)
        )

    def test_metadata_counts_must_be_non_negative_integers(self) -> None:
        for bad_count in (-1, True, "0"):
            with self.subTest(bad_count=bad_count):
                audit_report = report()
                audit_report["metadata"]["vulnerabilities"]["low"] = bad_count

                errors, _ = validate(audit_report, "development")

                self.assertTrue(
                    any("count for low" in error for error in errors)
                )


if __name__ == "__main__":
    unittest.main()
