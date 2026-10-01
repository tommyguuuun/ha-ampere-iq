"""Exercise the actual HA entity platform and registry for optional sensors."""

import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import EntityPlatform

from custom_components.ekd_ampere_iq.const import DOMAIN
from custom_components.ekd_ampere_iq.coordinator import OptionalCoordinator
from custom_components.ekd_ampere_iq.optional_registers import OPTIONAL_REGISTERS
from custom_components.ekd_ampere_iq.sensor import OptionalSensor, reconcile_entity_registry


@pytest.mark.asyncio
async def test_real_registry_defaults_to_disabled_and_user_enabled_field_gets_polled(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="test-entry", disabled_by=None, domain=DOMAIN,
        pref_disable_new_entities=False, pref_disable_polling=False,
        title="Ampere.IQ", async_on_unload=lambda _callback: None,
    )
    hass.config_entries = SimpleNamespace(async_get_entry=lambda _key: entry)
    devices = dr.DeviceRegistry(hass)
    await devices.async_load()
    hass.data[dr.DATA_REGISTRY] = devices
    entities = er.EntityRegistry(hass)
    await entities.async_load()
    hass.data[er.DATA_REGISTRY] = entities
    description = next(d for d in OPTIONAL_REGISTERS if d.key == "pv1_voltage")
    reader = SimpleNamespace(read_optional=AsyncMock(
        return_value={"pv1_voltage": 243.7}
    ))
    coordinator = OptionalCoordinator(hass, reader, entry, (description,))
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name=DOMAIN, platform=None,
        scan_interval=timedelta(minutes=1), entity_namespace=None,
    )
    platform.config_entry = entry

    await platform.async_add_entities([OptionalSensor(coordinator, "device-1", description)])
    registered = entities.async_get_entity_id(
        "sensor", DOMAIN, "device-1_optional_pv1_voltage"
    )
    assert registered is not None
    assert registered == "sensor.ampere_iq_pv_eingang_1_spannung"
    assert entities.async_get(registered).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert platform.entities == {}
    assert not tuple(coordinator.async_contexts())
    reader.read_optional.assert_not_awaited()

    entities.async_update_entity(registered, disabled_by=None)
    await platform.async_add_entities([OptionalSensor(coordinator, "device-1", description)])
    assert entities.async_get(registered).disabled_by is None
    assert tuple(coordinator.async_contexts()) == ("pv1_voltage",)
    await coordinator.async_refresh()
    reader.read_optional.assert_awaited_once_with((description,))
    assert platform.entities[registered].native_value == 243.7


@pytest.mark.asyncio
async def test_battery_deselection_preserves_opt_in_choice_and_default_disabled(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="test-entry", disabled_by=None, domain=DOMAIN, title="Ampere.IQ",
        pref_disable_new_entities=False, pref_disable_polling=False,
        async_on_unload=lambda _callback: None,
        data={"connection_type": "modbus"}, options={"equipment": ["inverter", "battery"]},
    )
    hass.config_entries = SimpleNamespace(async_get_entry=lambda _key: entry)
    devices = dr.DeviceRegistry(hass)
    await devices.async_load()
    hass.data[dr.DATA_REGISTRY] = devices
    entities = er.EntityRegistry(hass)
    await entities.async_load()
    hass.data[er.DATA_REGISTRY] = entities
    descriptors = tuple(
        d for d in OPTIONAL_REGISTERS
        if d.key in {"bms1_voltage", "bms2_voltage", "bms1_current"}
    )
    reader = SimpleNamespace(read_optional=AsyncMock(return_value={}))
    coordinator = OptionalCoordinator(hass, reader, entry, descriptors)
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name=DOMAIN, platform=None,
        scan_interval=timedelta(minutes=1), entity_namespace=None,
    )
    platform.config_entry = entry
    await platform.async_add_entities([
        OptionalSensor(coordinator, "device-1", d) for d in descriptors
    ])
    opted = entities.async_get_entity_id("sensor", DOMAIN, "device-1_optional_bms1_voltage")
    default = entities.async_get_entity_id("sensor", DOMAIN, "device-1_optional_bms2_voltage")
    user_disabled = entities.async_get_entity_id("sensor", DOMAIN, "device-1_optional_bms1_current")
    assert opted is not None and default is not None and user_disabled is not None
    entities.async_update_entity(opted, disabled_by=None, name="Meine Batteriespannung")
    entities.async_update_entity(user_disabled, disabled_by=er.RegistryEntryDisabler.USER)

    entry.options = {"equipment": ["inverter"]}
    reconcile_entity_registry(hass, entry, "device-1")
    assert entities.async_get(opted).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert entities.async_get(default).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert entities.async_get(user_disabled).disabled_by == er.RegistryEntryDisabler.USER

    entry.options = {"equipment": ["inverter", "battery"]}
    reconcile_entity_registry(hass, entry, "device-1")
    assert entities.async_get(opted).disabled_by is None
    assert entities.async_get(default).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert entities.async_get(user_disabled).disabled_by == er.RegistryEntryDisabler.USER
    assert entities.async_get(opted).name == "Meine Batteriespannung"
    assert entities.async_get_entity_id("sensor", DOMAIN, "device-1_optional_bms1_voltage") == opted


@pytest.mark.asyncio
async def test_entire_optional_catalog_registers_without_scheduling_network_reads(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = SimpleNamespace(
        entry_id="all-optionals", disabled_by=None, domain=DOMAIN, title="Ampere.IQ",
        pref_disable_new_entities=False, pref_disable_polling=False,
        async_on_unload=lambda _callback: None,
    )
    hass.config_entries = SimpleNamespace(async_get_entry=lambda _key: entry)
    devices = dr.DeviceRegistry(hass)
    await devices.async_load()
    hass.data[dr.DATA_REGISTRY] = devices
    entities = er.EntityRegistry(hass)
    await entities.async_load()
    hass.data[er.DATA_REGISTRY] = entities
    reader = SimpleNamespace(read_optional=AsyncMock())
    coordinator = OptionalCoordinator(hass, reader, entry, OPTIONAL_REGISTERS)
    platform = EntityPlatform(
        hass=hass, logger=logging.getLogger(__name__), domain="sensor",
        platform_name=DOMAIN, platform=None,
        scan_interval=timedelta(minutes=1), entity_namespace=None,
    )
    platform.config_entry = entry
    await platform.async_add_entities([
        OptionalSensor(coordinator, "device-1", d) for d in OPTIONAL_REGISTERS
    ])
    registered = [e for e in entities.entities.values() if e.config_entry_id == entry.entry_id]
    assert len(registered) == len(OPTIONAL_REGISTERS) == 156
    assert all(e.disabled_by == er.RegistryEntryDisabler.INTEGRATION for e in registered)
    assert platform.entities == {}
    assert not tuple(coordinator.async_contexts())
    reader.read_optional.assert_not_awaited()
