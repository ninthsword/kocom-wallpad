"""Run KocomClimate against the installed real Core in a fresh process, without shims."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

OLD_NAMES = (
    "current_temperature",
    "target_temperature",
    "temperature_unit",
    "_attr_current_temperature",
    "_attr_target_temperature",
    "_attr_temperature_unit",
)


class ClimateCoreCompatSubprocessTests(unittest.TestCase):
    def test_real_homeassistant_climate_compat(self):
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--real-ha"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


def _run_real_ha_tests():
    # Import the installed dependency only in this child; discovery in the parent
    # may have already installed the protocol tests' narrow module shims.
    import asyncio
    import logging
    import tempfile
    from typing import Any
    from unittest.mock import MagicMock, patch

    from homeassistant.components.climate import ClimateEntity
    from homeassistant.components.climate.const import HVACAction, HVACMode
    from homeassistant.const import Platform, UnitOfTemperature
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import entity as entity_helper
    from homeassistant.util.unit_system import METRIC_SYSTEM

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    core_has_native_names = hasattr(ClimateEntity, "native_current_temperature")

    class _Records(logging.Handler):
        def __init__(self) -> None:
            super().__init__(logging.WARNING)
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    # One recorder for the whole child: the integration imports once, in the first test.
    records = _Records()
    logging.getLogger().addHandler(records)

    class RealClimateTests(unittest.IsolatedAsyncioTestCase):
        async def asyncSetUp(self):
            connect = patch.object(
                asyncio.get_running_loop(),
                "create_connection",
                side_effect=AssertionError("Unexpected network probe"),
            )
            connect.start()
            self.addCleanup(connect.stop)
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.hass = HomeAssistant(directory.name)
            self.hass.config.units = METRIC_SYSTEM
            # Core reads this mapping while it reports a deprecated subclass at import.
            self.hass.data.setdefault("integrations", {})
            self.addAsyncCleanup(self.hass.async_stop, force=True)
            # Imported here so a class-definition warning is reported through the live Core.
            from custom_components.kocom_wallpad import climate

            self.climate_module: Any = climate
            self.confirmed = True

        def _entity(self, current: float | None, target: float | None, mode: HVACMode) -> Any:
            from custom_components.kocom_wallpad.const import DeviceType, SubType
            from custom_components.kocom_wallpad.models import DeviceKey, DeviceState

            key = DeviceKey(DeviceType.THERMOSTAT, 0, 0, SubType.NONE)
            device = DeviceState(
                key,
                Platform.CLIMATE,
                {"hvac_modes": [HVACMode.HEAT, HVACMode.OFF], "temp_step": 1.0},
                {"hvac_mode": mode, "current_temp": current, "target_temp": target},
            )
            gateway = MagicMock()
            gateway.host = "wallpad.example"
            gateway.device_registry_id = None
            gateway.is_device_state_confirmed.side_effect = lambda _key: self.confirmed
            entity = self.climate_module.KocomClimate(gateway, device)
            entity.hass = self.hass
            entity.entity_id = "climate.room"
            return entity

        def _deprecations(self) -> list[str]:
            return [
                message
                for message in records.messages
                if "2027.11" in message
                or ("deprecat" in message.lower() and "temperature" in message.lower())
            ]

        async def test_confirmed_room_reports_live_values_without_deprecations(self):
            entity = self._entity(21.5, 24.0, HVACMode.HEAT)
            self.assertEqual(HVACMode.HEAT, entity.state)
            attributes = entity.state_attributes
            self.assertEqual(HVACAction.HEATING, entity.hvac_action)
            self.assertEqual(21.5, attributes["current_temperature"])
            self.assertEqual(24.0, attributes["temperature"])
            self.assertEqual(21.5, entity.native_current_temperature)
            self.assertEqual(24.0, entity.native_target_temperature)
            self.assertEqual(UnitOfTemperature.CELSIUS, entity.native_temperature_unit)
            self.assertEqual([], self._deprecations())

        async def test_unconfirmed_room_reports_no_temperatures(self):
            self.confirmed = False
            entity = self._entity(21.5, 24.0, HVACMode.HEAT)
            attributes = entity.state_attributes
            self.assertIsNone(attributes["current_temperature"])
            self.assertIsNone(attributes["temperature"])
            self.assertIsNone(entity.native_current_temperature)
            self.assertIsNone(entity.native_target_temperature)
            self.assertIsNone(entity.hvac_action)
            self.assertEqual([], self._deprecations())

        async def test_class_defines_the_names_its_core_reads(self):
            klass = self.climate_module.KocomClimate
            for name in OLD_NAMES:
                with self.subTest(name=name):
                    if core_has_native_names:
                        self.assertNotIn(name, vars(klass))
                    elif name.startswith("_attr_"):
                        self.assertNotIn(name, vars(klass))
                    else:
                        self.assertIn(name, vars(klass))

        @unittest.skipIf(core_has_native_names, "Core reads only the native names")
        async def test_older_core_reads_the_unprefixed_names(self):
            entity = self._entity(21.5, 24.0, HVACMode.HEAT)
            self.assertEqual(21.5, entity.current_temperature)
            self.assertEqual(24.0, entity.target_temperature)
            self.assertEqual(UnitOfTemperature.CELSIUS, entity.temperature_unit)
            self.confirmed = False
            unconfirmed = self._entity(21.5, 24.0, HVACMode.HEAT)
            self.assertIsNone(unconfirmed.current_temperature)
            self.assertIsNone(unconfirmed.target_temperature)
            self.assertEqual(UnitOfTemperature.CELSIUS, unconfirmed.temperature_unit)

        async def test_hvac_action(self):
            for current, target, mode, expected in (
                (21.0, 24.0, HVACMode.HEAT, HVACAction.HEATING),
                (24.0, 24.0, HVACMode.HEAT, HVACAction.IDLE),
                (25.0, 24.0, HVACMode.HEAT, HVACAction.IDLE),
                (21.0, 24.0, HVACMode.OFF, HVACAction.OFF),
            ):
                with self.subTest(current=current, target=target, mode=mode):
                    self.assertEqual(expected, self._entity(current, target, mode).hvac_action)

        @unittest.skipUnless(core_has_native_names, "Core has no in-place entity_id change")
        async def test_no_class_requires_readd(self):
            from custom_components.kocom_wallpad.binary_sensor import KocomBinarySensor
            from custom_components.kocom_wallpad.entity_base import KocomBaseEntity
            from custom_components.kocom_wallpad.fan import KocomFan
            from custom_components.kocom_wallpad.light import KocomLight
            from custom_components.kocom_wallpad.sensor import KocomSensor
            from custom_components.kocom_wallpad.switch import KocomSwitch

            requires_readd = vars(entity_helper)["_entity_class_requires_readd"]
            for klass in (
                self.climate_module.KocomClimate,
                KocomLight,
                KocomSwitch,
                KocomSensor,
                KocomBinarySensor,
                KocomFan,
                KocomBaseEntity,
            ):
                with self.subTest(klass=klass.__name__):
                    self.assertFalse(requires_readd(klass))

        @unittest.skipUnless(core_has_native_names, "Core has no base hook")
        async def test_hook_calls_core_base(self):
            entity = self._entity(21.5, 24.0, HVACMode.HEAT)
            with patch.object(
                entity_helper.Entity, "async_entity_id_changed", autospec=True
            ) as base:
                entity.async_entity_id_changed("climate.old")
            base.assert_called_once_with(entity, "climate.old")

        @unittest.skipIf(core_has_native_names, "Core has a base hook")
        async def test_hook_raises_nothing_without_core_base(self):
            self._entity(21.5, 24.0, HVACMode.HEAT).async_entity_id_changed("climate.old")

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealClimateTests)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    if "--real-ha" in sys.argv:
        raise SystemExit(_run_real_ha_tests())
    unittest.main()
