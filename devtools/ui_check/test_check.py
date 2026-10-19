"""Failure-path tests for check.py that never start Home Assistant."""

from __future__ import annotations

import io
import logging
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import check  # noqa: E402
import wallpad  # noqa: E402

from custom_components.kocom_wallpad.controller import KocomController  # noqa: E402
from custom_components.kocom_wallpad.models import DeviceState  # noqa: E402

if TYPE_CHECKING:
    from custom_components.kocom_wallpad.gateway import KocomGateway

STAMP = "2026-10-19 18:46:41.123"
CUSTOM_WARNING = (
    f"{STAMP} WARNING (SyncWorker_0) [homeassistant.loader] We found a custom integration "
    "kocom_wallpad which has not been tested by Home Assistant. This component might cause "
    "stability problems, be sure to disable it if you experience issues with Home Assistant"
)
MET_ERROR = f"{STAMP} ERROR (MainThread) [homeassistant.components.met] Error fetching met data"
RADIO_ERROR = (
    f"{STAMP} ERROR (MainThread) [homeassistant.components.radio_browser] "
    "Could not connect to Radio Browser API"
)
INTEGRATION_ERROR = (
    f"{STAMP} ERROR (MainThread) [custom_components.kocom_wallpad.gateway] "
    "Unexpected error in kocom_wallpad read loop"
)
INTEGRATION_CRITICAL = (
    f"{STAMP} CRITICAL (MainThread) [homeassistant.config_entries] "
    "Error setting up entry for kocom_wallpad"
)
INTEGRATION_FRAME = (
    '  File "/work/custom_components/kocom_wallpad/gateway.py", line 120, '
    "in _read_loop"
)

INTEGRATION_FILE = "/work/custom_components/kocom_wallpad/gateway.py"
INTEGRATION_SOURCE = "def _read_loop():\n    raise ValueError('synthetic Kocom failure')\n"
# The record HA logs around a task's traceback; it does not name kocom_wallpad itself.
TASK_RECORD = f"{STAMP} ERROR (MainThread) [homeassistant] Error doing job: Task exception was never retrieved"


# The format string and datefmt HA's console and log file use (homeassistant/bootstrap.py).
HA_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s (%(threadName)s) [%(name)s] %(message)s"
HA_DATEFMT = "%Y-%m-%d %H:%M:%S"


def logged(logger_name: str, level: int, message: str, thread_name: str | None = None) -> list[str]:
    """The lines Python's logging module writes for one record through HA's formatter.

    With `thread_name`, the record is logged from a thread of that name.
    """
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(HA_FORMAT, datefmt=HA_DATEFMT))
    logger = logging.getLogger(logger_name)
    saved = (logger.level, logger.propagate)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        if thread_name is None:
            logger.log(level, message)
        else:
            thread = threading.Thread(target=logger.log, args=(level, message), name=thread_name)
            thread.start()
            thread.join()
    finally:
        logger.removeHandler(handler)
        logger.level, logger.propagate = saved
    return stream.getvalue().splitlines()


# Records of a logger outside custom_components whose first message line does not name the domain;
# only the continuation line does.
CONTINUATION = "  config entry 127.0.0.1: kocom_wallpad"
MULTILINE_MESSAGE = f"Error setting up entry 코콤 월패드\n{CONTINUATION}"


def integration_function() -> types.FunctionType:
    """Return a function compiled with a file name inside custom_components/kocom_wallpad."""
    module = compile(INTEGRATION_SOURCE, INTEGRATION_FILE, "exec")
    code = next(const for const in module.co_consts if isinstance(const, types.CodeType))
    return types.FunctionType(code, {})


def raised(function: Callable[[], object]) -> BaseException:
    """Call `function` and return what it raised, its traceback kept (assertRaises drops it)."""
    try:
        function()
    except BaseException as err:  # the test inspects whatever the function raised
        return err
    raise AssertionError("nothing was raised")


def group_of_integration_errors() -> None:
    raise ExceptionGroup(
        "unhandled errors in a TaskGroup", [cast(Exception, raised(integration_function())) for _ in range(2)]
    )


