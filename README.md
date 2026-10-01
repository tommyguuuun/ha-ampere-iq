# Ampere.IQ für Home Assistant

Integration für die Ampere.IQ-Cloud und einen lokal über Modbus TCP erreichbaren Ampere StoragePro E3. **Der Quellstand wird offline getestet; ein HACS-Download oder Metadaten-Refresh aktiviert geänderten Python-Code nicht im laufenden Home Assistant.** Ein geplanter Core-Neustart und gegebenenfalls eine Neueinrichtung bleiben Sache des Betreibers.

## Einrichtung und Messwerte

- Im Assistenten **Ampere.IQ Cloud** oder **Modbus (lokal)** auswählen. Cloud-Einträge behalten ihre bisherigen Messwerte und Geräteoptionen.
- Bei Modbus die Adresse, den Port und die Geräteadresse angeben. Der Assistent prüft Modell und Seriennummer und fragt anschließend nach der PV-Topologie.
- **Ein E3-Wechselrichter:** Die PV-Gesamtleistung und die nativen PV-Tages- und Gesamtzähler dieses E3 werden verwendet. Das ist nur dann eine Standortsumme, wenn keine weitere PV-Erzeugung außerhalb des E3 existiert.
- **Mehrere Erzeugungsquellen:** Nur vorhandene, nicht überlappende Kanäle aus MPPT 1, MPPT 2 und externem Meter 2 auswählen. Der E3-Gesamtwert wird nicht zusätzlich addiert. PV-Tages- und Gesamtenergie werden aus der gemessenen PV-Leistung berechnet und in Home Assistant gespeichert; Ausfälle zwischen Abtastungen können nicht nachträglich rekonstruiert werden.
- Cloud und Modbus liefern zusätzlich zu den getrennten nichtnegativen Sensoren **Batterieladung** und **Batterieentladung** einen eigenen Sensor **Batterieleistung** (`sensor.ampere_iq_batterieleistung`, W): Entladung ist positiv, Ladung negativ, Stillstand null. Fehlende oder unlesbare Werte bleiben unbekannt statt als null ausgegeben zu werden. Der Sensor gehört zur Batterieausstattung und eignet sich als ein einzelner vorzeichenbehafteter Leistungswert etwa für evcc; er ist **kein** vom HA-Energiedashboard erzeugter Hilfssensor. Wie bei anderen neu hinzugefügten Sensoren kann HA bei einer bereits belegten ID eine abweichende Entitäts-ID vergeben.
- Lokal gibt es außerdem PV-, Haus- und Netzleistung sowie SoC, Tagesenergie, native kumulative Netz- und Batterieenergie, Anlagenstatus, Störungsgrund, Batterie-SoH und -Temperatur. Der SoC bleibt als Prozent-Sensor erhalten, ohne als Batterie-Badge auf der Geräteansicht markiert zu werden.
- Eine lokale **Betriebsmodus-Auswahl** kann genau vier bekannte E3-Modi schreiben und liest den Wert zurück. Das Umschalten ist **nur gegen einen simulierten Modbus-Client geprüft**; Firmware, aktive Zeitpläne und konkurrierende Ampere.IQ-Steuerung können die tatsächliche Wirkung verändern. Kein unbeaufsichtigter Live-Schreibtest wurde durchgeführt. Andere schreibende Schalter gibt es nicht.
- **156 weitere E3-Werte** (Netzphasen, PV-Eingänge, Zähler, Batteriesteuerung, Notstrom und Diagnose) sind einzeln in der HA-Entitätenverwaltung aktivierbar, aber standardmäßig deaktiviert. Ohne Aktivierung erfolgen keine zusätzlichen Registerabfragen. Alle neuen Werte sind ausschließlich lesend, benutzen die vorhandene Modbus-Verbindung und sind in Deutsch benannt. Nicht verifizierte Steuerregister und unklare Fehlerbits sind bewusst ausgeschlossen. Einzelheiten und Grenzen: [Optionale E3-Werte](docs/optionale-e3-werte.md).

## Betrieb und Sicherheit

Der E3 erlaubt nur wenige gleichzeitige Modbus-Verbindungen. Ein registrierter Eintrag der alten StoragePro-E3-Integration blockiert die neue lokale Verbindung auch dann, wenn er deaktiviert ist; ein zweiter lokaler Ampere.IQ-Eintrag wird ebenfalls abgewehrt. Der Assistent öffnet zur Identitätsprüfung nur vorübergehend eine Verbindung, und die laufende Integration verwendet einen gemeinsamen Socket mit serialisierten Anfragen. Der Nutzer muss seinen bisherigen Direktzugang und andere Steuerungen berücksichtigen. **Keine bestehenden Integrationen oder Energy-/Dashboard-Verweise automatisch entfernen oder umstellen.**

Die Modbus-Leistungswerte können im Abstand von 10 bis 3600 Sekunden abgefragt werden; Diagnosen und Zähler laufen langsamer. Dass 10 Sekunden an einer Anlage funktionieren, ist keine generelle Stabilitätszusage. Ein HACS-Download aktiviert geänderten Python-Code erst nach einem späteren kontrollierten Home-Assistant-Core-Neustart. Bei einem kritischen HA müssen Backup, Ausfallzeit und Rückfall vorab geklärt werden.

Das Logo liegt für HA unter `custom_components/ekd_ampere_iq/brand/icon.png`. HACS kann in seiner Repository-Liste trotzdem ein generisches CDN-Platzhalterbild anzeigen, selbst wenn HA das lokale Logo korrekt ausliefert; ein zweites Bildverzeichnis im Repository behebt diesen Frontend-Bildpfad nicht.

## Verifikation des Entwicklungsstands

Der bisherige Stand 0.4.2 wurde auf HA Core 2026.9.4 und 2026.2.3 offline geprüft. Die neuen optionalen Register der Version 0.5.0 sind auf dem aktuellen lokalen Teststand offline geprüft; Kompatibilität mit der älteren Core-Version und die Werte auf dem konkreten E3 sind noch nicht bestätigt. Offline-Prüfungen ersetzen weder eine Sichtprüfung der echten HA-Geräteansicht noch einen beaufsichtigten Schreibtest am konkreten E3. Die ältere HA-Version verwendet möglicherweise eine andere PyModbus-Version in ihrer eigenen Modbus-Integration; gemeinsamer Produktivbetrieb ist dadurch nicht freigegeben.
