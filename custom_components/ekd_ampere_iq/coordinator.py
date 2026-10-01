"""One poll per endpoint per installation, shared by its sensors."""

import logging
from datetime import timedelta

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApiAuthError, ApiConnectionError, ApiResponseError
from .const import DEFAULT_POLL_INTERVAL
from .modbus import ModbusConnectionError, ModbusReadError

_LOGGER = logging.getLogger(__name__)


class PowerCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass, api, uuid, entry, interval=DEFAULT_POLL_INTERVAL, *, modbus=False):
        super().__init__(
            hass,
            _LOGGER,
            name="Ampere.IQ Leistung",
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )
        self.api = api
        self.uuid = uuid
        self.modbus = modbus
        self.pv_energy = None

    async def _async_update_data(self):
        try:
            data = await (self.api.power() if self.modbus else self.api.power(self.uuid))
            if not isinstance(data, dict):
                if self.modbus:
                    raise ModbusReadError("Invalid Modbus register response")
                raise ApiResponseError("Invalid power payload")
            if self.modbus and self.pv_energy is not None:
                try:
                    await self.pv_energy.async_observe_and_commit(
                        data.get("pvPower"), dt_util.utcnow()
                    )
                except Exception:
                    _LOGGER.warning("Calculated PV energy checkpoint unavailable")
                    self.pv_energy.invalidate()
            return data
        except (ModbusConnectionError, ModbusReadError):
            if self.pv_energy is not None:
                self.pv_energy.invalidate()
            raise UpdateFailed("EKD Modbus power update failed") from None
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed("EKD authentication failed") from err
        except (ApiConnectionError, ApiResponseError) as err:
            raise UpdateFailed(f"EKD power update failed: {err}") from err


class WorkCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass, api, uuid, entry, *, modbus=False):
        super().__init__(
            hass,
            _LOGGER,
            name="Ampere.IQ Tagesarbeit",
            config_entry=entry,
            update_interval=timedelta(minutes=15),
        )
        self.api = api
        self.uuid = uuid
        self.modbus = modbus
        self.data_day = None

    async def _async_update_data(self):
        try:
            day = dt_util.now().date()
            data = await (
                self.api.daily_work() if self.modbus else self.api.daily_work(self.uuid, day)
            )
            if not isinstance(data, dict):
                if self.modbus:
                    raise ModbusReadError("Invalid Modbus register response")
                raise ApiResponseError("Invalid work payload")
            if self.modbus and dt_util.now().date() != day:
                raise ModbusReadError("Modbus day changed during read")
            self.data_day = day
            return data
        except (ModbusConnectionError, ModbusReadError):
            raise UpdateFailed("EKD Modbus work update failed") from None
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed("EKD authentication failed") from err
        except (ApiConnectionError, ApiResponseError) as err:
            raise UpdateFailed(f"EKD work update failed: {err}") from err


class DiagnosticsCoordinator(DataUpdateCoordinator[dict]):
    """Poll local mode and device health less often than power at 10 seconds."""

    def __init__(self, hass, reader, entry):
        super().__init__(
            hass, _LOGGER, name="Ampere.IQ Gerätestatus",
            config_entry=entry, update_interval=timedelta(seconds=60),
        )
        self.reader = reader

    async def _async_update_data(self):
        try:
            data = await self.reader.diagnostics()
            if not isinstance(data, dict):
                raise ModbusReadError("Invalid Modbus diagnostics")
            return data
        except (ModbusConnectionError, ModbusReadError):
            raise UpdateFailed("EKD Modbus diagnostics update failed") from None


class LifetimeCoordinator(DataUpdateCoordinator[dict]):
    """Keep native lifetime counters separate from day-bound work readings."""

    def __init__(self, hass, reader, entry):
        super().__init__(
            hass, _LOGGER, name="Ampere.IQ Gesamtzähler",
            config_entry=entry, update_interval=timedelta(minutes=15),
        )
        self.reader = reader

    async def _async_update_data(self):
        try:
            data = await self.reader.native_totals()
            if not isinstance(data, dict):
                raise ModbusReadError("Invalid Modbus lifetime counters")
            return data
        except (ModbusConnectionError, ModbusReadError):
            raise UpdateFailed("EKD Modbus lifetime update failed") from None


class OptionalCoordinator(DataUpdateCoordinator[dict]):
    """Poll only enabled optional E3 entities without extra idle register reads."""

    MAX_READS = 12

    def __init__(self, hass, reader, entry, descriptions):
        super().__init__(
            hass, _LOGGER, name="Ampere.IQ optionale Messwerte",
            config_entry=entry, update_interval=timedelta(seconds=60),
        )
        self.reader = reader
        self.descriptions = tuple(descriptions)
        self._cursor = 0
        self.data_day = None

    async def _async_update_data(self):
        contexts = set(self.async_contexts())
        active = [d for d in self.descriptions if d.key in contexts]
        if not active:
            return {}
        start = self._cursor % len(active)
        selected = tuple(
            active[(start + index) % len(active)]
            for index in range(min(self.MAX_READS, len(active)))
        )
        self._cursor = (start + len(selected)) % len(active)
        daily = "inverter_generation_today"
        started = dt_util.now() if any(d.key == daily for d in selected) else None
        try:
            values = await self.reader.read_optional(selected)
            if not isinstance(values, dict):
                raise ModbusReadError("Invalid optional register response")
        except (ModbusConnectionError, ModbusReadError):
            raise UpdateFailed("Optional E3 diagnostics update failed") from None
        if started is not None:
            completed = dt_util.now()
            if (started.date() != completed.date()
                    or not isinstance(values.get(daily), (int, float))):
                values[daily] = None
                self.data_day = None
            else:
                self.data_day = started.date()
        previous = self.data if isinstance(self.data, dict) else {}
        return {**{key: value for key, value in previous.items() if key in contexts},
                **values}
