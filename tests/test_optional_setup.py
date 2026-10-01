"""Integration setup never probes optional registers until a user enables one."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.ekd_ampere_iq import async_setup_entry
from custom_components.ekd_ampere_iq.const import (
    CONF_CONNECTION_TYPE,
    CONF_INSTALLATION_UUID,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT_ID,
    CONNECTION_MODBUS,
)
from custom_components.ekd_ampere_iq.sensor import OptionalSensor
from custom_components.ekd_ampere_iq.sensor import async_setup_entry as sensors_setup


@pytest.mark.asyncio
async def test_local_setup_registers_opt_in_fields_without_extra_startup_reads():
    entry = SimpleNamespace(
        entry_id="e3", unique_id="modbus_serial",
        data={CONF_CONNECTION_TYPE: CONNECTION_MODBUS,
              CONF_INSTALLATION_UUID: "modbus_serial", CONF_MODBUS_HOST: "e3.local",
              CONF_MODBUS_PORT: 502, CONF_MODBUS_UNIT_ID: 247},
        options={"equipment": ["inverter", "battery"]}, runtime_data=None,
        async_on_unload=lambda callback: None,
    )
    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_entries=lambda _domain: [],
        async_forward_entry_setups=AsyncMock(),
    ))
    reader = SimpleNamespace(
        probe_identity=AsyncMock(return_value={"serial": "serial", "model": "ASP-12KW-3P-A-E3"}),
        read_optional=AsyncMock(), close=AsyncMock(),
    )
    with (
        patch("custom_components.ekd_ampere_iq.ModbusReader", return_value=reader),
        patch("homeassistant.helpers.dispatcher.async_dispatcher_connect", return_value=Mock()),
        patch("custom_components.ekd_ampere_iq.PowerCoordinator") as power,
        patch("custom_components.ekd_ampere_iq.WorkCoordinator") as work,
        patch("custom_components.ekd_ampere_iq.DiagnosticsCoordinator") as diagnostics,
        patch("custom_components.ekd_ampere_iq.LifetimeCoordinator") as lifetime,
        patch("custom_components.ekd_ampere_iq.sensor.reconcile_entity_registry"),
        patch("custom_components.ekd_ampere_iq.sensor.reconcile_device_registry"),
        patch("custom_components.ekd_ampere_iq.sensor.reconcile_energy_unit_registry"),
    ):
        power.return_value.async_config_entry_first_refresh = AsyncMock()
        work.return_value.async_refresh = AsyncMock()
        diagnostics.return_value.async_refresh = AsyncMock()
        lifetime.return_value.async_refresh = AsyncMock()
        assert await async_setup_entry(hass, entry)
        assert entry.runtime_data.optional is not None
        reader.read_optional.assert_not_awaited()
        added = []
        await sensors_setup(hass, entry, added.extend)
        optional = [entity for entity in added if isinstance(entity, OptionalSensor)]
        assert len(optional) >= 100
        assert all(not entity.entity_registry_enabled_default for entity in optional)
        assert len({entity.unique_id for entity in optional}) == len(optional)
        reader.read_optional.assert_not_awaited()
