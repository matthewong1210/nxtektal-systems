#!/usr/bin/env python3
"""Focused standard-library tests for CI policy scripts."""

from __future__ import annotations

import unittest

from verify_npm_audit import ACCEPTED_DEV_ADVISORIES, parse_report, validate


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
    return {
        "vulnerabilities": vulnerabilities,
        "metadata": {
            "vulnerabilities": {**counts, "total": len(vulnerabilities)},
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
            "severity": ACCEPTED_DEV_ADVISORIES[identifier],
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


class AuditPolicyTests(unittest.TestCase):
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
            for item, severity in ACCEPTED_DEV_ADVISORIES.items()
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
            for item, severity in ACCEPTED_DEV_ADVISORIES.items()
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
        severity = ACCEPTED_DEV_ADVISORIES[identifier]
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

    def test_malformed_json_containers_fail(self) -> None:
        malformed_reports = (
            [],
            {"vulnerabilities": [], "metadata": {"vulnerabilities": {}}},
            {"vulnerabilities": {}, "metadata": []},
            {"vulnerabilities": {}, "metadata": {"vulnerabilities": []}},
        )
        for audit_report in malformed_reports:
            with self.subTest(audit_report=audit_report):
                errors, _ = validate(audit_report, "development")
                self.assertTrue(errors)

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
