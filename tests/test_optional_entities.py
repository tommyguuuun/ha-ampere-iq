"""Optional E3 entities appear in the registry, but not in the default dashboard."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.ekd_ampere_iq.optional_registers import OPTIONAL_REGISTERS
from custom_components.ekd_ampere_iq.sensor import OptionalSensor


def test_optional_entity_has_german_name_and_never_enables_itself():
    coordinator = SimpleNamespace(data={"bms_temperature": 24.6}, last_update_success=True)
    description = SimpleNamespace(
        key="bms_temperature", name="Batteriemodul 1 Temperatur",
        unit="°C", device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    )
    entity = OptionalSensor(coordinator, "serial-123", description)
    assert entity.entity_registry_enabled_default is False
    assert entity.name == "Batteriemodul 1 Temperatur"
    assert entity.unique_id == "serial-123_optional_bms_temperature"
    assert entity.native_value == 24.6
    assert entity.native_unit_of_measurement == "°C"
    assert entity.device_class == SensorDeviceClass.TEMPERATURE
    assert entity.state_class == SensorStateClass.MEASUREMENT
    assert entity.coordinator_context == "bms_temperature"
    assert entity.available
    coordinator.data = {}
    assert entity.native_value is None
    coordinator.last_update_success = False
    assert not entity.available


def test_optional_status_value_is_diagnostic_and_unknown_not_zero():
    coordinator = SimpleNamespace(data={"firmware": None}, last_update_success=True)
    description = SimpleNamespace(
        key="firmware", name="Wechselrichter Firmwareversion",
        unit=None, device_class=None, state_class=None,
    )
    entity = OptionalSensor(coordinator, "serial-123", description)
    assert entity.native_value is None
    assert entity.entity_category == "diagnostic"
    assert entity.name == "Wechselrichter Firmwareversion"


def test_network_status_codes_are_understandable_in_german():
    coordinator = SimpleNamespace(data={"network_status": 2}, last_update_success=True)
    description = SimpleNamespace(
        key="network_status", name="Netzwerkverbindungsstatus",
        unit=None, device_class=None, state_class=None,
    )
    entity = OptionalSensor(coordinator, "serial-123", description)
    assert entity.native_value == "Verbunden"
    coordinator.data = {"network_status": 1}
    assert entity.native_value == "Verbindung unterbrochen"
    coordinator.data = {"network_status": 0}
    assert entity.native_value == "Nicht verbunden"
    coordinator.data = {"network_status": 47}
    assert entity.native_value == "Unbekannt (47)"


def test_daily_energy_has_successful_poll_day_and_hides_yesterdays_value():
    day = date(2026, 10, 1)
    tz = ZoneInfo("Europe/Berlin")
    description = next(d for d in OPTIONAL_REGISTERS if d.key == "inverter_generation_today")
    coordinator = SimpleNamespace(
        data={description.key: 1.5}, data_day=day, last_update_success=True
    )
    entity = OptionalSensor(coordinator, "device-1", description)
    with patch("custom_components.ekd_ampere_iq.sensor.dt_util.now",
               return_value=datetime(2026, 10, 1, 12, tzinfo=tz)):
        assert entity.native_value == 1.5
        assert entity.last_reset.date() == day
    with patch("custom_components.ekd_ampere_iq.sensor.dt_util.now",
               return_value=datetime(2026, 10, 2, 0, 1, tzinfo=tz)):
        assert entity.native_value is None
    coordinator.data_day = None
    assert entity.last_reset is None
