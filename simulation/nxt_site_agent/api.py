"""Versioned local Manager API over the Pilot Site Agent service.

SIMULATED PILOT SCENARIO — NOT LIVE CUSTOMER DATA.

A deliberately small loopback-only HTTP surface:

- ``GET  /api/v0/health``           noncanonical service diagnostics
- ``GET  /api/v0/state``            latest published state projection
- ``GET  /api/v0/evaluations``      existing evaluation records
- ``GET  /api/v0/recommendations``  manager decision queue projection
- ``GET  /api/v0/briefing``         shift briefing projection
- ``GET  /api/v0/demo``             fixture-only cycle metadata
- ``POST /api/v0/recommendations/{id}/accept|reject|modify``
- ``POST /api/v0/demo/advance|restart|reset``  fixture-only controls

The transport owns no semantics: every operation delegates to the
service shell, which delegates canonical behavior to the existing
runtime, queue, and ledger contracts.  A transport or browser error
can never mutate canonical evidence, no endpoint creates a physical
command, and manager acceptance stays workflow evidence only.

Security posture (V0): local fixture use only.  The server refuses to
bind anything but loopback, serves no authentication, and must not be
exposed to a facility network or the public internet.  There are no
cross-origin headers: the console is served same-origin.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .contracts import (
    API_SCHEMA_VERSION,
    DISCLAIMER,
    LOOPBACK_HOSTS,
    SiteAgentError,
)
from .service import SiteAgentService

_MAX_BODY_BYTES = 65536

_STATUS_BY_CODE = {
    "unknown_recommendation": 404,
    "invalid_request": 400,
    "invalid_response_kind": 400,
    "workflow_transition_rejected": 409,
    "advance_refused": 409,
    "restart_refused": 409,
    "reset_refused": 409,
    "no_scenario_time": 409,
    "service_stopped": 503,
    "not_found": 404,
    "method_not_allowed": 405,
    "body_too_large": 413,
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


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "NXTSiteAgent/0"
    sys_version = ""

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
        self.end_headers()
        self.wfile.write(body)

    def _send_error_code(self, code: str, detail: str) -> None:
        status = _STATUS_BY_CODE.get(code, 500)
        self._send_json(status, _error_payload(code, detail))

    def _read_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise SiteAgentError(
                "invalid_request", "Content-Length is not an integer"
            ) from exc
        if length < 0:
            raise SiteAgentError(
                "invalid_request", "Content-Length must be non-negative"
            )
        if length > _MAX_BODY_BYTES:
            raise SiteAgentError(
                "body_too_large",
                f"request bodies are limited to {_MAX_BODY_BYTES} bytes",
            )
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise SiteAgentError(
                "invalid_request", f"request body is not valid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise SiteAgentError(
                "invalid_request", "request body must be a JSON object"
            )
        return payload

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        path = self.path.split("?", 1)[0]
        try:
            if path == "/api/v0/health":
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
            body = self._read_body()
            if path == "/api/v0/demo/advance":
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
    ) -> None:
        self.service = service
        self.console_dir = console_dir
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
        self._server = _Server((host, port), service, resolved_console)
        self._thread: threading.Thread | None = None

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
        self._server.serve_forever()

    def start_background(self) -> None:
        if self._thread is not None:
            raise SiteAgentError(
                "invalid_request", "the server is already running"
            )
        thread = threading.Thread(
            target=self._server.serve_forever,
            name="site-agent-api",
            daemon=True,
        )
        thread.start()
        self._thread = thread

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


__all__ = ["SiteAgentApiServer"]
