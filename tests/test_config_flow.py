"""Run config-flow regressions with real HA in a fresh process, without shims."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class ConfigFlowSubprocessTests(unittest.TestCase):
    def test_real_homeassistant_config_flow(self):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--real-ha"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


def _run_real_ha_tests():
    # Import the installed dependency only in this child; discovery in the parent
    # may have already installed the protocol tests' narrow module shims.
    import asyncio
    import json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, create_autospec, patch

    from homeassistant.config_entries import ConfigEntries
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import AbortFlow, FlowResultType

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from custom_components.kocom_wallpad.config_flow import KocomConfigFlow

    class RealConfigFlowTests(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.flow = KocomConfigFlow()
            self.flow.context = {"source": "user"}
            self.flow.hass = create_autospec(HomeAssistant, instance=True)
            self.manager = create_autospec(ConfigEntries, instance=True)
            self.flow.hass.config_entries = self.manager
            self.manager.async_entry_for_domain_unique_id.return_value = None
            self.unique_id = AsyncMock(wraps=self.flow.async_set_unique_id)
            self.flow.async_set_unique_id = self.unique_id
            progress = patch.object(self.flow, "_async_in_progress", return_value=[])
            progress.start()
            self.addCleanup(progress.stop)
            # Fail immediately on an accidental TCP or serial probe.
            for target in (
                "asyncio.open_connection",
                "serialx.open_serial_connection",
            ):
                probe = patch(target, side_effect=AssertionError("Unexpected transport probe"))
                probe.start()
                self.addCleanup(probe.stop)
            loop_probe = patch.object(
                asyncio.get_running_loop(), "create_connection",
                side_effect=AssertionError("Unexpected network probe"),
            )
            loop_probe.start()
            self.addCleanup(loop_probe.stop)

        async def test_initial_form(self):
            result = await self.flow.async_step_user()
            self.assertEqual(FlowResultType.FORM, result.get("type"))
            self.assertEqual({}, result.get("errors"))
            schema = result.get("data_schema")
            assert schema is not None
            self.assertEqual(
                {"host": "wallpad.example", "port": 8899},
                schema({"host": "wallpad.example"}),
            )

        async def test_invalid_fields_do_not_set_unique_id(self):
            for host, port, errors in (
                ("", 8899, {"host": "invalid_host"}),
                (" \t\n", 8899, {"host": "invalid_host"}),
                ("wallpad.example", 0, {"port": "invalid_port"}),
                ("wallpad.example", -1, {"port": "invalid_port"}),
                ("wallpad.example", 65536, {"port": "invalid_port"}),
                ("wallpad.example", True, {"port": "invalid_port"}),
                ("wallpad.example", 1.5, {"port": "invalid_port"}),
                ("wallpad.example", None, {"port": "invalid_port"}),
                ("", 0, {"host": "invalid_host", "port": "invalid_port"}),
            ):
                with self.subTest(host=host, port=port):
                    result = await self.flow.async_step_user({"host": host, "port": port})
                    self.assertEqual(FlowResultType.FORM, result.get("type"))
                    self.assertEqual(errors, result.get("errors"))
            self.unique_id.assert_not_awaited()

        async def test_tcp_and_serial_keep_spelling_and_identity(self):
            for host, port, expected_port in (
                ("WallPad.Example", 1, 1),
                ("wallpad.example ", 65535, 65535),
                ("wallpad.example", 8899, 8899),
                ("/dev/serial/by-id/example", 0, None),
                ("/dev/ttyUSB0", 65536, None),
            ):
                with self.subTest(host=host, port=port):
                    result = await self.flow.async_step_user({"host": host, "port": port})
                    self.assertEqual(FlowResultType.CREATE_ENTRY, result.get("type"))
                    self.assertEqual(host, result.get("title"))
                    self.assertEqual({"host": host, "port": expected_port}, result.get("data"))
                    self.unique_id.assert_awaited_with(host)
                    self.assertEqual(host, self.flow.context.get("unique_id"))

        async def test_duplicate_host_still_aborts(self):
            for host in ("WallPad.Example", "/dev/ttyUSB0"):
                with self.subTest(host=host):
                    self.manager.async_entry_for_domain_unique_id.return_value = SimpleNamespace(
                        unique_id=host, source="user"
                    )
                    with self.assertRaises(AbortFlow) as raised:
                        await self.flow.async_step_user({"host": host, "port": 8899})
                    self.assertEqual("already_configured", raised.exception.reason)

        async def test_field_errors_are_translated(self):
            root = Path(__file__).resolve().parents[1]
            for language in ("en", "ko"):
                translation = json.loads((root / "custom_components" / "kocom_wallpad"
                                          / "translations" / f"{language}.json").read_text())
                for key in ("invalid_host", "invalid_port"):
                    self.assertTrue(translation["config"]["error"][key])

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealConfigFlowTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if "--real-ha" in sys.argv:
        raise SystemExit(_run_real_ha_tests())
    unittest.main()
