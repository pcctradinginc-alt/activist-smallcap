# activist-smallcap

Forschungssystem zu Aktivisten-Beteiligungen bei Small Caps: **Research Protocol v1, Phase 0/1 – Datenbeschaffung und Ereignisstudie.**
Es wird **kein Echtgeld** gehandelt; Papierhandel und Journal dienen nur der Prüfung. Keine Anlageberatung.

## Die vier Grundregeln

1. **Veröffentlichungszeitpunkt**: Maßgeblich ist der Zeitpunkt der Veröffentlichung; fehlt die Uhrzeit, gilt der Folgetag (`common.effective_decision_time`).
2. **Point-in-time**: Aktivistenliste und Kennzahlen gelten je Stichtag (`config/activists.yaml`, Feld `since`), nie rückwirkend.
3. **Einheitliche Ausführung und Kosten**: Dasselbe Orderausführungs- und Kostenmodell (`paper/execution.py`) für E-Mail, Journal und Ereignisstudie.
4. **Risikogrenzen nur manuell**: `config/risk_limits.yaml` darf vom System nie geschrieben werden (`tests/test_risk_guard.py`); ein Risikoverstoß verwirft, kein Konfidenzwert gleicht ihn aus.

## Verzeichnisse

```
config/     params.yaml (Protokollparameter), activists.yaml, risk_limits.yaml (nur manuell)
src/activist/
  sources/  edgar.py, edinet.py, prices.py   Datenquellen
  parse/    campaigns.py                     Aktivistenliste, Ereignisübersicht
  paper/    execution.py                     Orderausführung, Kostenmodell
  study/    event_study.py, stats.py         Ereignisstudie, Bootstrap, Nachweiskriterien
  risk/     rules.py                         ein Regelwerk: Kandidatenregeln (inkl. Großaktionär, Quartalszahlen-Sperre) + Portfolio-Risikotor (Studie und Papierdepot)
  learn/    ranker.py                        Challenger (Schattenmodus, handelt nicht)
  health.py                                  Systemstatus, Quellen-Überwachung
  notify/   mail.py                          E-Mail (SMTP)
  cli.py                                     Kommandozeile
data/       events (append-only JSONL), prices
journal/    trades.csv, shadow.csv, decisions/
reports/    Studienberichte
tests/      Offline-Tests (pytest)
.github/workflows/  ci, daily, weekly_status
```

## Alpha-Protokoll

- Zeitfenster: Training bis 2019, Validierung 2020–2022, Holdout ab 2023 (bleibt versiegelt, bis das Protokoll eingefroren ist – `PROTOCOL_FREEZE.json`).
- Der Holdout ab 2023 ist durch frühere Berichte vorbelastet (siehe `PROTOCOL.md`); unabhängig ist erst der Vorwärtstest nach dem Einfrieren.
- Mehrfachtests werden mit Holm korrigiert; Alpha gilt je Region/Klasse nur bei bestandenem Holdout.
- Es zählen nur geprüfte (verifizierte) Daten.
- Lernstufe bleibt inaktiv, bis 200 abgeschlossene Kampagnen vorliegen.

## Systemstatus

Wochenbericht und HTML-Mail beginnen mit dem Block „Systemstatus“ (`health.py`): Datenqualität, gültige Ereignisse/Kampagnen, Alpha-Nachweis (JA/NEIN je Region/Klasse), ungeklärte Fehler (fehlgeschlagene oder seit mehr als 4 Tagen nicht gelaufene Quellen aus `reports/health_*.json`, fehlendes Einfrieren) und Lernfortschritt (x / 200).

`python -m activist.cli health --core EDGAR,EDINET` bzw. `--pool six,nsm,amf` beendet einen Workflow mit Fehler, wenn eine Kernquelle ausfällt oder weniger als die Hälfte der aktiven Quellen liefert – ein grüner Lauf bedeutet damit vollständige Daten.

## Einrichtung

```bash
pip install -r requirements.txt
export PYTHONPATH=src
python3 -m pytest -q        # Tests laufen offline
```

## CLI

```bash
python -m activist.cli fetch --source edgar --days 3 [--date YYYY-MM-DD] [--limit N] [--no-headers]
python -m activist.cli status [--mail]      # Ereignis-/Kampagnenübersicht, optional per E-Mail
python -m activist.cli study [--main-only]  # Stufe 1: Kurse laden (Cache), simulieren, Bericht schreiben – dauert lange
python -m activist.cli study --reuse        # Stufe 2: Statistik nur aus gespeicherten Trades neu rechnen – ohne Netz, Sekunden
python -m activist.cli health               # Systemstatus; Exit-Code 1 bei Ausfall (siehe Systemstatus)
python -m activist.cli paper                # Papierdepot-Tageslauf
python -m activist.cli freeze --note "..."  # Protokoll einfrieren (Prüfsummen aller ergebnisrelevanten Dateien)
```

`fetch --source edinet` bzw. `all` benötigt `EDINET_API_KEY`; ohne Schlüssel wird EDINET übersprungen.

## GitHub Secrets

| Secret | Zweck |
|---|---|
| `SEC_USER_AGENT` | User-Agent mit Kontakt für SEC EDGAR |
| `EDINET_API_KEY` | EDINET-API (Japan) |
| `TIINGO_API_KEY` | Zweite Kursquelle USA (Kursabgleich, delistete Titel) |
| `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` | Mailversand |
| `MAIL_TO` | Empfänger |

Ohne SMTP-Konfiguration läuft der Versand als Dry-Run (Ausgabe auf der Konsole).

## Fahrplan

| Phase | Inhalt | Abbruchkriterium |
|---|---|---|
| 0 | Protokoll festlegen und einfrieren (`frozen: true`, Tag `protocol-v1`), Gebühren je Handelsplatz erheben | Protokoll nicht eindeutig prüfbar |
| 1 | Datenbeschaffung + Ereignisstudie | < 100 Kampagnen: „nicht beurteilbar“; mittlere Netto-Überrendite < 3 %, untere CI-Grenze <= 0, eine Hälfte negativ oder erwarteter Ertrag < 300 EUR/Jahr: „nicht bestanden“ -> Stopp |
| 2 | Schattenjournal (Papier, E-Mail-Signale mit „Was dagegen spricht“) | Schattenergebnis weicht deutlich von der Studie ab |
| 3 | Lernstufe (Konfidenz, Positionstabelle) | Kein Mehrwert gegenüber Stufe 1 |
| 4 | Erweiterung auf weitere Regionen | Region besteht Kriterien nicht |
| 5 | Entscheidung über Echtgeld (manuell, außerhalb dieses Systems) | Kein Nachweis in Phase 1-3 |

## Offene Punkte

- Europa-Register noch nicht angebunden.
- Item-4/Haltezweck-Klassifikation (Sprachmodell) fehlt.
- Exit-Erkennung aus Änderungsmeldungen fehlt.
- Historische Ticker-Zuordnung: aktuell nur aktuelle SEC-Liste (`company_tickers.json`).
- Schwellen EU aus dem Gedächtnis eingetragen – prüfen.
- Broker-Mindestgebühren je Handelsplatz erheben (`fixed_fee_eur_per_order`).
