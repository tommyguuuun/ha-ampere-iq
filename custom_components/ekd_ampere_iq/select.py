"""Explicitly scoped StoragePro E3 work-mode selector."""
from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CONNECTION_TYPE, CONNECTION_MODBUS, DOMAIN
from .modbus import ModbusConnectionError, ModbusReadError

MODE_TO_CODE = {
    "Eigenverbrauch": 1,
    "Einspeisung bevorzugen": 2,
    "Notstromreserve": 3,
    "Spitzenlastbegrenzung": 4,
}
CODE_TO_MODE = {value: key for key, value in MODE_TO_CODE.items()}


class WorkModeSelect(CoordinatorEntity, SelectEntity):
    """Change only the E3 work mode, with device readback before UI refresh."""

    _attr_has_entity_name = True
    _attr_name = "Betriebsmodus"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-sync"
    _attr_options = list(MODE_TO_CODE)

    def __init__(self, coordinator, reader, installation_uuid: str) -> None:
        super().__init__(coordinator)
        self._reader = reader
        self._attr_unique_id = f"{installation_uuid}_work_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, installation_uuid)},
            "name": "Ampere.IQ",
            "manufacturer": "EKD",
            "model": "Ampere StoragePro E3",
        }

    @property
    def current_option(self) -> str | None:
        data = self.coordinator.data
        return CODE_TO_MODE.get(data.get("workMode")) if isinstance(data, dict) else None

    async def async_select_option(self, option: str) -> None:
        if option not in MODE_TO_CODE:
            raise HomeAssistantError("Unsupported or unavailable E3 work mode")
        if self.current_option is None or not self.coordinator.last_update_success:
            await self.coordinator.async_refresh()
        if self.current_option is None or not self.coordinator.last_update_success:
            raise HomeAssistantError("Unsupported or unavailable E3 work mode")
        try:
            await self._reader.set_work_mode(MODE_TO_CODE[option])
        except (ModbusConnectionError, ModbusReadError):
            raise HomeAssistantError("E3 work mode change could not be verified") from None
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Cloud entries never expose a Modbus write entity."""
    if entry.data.get(CONF_CONNECTION_TYPE) != CONNECTION_MODBUS:
        return
    runtime = entry.runtime_data
    if runtime.reader is not None and runtime.diagnostics is not None:
        async_add_entities([WorkModeSelect(runtime.diagnostics, runtime.reader, runtime.uuid)])
