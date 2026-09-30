"""German read-only sensors for a single EKD installation."""

from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTemperature
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_CONNECTION_TYPE,
    CONF_EQUIPMENT,
    CONF_PV_SOURCES,
    CONNECTION_CLOUD,
    CONNECTION_MODBUS,
    DEFAULT_PV_SOURCES,
    DOMAIN,
    EQUIPMENT,
    MODBUS_EQUIPMENT,
)
from .device_status import summarize_device


@dataclass(frozen=True)
class Description:
    key: str
    name: str
    field: str
    direction: str = "raw"
    energy: bool = False
    soc: bool = False
    cumulative: bool = False
    equipment: str = "inverter"


POWER_SENSORS = (
    Description("pv", "PV-Leistung", "pvPower", "positive"),
    Description("house", "Hausverbrauch", "housePower", "negative"),
    Description("grid_import", "Netzbezug", "gridPower", "positive"),
    Description("grid_export", "Netzeinspeisung", "gridPower", "negative"),
    Description(
        "battery_power", "Batterieleistung", "batteryPower", equipment="battery"
    ),
    Description(
        "battery_charge", "Batterieladung", "batteryPower", "negative", equipment="battery"
    ),
    Description(
        "battery_discharge", "Batterieentladung", "batteryPower", "positive", equipment="battery"
    ),
    Description("wallbox", "Wallbox-Verbrauch", "wallboxPower", "negative", equipment="wallbox"),
    Description(
        "heat_pump", "Wärmepumpen-Verbrauch", "heatPumpPower", "negative", equipment="heat_pump"
    ),
    Description(
        "heating_rod", "Heizstab-Verbrauch", "heatingRodPower", "negative", equipment="heat_pump"
    ),
    Description("battery_soc", "Batterieladestand", "batterySoc", soc=True, equipment="battery"),
)
MODBUS_POWER_SENSORS = (
    Description("pv", "PV-Leistung", "pvPower", "positive"),
    Description("house", "Hausverbrauch", "housePower", "positive"),
    # HA Recorder comparison against the active cloud entities shows positive
    # E3 grid power during export, negative during import; battery uses the
    # opposite sign for charging and positive for discharge.
    Description("grid_import", "Netzbezug", "gridPower", "negative"),
    Description("grid_export", "Netzeinspeisung", "gridPower", "positive"),
    Description(
        "battery_power", "Batterieleistung", "batteryPower", equipment="battery"
    ),
    Description(
        "battery_charge", "Batterieladung", "batteryPower", "negative", equipment="battery"
    ),
    Description(
        "battery_discharge", "Batterieentladung", "batteryPower", "positive", equipment="battery"
    ),
    Description("battery_soc", "Batterieladestand", "batterySoc", soc=True, equipment="battery"),
)
ENERGY_SENSORS = (
    Description("generation", "PV-Ertrag heute", "generation", energy=True),
    Description("consumption", "Gesamtverbrauch heute", "consumption", energy=True),
    Description(
        "batteryFeed", "Batterieladung heute", "batteryFeed", energy=True, equipment="battery"
    ),
    Description(
        "batteryDraw", "Batterieentladung heute", "batteryDraw", energy=True, equipment="battery"
    ),
    Description("gridFeed", "Netzeinspeisung heute", "gridFeed", energy=True),
    Description("gridDraw", "Netzbezug heute", "gridDraw", energy=True),
)

NATIVE_TOTAL_SENSORS = (
    Description("generationTotal", "PV-Produktion gesamt", "generationTotal",
                energy=True, cumulative=True),
    Description("batteryFeedTotal", "Batterieladung gesamt", "batteryFeedTotal",
                energy=True, cumulative=True, equipment="battery"),
    Description("batteryDrawTotal", "Batterieentladung gesamt", "batteryDrawTotal",
                energy=True, cumulative=True, equipment="battery"),
    Description("gridFeedTotal", "Netzeinspeisung gesamt", "gridFeedTotal",
                energy=True, cumulative=True),
    Description("gridDrawTotal", "Netzbezug gesamt", "gridDrawTotal",
                energy=True, cumulative=True),
)

