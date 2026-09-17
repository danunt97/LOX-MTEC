# LOX-MTEC

**M-TEC Energybutler → Loxone Miniserver. Ein Docker-Container, Weboberfläche, Watchdog.**

[![Tests](https://github.com/danunt97/LOX-MTEC/actions/workflows/tests.yml/badge.svg)](https://github.com/danunt97/LOX-MTEC/actions/workflows/tests.yml)
[![Docker image](https://github.com/danunt97/LOX-MTEC/actions/workflows/docker.yml/badge.svg)](https://github.com/danunt97/LOX-MTEC/actions/workflows/docker.yml)
[![Image](https://img.shields.io/badge/ghcr.io-danunt97%2Flox--mtec-blue?logo=docker&logoColor=white)](https://github.com/danunt97/LOX-MTEC/pkgs/container/lox-mtec)

LOX-MTEC liest die Werte eines M-TEC Energybutler (Wattsonic / Sunways / Daxtromn) per
Modbus TCP aus und schreibt sie direkt in einen Loxone Miniserver. Der Umweg über
ioBroker + Simple-API + MQTT-Broker entfällt – es läuft alles in einem Container.

**Ab Werk auf den Loxone-Energiemonitor eingestellt:** die Werte kommen bereits in kW und
mit den Vorzeichen des Bausteins aus dem Container. Vorlage herunterladen, in Loxone Config
einfügen, verdrahten – fertig. Keine Korrektur-Felder, keine Multiplizierer.

> Dieses Projekt ist ein Fork von [croedel/MTECmqtt](https://github.com/croedel/MTECmqtt).
> Der ursprüngliche MQTT-/Home-Assistant-Teil bleibt erhalten (siehe [unten](#original-project-mtecmqtt)),
> LOX-MTEC ergänzt die Loxone-Anbindung, die Weboberfläche und das Docker-Setup.

## Inhalt

- [Was der Container macht](#was-der-container-macht)
- [Installation](#installation)
- [Loxone einrichten](#loxone-einrichten) — inkl. [Energiemonitor-Baustein](#der-schnelle-weg-energiemonitor-baustein)
- [Weboberfläche](#weboberfläche)
- [Konfiguration](#konfiguration)
- [REST-Schnittstelle](#rest-schnittstelle)
- [Watchdog und Health-Check](#watchdog-und-health-check)
- [Entwicklung](#entwicklung)
- [Changelog](#changelog)

---

## Was der Container macht

```
 ┌──────────────────┐  Modbus TCP   ┌───────────────────────────────┐
 │ M-TEC Wechsel-   │ ────────────► │           LOX-MTEC            │
 │ richter          │   Port 5743   │  ┌─────────────────────────┐  │
 │ (espressif-Stick)│   oder 502    │  │ Poller (alle 10 s)      │  │
 └──────────────────┘               │  │ Watchdog                │  │
                                    │  │ Web-GUI + REST-API      │  │
                                    │  └─────────────────────────┘  │
                                    └───────┬──────────┬────────────┘
                                 Push (UDP  │          │  Pull (REST)
                                 oder HTTP) │          │
                                            ▼          ▼
                                    ┌───────────────────────────────┐
                                    │      Loxone Miniserver        │
                                    │  virtuelle (UDP-)Eingänge     │
                                    └───────────────────────────────┘
```

* **85 Werte** vom Wechselrichter: PV-Leistung, Netzbezug/-einspeisung, Batterie (SOC, Strom,
  Temperatur, Zellspannungen), Backup-Phasen, Tages- und Gesamtstatistik sowie berechnete
  Werte wie Hausverbrauch, Autarkiegrad und Eigenverbrauchsquote.
* **Fertig für den Energiemonitor-Baustein**: Leistungen in kW, Vorzeichen passend,
  Vorlage mit sprechenden Kommentaren je Baustein-Eingang – alles ohne eine einzige
  Einstellung.
* **Drei Wege nach Loxone**, frei wählbar – UDP-Push, HTTP-Push oder REST-Pull.
* **Weboberfläche** zum Einstellen und Kontrollieren: Live-Werte, Zuordnung der Loxone-Namen,
  Verbindungseinstellungen, Log.
* **Vorlagen-Export** für Loxone Config: virtuelle Eingänge inklusive Befehlserkennung
  und Einheit als XML – einmal importieren statt von Hand anlegen.
* **Faktor und Einheit je Wert**: `0.001` macht aus W kW, ein negativer Faktor dreht das
  Vorzeichen – damit lassen sich auch Zählerbausteine und der Energieflussmonitor bedienen.
* **Watchdog**: erkennt abgerissene Modbus-Verbindungen, verbindet neu und startet den
  Container im Zweifel neu.
* **MQTT bleibt optional** verfügbar, falls parallel noch evcc oder Home Assistant beliefert
  werden soll.

Die Modbus-Register werden geclustert gelesen: benachbarte Register landen in einer einzigen
Anfrage, und die selten benötigten Gruppen (Tages-/Gesamtstatistik, Konfiguration) laufen auf
eigenen, langsameren Intervallen. Das hält die Last auf dem Wechselrichter niedrig.

---

## Installation

Es gibt ein fertiges Image für `amd64` und `arm64` — nichts bauen, nichts klonen:

```
ghcr.io/danunt97/lox-mtec:latest
```

### Synology Container Manager (empfohlen, ohne Kommandozeile)

1. **Container Manager** öffnen → links **Projekt** → **Erstellen**
2. Projektname `loxmtec`, als Pfad einen neuen Ordner wählen (z. B. `docker/loxmtec`)
3. Quelle: **Docker-compose.yml erstellen** und diesen Inhalt einfügen:

   ```yaml
   services:
     loxmtec:
       image: ghcr.io/danunt97/lox-mtec:latest
       container_name: loxmtec
       restart: unless-stopped
       ports:
         - "8080:8080"
       volumes:
         - loxmtec-config:/config
       environment:
         TZ: Europe/Berlin

   volumes:
     loxmtec-config:
   ```

4. **Weiter** → **Fertig**. Das Image wird automatisch geladen und gestartet.
5. `http://<NAS-IP>:8080` im Browser öffnen.

Kein `host`-Netzwerk nötig, keine Rechte-Anpassung, kein Login bei der Registry.

### Kommandozeile

```bash
docker run -d --name loxmtec --restart unless-stopped \
  -p 8080:8080 \
  -v loxmtec-config:/config \
  -e TZ=Europe/Berlin \
  ghcr.io/danunt97/lox-mtec:latest
```

Oder mit der `docker-compose.yml` aus diesem Repo:

```bash
curl -O https://raw.githubusercontent.com/danunt97/LOX-MTEC/main/docker-compose.yml
docker compose up -d
```

### Selbst bauen

Nur nötig, wenn du am Code etwas änderst:

```bash
git clone https://github.com/danunt97/LOX-MTEC.git && cd LOX-MTEC
docker compose -f docker-compose.build.yml up -d --build
```

### Konfiguration im Volume

Die Einstellungen liegen im benannten Volume `loxmtec-config` unter `/config/config.yaml`.
Docker legt es mit den richtigen Rechten an — deshalb der Vorzug gegenüber einem
Ordner auf der NAS. Wer die Datei trotzdem direkt im Dateisystem haben will,
ersetzt in der Compose-Datei die Volume-Zeile durch `- ./config:/config` und führt
einmalig `sudo chown -R 1000:1000 ./config` aus (der Container läuft als Benutzer 1000).

### Updates

```bash
docker compose pull && docker compose up -d
```

Im Container Manager: Projekt → **Aktion** → **Erstellen** (lädt das Image neu).
Die Konfiguration bleibt im Volume erhalten.

`:latest` bewegt sich mit jeder Änderung mit. Wer lieber auf einem festen Stand bleibt,
trägt in der Compose-Datei eine Version ein, z. B. `ghcr.io/danunt97/lox-mtec:1.1`.

### Erster Start

Beim ersten Start legt der Container eine `config.yaml` an, füllt sie mit allen 85 Werten
und stellt die sieben Werte des Energiemonitor-Bausteins passend ein. Danach in der
Weboberfläche unter **Einstellungen**:

1. **IP des Wechselrichters** eintragen (meist `espressif`; sonst die IP des WLAN-Sticks).
2. **Übertragungsart** und **Miniserver-IP** eintragen.
3. Optional ein **Präfix** wie `mtec_` setzen, damit die Eingänge im Miniserver zusammenstehen.

Sobald auf dem Dashboard *Modbus: ok* steht und Werte erscheinen, geht es auf der Seite
**Loxone** weiter. Auf der Seite **Werte** lässt sich abwählen, was nicht gebraucht wird —
weniger Werte heißt weniger virtuelle Eingänge im Miniserver.


## Loxone einrichten

### Der schnelle Weg: Energiemonitor-Baustein

**Ab Werk eingestellt — du musst dafür nichts konfigurieren.** Der Loxone-Energiemonitor
erwartet Leistungen in **kW** und hat eigene Vorzeichen-Konventionen. Der Container liefert
genau das: in Loxone Config brauchst du weder Korrektur-Felder noch Multiplizierer-Bausteine.

**1 · Virtuellen Eingang anlegen**
In Loxone Config einen **Virtuellen UDP-Eingang** mit Port `7000` anlegen. (Beim REST-Pull
entfällt das — den virtuellen HTTP-Eingang legt die Vorlage selbst an.)

**2 · Vorlage laden und einfügen**
Auf der Seite **Loxone** in der Weboberfläche auf *UDP-Vorlage laden* klicken. Die Datei
enthält genau die sieben Werte des Bausteins, jeder beschriftet mit seinem Baustein-Eingang:

```
mtec_pv                    <v.3> kW    Ppwr - Produktionsleistung
mtec_grid_power            <v.3> kW    Gpwr - Netzleistung
mtec_battery               <v.3> kW    Spwr - Speicherleistung
mtec_battery_soc           <v.1> %     SoC  - Ladezustand
mtec_pv_total              <v.1> kWh   Ptot - Produktion gesamt
mtec_grid_purchase_total   <v.1> kWh   Gi   - Netz Energie Import
mtec_grid_feed_total       <v.1> kWh   Ge   - Netz Energie Export
```

Einspielen: XML nach `Dokumente\Loxone\Loxone Config\Templates\VirtualIn\` kopieren,
Loxone Config neu starten, dann beim virtuellen Eingang **Vorlage einfügen** wählen.

**3 · Am Baustein einstellen**
Datenquelle `Objekteingänge`, Parameter **`Abs = 1`** und die Speicherkapazität des Akkus
in kWh. Das `Abs = 1` ist wichtig: der Container liefert Zählerstände, keine Zuwächse —
bei `0` würde der Baustein jeden Wert aufaddieren.

**Vorzeichen-Kontrolle:** nachts ohne PV muss `Gpwr` positiv sein (Bezug aus dem Netz).
Der Wechselrichter zählt Einspeisung positiv, der Energiemonitor den Bezug — deshalb steht
bei `grid_power` der Faktor `-0.001`, der in einem Schritt auf kW umrechnet und das
Vorzeichen dreht. Zählt deine Anlage andersherum, drehst du das Vorzeichen auf der Seite
**Werte** um.

### Mehr als die sieben Werte

Der Wechselrichter liefert 85 Werte: Batteriezellen, Backup-Phasen, Temperaturen, Tages-
und Gesamtstatistik. Was davon an Loxone geht, wählst du auf der Seite **Werte**. Die
Vorlagen unter *„alle aktivierten Werte"* enthalten dann alles Ausgewählte.

Jeder Wert hat dort einen **Faktor** und eine **Einheit**: `0.001` macht aus W kW, ein
negativer Faktor dreht zusätzlich das Vorzeichen. Damit lassen sich auch die
Zählerbausteine oder der Energieflussmonitor bedienen. Über *Vorlage anwenden* kommst du
jederzeit zurück auf die Energiemonitor-Einstellung oder auf Rohwerte.

### Die drei Übertragungswege

| | Wie | Wofür |
|---|---|---|
| **UDP-Push** (Standard) | ein Datagramm `name: wert` je Wert | kein Login, minimale Last, sofort aktuell |
| **HTTP-Push** | `/dev/sps/io/<name>/<wert>` am Miniserver | wenn UDP im Netz nicht erwünscht ist |
| **REST-Pull** | Loxone holt `/api/v1/values` selbst ab | wie Simple-API in ioBroker; läuft immer mit |

Der Push-Weg wird in den **Einstellungen** gewählt, die REST-Schnittstelle ist immer aktiv.

> **Textwerte** (Seriennummer, Datum, Firmware-Version) lassen sich nicht als analoger
> virtueller Eingang abbilden und sind deshalb nicht in den Vorlagen enthalten. Über die
> REST-Schnittstelle stehen sie trotzdem zur Verfügung.

> **Präfix nicht nachträglich ändern**, ohne die Vorlage neu zu laden und einzufügen —
> sonst passt die Befehlserkennung im Miniserver nicht mehr zu dem, was der Container sendet.


## Weboberfläche

| Seite | Inhalt |
|-------|--------|
| **Dashboard** | Live-Werte, Status von Modbus/Loxone/Watchdog, Buttons für „jetzt abfragen“, „alles senden“, „neu verbinden“ |
| **Werte** | Alle 85 Werte: senden ja/nein, Loxone-Name, Nachkommastellen, Totband; warnt bei doppelten Namen |
| **Loxone** | Anleitung für alle drei Varianten, Download der Loxone-Config-Vorlagen, Testwert senden |
| **Einstellungen** | Modbus, Loxone, Intervalle, Watchdog, Web-Login, MQTT, Logging |
| **Log** | Die letzten Meldungen aus dem Container, filterbar nach Level und Text |

Änderungen an den Einstellungen werden in die `config.yaml` geschrieben und sofort übernommen –
ein Neustart ist nur nötig, wenn Bind-Adresse oder Port der Weboberfläche geändert werden.

### Nur bei Änderung senden

Standardmäßig geht ein Wert erst wieder raus, wenn er sich geändert hat. Das hält die Last auf
dem Miniserver niedrig. Zwei Stellschrauben:

* **Totband** (je Wert): erst senden, wenn sich der Wert um mindestens X geändert hat – praktisch
  bei zappelnden Leistungswerten.
* **Heartbeat** (global): unveränderte Werte trotzdem alle N Sekunden erneut senden, damit der
  Miniserver nach einem Neustart nicht auf alten Werten sitzenbleibt.

---

## Konfiguration

Alle Einstellungen liegen in `/config/config.yaml` und lassen sich komplett über die
Weboberfläche pflegen. Für die Erstinbetriebnahme (oder eine reine Umgebungsvariablen-Konfiguration)
gibt es Environment-Overrides:

| Variable | Bedeutung | Default |
|----------|-----------|---------|
| `LOXMTEC_CONFIG` | Pfad zur config.yaml | `/config/config.yaml` |
| `LOXMTEC_MODBUS_HOST` | IP/Hostname des Wechselrichters | `espressif` |
| `LOXMTEC_MODBUS_PORT` | Modbus-Port (Firmware < V27.52.4.0) | `5743` |
| `LOXMTEC_MODBUS_PORT_FALLBACK` | Ausweich-Port (Firmware > V27.52.4.0) | `502` |
| `LOXMTEC_MODBUS_SLAVE` | Modbus-Slave-ID | `252` |
| `LOXMTEC_POLL_NOW` | Abfrage-Intervall der aktuellen Werte (s) | `10` |
| `LOXMTEC_LOXONE_MODE` | `udp`, `http` oder `off` | `udp` |
| `LOXMTEC_LOXONE_HOST` | IP/Hostname des Miniservers | – |
| `LOXMTEC_LOXONE_UDP_PORT` | Port des virtuellen UDP-Eingangs | `7000` |
| `LOXMTEC_LOXONE_HTTP_PORT` | Port des Miniservers (HTTP-Push) | `80` |
| `LOXMTEC_LOXONE_USER` / `..._PASSWORD` | Zugangsdaten für HTTP-Push | `admin` / – |
| `LOXMTEC_LOXONE_PREFIX` | Präfix für alle Loxone-Namen | – |
| `LOXMTEC_WEB_PORT` | Port der Weboberfläche | `8080` |
| `LOXMTEC_WEB_USER` / `..._PASSWORD` | Login für Weboberfläche und REST-API | – |
| `LOXMTEC_MQTT_ENABLED` | zusätzliche MQTT-Ausgabe | `false` |
| `LOXMTEC_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |

Umgebungsvariablen haben Vorrang vor der `config.yaml`. Wer eine Einstellung dauerhaft über die
Weboberfläche pflegen will, sollte die passende Variable also wieder aus der Compose-Datei nehmen.

### Passwörter

Die Passwörter für Miniserver und Weboberfläche stehen im Klartext in der `config.yaml` – so wie
bei den meisten Bridges dieser Art. Der Ordner sollte deshalb nicht öffentlich freigegeben werden.
In der Weboberfläche und über die REST-Schnittstelle werden sie nie ausgeliefert, sondern durch
`********` ersetzt.

---

## REST-Schnittstelle

| Endpunkt | Antwort |
|----------|---------|
| `GET /api/v1/values` | alle Werte als flaches JSON – das, was Loxone im Pull-Betrieb abfragt |
| `GET /api/v1/values/full` | Werte inklusive Name, Einheit, Gruppe, Alter, letztem Versand |
| `GET /api/v1/value/<name>` | ein einzelner Wert als reiner Text |
| `GET /api/v1/status` | Status von Modbus, Loxone, Watchdog und MQTT |
| `GET /api/v1/config` | aktuelle Konfiguration (ohne Passwörter) |
| `POST /api/v1/config` | Einstellungen ändern |
| `GET`/`POST` `/api/v1/mapping` | Zuordnung der Werte lesen/schreiben |
| `POST /api/v1/actions/{poll,resend,reconnect,test}` | Aktionen auslösen |
| `GET /api/v1/loxone-template/{udp,http}` | Loxone-Config-Vorlage als XML |
| `GET /healthz` | Health-Check: `200` wenn aktuelle Daten vorliegen, sonst `503` |

Ist ein Web-Login gesetzt, gilt er für alle Endpunkte außer `/healthz`. Loxone fragt dann
`http://benutzer:passwort@host:8080/api/v1/values` ab.

---

## Watchdog und Health-Check

Der Watchdog prüft alle 10 Sekunden, ob noch Daten kommen:

1. Läuft alles → nichts passiert.
2. Seit `stale_after` Sekunden (Standard 120) keine erfolgreiche Abfrage → Modbus-Reconnect.
3. Seit `exit_after` Sekunden (Standard 900) immer noch nichts, oder der Poller-Thread ist
   gestorben → der Prozess beendet sich mit Exit-Code 1 und Docker startet den Container neu
   (`restart: unless-stopped`).

Zusätzlich prüft der Docker-`HEALTHCHECK` alle 60 Sekunden `/healthz`. Direkt nach dem Start gibt
es eine Schonfrist, damit ein langsam anlaufender Wechselrichter den Container nicht sofort auf
„unhealthy“ setzt.

---

## Entwicklung

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest                       # 67 Tests, inkl. simuliertem Wechselrichter
loxmtec -c ./config.yaml     # lokal starten
```

Die Tests brauchen keine Hardware: `tests/fake_inverter.py` ist ein minimaler
Modbus-RTU-over-TCP-Server, gegen den der komplette Weg vom Register bis zum UDP-Paket
durchgespielt wird.

Aufbau des Pakets:

| Modul | Aufgabe |
|-------|---------|
| `loxmtec/modbus.py` | Modbus-Client, Register-Clustering, Dekodierung |
| `loxmtec/calc.py` | berechnete Werte (Hausverbrauch, Autarkie, Eigenverbrauch) |
| `loxmtec/datastore.py` | aktuelle Werte und Statuszähler |
| `loxmtec/mapping.py` | Zuordnung Register → Loxone-Name |
| `loxmtec/loxone/` | UDP-/HTTP-Transport, Änderungserkennung, Vorlagen-Export |
| `loxmtec/poller.py` | der Takt: lesen, rechnen, speichern, senden |
| `loxmtec/watchdog.py` | Überwachung und Neustart |
| `loxmtec/web/` | Weboberfläche und REST-API |

---

## Changelog

### 1.1 — Energiemonitor ab Werk

* Die sieben Werte des Loxone-Energiemonitors sind bei einer Neuinstallation bereits
  passend eingestellt: Leistungen in kW, `grid_power` mit gedrehtem Vorzeichen.
* Die Vorlagen-Downloads liefern standardmäßig genau diese sieben Eingänge — die drei
  Leistungen mit `<v.3> kW`. Jeder Befehl trägt als Kommentar seinen Baustein-Eingang
  (`Ppwr`, `Gpwr`, `Spwr`, …), damit das Verdrahten ohne Nachschlagen geht.
* Neu je Wert: **Faktor** und **Einheit**. Damit lassen sich auch Zählerbausteine und der
  Energieflussmonitor bedienen. Das Totband wird nach dem Faktor ausgewertet, also in der
  Einheit, die man sieht.
* **Vorlage anwenden** auf der Seite Werte: zurück auf Energiemonitor oder auf Rohwerte.
* Die Seite **Loxone** führt jetzt in drei Schritten durch die Einrichtung, inklusive
  Zuordnungstabelle und der nötigen Baustein-Parameter.
* Eine bestehende `config.yaml` wird nie umgeschrieben — der Standard greift nur beim
  allerersten Start.

### 1.0 — Erste Fassung

* Modbus-TCP-Anbindung an den M-TEC Energybutler, 85 Werte inklusive berechnetem
  Hausverbrauch, Autarkie und Eigenverbrauchsquote.
* Drei Wege nach Loxone: UDP-Push, HTTP-Push, REST-Pull.
* Weboberfläche mit Dashboard, Wert-Zuordnung, Einstellungen und Log.
* Watchdog mit Reconnect und Container-Neustart, Docker-Healthcheck.
* Vorlagen-Export für Loxone Config.
* Fertiges Image für amd64 und arm64.

---

## Original project: MTECmqtt

### Introduction
Welcome to the `MTECmqtt` project!

This project enables to read data from a M-TEC Energybutler (https://www.mtec-systems.com) system and write them to a MQTT broker. 

The highlights are:
* No additional hardware or modifications of your Inverter required 
* Just install on any existing (micro-)server, e.g. Rasperry Pi or NAS server 
* Works within you LAN - no internet connection required 
* Supports more than 80 parameters
* Clustered reading of sequential register to reduce modbus traffic and load 
* Enables frequent polling of data (e.g. every 10s)
* MQTT enables an easy integration into almost any EMS or home automation tool 
* Home Assistant (https://www.home-assistant.io) auto discovery via MQTT 
* Home Assistant demo dashboard included
* Easy and prepared integration into evcc (https://evcc.io), which enables PV surplus charging 

I hope you like it and it will help you with for your EMS or home automation project :-) !

#### Disclaimer 
This project is a pure hobby project which I created by reverse-engineering different internet sources and my M-TEC Energybutler. It is *not* related to or supported by M-TEC GmbH by any means. 

Usage is completely on you own risk. I don't take any responsibility on functionality or potential damage.

#### Credits
This project would not have been possible without the really valuable pre-work of other people, especially: 
* https://www.photovoltaikforum.com/thread/206243-erfahrungen-mit-m-tec-energy-butler-hybrid-wechselrichter
* https://forum.iobroker.net/assets/uploads/files/1681811699113-20221125_mtec-energybutler_modbus_rtu_protkoll.pdf
* https://smarthome.exposed/wattsonic-hybrid-inverter-gen3-modbus-rtu-protocol
* The Home Assistant "blue theme" background was thankfully provided by Enrico from redK! Webdesign & Content Management

#### Compatibility
The project was developed using my `M-TEC Energybutler 8kW-3P-3G25`, but I assume that it will also work with other Energybutler GEN3 versions (https://www.mtec-systems.com/batteriespeicher/energy-butler-11-bis-30-kwh/).

It seems that there are at least three more Inverter products on the market which share the same (or at least a very similar) Chinese firmware. It *might* be that this API also works with these products. But since I do not have access to any of them, this is just a guess and up to you and your own risk to try it.

| Provider  | Link 
|---------- | -------------------------------------- 
| Wattsonic | https://www.wattsonic.com/ |
| Sunways   | https://de.sunways-tech.com |
| Daxtromn  | https://daxtromn-power.com/products/ |


### Setup & configuration
#### Prerequisites
The MTECmqtt project connects to the espressif Modbus server of you M-TEC inverter, retrieves relevant data, and writes them to a MQTT broker (https://mqtt.org/) of your choice. MQTT provides a light-weight publish/subscribe model which is widely used for Internet of Things messaging. MQTT connectivity is implemented in many EMS or home automation tools. 
That means, you obviously require a MQTT server. 
If you don't have one yet, you might want to try https://mosquitto.org/. 
You can easily install it like this:

```
sudo apt install mosquitto mosquitto-clients
```

#### Installation
The basic installation requires only following 4 steps:

(1) Check your Python installation
```
python -V
```
If this returns something like `Python 3.8.x`, you can just continue with the next step.

If it return `command not found`, or `Python 2.x`, try
```
python3 -V
```
If this returns something like `Python 3.8.x`, remember that you need to use `python3` and continue.

If both command return `command not found`, you need to install python3 on you system before you can continue


(2) Create a new directory for the installation (e.g. within your HOME directory)
```
mkdir mtecmqtt && cd mtecmqtt
```

(3) Create and activate a virtual python environment for the project
```
python -m venv . && source bin/activate
```
If you remembered you require `python3`, use this instead:
```
python3 -m venv . && source bin/activate
```


(4) Install the MTECmqtt project from github
```
pip install https://github.com/croedel/MTECmqtt/archive/refs/heads/main.zip
```

As a next step, we can try to start the MQTT server. 
```
mtec_mqtt
```
Starting it for the first time will create your `config.yaml` file. Please review and (if necessary) adapt it you needs (see below for details).

Subsequent invocations will run the actual server. It will print out some debug info, so you can see what it does.

You can stop the service by pressing CTRL-C or sending a SIGHUB. This will initiate a graceful shutdown. Please be patient - this might take a few seconds.

Starting the service in a shell - as we just did - will not create a permanent running service and is probably only useful for testing. If you want a permanently running service, you need to install a systemd autostart script for `mtec_mytt.py`. The following command does this job:
```
sudo bin/install_systemd_service.sh 
```

To check if the service is running smoothly, you can execute:
```
sudo systemctl status mtec_mqtt
```

#### Advanced configuration 
This section give you more information about all configuration options. But don't be afraid - it should only be relevant for advanced use cases.

The installer will create a `config.yaml` file in the default location of your OS.
For a Linux system it's probably `~/.config/mtecmqtt/config.yaml`, on Windowns something like `C:\Users\xxxxx\AppData\Roaming\config.yaml`

##### Connect your M-TEC Inverter
In order to connect to your Inverter, you need the IP address or internal hostname of your `espressif` device. 
If you run a FRITZ!Box, the pre-configured internal hostname `espressif.fritz.box` will probably already work out-of-the-box.
Else you can easily adjust it like this: 
1. Login to your internet router
2. Look for the list of connected devices
3. You should find a devices called `espressif`
4. Copy the IPv4 address or internal hostname of this device to `config.yaml` file as value for `MODBUS_IP`.

You probably don't need to change any of the other `MODBUS_` config values.

_IMPORTANT:_ M-TEC changed their Modbus port with firmware V27.52.4.0. If you run that version or a newer one, you need to change the `MODBUS_PORT` in the `config.yaml` to 502 !  

```
## MODBUS Server
MODBUS_IP : espressif.fritz.box    # IP address / hostname of "espressif" modbus server
MODBUS_PORT : 5743                 # Port (IMPORTANT: you need to change this to 502 for firmware versions newer than 27.52.4.0) 
MODBUS_SLAVE : 252                 # Modbus slave id (usually no change required)
MODBUS_TIMEOUT : 5                 # Timeout for Modbus server (s)
MODBUS_FRAMER: rtu                 # Modbus Framer (usually no change required; options: 'ascii', 'binary', 'rtu', 'socket', 'tls')
```

Hint for advanced users: If you run an external modbus adapter, connected e.g. to the EMS bus of the MTEC inverter, you might require to change the `MODBUS_FRAMER`.   

##### Connect you MQTT broker
The `MQTT_` parameters in `config.yaml` define the connection to your MQTT server.

```
MQTT_SERVER : localhost     # MQTT server 
MQTT_PORT : 1883            # MQTT server port
MQTT_LOGIN  : " "           # MQTT Login
MQTT_PASSWORD : ""          # MQTT Password  
MQTT_TOPIC : "MTEC"         # MQTT topic name  
```

The other values of the `config.yaml` you probably don't need to change as of now.

That's already all you need to do and you are ready to go!

##### More configuration options
The `REFRESH_` parameters define how frequently the data gets fetched from your Inverter

```
REFRESH_NOW     : 10          # Refresh current data every N seconds
REFRESH_DAY     : 300         # Refresh daily statistic every N seconds
REFRESH_TOTAL   : 300         # Refresh total statistic every N seconds
REFRESH_CONFIG  : 3600        # Refresh config data every N seconds
``` 

#### Home Assistant support
`mtec_mqtt` provides Home Assistant (https://www.home-assistant.io) auto-discovery, which means that Home Assistant will automatically detect and configure your MTEC Inverter. 

If you want to enable Home Assistant support, set `HASS_ENABLE: True` in `config.yaml`. 

```
## Home Assistent
HASS_ENABLE : True                # Enable home assistant support
HASS_BASE_TOPIC : homeassistant   # Basis MQTT topic of home assistant
HASS_BIRTH_GRACETIME : 15         # Give HASS some time to get ready after the birth message was received
```

As next step, you need to enable and configure the MQTT integration within Home Assistant. After that, the auto discovery should do it's job and the Inverter sensors should appear on your dashboard.

If you want, you can use and install one of the Home Assistant dashboards in `templates` for a nice data visualization.
The map view requires to install a background image. To do so, create a sub-directory called `www` in the `config` directory of your Home Assistant installation (e.g. `/home/homeassistant/.homeassistant/www/`) and copy the image to this directory.

There are two versions you can chose from:
| Theme       | Dashboard                    | Image
|-------------|--------------------          | ---------------------
| Dark theme  | hass-dashboard.yaml          | PV_background.png
| Blue theme  | hass-dashboard-blue.yaml     | PV_background-blue.png

#### evcc support
If you want to integrate the data into evcc (https://evcc.io), you might want to have a look at the `evcc.yaml` snippet in the `templates` directory. It shows how to define and use the MTEC `meters`, provided in MQTT.
Please don't forget to replace `<MTEC_SERIAL_NO>` with the actual serial no of your Inverter.

### Data format written to MQTT
The exported data will be written to several MQTT topics. The topic path includes the serial number of your Inverter.
 
| Sub-topic                         | Refresh frequency            |  Description 
|---------------------------------- | --------------------------   | ---------------------------------------------- 
| MTEC/<serial_number>/config       | `REFRESH_CONFIG` seconds     | Relatively static config values     
| MTEC/<serial_number>/now-base     | `REFRESH_NOW` seconds        | Current base data      
| MTEC/<serial_number>/now-grid     | `REFRESH_NOWEXT` seconds     | Current extended grid data      
| MTEC/<serial_number>/now-inverter | `REFRESH_NOWEXT` seconds     | Current extended inverter data      
| MTEC/<serial_number>/now-backup   | `REFRESH_NOWEXT` seconds     | Current extended backup data      
| MTEC/<serial_number>/now-battery  | `REFRESH_NOWEXT` seconds     | Current extended battery data      
| MTEC/<serial_number>/now-pv       | `REFRESH_NOWEXT` seconds     | Current extended PV data      
| MTEC/<serial_number>/day          | `REFRESH_DAY` seconds        | Daily statistics     
| MTEC/<serial_number>/total        | `REFRESH_TOTAL` seconds      | Lifetime statistics     

All `float` values will be written according to the configured `MQTT_FLOAT_FORMAT`. The default is a format with 3 decimal digits.

This diagram tries to visualize the power flow values and directions: (at least from my understanding)
<pre>
     + ->               + ->                    + -> 
PV  -------  inverter  ------- power connector ------- grid                
              |    |                |
            ^ |    | +            + |
            + |    | v            v |
Battery -------    |                --------- house
                   -------------------------- backup power
</pre>

*Remark:* Some parameters - marked by `(*)` - are calculated values. 

#### config
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 10000    | serial_no               |      | Inverter serial number
| 10011    | firmware_version        |      | Firmware version
| 25100    | grid_inject_switch      |      | Grid injection limit switch
| 25103    | grid_inject_limit       | %    | Grid injection power limit
| 52502    | on_grid_soc_switch      |      | On-grid SOC limit switch
| 52503    | on_grid_soc_limit       | %    | On-grid SOC limit
| 52504    | off_grid_soc_switch     |      | Off-grid SOC limit switch
| 52505    | off_grid_soc_limit      | %    | Off-grid SOC limit
|          | api_date                |      | Local date of MTECmqtt server

#### now-base
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 10100    | inverter_date           |      | Inverter date
| 10105    | inverter_status         |      | Inverter status (0=wait for on-grid, 1=self-check, 2=on-grid, 3=fault, 4=firmware update, 5=off grid)
| 11000    | grid_power              | W    | Grid power
| 11016    | inverter                | W    | Inverter AC power
| 11028    | pv                      | W    | PV power
| 30230    | backup                  | W    | Backup power total
| 30254    | battery_voltage         | V    | Battery voltage
| 30255    | battery_current         | A    | Battery current
| 30256    | battery_mode            |      | Battery mode (0=Discharge, 1=Charge)
| 30258    | battery                 | W    | Battery power
| 33000    | battery_soc             | %    | Battery SOC
| 50000    | mode                    |      | Inverter operation mode (257=General mode, 258=Economic mode, 259=UPS mode, 512=Off grid 771=Manual mode)
|          | consumption             | W    | Household consumption (*)

#### now-backup
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 30200    | backup_voltage_a        | V    | Backup voltage phase A
| 30201    | backup_current_a        | A    | Backup current phase A
| 30202    | backup_frequency_a      | Hz   | Backup frequency phase A
| 30204    | backup_a                | W    | Backup power phase A
| 30210    | backup_voltage_b        | V    | Backup voltage phase B
| 30211    | backup_current_b        | A    | Backup current phase B
| 30212    | backup_frequency_b      | Hz   | Backup frequency phase B
| 30214    | backup_b                | W    | Backup power phase B
| 30220    | backup_voltage_c        | V    | Backup voltage phase C
| 30221    | backup_current_c        | A    | Backup current phase C
| 30222    | backup_frequency_c      | Hz   | Backup frequency phase C
| 30224    | backup_c                | W    | Backup power phase C

#### now-battery
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 33001    | battery_soh             | %    | Battery SOH
| 33003    | battery_temp            | ℃   | Battery temperature 
| 33009    | battery_cell_t_max      | ℃   | Battery cell temperature max.
| 33011    | battery_cell_t_min      | ℃   | Battery cell temperature min.
| 33013    | battery_cell_v_max      | V    | Battery cell voltage max.
| 33015    | battery_cell_v_min      | V    | Battery cell voltage min.

#### now-grid
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 10994    | grid_a                  | W    | Grid power phase A
| 10996    | grid_b                  | W    | Grid power phase B
| 10998    | grid_c                  | W    | Grid power phase C
| 11006    | ac_voltage_a_b          | V    | Inverter AC voltage lines A/B
| 11007    | ac_voltage_b_c          | V    | Inverter AC voltage lines B/C
| 11008    | ac_voltage_c_a          | V    | Inverter AC voltage lines C/A
| 11009    | ac_voltage_a            | V    | Inverter AC voltage phase A
| 11010    | ac_current_a            | A    | Inverter AC current phase A
| 11011    | ac_voltage_b            | V    | Inverter AC voltage phase B
| 11012    | ac_current_b            | A    | Inverter AC current phase B
| 11013    | ac_voltage_c            | V    | Inverter AC voltage phase C
| 11014    | ac_current_c            | A    | Inverter AC current phase C
| 11015    | ac_fequency             | Hz   | Inverter AC frequency

#### now-inverter
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 11032    | inverter_temp1          | ℃  | Temperature Sensor 1
| 11033    | inverter_temp2          | ℃  | Temperature Sensor 2
| 11034    | inverter_temp3          | ℃  | Temperature Sensor 3
| 11035    | inverter_temp4          | ℃  | Temperature Sensor 4
| 30236    | inverter_a              | W   | Inverter power phase A
| 30242    | inverter_b              | W   | Inverter power phase B
| 30248    | inverter_c              | W   | Inverter power phase C

#### now-pv
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 11022    | pv_generation_duration  | h    | PV generation time total
| 11038    | pv_voltage_1            | V    | PV1 voltage
| 11039    | pv_current_1            | A    | PV1 current
| 11040    | pv_voltage_2            | V    | PV2 voltage
| 11041    | pv_current_2            | A    | PV2 current
| 11062    | pv_1                    | W    | PV1 power
| 11064    | pv_2                    | W    | PV2 power

#### day
| Register | MQTT Parameter          | Unit | Description 
| -------- | ----------------------  | ---- | ---------------------------------------------- 
| 31000    | grid_feed_day           | kWh  | Grid injection energy (day)
| 31001    | grid_purchase_day       | kWh  | Grid purchased energy (day)
| 31002    | backup_day              | kWh  | Backup energy (day)
| 31003    | battery_charge_day      | kWh  | Battery charge energy (day)
| 31004    | battery_discharge_day   | kWh  | Battery discharge energy (day)
| 31005    | pv_day                  | kWh  | PV energy generated (day)
|          | autarky_rate_day        | %    | Household autarky (day) (*)
|          | consumption_day         | kWh  | Household energy consumed (day) (*)
|          | own_consumption_day     | %    | Own consumption rate (day) (*)

#### total
| Register | MQTT Parameter             | Unit | Description 
| -------- | ----------------------     | ---- | ---------------------------------------------- 
| 31102    | grid_feed_total            | kWh  | Grid energy injected (total)
| 31104    | grid_purchase_total        | kWh  | Grid energy purchased (total)
| 31106    | backup_total               | kWh  | Backup energy (total)
| 31108    | battery_charge_total       | kWh  | Battery energy charged (total)
| 31110    | battery_discharge_total    | kWh  | Battery energy discharged (total)
| 31112    | pv_total                   | kWh  | PV energy generated (total)
|          | autarky_rate_total         | %    | Household autarky (total) (*)
|          | consumption_total          | kWh  | Household energy consumed (total) (*)
|          | own_consumption_total      | %    | Own consumption rate (total) (*)


### What else you can find in the project?

#### Modbus Utility
`mtec_util` is an small inteative tool which enable to list the supported parameters and read and write registers of your Inverter.
You can choose between:

 * 1: List all known registers
 * 2: List register configuration by groups
 * 3: Read register group from Inverter
 * 4: Read single register from Inverter
 * 5: Write register to Inverter

(1) lists all know registers. This includes the ones which are written to MQTT as listed above. You will find a few more registers, which are not mapped to MQTT (=no value in "mqtt") - mostly because I'm not sure if they are reliable or what they really mean.   

(2) will give you a list of all mapped registers, similar to the one listed above.

(3) allows you to read the current values of all registers or of a certain group from your Inverter.

(4) allows you to read a sinfle register from your Inverter

(5) enables you to write a value to a register of your Inverter. WARNING: Be careful when writing data to your Inverter! This is definitively at your own risk!

#### Commandline export tool
The command-line tool `mtec_export` offers functionality to read data from your Inverter using Modbus and export it in various combinations and formats.

As default, it will connect to your device and retrieve a list of all known Modbus registers in a human readable format.

By specifying commandline parameters, you can:
* Specify a register group (e.g. `-g config`) or "all" (`-g all`) to export of all registers
* Provide a customize list of Modbus registers which you would like to retrieve, e.g. `-r "33000,10105,11000"`
* Request to export CSV instead of human readable (`-c`) 
* Write output to a file (`-f FILENAME`)
