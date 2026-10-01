"""The opt-in E3 diagnostics may never delay normal power polling by default."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ekd_ampere_iq import coordinator as coordinators
from custom_components.ekd_ampere_iq.modbus import ModbusConnectionError


def description(index):
    return SimpleNamespace(key=f"field_{index}", address=39070 + index)


def setup(descriptions):
    reader = SimpleNamespace(read_optional=AsyncMock(
        side_effect=lambda requested: {item.key: item.address for item in requested}
    ))
    entry = SimpleNamespace(async_on_unload=lambda _callback: None)
    coordinator = coordinators.OptionalCoordinator(
        SimpleNamespace(), reader, entry, descriptions
    )
    return coordinator, reader


@pytest.mark.asyncio
async def test_disabled_by_default_optional_entities_do_not_read_any_registers():
    coordinator, reader = setup((description(1),))
    assert coordinator.update_interval == timedelta(seconds=60)
    assert await coordinator._async_update_data() == {}
    reader.read_optional.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_activated_contexts_are_read_without_polling_other_registers():
    descriptions = tuple(description(i) for i in range(4))
    coordinator, reader = setup(descriptions)
    coordinator.async_contexts = lambda: iter(("field_1", "field_3"))
    assert await coordinator._async_update_data() == {"field_1": 39071, "field_3": 39073}
    reader.read_optional.assert_awaited_once_with((descriptions[1], descriptions[3]))


@pytest.mark.asyncio
async def test_large_opt_in_selection_is_bounded_and_rotates_without_stale_disabled_values():
    descriptions = tuple(description(i) for i in range(15))
    coordinator, reader = setup(descriptions)
    active = [item.key for item in descriptions]
    coordinator.async_contexts = lambda: iter(active)
    first = await coordinator._async_update_data()
    assert len(reader.read_optional.await_args.args[0]) <= 12
    coordinator.data = first
    second = await coordinator._async_update_data()
    assert set(second) == set(active)
    assert {item.key for item in reader.read_optional.await_args.args[0]} == {
        "field_12", "field_13", "field_14", "field_0", "field_1", "field_2",
        "field_3", "field_4", "field_5", "field_6", "field_7", "field_8",
    }
    active[:] = ["field_1"]
    coordinator.data = second
    assert await coordinator._async_update_data() == {"field_1": 39071}


@pytest.mark.asyncio
async def test_optional_transport_error_does_not_leak_private_connection_details():
    coordinator, reader = setup((description(0),))
    coordinator.async_contexts = lambda: iter(("field_0",))
    reader.read_optional.side_effect = ModbusConnectionError("private-host:502")
    with pytest.raises(UpdateFailed, match="Optional E3 diagnostics") as exc:
        await coordinator._async_update_data()
    assert "private-host" not in str(exc.value)


@pytest.mark.asyncio
async def test_daily_energy_uses_successful_poll_day_and_rejects_midnight_crossing():
    today = description(1)
    today.key = "inverter_generation_today"
    coordinator, reader = setup((today,))
    coordinator.async_contexts = lambda: iter((today.key,))
    tz = ZoneInfo("Europe/Berlin")
    late = datetime(2026, 10, 1, 23, 59, tzinfo=tz)
    early = datetime(2026, 10, 2, 0, 1, tzinfo=tz)
    with patch("custom_components.ekd_ampere_iq.coordinator.dt_util.now",
               side_effect=[late, late]):
        assert (await coordinator._async_update_data())[today.key] == today.address
    assert coordinator.data_day == date(2026, 10, 1)
    with patch("custom_components.ekd_ampere_iq.coordinator.dt_util.now",
               side_effect=[late, early]):
        assert (await coordinator._async_update_data())[today.key] is None
    assert coordinator.data_day is None
    reader.read_optional.assert_awaited()