def formatted(err: BaseException) -> list[str]:
    """The log lines Python's traceback module writes for `err` after HA's record line."""
    return [TASK_RECORD, *"".join(traceback.format_exception(err)).splitlines()]


def integration_frames(lines: list[str]) -> list[str]:
    return [line for line in lines if INTEGRATION_FILE in line]


class ScanLogTest(unittest.TestCase):
    def test_reports_integration_error_critical_and_frame(self) -> None:
        lines = [INTEGRATION_ERROR, INTEGRATION_CRITICAL, "Traceback (most recent call last):", INTEGRATION_FRAME]
        critical_record = "\n".join(lines[1:])
        self.assertEqual(check.scan_log(lines), [INTEGRATION_ERROR, critical_record, INTEGRATION_FRAME])

    def test_reports_frames_of_a_formatted_exception(self) -> None:
        lines = formatted(raised(integration_function()))
        frames = integration_frames(lines)
        self.assertTrue(frames)
        # The task record names the domain through its traceback's continuation lines.
        self.assertEqual(check.scan_log(lines), ["\n".join(lines), *frames])

    def test_reports_frames_of_an_exception_group(self) -> None:
        lines = formatted(raised(group_of_integration_errors))
        frames = integration_frames(lines)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(frame.lstrip().startswith("|") for frame in frames))
        self.assertNotIn("kocom_wallpad", lines[0])
        self.assertEqual(check.scan_log(lines), ["\n".join(lines), *frames])

    def assert_found_only_through_continuation(self, level: int) -> None:
        lines = logged("homeassistant.setup", level, MULTILINE_MESSAGE)
        self.assertEqual(len(lines), 2)
        self.assertIsNotNone(check.RECORD.match(lines[0]))
        self.assertIn(f"{logging.getLevelName(level)} (", lines[0])
        self.assertNotIn("kocom_wallpad", lines[0])
        self.assertEqual(lines[1], CONTINUATION)
        self.assertEqual(check.scan_log(lines), ["\n".join(lines)])
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(check.scan_log([line]), [])

    def test_reports_multiline_error_naming_domain_on_continuation(self) -> None:
        self.assert_found_only_through_continuation(logging.ERROR)

    def test_reports_multiline_critical_naming_domain_on_continuation(self) -> None:
        self.assert_found_only_through_continuation(logging.CRITICAL)

    def test_reports_error_logged_from_a_thread_named_with_parentheses(self) -> None:
        lines = logged("homeassistant.setup", logging.ERROR, MULTILINE_MESSAGE, "Thread-3 (_run)")
        self.assertEqual(len(lines), 2)
        self.assertIn("(Thread-3 (_run))", lines[0])
        self.assertIsNotNone(check.RECORD.match(lines[0]))
        self.assertEqual(check.scan_log(lines), ["\n".join(lines)])

    def test_ignores_multiline_warning_naming_domain_on_continuation(self) -> None:
        lines = logged("homeassistant.setup", logging.WARNING, MULTILINE_MESSAGE)
        self.assertEqual(len(lines), 2)
        self.assertIn("kocom_wallpad", lines[1])
        self.assertEqual(check.scan_log(lines), [])

    def test_ignores_multiline_error_of_another_logger_without_domain(self) -> None:
        lines = logged("homeassistant.components.met", logging.ERROR, "Error fetching met data\n  timeout")
        self.assertEqual(len(lines), 2)
        self.assertEqual(check.scan_log(lines), [])

    def test_ignores_foreign_errors_and_custom_warning(self) -> None:
        self.assertEqual(check.scan_log([MET_ERROR, RADIO_ERROR, CUSTOM_WARNING]), [])


