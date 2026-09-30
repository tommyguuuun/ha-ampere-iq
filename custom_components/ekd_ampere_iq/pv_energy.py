"""Accumulate selected local PV power into energy without inventing outage yield."""

import asyncio
from datetime import datetime, time
from hashlib import sha256
from math import isfinite
from zoneinfo import ZoneInfo

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util
from homeassistant.util import json as json_util

from .const import DOMAIN


def pv_energy_store_key(identity: str, sources: tuple[str, ...]) -> str:
    """Bind balances to a device and its selected sources without path input."""
    digest = sha256(f"{identity}\0{','.join(sorted(sources))}".encode()).hexdigest()
    return f"{DOMAIN}.pv_energy.{digest}"


class PvEnergyAccumulator:
    """Left-Riemann PV energy accounting from consecutive valid power samples."""

    def __init__(self, store, timezone: ZoneInfo, *, max_gap_seconds: float):
        self._store = store
        self._timezone = timezone
        self._max_gap_seconds = max_gap_seconds
        self._anchor: tuple[datetime, float] | None = None
        self.total_kwh = 0.0
        self.today_kwh = 0.0
        self.day = None
        self.available = False
        self._listeners = set()
        self._last_save_request: datetime | None = None
        self._uncommitted = False
        self._commit_in_progress = False
        self._commit_lock = asyncio.Lock()

    def _schedule_save(self, at: datetime) -> None:
        if self._last_save_request is not None:
            since_last = (at - self._last_save_request).total_seconds()
            if 0 <= since_last < 60:
                return
        self._last_save_request = at
        self._store.async_delay_save(self._snapshot, 0)

    def add_listener(self, callback):
        """Register a sensor update callback, returning its unsubscriber."""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    async def async_load(self, at: datetime) -> None:
        """Restore accounting balances, but never reuse stale power as an anchor."""
        self.day = at.astimezone(self._timezone).date()
        saved = await self._store.async_load()
        if saved is None:
            return
        if not isinstance(saved, dict):
            raise ValueError("Invalid stored PV energy balance")

        def balance(key: str) -> float:
            value = saved.get(key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
            ):
                raise ValueError("Invalid stored PV energy balance")
            return float(value)

        self.total_kwh = balance("total_kwh")
        today = balance("today_kwh")
        if saved.get("day") == self.day.isoformat():
            self.today_kwh = today

    def _snapshot(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "total_kwh": self.total_kwh,
            "today_kwh": self.today_kwh,
        }

    def invalidate(self) -> None:
        """A failed poll breaks integration continuity."""
        self._anchor = None
        self.available = False
        self._notify()

    def advance_day(self, at: datetime) -> None:
        """Reset the daily balance at local midnight even without new readings."""
        current_day = at.astimezone(self._timezone).date()
        if current_day != self.day:
            self.day = current_day
            self.today_kwh = 0.0
            if not self._commit_in_progress:
                self._schedule_save(at)
                self._notify()

    def observe(
        self, power_w: float | None, at: datetime, *,
        publish: bool = True, schedule: bool = True,
    ) -> None:
        """Add one segment only when both ends belong to a valid poll sequence."""
        if (
            isinstance(power_w, bool)
            or not isinstance(power_w, (int, float))
            or not isfinite(power_w)
            or power_w < 0
        ):
            if publish:
                self.invalidate()
            else:
                self._anchor = None
                self.available = False
            return
        current_day = at.astimezone(self._timezone).date()
        if current_day != self.day:
            self.day = current_day
            self.today_kwh = 0.0
            if schedule:
                self._schedule_save(at)
        if self._anchor is not None:
            previous_at, previous_w = self._anchor
            elapsed = (at - previous_at).total_seconds()
            if 0 < elapsed <= self._max_gap_seconds:
                energy = previous_w * elapsed / 3_600_000
                self.total_kwh += energy
                start_of_day = datetime.combine(current_day, time.min, self._timezone)
                today_seconds = (at - max(previous_at, start_of_day)).total_seconds()
                self.today_kwh += previous_w * max(0, today_seconds) / 3_600_000
                if energy and schedule:
                    self._schedule_save(at)
        self._anchor = (at, float(power_w))
        self.available = True
        if publish:
            self._notify()

    async def async_observe_and_commit(self, power_w: float | None, at: datetime) -> None:
        """Durably checkpoint each changed balance before notifying HA sensors."""
        async with self._commit_lock:
            previous = (self.total_kwh, self.today_kwh, self.day)
            self.observe(power_w, at, publish=False, schedule=False)
            if not self.available:
                self._notify()
                return
            if self._uncommitted or (self.total_kwh, self.today_kwh, self.day) != previous:
                self._uncommitted = True
                self._commit_in_progress = True
                try:
                    snapshot = self._snapshot()
                    await self._store.async_save(snapshot)
                    # HA's Store logs WriteError without raising; read the on-disk
                    # checkpoint before letting Recorder see the higher total.
                    if hasattr(self._store, "path"):
                        saved = await self._store.hass.async_add_executor_job(
                            json_util.load_json, self._store.path
                        )
                        if not isinstance(saved, dict) or saved.get("data") != snapshot:
                            raise OSError("PV energy checkpoint not persisted")
                    self._uncommitted = False
                except BaseException:
                    self.invalidate()
                    raise
                finally:
                    self._commit_in_progress = False
            self._notify()

    async def async_flush(self) -> None:
        """Persist balances before a configuration-entry unload."""
        await self._store.async_save(self._snapshot())


async def async_start_pv_energy(
    hass, entry, coordinator, store, timezone: ZoneInfo, *, interval_seconds: int,
) -> PvEnergyAccumulator:
    """Subscribe one accumulator to regular Modbus power polls and local midnight."""
    energy = PvEnergyAccumulator(
        store, timezone, max_gap_seconds=max(30, 2 * interval_seconds)
    )
    await energy.async_load(dt_util.utcnow())

    @callback
    def on_power_update() -> None:
        if not coordinator.last_update_success:
            energy.invalidate()

    @callback
    def on_midnight(now: datetime) -> None:
        energy.advance_day(now)

    unsubscribe_power = coordinator.async_add_listener(on_power_update)
    unsubscribe_midnight = None
    try:
        unsubscribe_midnight = async_track_time_change(
            hass, on_midnight, hour=0, minute=0, second=0
        )
        if coordinator.last_update_success and isinstance(coordinator.data, dict):
            energy.observe(coordinator.data.get("pvPower"), dt_util.utcnow())
        else:
            energy.invalidate()
    except BaseException:
        unsubscribe_power()
        if unsubscribe_midnight is not None:
            unsubscribe_midnight()
        raise
    entry.async_on_unload(unsubscribe_power)
    entry.async_on_unload(unsubscribe_midnight)
    return energy
