"""Conservative coexistence checks for a storage system with limited TCP clients."""

import asyncio
from contextlib import contextmanager

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntryChange,
)
from homeassistant.core import callback
from homeassistant.helpers import dispatcher

from .const import CONF_CONNECTION_TYPE, CONNECTION_MODBUS, DOMAIN


def legacy_e3_present(hass) -> bool:
    """Block local Modbus while any legacy E3 entry remains registered.

    A disabled entry can be re-enabled without a synchronous state-change
    notification, so NOT_LOADED cannot protect an existing idle socket.
    Unknown or failed registry lookups also block access.
    """
    manager = getattr(hass, "config_entries", None)
    if manager is None or not hasattr(manager, "async_entries"):
        return True
    try:
        return bool(manager.async_entries("ampere_storagepro_e3"))
    except Exception:
        return True


def modbus_entry_exists(hass, *, exclude_entry=None) -> bool:
    """Do not probe an E3 while another Ampere.IQ local entry exists."""
    manager = getattr(hass, "config_entries", None)
    if manager is None or not hasattr(manager, "async_entries"):
        return True
    try:
        excluded_id = getattr(exclude_entry, "entry_id", None)
        return any(
            entry.data.get(CONF_CONNECTION_TYPE) == CONNECTION_MODBUS
            for entry in manager.async_entries(DOMAIN)
            if entry is not exclude_entry
            and (excluded_id is None or getattr(entry, "entry_id", None) != excluded_id)
        )
    except Exception:
        return True


def initial_modbus_probe_lock(hass) -> asyncio.Lock:
    """Serialize initial identity probes on the same Home Assistant instance."""
    return hass.data.setdefault(f"{DOMAIN}_initial_probe_lock", asyncio.Lock())


def modbus_entry_setup_lock(hass, entry) -> asyncio.Lock:
    """Keep setup and reconfiguration of one entry from opening overlapping sockets."""
    data = getattr(hass, "data", None)
    if data is None:
        data = hass.data = {}
    locks = data.setdefault(f"{DOMAIN}_entry_setup_locks", {})
    return locks.setdefault(getattr(entry, "entry_id", id(entry)), asyncio.Lock())


@contextmanager
def watch_modbus_conflicts(hass, reader, *, exclude_entry=None):
    """Drop a probe socket synchronously when a competing entry becomes active."""
    unsubscribers = []
    observed_entries = set()

    def check_conflict():
        if legacy_e3_present(hass) or modbus_entry_exists(hass, exclude_entry=exclude_entry):
            reader.suspend()

    def watch_legacy(entry):
        entry_id = getattr(entry, "entry_id", id(entry))
        if entry_id not in observed_entries:
            observed_entries.add(entry_id)
            unsubscribers.append(entry.async_on_state_change(check_conflict))

    @callback
    def on_entry_change(change, entry):
        if change not in (ConfigEntryChange.ADDED, ConfigEntryChange.UPDATED):
            return
        if entry.domain == "ampere_storagepro_e3":
            watch_legacy(entry)
            check_conflict()
        elif entry.domain == DOMAIN:
            check_conflict()

    try:
        unsubscribers.append(dispatcher.async_dispatcher_connect(
            hass, SIGNAL_CONFIG_ENTRY_CHANGED, on_entry_change
        ))
        for legacy in hass.config_entries.async_entries("ampere_storagepro_e3"):
            watch_legacy(legacy)
        check_conflict()
        yield
    finally:
        for unsubscribe in reversed(unsubscribers):
            unsubscribe()
