from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


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

    def test_fetch_rejects_redirect_without_contacting_target(self) -> None:
        target_contacted = threading.Event()

        class RedirectTargetHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                target_contacted.set()
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), RedirectTargetHandler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        target_url = f"http://127.0.0.1:{target.server_address[1]}/healthy"

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(302)
                self.send_header("Location", target_url)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect_thread = threading.Thread(
            target=redirect.serve_forever, daemon=True
        )
        redirect_thread.start()
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            text=True,
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "redirect is not allowed"):
                SMOKE.fetch(
                    f"http://127.0.0.1:{redirect.server_address[1]}/",
                    time.monotonic() + 1,
                    process,
                )
            self.assertFalse(target_contacted.is_set())
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2)
            redirect.shutdown()
            redirect.server_close()
            redirect_thread.join(timeout=2)
            target.shutdown()
            target.server_close()
            target_thread.join(timeout=2)

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

    @unittest.skipIf(os.name == "nt", "POSIX signal lifecycle test")
    def test_managed_process_defers_interrupt_until_launch_is_assigned(self) -> None:
        original_popen = subprocess.Popen

        for interrupt in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=interrupt.name):
                launched: list[subprocess.Popen[str]] = []

                def spawn_then_interrupt(
                    command: list[str], **options: object
                ) -> subprocess.Popen[str]:
                    child = original_popen(command, **options)  # type: ignore[arg-type]
                    launched.append(child)
                    os.kill(os.getpid(), interrupt)
                    return child

                try:
                    with mock.patch.object(
                        SMOKE.subprocess, "Popen", side_effect=spawn_then_interrupt
                    ):
                        with self.assertRaisesRegex(
                            SMOKE.SmokeInterrupted, interrupt.name
                        ):
                            with SMOKE.managed_process(
                                [sys.executable, "-c", "import time; time.sleep(30)"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                text=True,
                            ):
                                self.fail("interrupted launch entered its body")
                    self.assertEqual(len(launched), 1)
                    self.assertIsNotNone(launched[0].poll())
                finally:
                    for child in launched:
                        if child.poll() is None:
                            child.kill()
                        child.wait(timeout=2)

    def test_pre_cleanup_exit_fails_only_an_otherwise_successful_body(self) -> None:
        child_source = """
import sys

print("ready", flush=True)
sys.stdin.readline()
raise SystemExit(17)
"""

        def exit_after_live_poll(process: subprocess.Popen[str]) -> None:
            assert process.stdout is not None
            assert process.stdin is not None
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertIsNone(process.poll())
            process.stdin.write("exit\n")
            process.stdin.flush()
            process.stdin.close()
            self.assertEqual(process.wait(timeout=2), 17)

        successful_child: subprocess.Popen[str] | None = None
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                "exited with status 17 before smoke cleanup began",
            ):
                with SMOKE.managed_process(
                    [sys.executable, "-B", "-c", child_source],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ) as successful_child:
                    exit_after_live_poll(successful_child)
        finally:
            if successful_child is not None:
                if successful_child.poll() is None:
                    successful_child.kill()
                successful_child.wait(timeout=2)
                if successful_child.stdout is not None:
                    successful_child.stdout.close()

        failed_child: subprocess.Popen[str] | None = None
        try:
            with self.assertRaisesRegex(ValueError, "existing body failure"):
                with SMOKE.managed_process(
                    [sys.executable, "-B", "-c", child_source],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                ) as failed_child:
                    exit_after_live_poll(failed_child)
                    raise ValueError("existing body failure")
        finally:
            if failed_child is not None:
                if failed_child.poll() is None:
                    failed_child.kill()
                failed_child.wait(timeout=2)
                if failed_child.stdout is not None:
                    failed_child.stdout.close()

    @unittest.skipIf(os.name == "nt", "POSIX signal lifecycle test")
    def test_stop_process_finishes_cleanup_when_signal_interrupts_wait(self) -> None:
        child_source = """
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("ready", flush=True)
time.sleep(30)
"""

        for interrupt in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=interrupt.name):
                process = subprocess.Popen(
                    [sys.executable, "-B", "-c", child_source],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                sender: threading.Thread | None = None
                try:
                    assert process.stdout is not None
                    self.assertEqual(process.stdout.readline().strip(), "ready")

                    def send_interrupt() -> None:
                        time.sleep(0.05)
                        os.kill(os.getpid(), interrupt)

                    sender = threading.Thread(target=send_interrupt, daemon=True)
                    with self.assertRaisesRegex(
                        SMOKE.SmokeInterrupted, interrupt.name
                    ):
                        with SMOKE._interrupt_as_exception():
                            sender.start()
                            SMOKE.stop_process(
                                process,
                                terminate_timeout_seconds=0.2,
                                kill_timeout_seconds=1,
                            )
                    sender.join(timeout=1)
                    self.assertFalse(sender.is_alive())
                    self.assertIsNotNone(process.poll())
                finally:
                    if sender is not None:
                        sender.join(timeout=1)
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=2)
                    if process.stdout is not None:
                        process.stdout.close()

    @unittest.skipIf(os.name == "nt", "POSIX signal lifecycle test")
    def test_managed_process_cleans_child_on_sigint_and_sigterm(self) -> None:
        wrapper_source = f"""
import importlib.util
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location("smoke_streamlit", {str(SCRIPT)!r})
assert spec and spec.loader
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)
with smoke.managed_process(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    text=True,
) as child:
    print(child.pid, flush=True)
    time.sleep(30)
"""

        for interrupt in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=interrupt.name):
                wrapper = subprocess.Popen(
                    [sys.executable, "-B", "-c", wrapper_source],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                child_pid: int | None = None
                try:
                    assert wrapper.stdout is not None
                    line: list[str] = []
                    reader = threading.Thread(
                        target=lambda: line.append(wrapper.stdout.readline()),
                        daemon=True,
                    )
                    reader.start()
                    reader.join(timeout=2)
                    self.assertFalse(reader.is_alive(), "child PID read timed out")
                    self.assertEqual(len(line), 1)
                    child_pid = int(line[0].strip())
                    os.kill(child_pid, 0)

                    wrapper.send_signal(interrupt)
                    _stdout, stderr = wrapper.communicate(timeout=5)
                    self.assertNotEqual(wrapper.returncode, 0)
                    self.assertIn(
                        f"Streamlit smoke interrupted by {interrupt.name}", stderr
                    )

                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        try:
                            os.kill(child_pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.01)
                    else:
                        self.fail("managed child survived wrapper interruption")
                finally:
                    if wrapper.poll() is None:
                        wrapper.kill()
                        wrapper.wait(timeout=2)
                    if wrapper.stdout is not None:
                        wrapper.stdout.close()
                    if wrapper.stderr is not None:
                        wrapper.stderr.close()
                    if child_pid is not None:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
