"""The privacy rule every adapter applies before any value is kept.

Minimum operational identity only.  A column is admitted only when the
adapter's allow-list names it; a header carrying a forbidden token (names,
contact details, pay, health, free text) rejects the whole batch; a value is
admitted only when it matches the bounded shape its column declares.  No
value is ever copied into an error, a diagnostic, or a log: errors carry the
row number, the column name, and a reason code only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import IDENTIFIER_PATTERN

#: Header tokens that mark a column this slice must never ingest.  Headers
#: are split on anything that is not a letter or digit and compared in lower
#: case, so ``EmployeeName``, ``phone_number`` and ``home-address`` all match.
FORBIDDEN_HEADER_TOKENS = frozenset(
    {
        "name", "names", "firstname", "lastname", "surname", "forename", "nickname",
        "phone", "mobile", "tel", "telephone", "email", "mail",
        "address", "street", "city", "postcode", "zip", "zipcode",
        "salary", "wage", "wages", "pay", "payroll", "payrate", "rate", "bonus", "bank", "iban", "account",
        "ssn", "passport", "national", "idnumber", "identity", "citizen",
        "medical", "health", "sick", "sickness", "diagnosis", "illness", "disability", "pregnan",
        "dob", "birth", "birthday", "age", "gender", "sex", "ethnicity", "race", "religion", "union",
        "note", "notes", "comment", "comments", "remark", "remarks", "description", "freetext", "text", "memo",
        "performance", "rating", "score", "ranking", "discipline", "warning",
    }
)

_SPLIT = re.compile(r"[^A-Za-z0-9]+")

#: Bounded code shape (statuses, record types, role codes).
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
#: Non-negative integer counts.
COUNT_PATTERN = re.compile(r"^(0|[1-9][0-9]{0,8})$")


@dataclass(frozen=True, slots=True)
class RowError:
    """One rejection reason: never carries a value from the file."""

    row_number: int | None
    column: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"row_number": self.row_number, "column": self.column, "reason": self.reason}


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def header_tokens(header: str) -> frozenset[str]:
    """Lower-case tokens of a header, split on separators and CamelCase boundaries."""

    return frozenset(token.lower() for token in _SPLIT.split(_CAMEL.sub(" ", header)) if token)


def forbidden_headers(headers: Iterable[str]) -> list[str]:
    """Headers whose tokens intersect the forbidden set (whole-token match)."""

    hits: list[str] = []
    for header in headers:
        tokens = header_tokens(header)
        if tokens & FORBIDDEN_HEADER_TOKENS or any(token.startswith("pregnan") for token in tokens):
            hits.append(header)
    return hits


def check_headers(headers: list[str], required: frozenset[str], optional: frozenset[str]) -> list[RowError]:
    """Header validation: forbidden, duplicate, missing, and unexpected columns."""

    errors: list[RowError] = []
    for header in forbidden_headers(headers):
        errors.append(RowError(None, header, "forbidden_column"))
    seen: set[str] = set()
    for header in headers:
        if header in seen:
            errors.append(RowError(None, header, "duplicate_column"))
        seen.add(header)
    for header in sorted(required - seen):
        errors.append(RowError(None, header, "missing_column"))
    for header in headers:
        if header not in required and header not in optional and header not in {hit.column for hit in errors}:
            errors.append(RowError(None, header, "unexpected_column"))
    return errors


def is_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER_PATTERN.match(value))


def is_code(value: Any) -> bool:
    return isinstance(value, str) and bool(CODE_PATTERN.match(value))


def is_count(value: Any) -> bool:
    return isinstance(value, str) and bool(COUNT_PATTERN.match(value))


def safe_errors(errors: Iterable[RowError]) -> list[dict[str, Any]]:
    """Plain-data error list for journals and API payloads (no values)."""

    return [error.to_dict() for error in errors]


def redact_mapping(values: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    """Keep only allow-listed keys; everything else is dropped, never logged."""

    return {key: values[key] for key in sorted(values) if key in allowed}


__all__ = [
    "CODE_PATTERN",
    "COUNT_PATTERN",
    "FORBIDDEN_HEADER_TOKENS",
    "RowError",
    "check_headers",
    "forbidden_headers",
    "header_tokens",
    "is_code",
    "is_count",
    "is_identifier",
    "redact_mapping",
    "safe_errors",
]
