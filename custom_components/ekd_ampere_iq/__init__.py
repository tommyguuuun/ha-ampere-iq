"""Read-only Ampere.IQ integration setup."""

import logging
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryChange
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import dispatcher
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from . import sensor
from .api import EkdApi
from .config_flow import e3_serial
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_EQUIPMENT,
    CONF_INSTALLATION_UUID,
    CONF_MODBUS_HOST,
    CONF_MODBUS_PORT,
    CONF_MODBUS_UNIT_ID,
    CONF_POLL_INTERVAL,
    CONF_PV_SOURCES,
    CONNECTION_CLOUD,
    CONNECTION_MODBUS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PV_SOURCES,
    MODBUS_EQUIPMENT,
)
from .coordinator import (
    DiagnosticsCoordinator,
    LifetimeCoordinator,
    OptionalCoordinator,
    PowerCoordinator,
    WorkCoordinator,
)
from .modbus import ModbusConnectionError, ModbusReader, ModbusReadError
from .pv_energy import PvEnergyAccumulator, async_start_pv_energy, pv_energy_store_key
from .safety import legacy_e3_present, modbus_entry_exists, modbus_entry_setup_lock

PLATFORMS = ["sensor", "select"]
_LOGGER = logging.getLogger(__name__)


@dataclass
class EkdRuntimeData:
    uuid: str
    power: PowerCoordinator
    work: WorkCoordinator
    connection_type: str = CONNECTION_CLOUD
    reader: ModbusReader | None = None
    pv_energy: PvEnergyAccumulator | None = None
    diagnostics: DiagnosticsCoordinator | None = None
    lifetime: LifetimeCoordinator | None = None
    optional: OptionalCoordinator | None = None


async def async_setup_entry(hass, entry) -> bool:
    """Initialize a single installation without duplicating endpoint requests."""
    if entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_CLOUD) == CONNECTION_MODBUS:
        async with modbus_entry_setup_lock(hass, entry):
            return await _async_setup_entry(hass, entry)
    return await _async_setup_entry(hass, entry)


