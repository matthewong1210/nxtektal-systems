#!/usr/bin/env python3
"""Launch the read-only demo and verify its live health and HTTP surfaces."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import math
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SIMULATION_ROOT = ROOT / "simulation"
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_RESPONSE_BYTES = 1_048_576


class SmokeInterrupted(RuntimeError):
    """Raised after an interrupt so managed child cleanup can complete."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        response: object,
        code: int,
        _message: str,
        _headers: object,
        _new_url: str,
    ) -> None:
        try:
            response.close()  # type: ignore[attr-defined]
        finally:
            raise RuntimeError(
                f"HTTP {code} redirect is not allowed for {request.full_url}"
            )


HTTP_OPENER = urllib.request.build_opener(_RejectRedirects())


@contextmanager
def _interrupt_as_exception(
    *, defer: bool = False
) -> Iterator[Callable[[], None]]:
    signal_name: str | None = None
    delivered = False
    deferred = defer
    originals: dict[signal.Signals, object] = {}

    def raise_interruption() -> None:
        nonlocal delivered
        if signal_name is None or delivered:
            return
        delivered = True
        raise SmokeInterrupted(f"Streamlit smoke interrupted by {signal_name}")

    def resume_interrupts() -> None:
        nonlocal deferred
        deferred = False
        raise_interruption()

    def handle_interrupt(signum: int, _frame: object) -> None:
        nonlocal signal_name
        if signal_name is not None:
            return
        signal_name = signal.Signals(signum).name
        if not deferred:
            raise_interruption()

    managed_signals = (signal.SIGINT, signal.SIGTERM)
    try:
        for candidate in managed_signals:
            originals[candidate] = signal.getsignal(candidate)
            signal.signal(candidate, handle_interrupt)
        yield resume_interrupts
    finally:
        for candidate, original in originals.items():
            signal.signal(candidate, original)
        raise_interruption()


def _finish_process_cleanup(
    process: subprocess.Popen[str],
    terminate_timeout_seconds: float,
    kill_timeout_seconds: float,
    reject_preexisting_exit: bool,
) -> None:
    returncode = process.poll()
    if returncode is not None:
        if reject_preexisting_exit:
            raise RuntimeError(
                "Streamlit exited with status "
                f"{returncode} before smoke cleanup began"
            )
        return
    process.terminate()
    try:
        process.wait(timeout=terminate_timeout_seconds)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=kill_timeout_seconds)


def stop_process(
    process: subprocess.Popen[str],
    *,
    terminate_timeout_seconds: float = 5,
    kill_timeout_seconds: float = 5,
    reject_preexisting_exit: bool = False,
) -> None:
    interruption: SmokeInterrupted | None = None
    try:
        _finish_process_cleanup(
            process,
            terminate_timeout_seconds,
            kill_timeout_seconds,
            reject_preexisting_exit,
        )
    except SmokeInterrupted as exc:
        interruption = exc
        _finish_process_cleanup(
            process,
            terminate_timeout_seconds,
            kill_timeout_seconds,
            False,
        )
    if interruption is not None:
        raise interruption


@contextmanager
def managed_process(
    command: list[str], **popen_options: object
) -> Iterator[subprocess.Popen[str]]:
    """Launch a child whose cleanup survives SIGINT and SIGTERM."""

    process: subprocess.Popen[str] | None = None
    body_completed = False

    with _interrupt_as_exception(defer=True) as resume_interrupts:
        try:
            process = subprocess.Popen(command, **popen_options)  # type: ignore[arg-type]
            resume_interrupts()
            yield process
            body_completed = True
        finally:
            if process is not None:
                stop_process(
                    process, reject_preexisting_exit=body_completed
                )