class LogVerdictTest(unittest.TestCase):
    def verdict(self, *lines: str) -> str | None:
        with tempfile.TemporaryDirectory() as out:
            Path(out, check.LOG_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")
            return check.log_verdict(Path(out))

    def test_warning_and_foreign_errors_pass(self) -> None:
        self.assertIsNone(self.verdict(CUSTOM_WARNING, MET_ERROR, RADIO_ERROR))

    def test_integration_error_fails(self) -> None:
        verdict = self.verdict(CUSTOM_WARNING, MET_ERROR, RADIO_ERROR, INTEGRATION_ERROR)
        self.assertIsNotNone(verdict)
        self.assertIn(INTEGRATION_ERROR, verdict or "")

    def test_multiline_error_naming_domain_on_continuation_fails(self) -> None:
        record = logged("homeassistant.setup", logging.ERROR, MULTILINE_MESSAGE)
        verdict = self.verdict(CUSTOM_WARNING, *record)
        self.assertIsNotNone(verdict)
        self.assertIn(CONTINUATION, verdict or "")

    def test_missing_custom_warning_fails(self) -> None:
        verdict = self.verdict(MET_ERROR, RADIO_ERROR)
        self.assertIsNotNone(verdict)
        self.assertIn("custom-integration warning", verdict or "")


class WaitReadyTest(unittest.TestCase):
    def test_fails_early_when_child_has_exited(self) -> None:
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])
        process.wait()
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "exited with 3"):
            check.wait_ready(check.free_port(), process, timeout=120)
        self.assertLess(time.monotonic() - started, 10)


class FakeProcess:
    """Stands in for the HA child: its first wait is interrupted, as by Ctrl-C."""

    def __init__(self, wait_errors: tuple[BaseException, ...] = (KeyboardInterrupt(),)) -> None:
        self.calls: list[str] = []
        self.returncode: int | None = None
        self.wait_errors = list(wait_errors)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.calls.append("terminate")

    def kill(self) -> None:
        self.calls.append("kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.calls.append("wait")
        if self.wait_errors:
            raise self.wait_errors.pop(0)
        return self.returncode or 0


class StopTest(unittest.TestCase):
    def test_interrupted_wait_kills_and_reaps_then_propagates(self) -> None:
        process = FakeProcess()
        with self.assertRaises(KeyboardInterrupt):
            check.stop(cast("subprocess.Popen[bytes]", process))
        self.assertEqual(process.calls, ["terminate", "wait", "kill", "wait"])

    def test_interrupted_wait_after_kill_reaps_then_propagates(self) -> None:
        process = FakeProcess(
            (subprocess.TimeoutExpired("hass", check.STOP_TIMEOUT), KeyboardInterrupt())
        )
        with self.assertRaises(KeyboardInterrupt):
            check.stop(cast("subprocess.Popen[bytes]", process))
        self.assertEqual(process.calls, ["terminate", "wait", "kill", "wait", "wait"])


class WallpadVerdictTest(unittest.TestCase):
    def test_no_connection_fails(self) -> None:
        self.assertIn("never connected", check.wallpad_verdict(0, b"") or "")

    def test_any_byte_sent_to_the_wallpad_fails(self) -> None:
        self.assertIn("sent 2 bytes", check.wallpad_verdict(1, b"\xaa\x55") or "")

    def test_connection_without_commands_passes(self) -> None:
        self.assertIsNone(check.wallpad_verdict(1, b""))


class ControllerGateway:
    """Collects the device states the integration's own controller decodes."""

    def __init__(self) -> None:
        self.states: list[DeviceState] = []

    def on_device_state(self, state: DeviceState) -> None:
        self.states.append(state)


class FakeClient:
    """Stands in for one accepted connection: scripted recv results, sendall recorded or failing."""

    def __init__(
        self, steps: list[bytes | BaseException | Callable[[], bytes]], send_error: OSError | None = None
    ) -> None:
        self.steps = steps
        self.send_error = send_error
        self.sent = bytearray()

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def settimeout(self, value: float) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.extend(data)

    def recv(self, size: int) -> bytes:
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step() if callable(step) else step


class WallpadShutdownTest(unittest.TestCase):
    """Bytes HA wrote before it exited are recorded however the stop meets the connection thread."""

    def serve(self, client: FakeClient, server: wallpad.Wallpad) -> None:
        try:
            server._serve(cast("socket.socket", client))
        finally:
            server.stop()

    def test_stop_during_a_receive_timeout_still_reads_to_the_end(self) -> None:
        server = wallpad.Wallpad()

        def stop_arrives() -> bytes:
            server._stopped.set()
            raise TimeoutError

        client = FakeClient([stop_arrives, b"\xaa\x55", b""])
        self.serve(client, server)
        self.assertEqual(bytes(server.received), b"\xaa\x55")

    def test_failed_resend_after_the_peer_left_still_reads_what_it_sent(self) -> None:
        server = wallpad.Wallpad()
        client = FakeClient([b"\xaa\x55", b""], send_error=BrokenPipeError())
        self.serve(client, server)
        self.assertEqual(bytes(server.received), b"\xaa\x55")

    def test_write_then_reset_then_immediate_stop_is_recorded(self) -> None:
        server = wallpad.Wallpad()
        server.start()
        try:
            client = socket.create_connection(("127.0.0.1", server.port), timeout=10)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            client.sendall(b"\xaa\x55")
            client.close()  # with linger 0 the close sends RST, as an exiting process with unread data does
        finally:
            server.stop()
        self.assertEqual(bytes(server.received), b"\xaa\x55")

    def test_write_then_close_then_immediate_stop_is_recorded(self) -> None:
        server = wallpad.Wallpad()
        server.start()
        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=10) as client:
                client.sendall(b"\xaa\x55")
        finally:
            server.stop()
        self.assertEqual(bytes(server.received), b"\xaa\x55")


