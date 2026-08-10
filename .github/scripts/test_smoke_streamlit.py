from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path(__file__).with_name("smoke_streamlit.py")
SPEC = importlib.util.spec_from_file_location("smoke_streamlit", SCRIPT)
assert SPEC and SPEC.loader
SMOKE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SMOKE)


class _HealthyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _DripHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        for byte in (b"a", b"b", b"c"):
            try:
                self.wfile.write(byte)
                self.wfile.flush()
            except BrokenPipeError:
                break
            time.sleep(0.1)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _ExitedProcess:
    def poll(self) -> int:
        return 98


class SmokeStreamlitTests(unittest.TestCase):
    def test_kernel_allocated_port_is_valid(self) -> None:
        self.assertIn(SMOKE.available_port(), range(1, 65536))

    def test_healthy_unrelated_server_cannot_mask_exited_child(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with self.assertRaisesRegex(RuntimeError, "exited with status 98"):
                SMOKE.fetch(
                    f"http://127.0.0.1:{port}/",
                    time.monotonic() + 1,
                    _ExitedProcess(),
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_live_child_fetch_reads_bounded_healthy_response(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            text=True,
        )
        try:
            body = SMOKE.fetch(
                f"http://127.0.0.1:{server.server_address[1]}/",
                time.monotonic() + 1,
                process,
            )
            self.assertEqual(body, b"ok")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_healthy_unrelated_server_cannot_mask_live_non_server_child(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            text=True,
        )
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/_stcore/health", timeout=1
            ) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b"ok")
            with tempfile.TemporaryDirectory() as log_dir:
                log_path = Path(log_dir) / "child.log"
                log_path.write_text("child is alive but serves nothing\n", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "did not announce"):
                    SMOKE.wait_for_startup(
                        f"http://127.0.0.1:{port}",
                        log_path,
                        time.monotonic() + 0.25,
                        process,
                    )
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_startup_log_allows_ansi_around_exact_endpoint(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            text=True,
        )
        try:
            with tempfile.TemporaryDirectory() as log_dir:
                log_path = Path(log_dir) / "streamlit.log"
                log_path.write_text(
                    "\x1b[34mURL: http://127.0.0.1:54321\x1b[0m\n",
                    encoding="utf-8",
                )
                SMOKE.wait_for_startup(
                    "http://127.0.0.1:54321",
                    log_path,
                    time.monotonic() + 1,
                    process,
                )
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)

    def test_response_body_cannot_outlive_overall_deadline(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _DripHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            text=True,
        )
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError, "failed before timeout"):
                SMOKE.fetch(
                    f"http://127.0.0.1:{server.server_address[1]}/",
                    started + 0.05,
                    process,
                )
            self.assertLess(time.monotonic() - started, 0.25)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
