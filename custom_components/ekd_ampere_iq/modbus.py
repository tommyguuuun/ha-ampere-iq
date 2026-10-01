"""Read-only Modbus TCP access to the Ampere.StoragePro E3 input registers."""

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from pymodbus.client import AsyncModbusTcpClient


class ModbusReadError(Exception):
    """A device response cannot be used."""


class ModbusConnectionError(Exception):
    """The local transport is unavailable."""


PV_SOURCE_REGISTERS = {
    "e3_total": (39118, 1),
    "meter2": (38914, 0.1),
    "mppt1": (39329, 1),
    "mppt2": (39333, 1),
}

class ModbusReader:
    """Own one lazy, persistent client; serialize all transactions and shutdown."""

    def __init__(
        self, host: str, port: int, slave: int, *,
        can_read: Callable[[], bool] | None = None,
        pv_sources: tuple[str, ...] = ("e3_total",),
        include_battery: bool = True,
    ) -> None:
        if (
            not isinstance(pv_sources, (tuple, list))
            or not pv_sources
            or any(type(source) is not str or source not in PV_SOURCE_REGISTERS
                   for source in pv_sources)
            or len(set(pv_sources)) != len(pv_sources)
            or ("e3_total" in pv_sources
                and any(source in pv_sources for source in ("mppt1", "mppt2")))
        ):
            raise ValueError("Invalid PV sources")
        self._pv_sources = tuple(pv_sources)
        self._include_battery = include_battery
        self._can_read = can_read or (lambda: True)
        self._host, self._port = host, port
        self._rejected_connection = False
        self._client = self._new_client()
        self._slave = slave
        self._lock = asyncio.Lock()
        self._connecting = False
        parameters = inspect.signature(self._client.read_input_registers).parameters
        if "device_id" in parameters:
            self._unit_keyword = "device_id"
        elif "slave" in parameters:
            self._unit_keyword = "slave"
        else:
            raise ModbusConnectionError("Unsupported Modbus client version")

    def _new_client(self) -> AsyncModbusTcpClient:
        """Start clean after PyModbus retains a reference to a closed transport."""
        client = AsyncModbusTcpClient(
            self._host, port=self._port, timeout=3, retries=0, reconnect_delay=0,
            trace_connect=lambda connected: self._on_connection_change(client, connected),
        )
        return client

    def _on_connection_change(self, client: AsyncModbusTcpClient, connected: bool) -> None:
        """Close a socket as soon as PyModbus reports a forbidden connection."""
        if connected and (client is not self._client or not self._can_read()):
            if client is self._client:
                self._rejected_connection = True
            try:
                client.close()
            except Exception:
                pass

    async def _read(
        self, address: int, count: int, *, kind: str = "input", value: int | None = None
    ) -> list[int]:
        if not self._can_read():
            try:
                self._client.close()
            except Exception:
                pass
            raise ModbusConnectionError("Modbus unavailable while legacy E3 entry is enabled")
        connect_pending = False
        try:
            if not self._client.connected:
                connect_pending = True
                self._connecting = True
                try:
                    connected = await asyncio.wait_for(self._client.connect(), 5)
                finally:
                    self._connecting = False
                connect_pending = False
                if not connected:
                    raise ModbusConnectionError("Modbus connection failed")
            if not self._can_read():
                raise ModbusConnectionError("Modbus connection no longer permitted")
            if kind == "input":
                request = self._client.read_input_registers(
                    address, count=count, **{self._unit_keyword: self._slave}
                )
            elif kind == "holding":
                request = self._client.read_holding_registers(
                    address, count=count, **{self._unit_keyword: self._slave}
                )
            elif kind == "write" and value is not None:
                request = self._client.write_register(
                    address, value, **{self._unit_keyword: self._slave}
                )
            else:
                raise ModbusReadError("Invalid Modbus transaction")
            response = await asyncio.wait_for(request, 5)
            if not self._can_read():
                raise ModbusConnectionError("Modbus connection no longer permitted")
        except asyncio.CancelledError:
            if connect_pending:
                abandoned = self._client
                self._client = self._new_client()
                self._rejected_connection = False
                if abandoned.connected:
                    try:
                        abandoned.close()
                    except Exception:
                        pass
            else:
                try:
                    self._client.close()
                except Exception:
                    pass
            raise
        except Exception:
            # A timeout can leave the library's connected flag stale. Force a
            # fresh socket for the next coordinator update.
            try:
                self._client.close()
            except Exception:
                pass
            if self._rejected_connection:
                self._client = self._new_client()
                self._rejected_connection = False
            raise ModbusConnectionError("Modbus connection failed") from None
        try:
            if response is None or response.isError():
                raise ModbusReadError("Invalid Modbus register response")
            if kind == "write":
                return []
            registers = response.registers
        except ModbusReadError:
            raise
        except Exception:
            raise ModbusReadError("Invalid Modbus register response") from None
        if (not isinstance(registers, (list, tuple)) or len(registers) != count
                or any(type(reg) is not int or not 0 <= reg <= 65535 for reg in registers)):
            raise ModbusReadError("Invalid Modbus register response")
        return list(registers)

    async def probe_identity(self) -> dict[str, str]:
        async with self._lock:
            model = await self._read(30000, 16)
            serial = await self._read(30016, 16)
        def decode(registers: list[int]) -> str:
            try:
                raw = b"".join(reg.to_bytes(2, "big") for reg in registers)
                value = raw.strip(b"\x00 ").decode("ascii")
            except UnicodeDecodeError:
                raise ModbusReadError("Invalid Modbus register response") from None
            if not value or any(ord(char) < 32 or ord(char) > 126 for char in value):
                raise ModbusReadError("Invalid Modbus register response")
            return value
        return {"model": decode(model), "serial": decode(serial)}

    async def power(self) -> dict[str, int | float | None]:
        async with self._lock:
            sources = []
            pv_valid = True
            for source in self._pv_sources:
                if source == "meter2":
                    try:
                        meter_connected = (await self._read(38901, 1))[0] == 1
                    except ModbusReadError:
                        meter_connected = False
                    if not meter_connected:
                        pv_valid = False
                        continue
                address, scale = PV_SOURCE_REGISTERS[source]
                watts = self._signed32(await self._read(address, 2)) * scale
                sources.append(watts if source == "e3_total" else max(watts, 0))
            pv = round(sum(sources)) if pv_valid else None
            house = self._signed32(await self._read(39225, 2))
            grid = self._signed32(await self._read(38814, 2))
            battery = None
            soc = None
            if self._include_battery:
                try:
                    battery = self._signed32(await self._read(39237, 2))
                except ModbusReadError:
                    pass
                bms1_connected = False
                bms2_connected = False
                try:
                    bms1_connected = (await self._read(37002, 1))[0] == 1
                    if bms1_connected:
                        soc = (await self._read(37612, 1))[0]
                except ModbusReadError:
                    pass
                if not bms1_connected:
                    try:
                        bms2_connected = (await self._read(37700, 1))[0] == 1
                    except ModbusReadError:
                        pass
                if not (bms1_connected or bms2_connected):
                    battery = None
            if pv is not None and not 0 <= pv <= 100_000:
                raise ModbusReadError("Invalid Modbus register response")
            if soc is not None and not 0 <= soc <= 100:
                soc = None
        return {
            "pvPower": pv, "housePower": house, "gridPower": grid / 10,
            "batteryPower": battery, "batterySoc": soc,
        }

    async def work_mode(self) -> int | None:
        """Read the known work-mode holding register without changing it."""
        async with self._lock:
            value = (await self._read(49203, 1, kind="holding"))[0]
            return value if value in (1, 2, 3, 4) else None

    async def set_work_mode(self, mode: int) -> int:
        """Write only a supported mode once and verify the device's actual value."""
        if type(mode) is not int or mode not in (1, 2, 3, 4):
            raise ValueError("Unsupported work mode")
        async with self._lock:
            before = (await self._read(49203, 1, kind="holding"))[0]
            if before not in (1, 2, 3, 4):
                raise ModbusReadError("Unknown current work mode")
            if before != mode:
                await self._read(49203, 1, kind="write", value=mode)
            after = (await self._read(49203, 1, kind="holding"))[0]
            if after != mode:
                raise ModbusReadError("Work mode readback mismatch")
            return after

    async def diagnostics(self) -> dict[str, int | float | bool | None]:
        """Read the E3 work mode, condensed alarms, and primary battery health."""
        async with self._lock:
            try:
                mode = (await self._read(49203, 1, kind="holding"))[0]
            except ModbusReadError:
                mode = None
            status = (await self._read(39063, 1))[0]
            off_grid = self._unsigned32(await self._read(39065, 2))
            alarms = await self._read(39067, 3)
            ambient = soh = fault = None
            connected = None
            if self._include_battery:
                try:
                    connected = (await self._read(37002, 1))[0] == 1
                except ModbusReadError:
                    pass
                if connected:
                    for address, field in ((37611, "ambient"), (37624, "soh"),
                                           (37626, "fault")):
                        try:
                            value = (await self._read(address, 1))[0]
                        except ModbusReadError:
                            value = None
                        if field == "ambient":
                            ambient = value
                        elif field == "soh":
                            soh = value
                        else:
                            fault = value
        temperature = None if ambient is None else (
            ambient - 65536 if ambient & 0x8000 else ambient
        ) / 10
        return {
            "workMode": mode if mode in (1, 2, 3, 4) else None,
            "inverterStatus": status,
            "offGrid": bool(off_grid & 1),
            "alarm1": alarms[0],
            "alarm2": alarms[1],
            "alarm3": alarms[2],
            "batteryConnected": connected,
            "batteryFault": fault if fault is not None else 0,
            "batteryFaultKnown": (
                not self._include_battery or (connected is True and fault is not None)
            ),
            "batterySoH": soh if soh is not None and 0 <= soh <= 100 else None,
            "batteryTemperature": (
                temperature if temperature is not None and -40 <= temperature <= 100 else None
            ),
        }

    async def native_totals(self) -> dict[str, int | None]:
        """Read native lifetime Wh counters; E3 PV excludes external Meter 2."""
        addresses = {
            "batteryFeedTotal": 39605,
            "batteryDrawTotal": 39609,
            "gridFeedTotal": 39613,
            "gridDrawTotal": 39617,
        }
        if self._pv_sources == ("e3_total",):
            addresses = {"generationTotal": 39601, **addresses}
        async with self._lock:
            values = {}
            for key, address in addresses.items():
                try:
                    values[key] = self._unsigned32(await self._read(address, 2)) * 10
                except ModbusReadError:
                    values[key] = None
        if self._pv_sources != ("e3_total",):
            values["generationTotal"] = None
        return values

    async def read_optional(self, descriptions) -> dict[str, int | float | str | None]:
        """Read only enabled optional fields over the existing serialized socket.

        A missing/unreadable component is unknown, never a plausible zero.  A
        transport failure is left to the optional coordinator and does not make
        the independent power coordinator unavailable.
        """
        if not descriptions:
            return {}
        gate_addresses = {"bms1": 37002, "bms2": 37700,
                          "meter1": 38801, "meter2": 38901}
        result: dict[str, int | float | str | None] = {}
        gates: dict[str, bool] = {}
        for description in descriptions:
            kind = description.kind
            count = description.count
            if (description.register_type not in ("input", "holding")
                    or kind not in ("uint16", "int16", "uint32", "int32", "str", "bit16")
                    or (kind in ("uint16", "int16", "bit16") and count != 1)
                    or (kind in ("uint32", "int32") and count != 2)
                    or kind == "str" and not 1 <= count <= 32):
                raise ValueError("Unsupported optional register definition")
            connection = description.connection
            if connection is not None:
                if connection not in gate_addresses:
                    raise ValueError("Unsupported optional connection gate")
                if (description.address == gate_addresses[connection]
                        and description.register_type == "input" and count == 1):
                    connection = None  # The disconnected flag itself is still 0.
            # Each operation releases the lock separately: a timed-out gate
            # and a timed-out field must not block a queued power poll twice.
            if connection is not None:
                if connection not in gates:
                    async with self._lock:
                        try:
                            gates[connection] = (
                                await self._read(gate_addresses[connection], 1)
                            )[0] == 1
                        except ModbusReadError:
                            gates[connection] = False
                if not gates[connection]:
                    result[description.key] = None
                    continue
            async with self._lock:
                try:
                    words = await self._read(
                        description.address, count, kind=description.register_type
                    )
                except ModbusReadError:
                    result[description.key] = None
                    continue
            if kind == "str":
                raw = b"".join(word.to_bytes(2, "big") for word in words)
                try:
                    text = raw.strip(b"\x00 ").decode("ascii")
                except UnicodeDecodeError:
                    text = ""
                value = text if text and all(32 <= ord(c) <= 126 for c in text) else None
            else:
                number = self._unsigned32(words) if count == 2 else words[0]
                if kind.startswith("int") and number & (1 << (count * 16 - 1)):
                    number -= 1 << (count * 16)
                value = number * description.scale
            result[description.key] = value
        return result

    async def daily_work(self) -> dict[str, int | None]:
        addresses = {
            "generation": 39603, "consumption": 39631,
            "batteryFeed": 39607, "batteryDraw": 39611,
            "gridFeed": 39615, "gridDraw": 39619,
        }
        if self._pv_sources != ("e3_total",):
            del addresses["generation"]
        async with self._lock:
            values = {
                key: self._unsigned32(await self._read(address, 2)) * 10
                for key, address in addresses.items()
            }
            if any(value > 1_000_000 for value in values.values()):
                raise ModbusReadError("Invalid Modbus register response")
            if self._pv_sources != ("e3_total",):
                # The E3 counter does not represent an arbitrary chosen subset
                # or an additional AC-coupled inverter at Meter 2.
                values["generation"] = None
        return values

    @staticmethod
    def _unsigned32(registers: list[int]) -> int:
        return (registers[0] << 16) | registers[1]

    @classmethod
    def _signed32(cls, registers: list[int]) -> int:
        value = cls._unsigned32(registers)
        return value - 0x100000000 if value & 0x80000000 else value

    def suspend(self) -> None:
        """Synchronously release the socket when a legacy entry starts."""
        # Closing before connection_made() sets PyModbus's is_closing flag,
        # which makes close() inside trace_connect() a no-op after it opens.
        if self._connecting and not self._client.connected:
            return
        try:
            self._client.close()
        except Exception:
            pass

    @asynccontextmanager
    async def exclusive_probe(self) -> AsyncIterator[None]:
        """Keep regular polls out while a replacement endpoint is probed."""
        async with self._lock:
            self._client.close()
            yield

    async def close(self) -> None:
        async with self._lock:
            self._client.close()