def available_port() -> int:
    """Ask the kernel for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_bounded_response(response: object, deadline: float) -> bytes:
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if headers is not None else None
    declared_length: int | None = None
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise RuntimeError("HTTP response has invalid Content-Length") from exc
        if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"HTTP response exceeds {MAX_RESPONSE_BYTES} byte smoke limit"
            )

    buffered = getattr(response, "fp", None)
    raw = getattr(buffered, "raw", None)
    response_socket = getattr(raw, "_sock", None)
    read_one = getattr(response, "read1", None)
    if response_socket is None or not callable(read_one):
        raise RuntimeError("HTTP response does not expose bounded socket reads")

    body = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("HTTP response body exceeded the smoke deadline")
        response_socket.settimeout(max(0.001, min(2.0, remaining)))
        chunk = read_one(min(65_536, MAX_RESPONSE_BYTES + 1 - len(body)))
        if time.monotonic() > deadline:
            raise RuntimeError("HTTP response body exceeded the smoke deadline")
        if not chunk:
            return bytes(body)
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"HTTP response exceeds {MAX_RESPONSE_BYTES} byte smoke limit"
            )
        if declared_length is not None:
            if len(body) > declared_length:
                raise RuntimeError("HTTP response exceeded its declared Content-Length")
            if len(body) == declared_length:
                return bytes(body)


def fetch(url: str, deadline: float, process: subprocess.Popen[str]) -> bytes:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"Streamlit exited with status {returncode} before serving {url}"
            )
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with HTTP_OPENER.open(
                url, timeout=max(0.001, min(2.0, remaining))
            ) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                body = _read_bounded_response(response, deadline)
                returncode = process.poll()
                if returncode is not None:
                    raise RuntimeError(
                        f"Streamlit exited with status {returncode} while serving {url}"
                    )
                print(f"{url}: HTTP {response.status}, {len(body)} bytes")
                return body
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(0.5, remaining))
    raise RuntimeError(f"{url} failed before timeout: {last_error}")


def wait_for_startup(
    url: str,
    log_path: Path,
    deadline: float,
    process: subprocess.Popen[str],
) -> None:
    """Require the launched process to announce the exact endpoint it owns."""
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"Streamlit exited with status {returncode} before announcing {url}"
            )
        log_text = ANSI_ESCAPE.sub(
            "", log_path.read_text(encoding="utf-8", errors="replace")
        )
        exact_url = re.compile(rf"(?<![\w.+-]){re.escape(url)}/?(?=\s|$)")
        if exact_url.search(log_text):
            print(f"Streamlit announced {url}")
            return
        time.sleep(0.1)
    raise RuntimeError(f"Streamlit did not announce {url} before timeout")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args()

    bundle = args.bundle.resolve()
    required = {"episode.json", "layout.json"}
    missing = sorted(name for name in required if not (bundle / name).is_file())
    if missing:
        parser.error("bundle is missing: " + ", ".join(missing))
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be finite and greater than zero")
    port = args.port if args.port is not None else available_port()

    environment = os.environ.copy()
    environment["NXT_DEMO_BUNDLE"] = str(bundle)
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "nxt_range_demo/app.py",
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.fileWatcherType=none",
        "--browser.gatherUsageStats=false",
    ]

    with tempfile.TemporaryDirectory() as log_dir:
        log_path = Path(log_dir) / "streamlit.log"
        with log_path.open("w", encoding="utf-8") as log:
            with managed_process(
                command,
                cwd=SIMULATION_ROOT,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            ) as process:
                try:
                    deadline = time.monotonic() + args.timeout_seconds
                    base_url = f"http://127.0.0.1:{port}"
                    wait_for_startup(base_url, log_path, deadline, process)
                    health = fetch(f"{base_url}/_stcore/health", deadline, process)
                    if health.strip() != b"ok":
                        raise RuntimeError(
                            f"unexpected Streamlit health body: {health!r}"
                        )
                    root = fetch(f"{base_url}/", deadline, process)
                    if not root:
                        raise RuntimeError("Streamlit root response was empty")
                    returncode = process.poll()
                    if returncode is not None:
                        raise RuntimeError(
                            "Streamlit exited with status "
                            f"{returncode} before smoke completed"
                        )
                except Exception:
                    print(
                        log_path.read_text(encoding="utf-8", errors="replace"),
                        file=sys.stderr,
                    )
                    raise

    print("Streamlit live smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
