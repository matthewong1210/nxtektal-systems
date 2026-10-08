"""Versioned local Manager API over the Pilot Site Agent service.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A deliberately small loopback-only HTTP surface:

- ``GET  /api/v0/health``           noncanonical service diagnostics
- ``GET  /api/v0/state``            latest published state projection
- ``GET  /api/v0/evaluations``      existing evaluation records
- ``GET  /api/v0/recommendations``  manager decision queue projection
- ``GET  /api/v0/briefing``         shift briefing projection
- ``GET  /api/v0/demo``             fixture-only cycle metadata
- ``GET  /api/v1/collection-executions`` saved V3 execution evidence
- ``GET  /api/v1/collection-executions/requests/{id}`` request receipt
- ``POST /api/v0/recommendations/{id}/accept|reject|modify``
- ``POST /api/v0/demo/advance|restart|reset``  fixture-only controls

The transport owns no semantics: every operation delegates to the
service shell, which delegates canonical behavior to the existing
runtime, queue, and ledger contracts. A transport or browser error
cannot roll back already committed evidence, no endpoint creates a physical
command, and manager acceptance stays workflow evidence only.

Security posture (V0): local fixture use only.  The server refuses to
bind anything but loopback, serves no authentication, and must not be
exposed to a facility network or the public internet.  There are no
cross-origin headers: the console is served same-origin.
"""

from __future__ import annotations

import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .contracts import (
    API_SCHEMA_VERSION,
    DISCLAIMER,
    LOOPBACK_HOSTS,
    SiteAgentError,
)
from .service import SiteAgentService

_MAX_BODY_BYTES = 65536
_MAX_ROSTER_BODY_BYTES = 1_048_576
_COLLECTION_EXECUTIONS_PATH = "/api/v1/collection-executions"
_COLLECTION_REQUESTS_PATH = f"{_COLLECTION_EXECUTIONS_PATH}/requests/"
_STAFFING_PATH = "/api/v1/staffing"
_STAFFING_ROSTER_IMPORTS_PATH = f"{_STAFFING_PATH}/roster-imports"
_STAFFING_SUGGESTIONS_PATH = f"{_STAFFING_PATH}/suggestions"
_COLLECTION_ERROR_CODES = frozenset(
    {
        "collection_execution_invalid_request",
        "collection_execution_not_found",
        "collection_execution_request_not_found",
        "collection_execution_conflict",
        "collection_execution_unavailable",
        "collection_execution_result_unknown",
    }
)
_STAFFING_ERROR_DETAILS = {
    "staffing_invalid_request": "request body is invalid",
    "staffing_not_found": "staffing resource was not found",
    "staffing_request_not_found": (
        "no committed request in verified evidence"
    ),
    "staffing_conflict": (
        "request_id is already bound to different content"
    ),
    "staffing_exception_overlap": (
        "an active exception already covers this worker on the service date"
    ),
    "staffing_stale_suggestion": "staffing basis has changed",
    "staffing_suggestion_expired": "the suggested shift has already ended",
    "staffing_busy": "generation capacity is full",
    "staffing_unavailable": "staffing evidence is unavailable",
}
_STAFFING_ERROR_CODES = frozenset(_STAFFING_ERROR_DETAILS)

