"""Map a small set of E3 fault bits to actionable HA status text."""

ALARM_BITS = {
    "alarm1": {
        0: "PV-Eingang: Überspannung",
        1: "DC-Lichtbogen",
        2: "PV-String falsch angeschlossen",
        7: "Netzausfall",
        8: "Netzspannung außerhalb des Bereichs",
        11: "Netzfrequenz außerhalb des Bereichs",
        14: "Ausgangsüberstrom",
    },
    "alarm2": {
        0: "Fehlerstrom",
        1: "Erdungsfehler",
        2: "Isolationsfehler",
        3: "Übertemperatur",
        9: "Batteriespeicher-Störung",
        10: "Inselnetzbetrieb gemeldet",
        14: "Notstrom-Ausgang überlastet",
    },
    "alarm3": {
        3: "Lüfterstörung",
        4: "Batterieanschluss prüfen",
        9: "Zählerverbindung verloren",
        10: "BMS-Verbindung verloren",
    },
    "batteryFault": {
        0: "Batteriezelle: Überspannung",
        1: "Batteriezelle: Unterspannung",
        4: "Batterie-Ladeüberstrom",
        5: "Batterie-Entladeüberstrom",
        6: "Batterie zu warm",
        7: "Batterie zu kalt",
        8: "BMS-Kommunikation verloren",
    },
}


def summarize_device(data: dict) -> dict[str, str]:
    """Keep unknown alarm bits visible instead of silently calling them OK."""
    if not isinstance(data, dict):
        return {"status": "Unbekannt", "reason": "Status nicht verfügbar"}
    reasons = []
    if data.get("batteryConnected") is False:
        reasons.append("BMS nicht verbunden")
    for register, labels in ALARM_BITS.items():
        value = data.get(register, 0)
        if type(value) is not int or not 0 <= value <= 65535:
            return {"status": "Unbekannt", "reason": "Status nicht verfügbar"}
        for bit in range(16):
            if value & (1 << bit):
                reasons.append(labels.get(bit, f"Unbekannter Alarm ({register}, Bit {bit})"))
    flags = data.get("inverterStatus")
    if type(flags) is not int or not 0 <= flags <= 65535:
        return {"status": "Unbekannt", "reason": "Status nicht verfügbar"}
    if flags & (1 << 6) or reasons:
        status = "Störung"
    elif data.get("batteryFaultKnown") is False:
        status = "Unbekannt"
    elif data.get("offGrid") is True:
        status = "Notstrom"
    elif flags & (1 << 2):
        status = "Betrieb"
    elif flags & 1:
        status = "Bereitschaft"
    else:
        status = "Unbekannt"
    return {
        "status": status,
        "reason": (
            "; ".join(reasons) if reasons else
            "BMS-Störungsstatus nicht verfügbar"
            if data.get("batteryFaultKnown") is False else
            "Gerätestörung" if status == "Störung" else "Keine Störung"
        ),
    }
