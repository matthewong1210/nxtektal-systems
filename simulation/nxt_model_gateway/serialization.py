"""Strict local JSON validation and content-derived generation digests."""

from collections.abc import Mapping
import hashlib
import json
import math
import re
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from .contracts import FailureCode, GatewayContractError


def _canonical_tree(value: object) -> Any:
    """Detach a JSON value, including our immutable mapping/tuple representation."""
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "JSON keys must be strings")
        return {_canonical_tree(key): _canonical_tree(child) for key, child in value.items()}
    if type(value) in (list, tuple):
        return [_canonical_tree(child) for child in value]
    if value is None or type(value) in (bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "JSON text must be valid Unicode") from None
        return value
    raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "unsupported JSON value")


def _freeze_json(value: object) -> Any:
    """Return a recursively immutable detached JSON value."""
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "JSON keys must be strings")
            return MappingProxyType({_canonical_tree(key): _freeze_json(child) for key, child in value.items()})
        if type(value) in (list, tuple):
            return tuple(_freeze_json(child) for child in value)
        return _canonical_tree(value)
    except RecursionError:
        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "JSON nesting is too deep") from None


def canonical_json(value: object) -> str:
    try:
        return json.dumps(_canonical_tree(value), ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":"))
    except (RecursionError, ValueError, UnicodeError) as error:
        if isinstance(error, GatewayContractError):
            raise
        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "invalid canonical JSON value") from None


def stable_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_local_object_schema(schema: object) -> None:
    """Bound and reject reference-bearing schemas before invoking jsonschema."""
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "schema must describe an object")

    def visit(value: object, depth: int) -> None:
        if depth > 20:
            raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "schema nesting exceeds limit")
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in ("$ref", "$dynamicRef"):
                    raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "schema references are forbidden")
                if key == "$id":
                    if (type(child) is not str or child.startswith(("/", "\\"))
                            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", child)):
                        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "schema identifier must be local")
                visit(child, depth + 1)
        elif type(value) in (list, tuple):
            for child in value:
                visit(child, depth + 1)

    visit(schema, 0)
    schema_copy = _canonical_tree(schema)
    if len(canonical_json(schema_copy).encode("utf-8")) > 65536:
        raise GatewayContractError(FailureCode.INPUT_TOO_LARGE, "schema exceeds size limit")
    try:
        Draft202012Validator.check_schema(schema_copy)
    except (SchemaError, ValueError, TypeError, RecursionError, OverflowError, re.error):
        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "invalid output schema") from None


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def decode_validated_json(raw: bytes, *, schema: Mapping[str, Any]) -> Mapping[str, Any]:
    _validate_local_object_schema(schema)
    schema_copy = _canonical_tree(schema)
    try:
        if type(raw) is not bytes:
            raise ValueError("output must be bytes")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        if type(value) is not dict:
            raise ValueError("output must be an object")
        # json.loads also accepts overflowing exponents and escaped surrogates.
        value = _canonical_tree(value)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise GatewayContractError(FailureCode.MALFORMED_PROVIDER_RESPONSE, "invalid JSON object output") from None
    try:
        Draft202012Validator(schema_copy).validate(value)
    except ValidationError:
        raise GatewayContractError(FailureCode.SCHEMA_MISMATCH, "output does not match schema") from None
    except (ValueError, TypeError, RecursionError, OverflowError, re.error):
        raise GatewayContractError(FailureCode.INVALID_PROVIDER_REQUEST, "invalid output schema") from None
    return _freeze_json(value)