_STATUS_BY_CODE = {
    "course_ops_unavailable": 503,
    "course_ops_not_found": 404,
    "collection_execution_invalid_request": 400,
    "collection_execution_not_found": 404,
    "collection_execution_request_not_found": 404,
    "collection_execution_conflict": 409,
    "collection_execution_unavailable": 503,
    "collection_execution_result_unknown": 503,
    "planning_invalid_request": 400,
    "planning_not_found": 404,
    "planning_request_not_found": 404,
    "planning_conflict": 409,
    "planning_expired": 409,
    "planning_not_ready": 409,
    "planning_unavailable": 503,
    "planning_result_unknown": 503,
    "staffing_invalid_request": 400,
    "staffing_not_found": 404,
    "staffing_request_not_found": 404,
    "staffing_conflict": 409,
    "staffing_exception_overlap": 409,
    "staffing_stale_suggestion": 409,
    "staffing_suggestion_expired": 409,
    "staffing_busy": 429,
    "staffing_unavailable": 503,
    "unknown_recommendation": 404,
    "task_ops_conflict": 409,
    "task_ops_unavailable": 503,
    "invalid_request": 400,
    "invalid_response_kind": 400,
    "workflow_transition_rejected": 409,
    "manager_response_result_unknown": 503,
    "advance_refused": 409,
    "restart_refused": 409,
    "reset_refused": 409,
    "no_scenario_time": 409,
    "service_stopped": 503,
    "not_found": 404,
    "method_not_allowed": 405,
    "body_too_large": 413,
    "forbidden_origin": 403,
    "evidence_root_collision": 409,
    "workflow_not_ready": 409,
    "composition_failed": 500,
    "runtime_failed": 500,
    "runtime_stopped": 409,
}

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".webmanifest": "application/manifest+json",
}


def _envelope(data: Any) -> dict[str, Any]:
    return {
        "schema": API_SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "data": data,
    }


def _error_payload(code: str, detail: str) -> dict[str, Any]:
    return {
        "schema": API_SCHEMA_VERSION,
        "disclaimer": DISCLAIMER,
        "error": {"code": code, "detail": detail},
    }


def _is_collection_execution_path(path: str) -> bool:
    return path == _COLLECTION_EXECUTIONS_PATH or path.startswith(
        f"{_COLLECTION_EXECUTIONS_PATH}/"
    )


def _classify_staffing_path(path: str) -> tuple[bool, str | None]:
    """Return namespace membership and the exact route's allowed method."""

    if path != _STAFFING_PATH and not path.startswith(
        f"{_STAFFING_PATH}/"
    ):
        return False, None
    parts = tuple(path[1:].split("/"))
    if parts == ("api", "v1", "staffing"):
        return True, "GET"
    if (
        len(parts) == 5
        and parts[:4] == ("api", "v1", "staffing", "dates")
        and bool(parts[4])
    ):
        return True, "GET"
    if (
        len(parts) == 6
        and parts[:4] == ("api", "v1", "staffing", "requests")
        and bool(parts[4])
        and bool(parts[5])
    ):
        return True, "GET"
    if parts in (
        ("api", "v1", "staffing", "roster-imports"),
        ("api", "v1", "staffing", "exceptions"),
        ("api", "v1", "staffing", "suggestions"),
    ):
        return True, "POST"
    if (
        len(parts) == 6
        and parts[:4] == ("api", "v1", "staffing", "exceptions")
        and bool(parts[4])
        and parts[5] in ("cancel", "correct")
    ):
        return True, "POST"
    if (
        len(parts) == 6
        and parts[:4] == ("api", "v1", "staffing", "suggestions")
        and bool(parts[4])
        and parts[5] in ("accept", "modify", "reject")
    ):
        return True, "POST"
    return True, None


def _is_staffing_path(path: str) -> bool:
    in_namespace, _ = _classify_staffing_path(path)
    return in_namespace


def _staffing_method(path: str) -> str | None:
    _, expected_method = _classify_staffing_path(path)
    return expected_method


def _is_staffing_route(method: str, path: str) -> bool:
    return _staffing_method(path) == method