# Explicit icons override the generic power/energy device-class lightning bolt.
SENSOR_ICONS = {
    "pv": "mdi:solar-power",
    "house": "mdi:home-lightning-bolt",
    "grid_import": "mdi:transmission-tower-import",
    "grid_export": "mdi:transmission-tower-export",
    "battery_power": "mdi:battery-sync",
    "battery_charge": "mdi:battery-charging",
    "battery_discharge": "mdi:battery-arrow-down",
    "wallbox": "mdi:ev-station",
    "heat_pump": "mdi:heat-pump",
    "heating_rod": "mdi:radiator",
    "battery_soc": "mdi:battery-high",
    "generation": "mdi:solar-power-variant",
    "consumption": "mdi:home-lightning-bolt-outline",
    "batteryFeed": "mdi:battery-plus",
    "batteryDraw": "mdi:battery-minus",
    "gridFeed": "mdi:home-export-outline",
    "gridDraw": "mdi:home-import-outline",
    "generationTotal": "mdi:solar-power-variant",
    "batteryFeedTotal": "mdi:battery-plus",
    "batteryDrawTotal": "mdi:battery-minus",
    "gridFeedTotal": "mdi:home-export-outline",
    "gridDrawTotal": "mdi:home-import-outline",
}


def suggested_object_id(description: Description) -> str:
    """Suggest the existing cloud-style German entity ID for clean installs."""
    return f"ampere_iq_{slugify(description.name)}"


class PvEnergySensor(SensorEntity):
    """Local, source-specific power integral rather than an incomplete E3 register."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 3

    def __init__(self, energy, installation_uuid: str, kind: str, sources: str) -> None:
        self._energy = energy
        self._kind = kind
        self._attr_name = (
            "PV-Produktion heute (berechnet)" if kind == "today"
            else "PV-Produktion gesamt (berechnet)"
        )
        self._attr_icon = "mdi:solar-power-variant"
        self._attr_unique_id = f"{installation_uuid}_pv_energy_{kind}_{sources}"
        self.entity_id = f"sensor.{self.suggested_object_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, installation_uuid)},
            "name": "Ampere.IQ",
            "manufacturer": "EKD",
            "model": "Ampere StoragePro E3",
        }

    @property
    def suggested_object_id(self) -> str:
        return f"ampere_iq_{slugify(self._attr_name)}"

    @property
    def available(self) -> bool:
        return self._energy.available

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._energy.add_listener(self.async_write_ha_state))

    @property
    def last_reset(self):
        if self._kind == "today" and self._energy.day is not None:
            return datetime.combine(self._energy.day, time.min, self._energy._timezone)
        return None

    @property
    def native_value(self) -> float:
        return self._energy.today_kwh if self._kind == "today" else self._energy.total_kwh


class DiagnosticStatusSensor(CoordinatorEntity, SensorEntity):
    """Condensed E3 operating status or human-readable fault reason."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, installation_uuid: str, kind: str) -> None:
        super().__init__(coordinator)
        if kind not in ("status", "reason"):
            raise ValueError("Unknown diagnostic status kind")
        self._kind = kind
        self._attr_name = "Anlagenstatus" if kind == "status" else "Störungsgrund"
        self._attr_icon = (
            "mdi:information-outline" if kind == "status" else "mdi:alert-circle-outline"
        )
        suffix = "device_status" if kind == "status" else "device_fault_reason"
        self._attr_unique_id = f"{installation_uuid}_{suffix}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, installation_uuid)},
            "name": "Ampere.IQ", "manufacturer": "EKD", "model": "Ampere StoragePro E3",
        }

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return summarize_device(data)[self._kind] if isinstance(data, dict) else None


