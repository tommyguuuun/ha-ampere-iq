"""Optional Modbus reads must remain on the existing socket and opt-in only."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from custom_components.ekd_ampere_iq.modbus import ModbusReader


class OptionalClient:
    instances = []

    def __init__(self, host, *, port, timeout, retries, trace_connect=None,
                 reconnect_delay=None):
        self.connected = False
        self.calls = []
        self.responses = {}
        self.instances.append(self)

    async def connect(self):
        self.connected = True
        return True

    def close(self):
        self.connected = False

    async def read_input_registers(self, address, *, count, device_id):
        self.calls.append(("input", address, count, device_id))
        response = self.responses[address]
        return SimpleNamespace(registers=response, isError=lambda: response is None)

    async def read_holding_registers(self, address, *, count, device_id):
        self.calls.append(("holding", address, count, device_id))
        response = self.responses[address]
        return SimpleNamespace(registers=response, isError=lambda: response is None)

    async def write_register(self, *args, **kwargs):
        raise AssertionError("An optional measurement must never write to E3")


def spec(key, address, *, count=1, kind="uint16", scale=1, connection=None,
         register_type="input"):
    return SimpleNamespace(key=key, address=address, count=count, kind=kind,
                           scale=scale, connection=connection,
                           register_type=register_type)


@pytest.mark.asyncio
async def test_no_enabled_optional_entities_mean_no_modbus_transactions():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        assert await reader.read_optional(()) == {}
        assert not client.connected
        assert client.calls == []
        await reader.close()


@pytest.mark.asyncio
async def test_optional_registers_decode_signed_unsigned_text_and_holding_on_one_socket():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        client.responses = {
            39070: [0xFF85], 39000: [1, 0],
            30000: [0x4533, 0x0000], 49249: [0xFF9C],
        }
        values = await reader.read_optional((
            spec("pv_voltage", 39070, kind="int16", scale=0.1),
            spec("protocol", 39000, count=2, kind="uint32", scale=0.01),
            spec("model", 30000, count=2, kind="str"),
            spec("fault_current", 49249, kind="int16", scale=0.01,
                 register_type="holding"),
        ))
        assert values == {
            "pv_voltage": -12.3, "protocol": 655.36,
            "model": "E3", "fault_current": -1,
        }
        assert client.calls == [
            ("input", 39070, 1, 7), ("input", 39000, 2, 7),
            ("input", 30000, 2, 7), ("holding", 49249, 1, 7),
        ]
        assert len(OptionalClient.instances) >= 1
        await reader.close()


@pytest.mark.asyncio
async def test_disconnected_battery_or_meter_is_unknown_without_reading_its_values():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        client.responses = {37002: [0], 38901: [0], 39070: [200]}
        values = await reader.read_optional((
            spec("bms_voltage", 37609, connection="bms1"),
            spec("meter_voltage", 38902, count=2, kind="uint32", connection="meter2"),
            spec("pv_voltage", 39070, scale=0.1),
        ))
        assert values == {"bms_voltage": None, "meter_voltage": None,
                          "pv_voltage": 20}
        assert client.calls == [
            ("input", 37002, 1, 7), ("input", 38901, 1, 7),
            ("input", 39070, 1, 7),
        ]
        await reader.close()


@pytest.mark.asyncio
async def test_invalid_optional_definition_does_not_query_connection_gate_first():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        client.responses = {37002: [1]}
        with pytest.raises(ValueError, match="Unsupported optional"):
            await reader.read_optional((spec("bad", 40000, kind="unknown", connection="bms1"),))
        assert client.calls == []
        await reader.close()


@pytest.mark.asyncio
async def test_connection_flag_itself_reports_zero_instead_of_unknown():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        client.responses = {37002: [0]}
        assert await reader.read_optional((
            spec("bms1_connected", 37002, connection="bms1"),
        )) == {"bms1_connected": 0}
        assert client.calls == [("input", 37002, 1, 7)]
        await reader.close()


@pytest.mark.asyncio
async def test_one_invalid_optional_register_does_not_make_other_sensors_unavailable():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        client = OptionalClient.instances[-1]
        client.responses = {39070: None, 39072: [2200]}
        assert await reader.read_optional((
            spec("pv1_voltage", 39070, scale=0.1),
            spec("pv2_voltage", 39072, scale=0.1),
        )) == {"pv1_voltage": None, "pv2_voltage": 220}
        await reader.close()


@pytest.mark.asyncio
async def test_urgent_power_read_interleaves_between_two_optional_registers():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        started, release_first, power_acquired, release_power = (
            asyncio.Event() for _ in range(4)
        )
        second_started = asyncio.Event()

        async def simulated_register(address, count, *, kind="input"):
            if address == 39070:
                started.set()
                await release_first.wait()
            if address == 39072:
                second_started.set()
            return [200]

        async def urgent_power():
            async with reader._lock:
                power_acquired.set()
                await release_power.wait()

        reader._read = simulated_register
        optional = asyncio.create_task(reader.read_optional((
            spec("pv1_voltage", 39070), spec("pv2_voltage", 39072),
        )))
        try:
            await asyncio.wait_for(started.wait(), 1)
            urgent = asyncio.create_task(urgent_power())
            await asyncio.sleep(0)  # Put the power reader in the lock's queue.
            release_first.set()
            await asyncio.wait_for(power_acquired.wait(), 1)
            assert not second_started.is_set()
            release_power.set()
            await asyncio.wait_for(asyncio.gather(optional, urgent), 1)
        finally:
            release_first.set()
            release_power.set()
            optional.cancel()
            await reader.close()


@pytest.mark.asyncio
async def test_urgent_power_read_interleaves_between_gate_and_optional_measurement():
    with patch("custom_components.ekd_ampere_iq.modbus.AsyncModbusTcpClient", OptionalClient):
        reader = ModbusReader("example.invalid", 502, 7)
        gate_started, release_gate, power_acquired, release_power = (
            asyncio.Event() for _ in range(4)
        )
        optional_measurement_started = asyncio.Event()

        async def simulated_register(address, count, *, kind="input"):
            if address == 37002:
                gate_started.set()
                await release_gate.wait()
                return [1]
            optional_measurement_started.set()
            return [231]

        async def urgent_power():
            async with reader._lock:
                power_acquired.set()
                await release_power.wait()

        reader._read = simulated_register
        optional = asyncio.create_task(reader.read_optional((
            spec("battery_voltage", 37609, connection="bms1", scale=0.1),
        )))
        try:
            await asyncio.wait_for(gate_started.wait(), 1)
            urgent = asyncio.create_task(urgent_power())
            await asyncio.sleep(0)
            release_gate.set()
            await asyncio.wait_for(power_acquired.wait(), 1)
            assert not optional_measurement_started.is_set()
            release_power.set()
            assert await asyncio.wait_for(optional, 1) == {"battery_voltage": 23.1}
            await asyncio.wait_for(urgent, 1)
        finally:
            release_gate.set()
            release_power.set()
            optional.cancel()
            await reader.close()
