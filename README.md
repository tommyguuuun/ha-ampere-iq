# Ampere.IQ für Home Assistant

**PV, Batterie und Hausverbrauch an einem Ort.** Ampere.IQ bindet deine Energiedaten wahlweise über die EKD-Cloud oder direkt über Modbus TCP von einem Ampere StoragePro E3 in Home Assistant ein. Die Einrichtung läuft über die Home-Assistant-Oberfläche; YAML ist nicht nötig.

## ✨ Was du bekommst

- Leistung und Energie für PV, Haus, Netz und Batterie mit verständlichen deutschen Namen.
- Eine vorzeichenbehaftete **Batterieleistung**: positiv beim Entladen, negativ beim Laden. Zusätzlich gibt es getrennte Sensoren für Ladung und Entladung.
- Frei wählbare Geräte und Abrufintervalle. Cloud und Modbus können unabhängig voneinander eingerichtet werden.
- Lokal am E3: Anlagenstatus, Batteriediagnose und **156 weitere Messwerte**, die einzeln aktivierbar, aber zunächst deaktiviert sind. Ohne Aktivierung entstehen keine zusätzlichen Registerabfragen.

## 🔌 Cloud oder Modbus?

| | Ampere.IQ Cloud | StoragePro E3 per Modbus TCP |
| --- | --- | --- |
| Verbindung | EKD Customer-API mit API-Schlüssel | Direkt im lokalen Netzwerk zum E3 |
| PV, Haus, Netz und Batterie | Ja | Ja |
| Wärmepumpe, Heizstab und Wallbox | Ja, sofern die Anlage die Werte liefert | Nein |
| Tagesenergie | Werte aus der Cloud | E3-Zähler; bei mehreren PV-Quellen aus der gemessenen Leistung berechnet |
| Kumulative Energie und Anlagenstatus | Nicht über diesen Zugang angeboten | Ja |
| Betriebsmodus des E3 ändern | Nein | Ja, als schreibende Auswahl |
| Zusätzliche E3-Messwerte | Nein | 156, standardmäßig deaktiviert |
| Einstellbares Leistungsintervall | Ab 30 Sekunden | Ab 10 Sekunden |

Die Cloud ist sinnvoll, wenn du auch Wallbox- oder Wärmepumpenwerte der Ampere.IQ-Anlage verwenden möchtest. Modbus liefert lokale E3-Daten ohne Cloud-Abfrage; dafür muss Home Assistant den Wechselrichter im Netzwerk erreichen können. Die tatsächlichen Messwerte hängen von deiner angeschlossenen Ausstattung ab.

## 📦 Installation mit HACS

1. Öffne **HACS → Integrationen**. Falls das Projekt noch nicht hinterlegt ist: Über das Menü **⋮ → Benutzerdefinierte Repositories** `https://github.com/tommyguuuun/ha-ampere-iq` als Typ **Integration** hinzufügen.
2. **Ampere.IQ** in HACS öffnen und herunterladen.
3. **Home Assistant neu starten**, damit der heruntergeladene Python-Code geladen wird.
4. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **Ampere.IQ** suchen. Im Assistenten **Ampere.IQ Cloud** oder **Modbus (lokal)** auswählen.

### ☁️ Ampere.IQ Cloud einrichten

1. Deinen gültigen **EKD-API-Schlüssel** im Home-Assistant-Assistenten eingeben. Der Schlüssel gehört nicht in YAML oder in eine GitHub-Issue.
2. Falls der Schlüssel mehrere Anlagen freigibt, die gewünschte Anlage auswählen.
3. Vorhandene Geräte auswählen: Wechselrichter, Batterie, Wärmepumpe und/oder Wallbox. Das Leistungsintervall beginnt bei 30 Sekunden; der Standardwert ist 60 Sekunden.

Die Cloud stellt unter anderem PV- und Hausleistung, Netzbezug/-einspeisung, Batteriewerte sowie Tagesenergien bereit. Wärmepumpe, Heizstab und Wallbox erscheinen nur, wenn du die jeweilige Gerätegruppe auswählst und die API dafür Werte liefert.

### 🏠 StoragePro E3 per Modbus einrichten

1. Sicherstellen, dass der E3 über **Modbus TCP** erreichbar ist. Du brauchst seine Adresse, den TCP-Port (standardmäßig `502`) und die Modbus-Geräteadresse (standardmäßig `247`).
2. Im Assistenten **Modbus (lokal)** wählen und diese Verbindungsdaten eingeben. Ampere.IQ prüft Modell und Seriennummer des antwortenden E3.
3. Die PV-Konfiguration wählen: Bei **einem E3** verwendet die Integration dessen Gesamtleistung und native Energiezähler. Bei **mehreren Erzeugungsquellen** wählst du nur tatsächlich vorhandene, nicht überlappende Kanäle aus **MPPT 1**, **MPPT 2** und dem externen **Meter 2**. Dann wird der PV-Ertrag aus den ausgewählten Leistungswerten berechnet; Messlücken lassen sich nachträglich nicht auffüllen.
4. Wechselrichter und/oder Batterie sowie das Abrufintervall auswählen. Möglich sind 10 bis 3600 Sekunden, standardmäßig 60 Sekunden.

**Wichtig:** Ein noch registrierter Eintrag der älteren StoragePro-E3-Integration blockiert die lokale Einrichtung auch dann, wenn er deaktiviert ist. Ampere.IQ eröffnet keinen zusätzlichen E3-Zugang parallel dazu; prüfe bestehende Dashboards und Automationen, bevor du eine alte Integration entfernst. Die lokale **Betriebsmodus-Auswahl schreibt** auf den Wechselrichter. Verwende sie nur, wenn du die Wirkung an deiner Anlage kennst.

Die 156 Zusatzwerte findest du nach der Einrichtung unter **Einstellungen → Geräte & Dienste → Entitäten**. Sie bleiben zunächst deaktiviert und werden erst bei Bedarf abgefragt. Die Registerzuordnung ist nicht für jede E3-Firmware durch Herstellerunterlagen bestätigt; für sicherheitskritische Automationen sollten die Werte am Gerät geprüft werden. [Details und bewusst ausgeschlossene Register](docs/optionale-e3-werte.md).

## 💬 Hilfe

Fehler und Verbesserungsvorschläge bitte über die [GitHub-Issues](https://github.com/tommyguuuun/ha-ampere-iq/issues) melden. Gib bei Problemen an, ob du die **Cloud** oder **Modbus** verwendest – aber niemals API-Schlüssel, Zugangsdaten oder öffentliche Screenshots mit privaten Netzwerkadressen.
