"""Detached, optional E3 read-only register catalog (no transport or HA setup).

Technical register facts are transcribed from the public StoragePro E3 source
`sensors.py` and its three read-only holding status sensors (743d813).
The old integration's implementation is not reused. These descriptors do not
perform polling, writes, entity creation, or automatic enabling. The parent
reader must apply the equipment and confirmed connection gates before reads.
A source's cumulative PV values exclude any external Meter 2 PV generator.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptionalRegister:
    key: str
    name: str
    address: int
    count: int
    kind: str
    scale: float
    unit: str | None
    equipment: str
    connection: str | None
    device_class: str | None
    register_type: str = "input"
    enabled_by_default: bool = False
    entity_category: str | None = None
    state_class: str | None = None


@dataclass(frozen=True, slots=True)
class ExcludedRegister:
    address: int
    reason: str
    register_type: str = "input"


_catalog: list[OptionalRegister] = []


def _add(equipment: str, connection: str | None, rows: tuple[tuple, ...]) -> None:
    """Expand independently specified register facts into immutable descriptors."""
    for key, name, address, count, kind, scale, unit, device_class in rows:
        _catalog.append(
            OptionalRegister(
                key,
                name,
                address,
                count,
                kind,
                scale,
                unit,
                equipment,
                connection,
                device_class,
                entity_category="diagnostic" if device_class is None else None,
                state_class=(
                    "total_increasing"
                    if key in ("inverter_generation_total", "load_energy_total")
                    else "total"
                    if key == "inverter_generation_today"
                    else "measurement"
                    if device_class in (
                        "voltage", "current", "power", "frequency", "temperature", "battery"
                    )
                    else None
                ),
            )
        )


# Static identification and diagnostics, independent of optional attachments.
_add(
    "inverter",
    None,
    (
        ("manufacturer_id", "Herstellerkennung", 30032, 16, "str", 1, None, None),
        ("master_version", "Master-Firmwareversion", 36001, 1, "uint16", 1, None, None),
        ("slave_version", "Slave-Firmwareversion", 36002, 1, "uint16", 1, None, None),
        ("manager_version", "Manager-Firmwareversion", 36003, 1, "uint16", 1, None, None),
        ("protocol_version", "Modbus-Protokollversion", 39000, 2, "uint32", 1, None, None),
        ("product_number", "Produktnummer", 39034, 16, "str", 1, None, None),
        ("model_id", "Modellkennung", 39050, 1, "uint16", 1, None, None),
        ("string_count", "Anzahl PV-Strings", 39051, 1, "uint16", 1, None, None),
        ("mppt_count", "Anzahl MPPT-Eingänge", 39052, 1, "uint16", 1, None, None),
        (
            "rated_power",
            "Nennleistung des Wechselrichters",
            39053,
            2,
            "int32",
            0.001,
            "kW",
            "power",
        ),
        ("maximum_active_power", "Maximale Wirkleistung", 39055, 2, "int32", 0.001, "kW", "power"),
        (
            "maximum_apparent_power",
            "Maximale Scheinleistung",
            39057,
            2,
            "int32",
            0.001,
            "kVA",
            None,
        ),
        (
            "maximum_reactive_feed",
            "Maximale Blindleistung bei Einspeisung",
            39059,
            2,
            "uint32",
            0.001,
            "kVar",
            None,
        ),
        (
            "maximum_reactive_draw",
            "Maximale Blindleistung bei Bezug",
            39061,
            2,
            "uint32",
            0.001,
            "kVar",
            None,
        ),
    ),
)

# Meter identification (the source calls the one-word version a string, but its
# encoding cannot be established; those two version rows are excluded below).
for number, base in ((1, 36100), (2, 36200)):
    gate = f"meter{number}"
    _add(
        "inverter",
        gate,
        (
            (f"{gate}_serial", f"Zähler {number} Seriennummer", base, 16, "str", 1, None, None),
            (
                f"{gate}_manufacturer",
                f"Zähler {number} Herstellerkennung",
                base + 16,
                16,
                "str",
                1,
                None,
                None,
            ),
            (f"{gate}_type", f"Zähler {number} Typ", base + 32, 16, "str", 1, None, None),
        ),
    )

# Each BMS bank has its own independently confirmed connection flag.
for number, base, slave_base in ((1, 37003, 37033), (2, 37701, 37731)):
    gate = f"bms{number}"
    serial = 37005 if number == 1 else 37703
    slave_serial = 37097 if number == 1 else 37795
    _add(
        "battery",
        gate,
        (
            (
                f"{gate}_master_version",
                f"Batteriesteuerung {number} Hauptmodul-Firmwareversion",
                base,
                1,
                "uint16",
                1,
                None,
                None,
            ),
            (
                f"{gate}_serial",
                f"Batteriesteuerung {number} Seriennummer",
                serial,
                16,
                "str",
                1,
                None,
                None,
            ),
            (
                f"{gate}_slave1_version",
                f"Batteriesteuerung {number} Untermodul 1 Firmwareversion",
                slave_base,
                1,
                "uint16",
                1,
                None,
                None,
            ),
            (
                f"{gate}_slave2_version",
                f"Batteriesteuerung {number} Untermodul 2 Firmwareversion",
                slave_base + 1,
                1,
                "uint16",
                1,
                None,
                None,
            ),
            (
                f"{gate}_slave1_serial",
                f"Batteriesteuerung {number} Untermodul 1 Seriennummer",
                slave_serial,
                16,
                "str",
                1,
                None,
                None,
            ),
            (
                f"{gate}_slave2_serial",
                f"Batteriesteuerung {number} Untermodul 2 Seriennummer",
                slave_serial + 16,
                16,
                "str",
                1,
                None,
                None,
            ),
        ),
    )

for number, base in ((1, 37609), (2, 38307)):
    gate = f"bms{number}"
    # For BMS1, ambient/SoC/SoH and fault 1 already have primary sensors.
    present = (
        ("voltage", "Spannung", 0, "uint16", 0.1, "V", "voltage"),
        ("current", "Strom", 1, "int16", 0.1, "A", "current"),
        ("ambient", "Umgebungstemperatur", 2, "int16", 0.1, "°C", "temperature"),
        ("soc", "Ladestand", 3, "uint16", 1, "%", "battery"),
        ("max_temperature", "Höchste Zelltemperatur", 8, "int16", 0.1, "°C", "temperature"),
        ("min_temperature", "Niedrigste Zelltemperatur", 9, "int16", 0.1, "°C", "temperature"),
        ("max_cell_voltage", "Höchste Zellspannung", 10, "uint16", 1, "mV", "voltage"),
        ("min_cell_voltage", "Niedrigste Zellspannung", 11, "uint16", 1, "mV", "voltage"),
        ("soh", "Batteriegesundheit", 15, "uint16", 1, "%", None),
        ("remaining_energy", "Verbleibende Batterieenergie", 23, "uint16", 0.1, "Wh", "energy"),
        ("full_charge_capacity", "Batteriekapazität bei Vollladung", 24, "uint16", 0.1, "Ah", None),
        ("design_energy", "Auslegungsenergie der Batterie", 26, "uint16", 0.1, "Wh", "energy"),
    )
    _add(
        "battery",
        gate,
        tuple(
            (
                f"{gate}_{key}",
                f"Batteriesteuerung {number} {label}",
                base + offset,
                1,
                kind,
                scale,
                unit,
                device_class,
            )
            for key, label, offset, kind, scale, unit, device_class in present
            if number == 2 or key not in {"ambient", "soc", "soh"}
        ),
    )

# Meter electrical measurements use identical layouts for meter 1 and 2.
# Combined meter 1 active power is already the grid power source; meter 2
# combined active power is already the separately selectable PV source.
for number, base in ((1, 38802), (2, 38902)):
    gate = f"meter{number}"
    rows = []
    for index, phase in enumerate(("R", "S", "T")):
        rows.append(
            (
                f"{gate}_{phase.lower()}_voltage",
                f"Zähler {number} Spannung Phase {phase}",
                base + index * 2,
                2,
                "int32",
                0.1,
                "V",
                "voltage",
            )
        )
        rows.append(
            (
                f"{gate}_{phase.lower()}_current",
                f"Zähler {number} Strom Phase {phase}",
                base + 6 + index * 2,
                2,
                "int32",
                0.001,
                "A",
                "current",
            )
        )
    for key, label, offset, scale, unit, device_class in (
        ("active", "Wirkleistung", 12, 0.1, "W", "power"),
        ("reactive", "Blindleistung", 20, 0.1, "Var", None),
        ("apparent", "Scheinleistung", 28, 0.1, "VA", None),
        ("power_factor", "Leistungsfaktor", 36, 0.001, None, None),
    ):
        for index, phase in enumerate((None, "R", "S", "T")):
            if key == "active" and phase is None:
                continue
            suffix = "gesamt" if phase is None else f"phase_{phase.lower()}"
            name = (
                f"Zähler {number} {label} gesamt"
                if phase is None
                else f"Zähler {number} {label} Phase {phase}"
            )
            rows.append(
                (
                    f"{gate}_{key}_{suffix}",
                    name,
                    base + offset + index * 2,
                    2,
                    "int32",
                    scale,
                    unit,
                    device_class,
                )
            )
    rows.append(
        (
            f"{gate}_frequency",
            f"Zähler {number} Frequenz",
            base + 44,
            2,
            "int32",
            0.01,
            "Hz",
            "frequency",
        )
    )
    _add("inverter", gate, tuple(rows))

_add(
    "inverter",
    None,
    (
        ("pv1_voltage", "PV-Eingang 1 Spannung", 39070, 1, "int16", 0.1, "V", "voltage"),
        ("pv1_current", "PV-Eingang 1 Strom", 39071, 1, "int16", 0.01, "A", "current"),
        ("pv2_voltage", "PV-Eingang 2 Spannung", 39072, 1, "int16", 0.1, "V", "voltage"),
        ("pv2_current", "PV-Eingang 2 Strom", 39073, 1, "int16", 0.01, "A", "current"),
        ("grid_phase_r_voltage", "Netzspannung Phase R", 39123, 1, "int16", 0.1, "V", "voltage"),
        ("grid_phase_s_voltage", "Netzspannung Phase S", 39124, 1, "int16", 0.1, "V", "voltage"),
        ("grid_phase_t_voltage", "Netzspannung Phase T", 39125, 1, "int16", 0.1, "V", "voltage"),
        (
            "inverter_phase_r_current",
            "Wechselrichterstrom Phase R",
            39126,
            2,
            "int32",
            0.001,
            "A",
            "current",
        ),
        (
            "inverter_phase_s_current",
            "Wechselrichterstrom Phase S",
            39128,
            2,
            "int32",
            0.001,
            "A",
            "current",
        ),
        (
            "inverter_phase_t_current",
            "Wechselrichterstrom Phase T",
            39130,
            2,
            "int32",
            0.001,
            "A",
            "current",
        ),
        (
            "inverter_active_power",
            "Wirkleistung des Wechselrichters",
            39134,
            2,
            "int32",
            0.001,
            "kW",
            "power",
        ),
        (
            "inverter_reactive_power",
            "Blindleistung des Wechselrichters",
            39136,
            2,
            "int32",
            0.001,
            "kVar",
            None,
        ),
        (
            "inverter_power_factor",
            "Leistungsfaktor des Wechselrichters",
            39138,
            1,
            "int16",
            0.001,
            None,
            None,
        ),
        ("grid_frequency", "Netzfrequenz", 39139, 1, "int16", 0.1, "Hz", "frequency"),
        (
            "internal_temperature",
            "Wechselrichter-Innentemperatur",
            39141,
            1,
            "int16",
            1,
            "°C",
            "temperature",
        ),
        (
            "inverter_generation_total",
            "PV-Ertrag am Wechselrichter gesamt",
            39149,
            2,
            "uint32",
            0.01,
            "kWh",
            "energy",
        ),
        (
            "inverter_generation_today",
            "PV-Ertrag am Wechselrichter heute",
            39151,
            2,
            "uint32",
            0.01,
            "kWh",
            "energy",
        ),
    ),
)

# Backup/EPS output: electrical output is measured, not a switch or control.
_add(
    "inverter",
    None,
    tuple(
        (
            f"eps_{phase.lower()}_voltage",
            f"Notstromausgang Spannung Phase {phase}",
            addr,
            1,
            "uint16",
            0.1,
            "V",
            "voltage",
        )
        for phase, addr in (("R", 39201), ("S", 39202), ("T", 39203))
    ),
)
for key, label, base, unit, scale, kind, device_class in (
    ("eps_current", "Notstromausgang Strom", 39204, "A", 0.001, "int32", "current"),
    ("eps_power", "Notstromausgang Leistung", 39210, "W", 1, "int32", "power"),
    ("load_power", "Verbrauchsleistung", 39219, "W", 1, "int32", "power"),
    ("inverter_active", "Wechselrichter-Wirkleistung", 39248, "W", 1, "int32", "power"),
    ("inverter_reactive", "Wechselrichter-Blindleistung", 39256, "Var", 1, "int32", None),
    ("inverter_apparent", "Wechselrichter-Scheinleistung", 39264, "VA", 1, "int32", None),
):
    _add(
        "inverter",
        None,
        tuple(
            (
                f"{key}_{phase.lower()}",
                f"{label} Phase {phase}",
                base + 2 * index,
                2,
                kind,
                scale,
                unit,
                device_class,
            )
            for index, phase in enumerate(("R", "S", "T"))
        ),
    )
_add(
    "inverter",
    None,
    (
        (
            "eps_combined_power",
            "Notstromausgang Leistung gesamt",
            39216,
            2,
            "int32",
            1,
            "W",
            "power",
        ),
        ("eps_frequency", "Notstromausgang Frequenz", 39218, 1, "int16", 0.1, "Hz", "frequency"),
        (
            "inverter_apparent_combined",
            "Wechselrichter-Scheinleistung gesamt",
            39270,
            2,
            "int32",
            1,
            "VA",
            None,
        ),
        (
            "inverter_frequency_r",
            "Wechselrichterfrequenz Phase R",
            39272,
            1,
            "int16",
            0.1,
            "Hz",
            "frequency",
        ),
        (
            "inverter_frequency_s",
            "Wechselrichterfrequenz Phase S",
            39273,
            1,
            "int16",
            0.1,
            "Hz",
            "frequency",
        ),
        (
            "inverter_frequency_t",
            "Wechselrichterfrequenz Phase T",
            39274,
            1,
            "int16",
            0.1,
            "Hz",
            "frequency",
        ),
    ),
)

for number, base in ((1, 39227), (2, 39232)):
    _add(
        "battery",
        f"bms{number}",
        (
            (
                f"battery{number}_voltage",
                f"Batterie {number} Spannung",
                base,
                1,
                "uint16",
                0.1,
                "V",
                "voltage",
            ),
            (
                f"battery{number}_current",
                f"Batterie {number} Strom",
                base + 1,
                2,
                "int32",
                0.001,
                "A",
                "current",
            ),
            (
                f"battery{number}_power",
                f"Batterie {number} Leistung",
                base + 3,
                2,
                "int32",
                1,
                "W",
                "power",
            ),
        ),
    )

for number, base in ((1, 39279), (2, 39281)):
    _add(
        "inverter",
        None,
        (
            (
                f"pv{number}_power",
                f"PV-Eingang {number} Leistung",
                base,
                2,
                "int32",
                1,
                "W",
                "power",
            ),
        ),
    )
for number, base in ((1, 39327), (2, 39331)):
    _add(
        "inverter",
        None,
        (
            (
                f"mppt{number}_voltage",
                f"MPPT {number} Spannung",
                base,
                1,
                "int16",
                0.1,
                "V",
                "voltage",
            ),
            (
                f"mppt{number}_current",
                f"MPPT {number} Strom",
                base + 1,
                1,
                "int16",
                0.01,
                "A",
                "current",
            ),
        ),
    )

# Native E3 counters (not whole-site PV when Meter 2 is an external source).
_add(
    "inverter",
    None,
    (("load_energy_total", "Verbrauchsenergie gesamt", 39629, 2, "uint32", 0.01, "kWh", "energy"),),
)

# These holding registers are *read* with FC03, not FC04; no command/register
# used for changing the operating mode appears in this catalog.
_catalog.extend(
    (
        OptionalRegister(
            "network_status",
            "Netzwerkverbindungsstatus",
            49240,
            1,
            "uint16",
            1,
            None,
            "inverter",
            None,
            None,
            "holding",
            entity_category="diagnostic",
        ),
        OptionalRegister(
            "gfci_current",
            "Fehlerstrom am Schutzschalter",
            49249,
            1,
            "int16",
            0.01,
            "A",
            "inverter",
            None,
            "current",
            "holding",
            state_class="measurement",
        ),
    )
)

OPTIONAL_REGISTERS: tuple[OptionalRegister, ...] = tuple(_catalog)


def _excluded(reason: str, *addresses: int) -> tuple[ExcludedRegister, ...]:
    return tuple(ExcludedRegister(address, reason) for address in addresses)


EXCLUDED_REGISTERS: tuple[ExcludedRegister, ...] = (
    *_excluded(
        "Bereits als primärer Messwert oder Identität vorhanden.",
        30000,
        30016,
        37002,
        37611,
        37612,
        37624,
        37626,
        37700,
        38801,
        38814,
        38901,
        38914,
        39063,
        39065,
        39067,
        39068,
        39069,
        39118,
        39225,
        39237,
        39329,
        39333,
        39601,
        39603,
        39605,
        39607,
        39609,
        39611,
        39613,
        39615,
        39617,
        39619,
        39631,
    ),
    *_excluded(
        "Reserviert oder vom Altprojekt explizit als unsichtbar markiert.",
        37634,
        38332,
        39064,
        39120,
        39121,
        39122,
        39132,
        39140,
        39142,
        39143,
        39144,
        39145,
        39147,
        39153,
        39154,
        39156,
        39157,
        39158,
        39160,
        39161,
        39164,
        39166,
        39170,
        39171,
        39172,
        39200,
        39239,
        39240,
        39241,
        39242,
        39244,
        39246,
        39254,
        39262,
        39600,
    ),
    *_excluded("Unbekannte Bedeutung des Feldes.", 37004, 37032, 37702, 37730),
    *_excluded("Ein-Wort-Zeichenkette ohne belegte Kodierung.", 36148, 36248),
    *_excluded(
        "Rohes BMS-Fehlerbitfeld ohne verifizierte Einzelbit-Bedeutung.",
        37627,
        37628,
        37629,
        37630,
        37631,
        38324,
        38325,
        38326,
        38327,
        38328,
        38329,
    ),
    *_excluded("Batteriewechsel-Flag hat steuerungsnahe, unklare Bedeutung.", 37636, 38334),
    *_excluded(
        "Modellabhängiger zusätzlicher PV-Eingang ohne bestätigte Bestückung.",
        39074,
        39075,
        39076,
        39077,
        39283,
        39285,
        39335,
        39336,
        39337,
    ),
    *_excluded("Zweites Identitätsfeld; Verhältnis zum primären Modell/SN unklar.", 39002, 39018),
    *_excluded("Leistungswert aggregiert unbekannte Zähler oder Speichermodule.", 39162, 39168),
    *_excluded(
        "Verfügbare Import-/Exportleistung ohne geklärte Netz-/Regelungs-Semantik.", 39275, 39277
    ),
    *_excluded("Unklare Richtung und Bilanzgrenze des Energiezählers.", 39621, 39623, 39625, 39627),
    ExcludedRegister(49242, "Trigger-K1–K4-Bitfeld mit steuerungsnaher Bedeutung.", "holding"),
)
