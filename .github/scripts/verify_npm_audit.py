#!/usr/bin/env python3
"""Apply the repository's explicit ROI npm-audit policy to JSON evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


SEVERITY = {"info": 0, "low": 1, "moderate": 2, "high": 3, "critical": 4}

# Development-only advisories accepted by the unchanged baseline lockfile.
# A missing advisory is considered resolved. New advisories and severity
# increases fail verification.
ACCEPTED_DEV_ADVISORIES = {
    "GHSA-67mh-4wv8-2f99": "moderate",
    "GHSA-2v37-7h3g-55p8": "high",
    "GHSA-fxqj-rqcc-2cmp": "moderate",
    "GHSA-4w7w-66w2-5vf9": "moderate",
    "GHSA-v6wh-96g9-6wx3": "moderate",
    "GHSA-fx2h-pf6j-xcff": "high",
    "GHSA-5xrq-8626-4rwp": "critical",
}

# npm reports counts for vulnerable dependency-graph nodes, not just unique
# advisories. Keep that graph shape bounded as well as the advisory allowlist:
# advisories disappearing (and therefore counts shrinking) are accepted, but a
# new wrapper node cannot hide behind an already accepted advisory.
ACCEPTED_DEV_SEVERITY_COUNTS = {
    "info": 0,
    "low": 0,
    "moderate": 4,
    "high": 2,
    "critical": 1,
}

# npm's current lockfile graph has seven vulnerable package nodes. Bind each
# node to the accepted advisories it can currently reach so a disappearing
# advisory or node is allowed, while a replacement wrapper cannot reuse an
# unrelated accepted advisory to keep the aggregate counts unchanged.
ACCEPTED_DEV_NODES = {
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


class DuplicateJsonKey(ValueError):
    """Raised when audit evidence contains an ambiguous JSON object."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_report(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_object_without_duplicate_keys)


def advisory_id(item: dict[str, Any]) -> str | None:
    url = item.get("url")
    if not isinstance(url, str):
        return None
    marker = "/advisories/"
    if marker not in url:
        return None
    identifier = url.rsplit(marker, 1)[1].strip("/")
    if not identifier or "/" in identifier:
        return None
    return identifier


