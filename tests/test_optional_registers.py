"""Safety and reference-coverage contract for the detached E3 optional catalog."""

import json
import re
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorStateClass

from custom_components.ekd_ampere_iq.optional_registers import (
    EXCLUDED_REGISTERS,
    OPTIONAL_REGISTERS,
    OptionalRegister,
)

SOURCE = Path(__file__).parent / "fixtures/e3_reference_registers.json"


def test_public_descriptors_are_immutable_and_stable():
    assert isinstance(OPTIONAL_REGISTERS, tuple)
    assert isinstance(EXCLUDED_REGISTERS, tuple)
    assert {f.name for f in fields(OptionalRegister)} >= {
        "key",
        "name",
        "address",
        "count",
        "kind",
        "scale",
        "unit",
        "equipment",
        "connection",
        "device_class",
        "register_type",
    }
    with pytest.raises(FrozenInstanceError):
        OPTIONAL_REGISTERS[0].address = 0
    assert all(re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", d.key) for d in OPTIONAL_REGISTERS)
    assert len({d.key for d in OPTIONAL_REGISTERS}) == len(OPTIONAL_REGISTERS)
    assert len({d.name for d in OPTIONAL_REGISTERS}) == len(OPTIONAL_REGISTERS)
    assert all("?" not in d.name and d.name != d.key for d in OPTIONAL_REGISTERS)


def test_battery_names_explain_technical_controller_and_submodule_terms():
    battery_names = [d.name for d in OPTIONAL_REGISTERS if d.equipment == "battery"]
    assert all("BMS" not in name and "Slave" not in name for name in battery_names)
    assert any(name.startswith("Batteriesteuerung 2 ") for name in battery_names)


def test_known_signed_unsigned_and_scaled_registers():
    by_address = {d.address: d for d in OPTIONAL_REGISTERS}
    assert (
        by_address[38808].kind,
        by_address[38808].count,
        by_address[38808].scale,
        by_address[38808].unit,
    ) == ("int32", 2, 0.001, "A")
    assert (by_address[37610].kind, by_address[37610].scale) == ("int16", 0.1)
    assert (by_address[39629].kind, by_address[39629].scale, by_address[39629].unit) == (
        "uint32",
        0.01,
        "kWh",
    )
    assert (by_address[49249].register_type, by_address[49249].kind, by_address[49249].scale) == (
        "holding",
        "int16",
        0.01,
    )
    assert by_address[37619].unit == "mV"
    assert 38814 not in by_address  # already exposed


def test_state_classes_distinguish_live_measurements_and_energy_counters():
    by_key = {d.key: d for d in OPTIONAL_REGISTERS}
    assert by_key["pv1_voltage"].state_class == SensorStateClass.MEASUREMENT
    assert by_key["grid_frequency"].state_class == SensorStateClass.MEASUREMENT
    assert by_key["inverter_generation_total"].state_class == SensorStateClass.TOTAL_INCREASING
    assert by_key["load_energy_total"].state_class == SensorStateClass.TOTAL_INCREASING
    assert by_key["inverter_generation_today"].state_class == SensorStateClass.TOTAL
    assert by_key["gfci_current"].state_class == SensorStateClass.MEASUREMENT
    assert by_key["bms1_remaining_energy"].state_class is None


def test_catalog_is_only_disabled_by_default_read_only_modbus_with_safe_gates():
    spans = []
    for d in OPTIONAL_REGISTERS:
        assert d.register_type in {"input", "holding"}
        assert d.kind in {"uint16", "int16", "uint32", "int32", "str", "bit16"}
        assert d.connection in {None, "bms1", "bms2", "meter1", "meter2"}
        assert d.equipment in {"inverter", "battery"}
        assert d.device_class is not None or d.entity_category == "diagnostic"
        assert d.count == (2 if d.kind in {"uint32", "int32"} else 1) or d.kind == "str"
        assert d.count >= 1 and d.scale > 0
        assert d.enabled_by_default is False
        spans.extend((d.register_type, word) for word in range(d.address, d.address + d.count))
    assert len(spans) == len(set(spans))
    for d in OPTIONAL_REGISTERS:
        if d.connection in ("bms1", "bms2"):
            assert d.equipment == "battery"


def test_reference_rows_partition_into_catalog_or_explicit_exclusion():
    reference = json.loads(SOURCE.read_text())
    assert reference["source"] == "Sven0111/Ampere-StoragePro-E3@743d813 sensors.py"
    source = {row["address"]: row for row in reference["registers"]}
    inputs = {d.address for d in OPTIONAL_REGISTERS if d.register_type == "input"}
    excluded = {e.address for e in EXCLUDED_REGISTERS if e.register_type == "input"}
    assert len(excluded) == len([e for e in EXCLUDED_REGISTERS if e.register_type == "input"])
    assert inputs.isdisjoint(excluded)
    assert len(source) == 260
    assert sum(row["visible"] for row in source.values()) == 225
    assert len(inputs) == 154 and len(excluded) == 106
    assert set(source) == inputs | excluded
    for d in OPTIONAL_REGISTERS:
        if d.register_type != "input":
            continue
        row = source[d.address]
        assert row["visible"], d.address
        assert (d.count, d.kind, d.scale, d.unit) == (
            row["count"], row["kind"], row["scale"], row["unit"]
        )
    assert {e.address for e in EXCLUDED_REGISTERS if e.register_type == "holding"} == {49242}
    assert {d.address for d in OPTIONAL_REGISTERS if d.register_type == "holding"} == {49240, 49249}
    assert all(e.reason and isinstance(e.reason, str) for e in EXCLUDED_REGISTERS)
