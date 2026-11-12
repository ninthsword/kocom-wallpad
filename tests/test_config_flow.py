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
    from types import MappingProxyType, SimpleNamespace
    from unittest.mock import AsyncMock, create_autospec, patch

    from homeassistant.config_entries import ConfigEntries, ConfigEntry
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
                {"host": "wallpad.example", "port": 8899, "cutoff_release_after_light": False},
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

        async def test_rejected_input_is_suggested_again(self):
            initial = (await self.flow.async_step_user()).get("data_schema")
            assert initial is not None
            self.assertEqual(
                {}, {str(key): key.description for key in initial.schema if key.description}
            )
            for host, port in (("wallpad.example", 0), ("", 8899), (" \t\n", 70000)):
                with self.subTest(host=host, port=port):
                    result = await self.flow.async_step_user({"host": host, "port": port})
                    self.assertEqual(FlowResultType.FORM, result.get("type"))
                    schema = result.get("data_schema")
                    assert schema is not None
                    suggested = {
                        str(key): (key.description or {}).get("suggested_value")
                        for key in schema.schema
                    }
                    self.assertEqual({"host": host, "port": port, "cutoff_release_after_light": None}, suggested)

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
                    self.assertEqual({"host": host, "port": expected_port, "cutoff_release_after_light": False}, result.get("data"))
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

        async def test_initial_option_is_strict_and_retained_on_port_error(self):
            key = "cutoff_release_after_light"
            for value in (False, True):
                result = await self.flow.async_step_user({"host": "test", "port": 0, key: value})
                schema = result.get("data_schema")
                assert schema is not None
                self.assertIs(value, next(k.description["suggested_value"] for k in schema.schema
                                          if str(k) == key))
                result = await self.flow.async_step_user({"host": "test", "port": 8899, key: value})
                data = result.get("data")
                assert data is not None
                self.assertIs(value, data[key])
            for value in (0, 1, "true", "false", None):
                result = await self.flow.async_step_user({"host": "test", "port": 8899, key: value})
                self.assertEqual({key: "invalid_cutoff_release"}, result.get("errors"))

        async def test_field_errors_are_translated(self):
            root = Path(__file__).resolve().parents[1]
            for language in ("en", "ko"):
                translation = json.loads((root / "custom_components" / "kocom_wallpad"
                                          / "translations" / f"{language}.json").read_text())
                for key in ("invalid_host", "invalid_port", "invalid_cutoff_release"):
                    self.assertTrue(translation["config"]["error"][key])

    class NativeReconfigureTests(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            self.hass = HomeAssistant("/tmp/kocom-flow-test")
            self.manager = ConfigEntries(self.hass, {})
            self.hass.config_entries = self.manager
            self.key = "cutoff_release_after_light"
            self.reload_patch = patch.object(self.manager, "async_schedule_reload")
            self.reload = self.reload_patch.start()
            self.addCleanup(self.reload_patch.stop)
            self.entry = ConfigEntry(
                version=1, minor_version=1, domain="kocom_wallpad", source="user",
                title="Original title", unique_id="WallPad.Example ",
                data={"host": "WallPad.Example ", "port": 8899, "other": "retained"},
                options={"existing": "option"}, discovery_keys=MappingProxyType({}),
                subentries_data=None,
            )
            self.manager._entries[self.entry.entry_id] = self.entry
            self.flow = KocomConfigFlow()
            self.flow.hass = self.hass
            self.flow.context = {"source": "reconfigure", "entry_id": self.entry.entry_id}

        async def asyncTearDown(self):
            await self.hass.async_block_till_done()
            await self.hass.async_stop()

        def assert_identity(self):
            self.assertEqual("Original title", self.entry.title)
            self.assertEqual("WallPad.Example ", self.entry.unique_id)
            self.assertEqual("WallPad.Example ", self.entry.data["host"])
            self.assertEqual(8899, self.entry.data["port"])
            self.assertEqual("retained", self.entry.data["other"])
            self.assertEqual({"existing": "option"}, self.entry.options)

        async def test_native_update_persists_only_option_and_reloads_only_changes(self):
            for value in (True, True, False, False):
                before = self.entry.data.get(self.key)
                self.reload.reset_mock()
                result = await self.flow.async_step_reconfigure({self.key: value})
                self.assertEqual(FlowResultType.ABORT, result.get("type"))
                self.assertEqual("reconfigure_successful", result.get("reason"))
                self.assertIs(value, self.entry.data[self.key])
                self.assertIs(self.entry, self.manager.async_get_entry(self.entry.entry_id))
                self.assert_identity()
                if before is value:
                    self.reload.assert_not_called()
                else:
                    self.reload.assert_called_once_with(self.entry.entry_id)

        async def test_valid_submission_corrects_numeric_legacy_value(self):
            for stored, selected in ((1, True), (0, False)):
                self.manager.async_update_entry(self.entry, data={
                    key: value for key, value in self.entry.data.items() if key != self.key
                })
                self.manager.async_update_entry(self.entry, data={**self.entry.data, self.key: stored})
                self.reload.reset_mock()
                await self.flow.async_step_reconfigure({self.key: selected})
                self.assertIs(selected, self.entry.data[self.key])
                self.reload.assert_called_once_with(self.entry.entry_id)
                self.assert_identity()

        async def test_missing_input_preserves_current_value_and_cancel_does_not_write(self):
            for value in (True, False):
                self.manager.async_update_entry(self.entry, data={**self.entry.data, self.key: value})
                self.reload.reset_mock()
                original = dict(self.entry.data)
                form = await self.flow.async_step_reconfigure()
                schema = form.get("data_schema")
                assert schema is not None
                self.assertEqual({self.key: value}, schema({}))
                self.assertEqual(original, self.entry.data)
                self.reload.assert_not_called()
                await self.flow.async_step_reconfigure({})
                self.assertIs(value, self.entry.data[self.key])
                self.reload.assert_not_called()
                self.assert_identity()

        async def test_legacy_and_malformed_values_default_off_and_submissions_reject(self):
            for value in (None, 1, "true"):
                self.manager.async_update_entry(self.entry, data={**self.entry.data, self.key: value})
                form = await self.flow.async_step_reconfigure()
                schema = form.get("data_schema")
                assert schema is not None
                self.assertEqual({self.key: False}, schema({}))
                original = dict(self.entry.data)
                for invalid in (None, 0, 1, "true", "false"):
                    result = await self.flow.async_step_reconfigure({self.key: invalid})
                    self.assertEqual({self.key: "invalid_cutoff_release"}, result.get("errors"))
                    self.assertEqual(original, self.entry.data)
                    self.reload.assert_not_called()
            self.assert_identity()

    suite = unittest.TestSuite([
        unittest.defaultTestLoader.loadTestsFromTestCase(RealConfigFlowTests),
        unittest.defaultTestLoader.loadTestsFromTestCase(NativeReconfigureTests),
    ])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if "--real-ha" in sys.argv:
        raise SystemExit(_run_real_ha_tests())
    unittest.main()