def validate(report: Any, policy: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    notes: list[str] = []
    if not isinstance(report, dict):
        return ["npm audit JSON root must be an object"], notes

    vulnerabilities = report.get("vulnerabilities")
    metadata_container = report.get("metadata")
    if not isinstance(vulnerabilities, dict):
        errors.append("npm audit JSON vulnerabilities must be an object")
    if not isinstance(metadata_container, dict):
        errors.append("npm audit JSON metadata must be an object")
        metadata: Any = None
    else:
        metadata = metadata_container.get("vulnerabilities")
        if not isinstance(metadata, dict):
            errors.append("npm audit JSON metadata.vulnerabilities must be an object")
    if not isinstance(vulnerabilities, dict) or not isinstance(metadata, dict):
        return errors, notes

    metadata_counts: dict[str, int] = {}
    for severity in SEVERITY:
        count = metadata.get(severity)
        if type(count) is not int or count < 0:
            errors.append(
                f"npm audit JSON metadata count for {severity} must be a "
                "non-negative integer"
            )
        else:
            metadata_counts[severity] = count
    total = metadata.get("total")
    if type(total) is not int or total < 0:
        errors.append(
            "npm audit JSON vulnerability total must be a non-negative integer"
        )
        total = None
    if total is not None and len(metadata_counts) == len(SEVERITY):
        counted_total = sum(metadata_counts.values())
        if total != counted_total:
            errors.append(
                "npm audit JSON vulnerability total does not match severity counts: "
                f"{total} != {counted_total}"
            )

    node_counts = {severity: 0 for severity in SEVERITY}
    actual: dict[str, str] = {}
    direct_advisories: dict[str, list[tuple[str, str]]] = {}
    references: dict[str, list[str]] = {}
    for package_name, vulnerability in vulnerabilities.items():
        if not isinstance(package_name, str) or not package_name:
            errors.append("npm audit vulnerability names must be non-empty strings")
            continue
        if not isinstance(vulnerability, dict):
            errors.append(f"npm audit vulnerability for {package_name} must be an object")
            continue

        node_severity = vulnerability.get("severity")
        if not isinstance(node_severity, str) or node_severity not in SEVERITY:
            errors.append(
                f"npm audit vulnerability for {package_name} has invalid severity"
            )
        else:
            node_counts[node_severity] += 1

        via_entries = vulnerability.get("via")
        if not isinstance(via_entries, list):
            errors.append(f"npm audit vulnerability via for {package_name} must be a list")
            references[package_name] = []
            continue

        references[package_name] = []
        for via in via_entries:
            if isinstance(via, str):
                if not via or via != via.strip():
                    errors.append(
                        f"npm audit vulnerability for {package_name} has malformed "
                        "string via entry"
                    )
                else:
                    references[package_name].append(via)
                continue
            if not isinstance(via, dict):
                errors.append(
                    f"npm audit vulnerability for {package_name} has malformed via entry"
                )
                continue
            identifier = advisory_id(via)
            severity = via.get("severity")
            if (
                identifier is None
                or not isinstance(severity, str)
                or severity not in SEVERITY
            ):
                errors.append(
                    f"npm audit vulnerability for {package_name} has an "
                    "unclassified advisory"
                )
                continue
            direct_advisories.setdefault(package_name, []).append(
                (identifier, severity)
            )
            previous = actual.get(identifier)
            if previous is None or SEVERITY[severity] > SEVERITY[previous]:
                actual[identifier] = severity

    if total is not None and total != len(vulnerabilities):
        errors.append(
            "npm audit JSON vulnerability total does not match vulnerability entries: "
            f"{total} != {len(vulnerabilities)}"
        )
    for severity, count in metadata_counts.items():
        if count != node_counts[severity]:
            errors.append(
                f"npm audit JSON {severity} count does not match vulnerability entries: "
                f"{count} != {node_counts[severity]}"
            )

    for package_name, via_references in references.items():
        for referenced_package in via_references:
            if referenced_package not in vulnerabilities:
                errors.append(
                    f"npm audit vulnerability for {package_name} has dangling via "
                    f"reference: {referenced_package}"
                )

    def reachable_advisories(package_name: str) -> list[tuple[str, str]]:
        pending = [package_name]
        visited: set[str] = set()
        reachable: list[tuple[str, str]] = []
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            reachable.extend(direct_advisories.get(current, []))
            pending.extend(
                reference
                for reference in references.get(current, [])
                if reference in vulnerabilities
            )
        return reachable

    reachable_by_package: dict[str, set[str]] = {}
    for package_name in vulnerabilities:
        if not isinstance(package_name, str):
            continue
        reachable = reachable_advisories(package_name)
        reachable_by_package[package_name] = {
            identifier for identifier, _severity in reachable
        }
        if not reachable:
            errors.append(
                f"npm audit vulnerability for {package_name} does not resolve "
                "to a classified advisory"
            )
            continue
        accepted_severities = [
            ACCEPTED_DEV_ADVISORIES[identifier]
            for identifier, _reported_severity in reachable
            if identifier in ACCEPTED_DEV_ADVISORIES
        ]
        reachable_severity = max(
            accepted_severities,
            key=SEVERITY.__getitem__,
            default=None,
        )
        node_severity = vulnerabilities[package_name].get("severity")
        if (
            reachable_severity is not None
            and isinstance(node_severity, str)
            and node_severity in SEVERITY
            and SEVERITY[node_severity] > SEVERITY[reachable_severity]
        ):
            errors.append(
                f"npm audit vulnerability node {package_name} claims "
                f"{node_severity} severity but reaches at most "
                f"{reachable_severity} accepted advisory severity"
            )

    if policy == "production":
        if vulnerabilities or total != 0 or any(metadata_counts.values()):
            errors.append(f"production audit must be zero; npm reported {total!r}")
        elif not errors:
            notes.append("production dependency audit: 0 vulnerabilities")
        return errors, notes

    for severity, accepted_count in ACCEPTED_DEV_SEVERITY_COUNTS.items():
        actual_count = metadata_counts.get(severity)
        if actual_count is not None and actual_count > accepted_count:
            errors.append(
                f"development {severity} vulnerability count exceeds accepted "
                f"baseline: {actual_count} > {accepted_count}"
            )

    for package_name, reachable_ids in sorted(reachable_by_package.items()):
        accepted_ids = ACCEPTED_DEV_NODES.get(package_name)
        if accepted_ids is None:
            errors.append(f"new development vulnerability node: {package_name}")
        elif not reachable_ids <= accepted_ids:
            unexpected = ", ".join(sorted(reachable_ids - accepted_ids))
            errors.append(
                f"development vulnerability node {package_name} reaches "
                f"unexpected advisories: {unexpected}"
            )

    resolved_nodes = sorted(set(ACCEPTED_DEV_NODES) - set(vulnerabilities))
    notes.extend(
        f"known development vulnerability node no longer present: {item}"
        for item in resolved_nodes
    )

    if vulnerabilities and not actual:
        errors.append("npm reported vulnerabilities but no advisory IDs could be classified")

    for identifier, severity in sorted(actual.items()):
        accepted = ACCEPTED_DEV_ADVISORIES.get(identifier)
        if accepted is None:
            errors.append(f"new development advisory: {identifier} ({severity})")
        elif SEVERITY[severity] > SEVERITY[accepted]:
            errors.append(
                f"development advisory severity increased: {identifier} "
                f"({accepted} -> {severity})"
            )
        else:
            notes.append(f"accepted development advisory: {identifier} ({severity})")

    resolved = sorted(set(ACCEPTED_DEV_ADVISORIES) - set(actual))
    notes.extend(f"known development advisory no longer present: {item}" for item in resolved)
    notes.append(
        f"development advisory evidence: {len(actual)} present, "
        f"{len(resolved)} no longer present"
    )
    return errors, notes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy", choices=("production", "development"))
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        report = parse_report(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read npm audit evidence: {exc}", file=sys.stderr)
        return 2

    errors, notes = validate(report, args.policy)
    for note in notes:
        print(note)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