class WallpadTest(unittest.TestCase):
    def test_frames_are_the_repository_fixtures(self) -> None:
        fixtures = (ROOT / "tests" / "test_control_path_safety.py").read_text(encoding="utf-8")
        for name, frame in wallpad.FRAMES.items():
            with self.subTest(frame=name):
                self.assertIn(f'"{frame}"', fixtures)

    def test_frames_decode_into_eighteen_entities(self) -> None:
        gateway = ControllerGateway()
        controller = KocomController(cast("KocomGateway", gateway))
        for frame in wallpad.FRAMES.values():
            controller.feed(bytes.fromhex(frame))
        keys = {
            (state.key.device_type.name, state.key.room_index, state.key.sub_type.name)
            for state in gateway.states
        }
        self.assertEqual(len(keys), 18)
        self.assertIn(("THERMOSTAT", 1, "NONE"), keys)
        self.assertIn(("AIRQUALITY", 1, "CO2"), keys)

    def test_serves_frames_and_records_what_it_receives(self) -> None:
        server = wallpad.Wallpad()
        server.start()
        payload = b"".join(bytes.fromhex(frame) for frame in wallpad.FRAMES.values())
        try:
            with socket.create_connection(("127.0.0.1", server.port), timeout=10) as client:
                received = b""
                while len(received) < len(payload):
                    chunk = client.recv(4096)
                    self.assertTrue(chunk)
                    received += chunk
                self.assertEqual(received[: len(payload)], payload)
                client.sendall(b"\xaa\x55")
                deadline = time.monotonic() + 10
                while not server.received and time.monotonic() < deadline:
                    time.sleep(0.05)
        finally:
            server.stop()
        self.assertEqual(server.connections, 1)
        self.assertEqual(bytes(server.received), b"\xaa\x55")


class RefusedOutTest(unittest.TestCase):
    def test_non_empty_dir_is_refused_before_home_assistant_starts(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            Path(out, "earlier.png").write_bytes(b"")
            started = time.monotonic()
            result = subprocess.run(
                [sys.executable, "-B", str(HERE / "check.py"), "--out", out],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL", result.stderr)
            self.assertNotIn("PASS", result.stdout)
            self.assertFalse(Path(out, check.LOG_NAME).exists())
            self.assertEqual(sorted(p.name for p in Path(out).iterdir()), ["earlier.png"])
            self.assertLess(time.monotonic() - started, 30)

    def test_regular_file_dir_is_refused_before_home_assistant_starts(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            out = Path(parent, "shots")
            out.write_bytes(b"earlier")
            result = subprocess.run(
                [sys.executable, "-B", str(HERE / "check.py"), "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL", result.stderr)
            self.assertNotIn("PASS", result.stdout)
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_bytes(), b"earlier")
            self.assertEqual(sorted(p.name for p in Path(parent).iterdir()), ["shots"])

    def test_unusable_dir_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            Path(parent, "blocker").write_bytes(b"")
            out = Path(parent, "blocker", "shots")
            result = subprocess.run(
                [sys.executable, "-B", str(HERE / "check.py"), "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FAIL", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertNotIn("PASS", result.stdout)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