def _valid_identifier(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return False
    if not value.isascii():
        return False
    first = value[0]
    if not ("A" <= first <= "Z" or "a" <= first <= "z" or "0" <= first <= "9"):
        return False
    allowed = "_.:-"
    return all(
        "A" <= character <= "Z"
        or "a" <= character <= "z"
        or "0" <= character <= "9"
        or character in allowed
        for character in value[1:]
    )


def _decode_collection_request_id(segment: str) -> str:
    """Decode one URL segment once, then apply the frozen ASCII ID grammar."""

    decoded = bytearray()
    index = 0
    while index < len(segment):
        character = segment[index]
        if character == "%":
            if index + 2 >= len(segment):
                raise SiteAgentError(
                    "collection_execution_invalid_request",
                    "request_id has malformed percent encoding",
                )
            pair = segment[index + 1 : index + 3]
            if any(value not in "0123456789abcdefABCDEF" for value in pair):
                raise SiteAgentError(
                    "collection_execution_invalid_request",
                    "request_id has malformed percent encoding",
                )
            decoded.append(int(pair, 16))
            index += 3
            continue
        if ord(character) > 127:
            raise SiteAgentError(
                "collection_execution_invalid_request",
                "request_id must be ASCII",
            )
        decoded.append(ord(character))
        index += 1
    try:
        request_id = bytes(decoded).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SiteAgentError(
            "collection_execution_invalid_request",
            "request_id is not valid UTF-8",
        ) from exc
    if not _valid_identifier(request_id) or "/" in request_id:
        raise SiteAgentError(
            "collection_execution_invalid_request",
            "request_id must match the collection execution ID contract",
        )
    return request_id


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NXTSiteAgent/0"
    sys_version = ""
    # Bound a stalled or slow client so keep-alive connections cannot
    # accumulate handler threads indefinitely.
    timeout = 30

    # -- plumbing --------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Silence per-request stderr logging; diagnostics live in the API."""

    @property
    def _service(self) -> SiteAgentService:
        return self.server.service  # type: ignore[attr-defined]

    @property
    def _console_dir(self) -> Path | None:
        return self.server.console_dir  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_code(self, code: str, detail: str) -> None:
        status = _STATUS_BY_CODE.get(code, 500)
        self._send_json(status, _error_payload(code, detail))

    def _allowed_hosts_and_origins(self) -> tuple[set[str], set[str]]:
        host, port = self.server.server_address[:2]  # type: ignore[attr-defined]
        loopback = {"127.0.0.1", "localhost"}
        names = loopback if host in loopback else {str(host)}
        hosts: set[str] = set()
        origins: set[str] = set()
        for name in names:
            hosts.add(name)
            hosts.add(f"{name}:{port}")
            # The service is plain HTTP: the only acceptable browser
            # origin is its own exact scheme + loopback host + bound
            # port. Browsers omit the default port in Origin, so the
            # bare form is acceptable only when bound to port 80.
            origins.add(f"http://{name}:{port}")
            if port == 80:
                origins.add(f"http://{name}")
        return hosts, origins

    def _request_allowed(self) -> tuple[bool, str | None]:
        """Reject cross-origin and rebound-host requests.

        The service is loopback-only with no authentication, so a page
        the operator merely visits must not be able to drive it.  A
        present ``Origin`` header must exactly equal the service's own
        HTTP origin (scheme, loopback host, and bound port): ``null``
        origins (sandboxed iframes, ``file:``/``data:`` documents), a
        mismatched scheme such as ``https`` against this plain-HTTP
        service, malformed values, and foreign origins are all refused.
        A request without an ``Origin`` header is accepted — browsers
        always attach ``Origin`` to cross-site POSTs, so the absent
        header identifies non-browser local tooling, not a page.  A
        foreign ``Host`` header (DNS rebinding) is refused as well.
        """
        hosts, origins = self._allowed_hosts_and_origins()
        host_header = self.headers.get("Host")
        if host_header is not None and host_header not in hosts:
            return False, f"request Host {host_header!r} is not loopback"
        origin = self.headers.get("Origin")
        if origin is not None and origin not in origins:
            return False, f"cross-origin request from {origin!r} refused"
        return True, None

    def _read_body(
        self,
        *,
        max_bytes: int = _MAX_BODY_BYTES,
        strict_json: bool = False,
    ) -> dict[str, Any]:
        # An unsupported framing (chunked) or an error before the body is
        # read leaves bytes on a keep-alive socket that would be parsed
        # as the next request, so close the connection in those cases.
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise SiteAgentError(
                "invalid_request", "chunked request bodies are not supported"
            )
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            self.close_connection = True
            raise SiteAgentError(
                "invalid_request", "Content-Length is not an integer"
            ) from exc
        if length < 0:
            self.close_connection = True
            raise SiteAgentError(
                "invalid_request", "Content-Length must be non-negative"
            )
        if length > max_bytes:
            self.close_connection = True
            raise SiteAgentError(
                "body_too_large",
                f"request bodies are limited to {max_bytes} bytes",
            )
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            if strict_json:

                def reject_pairs(
                    pairs: list[tuple[str, Any]],
                ) -> dict[str, Any]:
                    result: dict[str, Any] = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError(f"duplicate JSON key: {key}")
                        result[key] = value
                    return result

                def reject_constant(value: str) -> None:
                    raise ValueError(f"non-finite JSON constant: {value}")

                def parse_finite_float(value: str) -> float:
                    result = float(value)
                    if not math.isfinite(result):
                        raise ValueError(f"non-finite JSON number: {value}")
                    return result

                payload = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=reject_pairs,
                    parse_constant=reject_constant,
                    parse_float=parse_finite_float,
                )
            else:
                payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            if strict_json:
                raise SiteAgentError(
                    "staffing_invalid_request", "request body is invalid"
                ) from exc
            raise SiteAgentError(
                "invalid_request", f"request body is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            if strict_json:
                raise SiteAgentError(
                    "staffing_invalid_request", "request body is invalid"
                )
            raise SiteAgentError(
                "invalid_request", "request body must be a JSON object"
            )
        return payload

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        path = self.path.split("?", 1)[0]
        try:
            if self.headers.get("Content-Length") or self.headers.get(
                "Transfer-Encoding"
            ):
                # Set this before any Host/Origin response: an unread GET
                # body must never remain on a keep-alive connection.
                self.close_connection = True
            allowed, reason = self._request_allowed()
            if not allowed:
                self._send_error_code("forbidden_origin", reason or "refused")
                return
            if _is_staffing_path(path):
                self._serve_staffing("GET", path, {})
            elif _is_collection_execution_path(path):
                self._serve_collection_executions(path)
            elif path == "/api/v1/course-ops" or path.startswith("/api/v1/course-ops/"):
                self._serve_course(path)
            elif path == "/api/v1/planning" or path.startswith("/api/v1/planning/"):
                self._send_json(200, _envelope(self._route_planning("GET", path, {})))
            elif path == "/api/v0/task-ops" or path.startswith("/api/v0/task-ops/"):
                self._send_json(200, _envelope(self._route_task_ops("GET", path, {})))
            elif path == "/api/v0/health":
                self._send_json(
                    200, _envelope(self._service.health_snapshot())
                )
            elif path == "/api/v0/state":
                self._send_json(200, _envelope(self._service.state_snapshot()))
            elif path == "/api/v0/evaluations":
                self._send_json(
                    200, _envelope(self._service.evaluations_snapshot())
                )
            elif path == "/api/v0/recommendations":
                self._send_json(
                    200, _envelope(self._service.recommendations_snapshot())
                )
            elif path == "/api/v0/briefing":
                self._send_json(
                    200, _envelope(self._service.briefing_snapshot())
                )
            elif path == "/api/v0/demo":
                self._send_json(
                    200, _envelope(self._service.fixture_snapshot())
                )
            elif path.startswith("/api/"):
                self._send_error_code(
                    "not_found", f"unknown API path: {path}"
                )
            else:
                self._serve_static(path)
        except SiteAgentError as exc:
            self._send_error_code(exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - transport boundary
            self._send_json(
                500,
                _error_payload(
                    "internal_error", f"{type(exc).__name__}: {exc}"
                ),
            )

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        path = self.path.split("?", 1)[0]
        try:
            allowed, reason = self._request_allowed()
            if not allowed:
                self.close_connection = True
                self._send_error_code("forbidden_origin", reason or "refused")
                return
            if _is_collection_execution_path(path):
                self._reject_collection_execution_mutation()
                return
            in_staffing, expected_method = _classify_staffing_path(path)
            if in_staffing and expected_method != "POST":
                if self.headers.get("Content-Length") or self.headers.get(
                    "Transfer-Encoding"
                ):
                    self.close_connection = True
                if expected_method is None:
                    self._send_error_code(
                        "staffing_not_found",
                        _STAFFING_ERROR_DETAILS["staffing_not_found"],
                    )
                else:
                    self._send_error_code(
                        "method_not_allowed",
                        f"staffing path requires {expected_method}",
                    )
                return
            if in_staffing:
                max_bytes = (
                    _MAX_ROSTER_BODY_BYTES
                    if path == _STAFFING_ROSTER_IMPORTS_PATH
                    else _MAX_BODY_BYTES
                )
                body = self._read_body(
                    max_bytes=max_bytes,
                    strict_json=True,
                )
                self._serve_staffing("POST", path, body)
                return
            body = self._read_body()
            if path == "/api/v1/course-ops" or path.startswith("/api/v1/course-ops/"):
                raise SiteAgentError("method_not_allowed", "course evidence is read-only")
            elif path == "/api/v1/planning" or path.startswith("/api/v1/planning/"):
                self._send_json(200, _envelope(self._route_planning("POST", path, body)))
            elif path == "/api/v0/task-ops" or path.startswith("/api/v0/task-ops/"):
                self._send_json(200, _envelope(self._route_task_ops("POST", path, body)))
            elif path == "/api/v0/demo/advance":
                self._send_json(200, _envelope(self._service.advance()))
            elif path == "/api/v0/demo/restart":
                self._send_json(
                    200, _envelope(self._service.restart_runtime())
                )
            elif path == "/api/v0/demo/reset":
                self._send_json(200, _envelope(self._service.reset()))
            else:
                response = self._route_recommendation_post(path, body)
                if response is None:
                    self._send_error_code(
                        "not_found", f"unknown API path: {path}"
                    )
                else:
                    self._send_json(200, _envelope(response))
        except SiteAgentError as exc:
            self._send_error_code(exc.code, exc.detail)
        except Exception as exc:  # noqa: BLE001 - transport boundary
            self._send_json(
                500,
                _error_payload(
                    "internal_error", f"{type(exc).__name__}: {exc}"
                ),
            )

    def do_PUT(self) -> None:  # noqa: N802 - stdlib handler naming
        self._serve_other_mutation_method()

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler naming
        self._serve_other_mutation_method()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler naming
        self._serve_other_mutation_method()

    def _serve_other_mutation_method(self) -> None:
        path = self.path.split("?", 1)[0]
        is_collection = _is_collection_execution_path(path)
        is_staffing, expected_method = _classify_staffing_path(path)
        if not is_collection and not is_staffing:
            self.send_error(501, f"Unsupported method ({self.command!r})")
            return
        allowed, reason = self._request_allowed()
        if not allowed:
            self.close_connection = True
            self._send_error_code("forbidden_origin", reason or "refused")
            return
        if is_staffing:
            if self.headers.get("Content-Length") or self.headers.get(
                "Transfer-Encoding"
            ):
                self.close_connection = True
            if expected_method is None:
                self._send_error_code(
                    "staffing_not_found",
                    _STAFFING_ERROR_DETAILS["staffing_not_found"],
                )
            else:
                self._send_error_code(
                    "method_not_allowed",
                    f"staffing path requires {expected_method}",
                )
            return
        self._reject_collection_execution_mutation()

    def _reject_collection_execution_mutation(self) -> None:
        # Do not parse a body for a read-only namespace. Leaving body bytes on
        # keep-alive would desynchronise the next request, so close when any
        # framing claims a body.
        if self.headers.get("Content-Length") or self.headers.get(
            "Transfer-Encoding"
        ):
            self.close_connection = True
        self._send_error_code(
            "method_not_allowed", "collection execution evidence is read-only"
        )

    def _serve_collection_executions(self, path: str) -> None:
        if path == _COLLECTION_EXECUTIONS_PATH:
            callback = self.server.collection_executions
            parser = self.server.collection_execution_parser
            if callback is None or parser is None:
                raise SiteAgentError(
                    "collection_execution_unavailable",
                    "collection execution evidence is unavailable",
                )
            data = self._call_collection_parser(
                parser,
                self._call_collection_reader(callback),
                "ExecutionSnapshot",
            )
            self._send_json(200, _envelope(data))
            return

        if path.startswith(_COLLECTION_REQUESTS_PATH):
            segment = path[len(_COLLECTION_REQUESTS_PATH) :]
            if "/" in segment:
                raise SiteAgentError(
                    "collection_execution_not_found",
                    "unknown collection execution evidence path",
                )
            request_id = _decode_collection_request_id(segment)
            callback = self.server.collection_execution_request
            parser = self.server.collection_execution_parser
            if callback is None or parser is None:
                raise SiteAgentError(
                    "collection_execution_unavailable",
                    "collection execution evidence is unavailable",
                )
            data = self._call_collection_parser(
                parser,
                self._call_collection_reader(callback, request_id),
                "RequestReceipt",
            )
            if data.get("request_id") != request_id:
                raise SiteAgentError(
                    "collection_execution_unavailable",
                    "collection execution evidence is unavailable",
                )
            self._send_json(200, _envelope(data))
            return

        raise SiteAgentError(
            "collection_execution_not_found",
            "unknown collection execution evidence path",
        )

    @staticmethod
    def _call_collection_reader(callback: Callable[..., object], *args: str) -> object:
        try:
            return callback(*args)
        except Exception as exc:  # noqa: BLE001 - injected read boundary
            code = getattr(exc, "code", None)
            detail = getattr(exc, "detail", None)
            if (
                code in _COLLECTION_ERROR_CODES
                and isinstance(detail, str)
                and bool(detail)
            ):
                raise SiteAgentError(code, detail) from exc
            raise SiteAgentError(
                "collection_execution_unavailable",
                "collection execution evidence is unavailable",
            ) from exc

    @staticmethod
    def _call_collection_parser(
        parser: Callable[[object, str], dict[str, Any]],
        value: object,
        definition: str,
    ) -> dict[str, Any]:
        try:
            parsed = parser(value, definition)
            if not isinstance(parsed, dict):
                raise TypeError("collection execution parser returned non-object")
            return parsed
        except Exception as exc:  # noqa: BLE001 - injected parser boundary
            raise SiteAgentError(
                "collection_execution_unavailable",
                "collection execution evidence is unavailable",
            ) from exc

    def _serve_course(self, path: str) -> None:
        # Evidence selection and integrity belong to the injected reader.
        # The transport accepts no filesystem paths or mutation callback.
        if path == "/api/v1/course-ops":
            callback = self.server.course_operations
            if callback is None:
                raise SiteAgentError("not_found", "course evidence is not configured")
            self._send_json(200, _envelope(callback()))
            return
        prefix = "/api/v1/course-ops/media/"
        parts = path[len(prefix):].split("/") if path.startswith(prefix) else []
        callback = self.server.course_media
        if len(parts) != 2 or callback is None:
            raise SiteAgentError("course_ops_not_found", "unknown course evidence path")
        round_id, name = parts
        frame_id = name.removesuffix(".png")
        if not (
            len(round_id) == 16 and round_id.startswith("round-")
            and round_id[6:].isascii() and round_id[6:].isdigit()
            and name.endswith(".png") and len(frame_id) == 12
            and frame_id.startswith("frame-") and frame_id[6:].isascii() and frame_id[6:].isdigit()
        ):
            raise SiteAgentError("course_ops_not_found", "unknown course evidence path")
        query = self.path.partition("?")[2]
        expected_sha = query.removeprefix("sha256=")
        if not (query.startswith("sha256=") and len(expected_sha) == 64
                and all(character in "0123456789abcdef" for character in expected_sha)):
            raise SiteAgentError("course_ops_not_found", "image snapshot hash is required")
        body = callback(round_id, frame_id, expected_sha)
        if not isinstance(body, bytes) or len(body) > 4 * 1024 * 1024 or not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise SiteAgentError("course_ops_unavailable", "invalid course image response")
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _serve_staffing(
        self, method: str, path: str, body: dict[str, Any]
    ) -> None:
        expected_method = _staffing_method(path)
        if expected_method is None:
            raise SiteAgentError(
                "staffing_not_found",
                _STAFFING_ERROR_DETAILS["staffing_not_found"],
            )
        if not _is_staffing_route(method, path):
            raise SiteAgentError(
                "method_not_allowed",
                f"staffing path requires {expected_method}",
            )
        status = (
            202
            if method == "POST" and path == _STAFFING_SUGGESTIONS_PATH
            else 200
        )
        self._send_json(
            status,
            _envelope(self._route_staffing(method, path, body)),
        )

    def _route_staffing(
        self, method: str, path: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        callback = self.server.staffing_operations  # type: ignore[attr-defined]
        if callback is None:
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            )
        try:
            result = callback(method, path, body)
            if not isinstance(result, dict):
                raise TypeError("staffing callback returned non-dictionary")
            normalized = json.loads(
                json.dumps(
                    result,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            )
            if type(normalized) is not dict:
                raise TypeError("staffing callback returned non-object JSON")
            return normalized
        except SiteAgentError as exc:
            if exc.code in _STAFFING_ERROR_CODES:
                raise SiteAgentError(
                    exc.code, _STAFFING_ERROR_DETAILS[exc.code]
                ) from exc
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - injected callback boundary
            raise SiteAgentError(
                "staffing_unavailable", "staffing evidence is unavailable"
            ) from exc

    def _route_planning(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        callback = self.server.planning_operations
        if callback is None:
            raise SiteAgentError("planning_unavailable", "planning is not configured for this service")
        return callback(method, path, body)

    def _route_task_ops(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        # Optional composition-root route. It cannot intercept recommendations
        # or fixture controls, and receives no service/runtime object.
        handler = self.server.task_operations  # type: ignore[attr-defined]
        if handler is None:
            raise SiteAgentError("not_found", "task operations are not enabled by this local runner")
        return handler(method, path, body)

    def _route_recommendation_post(
        self, path: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        prefix = "/api/v0/recommendations/"
        if not path.startswith(prefix):
            return None
        remainder = path[len(prefix):]
        parts = remainder.split("/")
        if len(parts) != 2 or not parts[0]:
            return None
        recommendation_id, kind = parts
        if kind not in ("accept", "reject", "modify"):
            return None
        allowed_keys = {
            "operator_id",
            "reason_code",
            "note",
            "replacement_action",
            "replacement_robot_id",
            "replacement_execute_before",
            "responded_at",
        }
        unknown = sorted(set(body) - allowed_keys)
        if unknown:
            raise SiteAgentError(
                "invalid_request", f"unknown request fields: {unknown}"
            )
        return self._service.respond(
            recommendation_id,
            kind=kind,
            operator_id=body.get("operator_id", ""),
            reason_code=body.get("reason_code", ""),
            note=body.get("note"),
            replacement_action=body.get("replacement_action"),
            replacement_robot_id=body.get("replacement_robot_id"),
            replacement_execute_before=body.get(
                "replacement_execute_before"
            ),
            responded_at=body.get("responded_at"),
        )

    # -- static console --------------------------------------------------

    def _serve_static(self, path: str) -> None:
        root = self._console_dir
        if root is None:
            self._send_error_code(
                "not_found",
                "no console build is configured; the Manager API lives "
                "under /api/v0/",
            )
            return
        relative = path.lstrip("/")
        candidate = root / relative if relative else root / "index.html"
        if candidate.is_dir():
            candidate = candidate / "index.html"
        try:
            resolved = candidate.resolve()
            resolved_root = root.resolve()
        except OSError:
            self._send_error_code("not_found", "unreadable console path")
            return
        if not resolved.is_relative_to(resolved_root):
            self._send_error_code("not_found", "path escapes the console root")
            return
        if not resolved.is_file():
            self._send_error_code("not_found", f"no console file at {path}")
            return
        content_type = _CONTENT_TYPES.get(
            resolved.suffix.lower(), "application/octet-stream"
        )
        try:
            body = resolved.read_bytes()
        except OSError:
            self._send_error_code("not_found", "unreadable console file")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        service: SiteAgentService,
        console_dir: Path | None,
        task_operations: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        planning_operations: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        course_operations: Callable[[], dict[str, Any]] | None = None,
        course_media: Callable[[str, str, str], bytes] | None = None,
        collection_executions: Callable[[], dict[str, Any]] | None = None,
        collection_execution_request: Callable[[str], dict[str, Any]] | None = None,
        collection_execution_parser: Callable[[object, str], dict[str, Any]] | None = None,
        staffing_operations: Callable[
            [str, str, dict[str, Any]], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        self.service = service
        self.console_dir = console_dir
        self.task_operations = task_operations
        self.planning_operations = planning_operations
        self.course_operations = course_operations
        self.course_media = course_media
        self.collection_executions = collection_executions
        self.collection_execution_request = collection_execution_request
        self.collection_execution_parser = collection_execution_parser
        self.staffing_operations = staffing_operations
        super().__init__(address, _Handler)


class SiteAgentApiServer:
    """Loopback-only HTTP server wrapper around one service instance."""

    def __init__(
        self,
        service: SiteAgentService,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        console_dir: Path | None = None,
        task_operations: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        planning_operations: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        course_operations: Callable[[], dict[str, Any]] | None = None,
        course_media: Callable[[str, str, str], bytes] | None = None,
        collection_executions: Callable[[], dict[str, Any]] | None = None,
        collection_execution_request: Callable[[str], dict[str, Any]] | None = None,
        collection_execution_parser: Callable[[object, str], dict[str, Any]] | None = None,
        staffing_operations: Callable[
            [str, str, dict[str, Any]], dict[str, Any]
        ]
        | None = None,
    ) -> None:
        if host not in LOOPBACK_HOSTS:
            raise SiteAgentError(
                "nonlocal_bind_refused",
                "the v0 service is local-only and binds loopback hosts "
                f"only ({', '.join(LOOPBACK_HOSTS)}); refusing {host!r}",
            )
        if isinstance(port, bool) or not isinstance(port, int) or port < 0:
            raise SiteAgentError(
                "invalid_request", "port must be a non-negative integer"
            )
        resolved_console: Path | None = None
        if console_dir is not None:
            resolved_console = Path(console_dir)
            if not resolved_console.is_dir():
                raise SiteAgentError(
                    "console_dir_missing",
                    f"console directory does not exist: {resolved_console}",
                )
        self._service = service
        self._server = _Server(
            (host, port),
            service,
            resolved_console,
            task_operations,
            planning_operations,
            course_operations,
            course_media,
            collection_executions,
            collection_execution_request,
            collection_execution_parser,
            staffing_operations,
        )
        self._thread: threading.Thread | None = None
        self._serving = False

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def serve_forever(self) -> None:
        self._serving = True
        try:
            self._server.serve_forever()
        finally:
            self._serving = False

    def start_background(self) -> None:
        if self._thread is not None:
            raise SiteAgentError(
                "invalid_request", "the server is already running"
            )
        self._serving = True
        thread = threading.Thread(
            target=self._server.serve_forever,
            name="site-agent-api",
            daemon=True,
        )
        thread.start()
        self._thread = thread

    def shutdown(self) -> None:
        # BaseServer.shutdown() blocks on an event only set by
        # serve_forever(), so calling it before serving ever started
        # would hang; skip straight to closing the socket in that case.
        if self._serving:
            self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._serving = False


__all__ = ["SiteAgentApiServer"]