class DiagnosticNumericSensor(CoordinatorEntity, SensorEntity):
    """Primary E3 BMS health and temperature, not per-cell debug output."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, installation_uuid: str, kind: str) -> None:
        super().__init__(coordinator)
        if kind not in ("soh", "temperature"):
            raise ValueError("Unknown battery diagnostic kind")
        self._kind = kind
        self._attr_name = "Batteriegesundheit" if kind == "soh" else "Batterietemperatur"
        self._attr_icon = "mdi:battery-heart-variant" if kind == "soh" else "mdi:thermometer"
        self._attr_unique_id = f"{installation_uuid}_battery_{kind}"
        self._attr_device_class = SensorDeviceClass.TEMPERATURE if kind == "temperature" else None
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS if kind == "temperature" else PERCENTAGE
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, installation_uuid)},
            "name": "Ampere.IQ", "manufacturer": "EKD", "model": "Ampere StoragePro E3",
        }

    @property
    def native_value(self) -> int | float | None:
        data = self.coordinator.data
        field = "batterySoH" if self._kind == "soh" else "batteryTemperature"
        value = data.get(field) if isinstance(data, dict) else None
        if type(value) not in (int, float) or not isfinite(value):
            return None
        if self._kind == "soh" and not 0 <= value <= 100:
            return None
        if self._kind == "temperature" and not -40 <= value <= 100:
            return None
        return value


class EkdSensor(CoordinatorEntity, SensorEntity):
    """A coordinator-backed read-only measurement."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, installation_uuid: str, description: Description,
        connection_type: str = CONNECTION_CLOUD,
    ) -> None:
        super().__init__(coordinator)
        self._description = description
        self._attr_name = description.name
        self._attr_icon = SENSOR_ICONS[description.key]
        self._attr_unique_id = f"{installation_uuid}_{description.key}"
        # A clean Modbus install suggests the familiar cloud entity ID; an
        # existing registry entry always wins by stable unique_id instead.
        self.entity_id = f"sensor.{suggested_object_id(description)}"

        # All measurements belong to the original installation device. Stable
        # unique IDs and entity IDs retain dashboards, history and automations.
        self._attr_device_info = {
            "identifiers": {(DOMAIN, installation_uuid)},
            "name": "Ampere.IQ",
            "manufacturer": "EKD",
            "model": (
                "Ampere StoragePro E3"
                if connection_type == CONNECTION_MODBUS
                else "Ampere.IQ"
            ),
        }
        self._attr_device_class = (
            SensorDeviceClass.ENERGY
            if description.energy
            else None
            if description.soc
            else SensorDeviceClass.POWER
        )
        self._attr_state_class = (
            SensorStateClass.TOTAL_INCREASING if description.cumulative
            else SensorStateClass.TOTAL if description.energy
            else SensorStateClass.MEASUREMENT
        )
        self._attr_suggested_display_precision = 3 if description.energy else None
        self._attr_native_unit_of_measurement = (
            UnitOfEnergy.KILO_WATT_HOUR
            if description.energy
            else PERCENTAGE
            if description.soc
            else UnitOfPower.WATT
        )

    @property
    def suggested_object_id(self) -> str:
        """Suggest the established cloud-style ID without the HA device prefix."""
        return suggested_object_id(self._description)

    @property
    def last_reset(self):
        """Use the successfully polled local day, not the current clock day."""
        day = getattr(self.coordinator, "data_day", None)
        if not self._description.energy or self._description.cumulative or day is None:
            return None
        return dt_util.start_of_local_day(day)

    @property
    def native_value(self) -> float | None:
        """Normalize EKD signed values; absent/invalid measurements stay unknown."""
        data = self.coordinator.data
        value = data.get(self._description.field) if isinstance(data, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            return None
        if self._description.soc and not 0 <= value <= 100:
            return None
        if self._description.energy and value < 0:
            return None
        if self._description.energy:
            # The vendor reports daily work in Wh; HA exposes it in kWh.
            return value / 1000
        if self._description.direction == "negative":
            return max(0, -value)
        if self._description.direction == "positive":
            return max(0, value)
        return value


def device_by_identifier(registry, entry_id: str, identifier: str):
    """Find only this entry's device across supported HA registry APIs."""
    if getter := getattr(registry, "async_get_device_by_identifier", None):
        return getter((DOMAIN, identifier), entry_id)
    return registry.async_get_device(identifiers={(DOMAIN, identifier)})


def reconcile_device_registry(hass, entry, installation_uuid: str) -> None:
    """Return legacy component sensors to one installation device.

    Only remove unmodified, empty devices created by this integration. A user's
    customized device is retained rather than deleting their configuration.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    model = (
        "Ampere StoragePro E3"
        if getattr(entry, "data", {}).get("connection_type") == CONNECTION_MODBUS
        else "Ampere.IQ"
    )
    root = device_by_identifier(device_registry, entry.entry_id, installation_uuid)
    if root is None:
        root = device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, installation_uuid)},
            name="Ampere.IQ",
            manufacturer="EKD",
            model=model,
        )
    if entry.entry_id not in root.config_entries:
        return

    known_keys = {
        desc.key for desc in (
            *POWER_SENSORS, *MODBUS_POWER_SENSORS, *ENERGY_SENSORS,
            *NATIVE_TOTAL_SENSORS,
        )
    } | {"device_status", "device_fault_reason", "battery_soh", "battery_temperature"}
    for equipment in ("battery", "wallbox", "heat_pump"):
        old = device_by_identifier(
            device_registry, entry.entry_id, f"{installation_uuid}_{equipment}"
        )
        if old is None or old.config_entries != {entry.entry_id}:
            continue
        for entity in tuple(entity_registry.entities.values()):
            if (
                entity.device_id == old.id
                and entity.config_entry_id == entry.entry_id
                and entity.platform == DOMAIN
                and entity.unique_id.startswith(f"{installation_uuid}_")
                and entity.unique_id[len(installation_uuid) + 1 :] in known_keys
            ):
                entity_registry.async_update_entity(entity.entity_id, device_id=root.id)
        if (
            not any(e.device_id == old.id for e in entity_registry.entities.values())
            and not any(
                d.via_device_id == old.id
                for d in (
                    device_registry.devices
                    if hasattr(device_registry, "async_get_device_by_identifier")
                    else device_registry.devices.values()
                )
            )
            and old.name_by_user is None
            and old.area_id is None
            and not old.labels
            and old.disabled_by is None
        ):
            device_registry.async_remove_device(old.id)


def reconcile_energy_unit_registry(hass, entry, installation_uuid: str) -> None:
    """Update HA's stored automatic Wh suggestion after exposing daily kWh.

    An explicit user unit selection in ``options.sensor`` is left untouched.
    """
    registry = er.async_get(hass)
    energy_ids = {f"{installation_uuid}_{desc.key}" for desc in ENERGY_SENSORS}
    for entity in tuple(registry.entities.values()):
        if (
            entity.config_entry_id != entry.entry_id
            or entity.platform != DOMAIN
            or entity.unique_id not in energy_ids
        ):
            continue
        saved = entity.options.get("sensor.private", {})
        if saved.get("suggested_unit_of_measurement") == UnitOfEnergy.WATT_HOUR:
            registry.async_update_entity_options(
                entity.entity_id,
                "sensor.private",
                {**saved, "suggested_unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR},
            )


def reconcile_entity_registry(hass, entry, installation_uuid: str) -> None:
    """Disable unselected EKD sensors without deleting IDs or user customizations."""
    selected = set(entry.options.get(CONF_EQUIPMENT, EQUIPMENT))
    modbus = getattr(entry, "data", {}).get(CONF_CONNECTION_TYPE) == CONNECTION_MODBUS
    pv_daily_supported = (
        not modbus or tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
        == DEFAULT_PV_SOURCES
    )
    equipment_by_key = {
        description.key: description.equipment
        for description in (
            *POWER_SENSORS, *MODBUS_POWER_SENSORS, *ENERGY_SENSORS,
            *NATIVE_TOTAL_SENSORS,
        )
    }
    equipment_by_key.update({
        "battery_soh": "battery", "battery_temperature": "battery",
        "device_status": "system", "device_fault_reason": "system",
    })
    prefix = f"{installation_uuid}_"
    sources_signature = "_".join(sorted(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES)))
    selected_derived = {
        f"pv_energy_{kind}_{sources_signature}" for kind in ("today", "total")
    }
    registry = er.async_get(hass)
    for entity in tuple(registry.entities.values()):
        if (
            entity.config_entry_id != entry.entry_id
            or entity.platform != DOMAIN
            or not entity.unique_id.startswith(prefix)
        ):
            continue
        key = entity.unique_id[len(prefix) :]
        derived = key.startswith(("pv_energy_today_", "pv_energy_total_"))
        equipment = equipment_by_key.get(key)
        if equipment is None and not derived:
            continue
        enabled = (
            modbus and "inverter" in selected and not pv_daily_supported
            and key in selected_derived
            if derived else
            modbus and bool(selected & set(MODBUS_EQUIPMENT))
            if equipment == "system" else
            equipment in selected and not (
                key in ("generation", "generationTotal") and not pv_daily_supported
            )
        )
        if not enabled:
            if entity.disabled_by is None:
                registry.async_update_entity(
                    entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
                )
        elif entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(entity.entity_id, disabled_by=None)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Create measurements for the two independently polled endpoints."""
    runtime = entry.runtime_data
    connection_type = getattr(runtime, "connection_type", CONNECTION_CLOUD)
    modbus = connection_type == CONNECTION_MODBUS
    selected = set(entry.options.get(CONF_EQUIPMENT, MODBUS_EQUIPMENT if modbus else EQUIPMENT))
    descriptions = MODBUS_POWER_SENSORS if modbus else POWER_SENSORS
    if modbus:
        selected.intersection_update(MODBUS_EQUIPMENT)
    async_add_entities(
        [
            *(
                EkdSensor(runtime.power, runtime.uuid, desc, connection_type)
                for desc in descriptions
                if desc.equipment in selected
            ),
            *(
                EkdSensor(runtime.work, runtime.uuid, desc, connection_type)
                for desc in ENERGY_SENSORS
                if desc.equipment in selected
                and not (modbus and desc.key == "generation"
                         and tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
                         != DEFAULT_PV_SOURCES)
            ),
            *(
                EkdSensor(runtime.lifetime, runtime.uuid, desc, connection_type)
                for desc in NATIVE_TOTAL_SENSORS
                if modbus and getattr(runtime, "lifetime", None) is not None
                and desc.equipment in selected
                and not (desc.key == "generationTotal"
                         and tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
                         != DEFAULT_PV_SOURCES)
            ),
            *(
                DiagnosticStatusSensor(runtime.diagnostics, runtime.uuid, kind)
                for kind in ("status", "reason")
                if modbus and getattr(runtime, "diagnostics", None) is not None
            ),
            *(
                DiagnosticNumericSensor(runtime.diagnostics, runtime.uuid, kind)
                for kind in ("soh", "temperature")
                if modbus and "battery" in selected
                and getattr(runtime, "diagnostics", None) is not None
            ),
            *(
                PvEnergySensor(
                    runtime.pv_energy, runtime.uuid, kind,
                    "_".join(sorted(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))),
                )
                for kind in ("today", "total")
                if modbus and "inverter" in selected
                and tuple(entry.options.get(CONF_PV_SOURCES, DEFAULT_PV_SOURCES))
                != DEFAULT_PV_SOURCES
                and getattr(runtime, "pv_energy", None) is not None
            ),
        ]
    )