async def _async_setup_entry(hass, entry) -> bool:
    """Run setup under the local entry lock for Modbus entries."""
    uuid = entry.data[CONF_INSTALLATION_UUID]
    connection_type = entry.data.get(CONF_CONNECTION_TYPE, CONNECTION_CLOUD)
    if connection_type not in (CONNECTION_CLOUD, CONNECTION_MODBUS):
        raise ValueError("Unsupported EKD connection type")
    if connection_type == CONNECTION_MODBUS:
        if legacy_e3_present(hass):
            raise ConfigEntryNotReady("Remove the legacy E3 entry before Modbus setup")
        if modbus_entry_exists(hass, exclude_entry=entry):
            raise ConfigEntryNotReady("Another Ampere.IQ Modbus entry exists")
        reader = ModbusReader(
            entry.data[CONF_MODBUS_HOST],
            entry.data[CONF_MODBUS_PORT],
            entry.data[CONF_MODBUS_UNIT_ID],
            pv_sources=tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES)),
            include_battery="battery" in entry.options.get(
                CONF_EQUIPMENT, ("inverter", "battery")
            ),
            can_read=lambda: (
                not legacy_e3_present(hass)
                and not modbus_entry_exists(hass, exclude_entry=entry)
            ),
        )
        api = reader
    else:
        reader = None
        api = EkdApi(async_get_clientsession(hass), entry.data[CONF_API_KEY])
    legacy_unsubscribers = []
    observed_legacy = set()
    listeners_persisted = False

    def on_legacy_change() -> None:
        if reader is None or not legacy_e3_present(hass):
            return
        reader.suspend()
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None:
            for coordinator in (
                runtime.power, runtime.work, runtime.diagnostics, runtime.lifetime,
                runtime.optional,
            ):
                if coordinator is not None:
                    coordinator.async_set_update_error(
                        UpdateFailed("Modbus unavailable while a legacy E3 entry still exists")
                    )

    def watch_legacy(legacy) -> None:
        identifier = getattr(legacy, "entry_id", id(legacy))
        if identifier in observed_legacy:
            return
        unsubscribe = legacy.async_on_state_change(on_legacy_change)
        observed_legacy.add(identifier)
        if listeners_persisted:
            entry.async_on_unload(unsubscribe)
        else:
            legacy_unsubscribers.append(unsubscribe)

    @callback
    def on_entry_change(change, changed_entry) -> None:
        if (
            changed_entry.domain == "ampere_storagepro_e3"
            and change in (ConfigEntryChange.ADDED, ConfigEntryChange.UPDATED)
        ):
            watch_legacy(changed_entry)
            on_legacy_change()

    try:
        if reader is not None:
            legacy_unsubscribers.append(
                dispatcher.async_dispatcher_connect(
                    hass, SIGNAL_CONFIG_ENTRY_CHANGED, on_entry_change
                )
            )
            for legacy in hass.config_entries.async_entries("ampere_storagepro_e3"):
                watch_legacy(legacy)
            on_legacy_change()
            if legacy_e3_present(hass):
                raise ConfigEntryNotReady("Remove the legacy E3 entry before Modbus setup")
            try:
                identity = await reader.probe_identity()
            except (ModbusConnectionError, ModbusReadError):
                raise ConfigEntryNotReady("StoragePro E3 identity unavailable") from None
            if legacy_e3_present(hass):
                raise ConfigEntryNotReady("Remove the legacy E3 entry before Modbus setup")
            serial = e3_serial(identity)
            if (
                serial is None
                or uuid != f"modbus_{serial}"
                or getattr(entry, "unique_id", uuid) != uuid
            ):
                raise ConfigEntryNotReady("StoragePro E3 identity mismatch")
        interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        if reader is None:
            power = PowerCoordinator(hass, api, uuid, entry, interval)
            work = WorkCoordinator(hass, api, uuid, entry)
            diagnostics = None
            lifetime = None
            optional = None
        else:
            from .optional_registers import OPTIONAL_REGISTERS

            power = PowerCoordinator(hass, api, uuid, entry, interval, modbus=True)
            work = WorkCoordinator(hass, api, uuid, entry, modbus=True)
            diagnostics = DiagnosticsCoordinator(hass, reader, entry)
            lifetime = LifetimeCoordinator(hass, reader, entry)
            selected = set(entry.options.get(CONF_EQUIPMENT, MODBUS_EQUIPMENT))
            optional = OptionalCoordinator(
                hass, reader, entry,
                tuple(d for d in OPTIONAL_REGISTERS if d.equipment in selected),
            )
        await power.async_config_entry_first_refresh()
        # A new installation may not yet have today's history; keep power available.
        await work.async_refresh()
        if diagnostics is not None:
            await diagnostics.async_refresh()
        if lifetime is not None:
            await lifetime.async_refresh()
        pv_energy = None
        if (
            reader is not None
            and "inverter" in entry.options.get(CONF_EQUIPMENT, ("inverter",))
            and tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
            != DEFAULT_PV_SOURCES
        ):
            sources = tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
            try:
                store = Store(hass, 1, pv_energy_store_key(uuid, sources), atomic_writes=True)
                pv_energy = await async_start_pv_energy(
                    hass, entry, power, store,
                    ZoneInfo(getattr(getattr(hass, "config", None), "time_zone", "UTC")),
                    interval_seconds=interval,
                )
                power.pv_energy = pv_energy
            except Exception as err:
                # The derived meter must never make live power/SOC measurements fail.
                # Do not log a storage exception's potentially sensitive path/data.
                _LOGGER.warning("Calculated PV energy unavailable (%s)", type(err).__name__)
        entry.runtime_data = EkdRuntimeData(
            uuid, power, work, connection_type, reader, pv_energy, diagnostics, lifetime,
            optional,
        )
        sensor.reconcile_entity_registry(hass, entry, uuid)
        sensor.reconcile_device_registry(hass, entry, uuid)
        sensor.reconcile_energy_unit_registry(hass, entry, uuid)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        entry.runtime_data = None
        for unsubscribe in legacy_unsubscribers:
            unsubscribe()
        if reader is not None:
            await reader.close()
        raise
    for unsubscribe in legacy_unsubscribers:
        entry.async_on_unload(unsubscribe)
    listeners_persisted = True
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload the sensor platform and coordinator listeners."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    pv_energy = getattr(entry.runtime_data, "pv_energy", None)
    reader = getattr(entry.runtime_data, "reader", None)
    try:
        if pv_energy is not None:
            await pv_energy.async_flush()
    finally:
        if reader is not None:
            await reader.close()
    return True
