# Research Protocol v1 (Entwurf – wird am Ende von Phase 0 eingefroren)

Stand: 2026-11-14 · Status: **Entwurf** (`config/params.yaml: frozen: false`)

Das Protokoll wird vor Beginn der Ereignisstudie eingefroren: `frozen: true`, Git-Tag `protocol-v1`.
Jede spätere Änderung steht mit Begründung in `PROTOCOL_CHANGES.md` und im Commit.

## Grundregeln
1. Erst Alpha nachweisen, dann automatisieren, zuletzt lernen.
2. Echtes Geld erst nach bestandenem Vorwärtstest (Phase 3).
3. Das System lockert seine eigenen Risikoregeln nie (`config/risk_limits.yaml` nur manuell; Test `test_risk_guard.py`).
4. Protokoll eingefroren vor der Ereignisstudie.

## Hypothesen (vorab festgelegt)
| Nr | Hypothese | Test im Code | Widerlegt, wenn |
|---|---|---|---|
| H1 | Kauf nach Meldung bringt Nettoüberrendite bis zum regelgemäßen Ausstieg | `qualifiziert:main` je Region/Klasse | Ø netto ≤ 0 |
| H2 | Ergebnis ist vorab an Merkmalen erkennbar | Forderung (`demand`), Aktivisten-Kennzahlen, out-of-sample | keine Trennung außerhalb Lernstichprobe |
| H3 | Aktivisten-Fähigkeit ist stabil | Rangkorrelation Aktivisten-Ø in zwei Zeithälften | unkorreliert |
| H4 | Japan: Druck auf Kapitalrückgabe wirkt ohne Abstimmungssieg | erst nach EDINET-Backfill | kein Unterschied zu Nachbarwerten |

## Haupttest (einziger Nachweis)
- Einheit: Kampagne = erstes Ereignis je (Melder, Emittent). Folgeereignisse separat.
- Signalklassen: A = 13D/Erstmeldung mit Einflussabsicht eines Aktivisten (PIT-Register), B = 13G eines Aktivisten, C = öffentliche Kampagne ohne Meldung (noch nicht erfasst).
- Aktivist (PIT): Liste in `config/activists.yaml` (ab `since`) **oder** ≥ 3 frühere 13D-Erstmeldungen bei verschiedenen Emittenten vor dem Stichtag.
- Universum USA: 50 Mio.–2 Mrd. USD (Aktienzahl PIT aus XBRL × Vortagesschluss), Kurs ≥ 1 USD, Median-Dollarumsatz 20T ≥ 100.000 USD, kein OTC, keine Banken/Versicherer/Mäntel.
- Ausschlussregeln (historisch bestimmbar): Spread (Corwin-Schultz-Schätzer) ≤ 2 %, Anstieg 20T ≤ 20 %, Kurs ≤ 15 % über Ø-Einstand des Aktivisten (Item 5c), Liquidität. Nicht historisch bestimmbar und daher in der Studie nicht angewandt: Kapitalerhöhung, Fortführungsvermerk, Mehrheitsaktionär, Quartalstermin – das ist eine bekannte Abweichung zum Live-Betrieb.
- Order: Limit = letzter Schluss + 3 %, max. 3 Handelstage, max. 5 % des Tagesumsatzes je Tag.
- Ausstieg: Reduktionsmeldung (13D/A mit < 5 % oder Exit-Formulierung) → nächste Eröffnung; −30 % Stopp; 252 Handelstage.
- Kosten je Seite: max(halber Spread, 0,5 %) + 0,25 % (+ Abgaben). Mindestens 1,5 % Round-Trip.
- Vergleich: Region-Index (USA: IWM), Abschnitt Einstieg→Ausstieg.
- Unsicherheit: 90-%-Bootstrap gebündelt nach Aktivist und nach Monat; der breitere Bereich zählt.
- Bestanden, wenn: n ≥ 100 Kampagnen, Ø ≥ 3 %, untere Grenze > 0, beide Zeithälften positiv, Wirtschaftlichkeit (≥ 300 €/Jahr auf 10.000 €).
- Erkundung (kein Nachweis): ohne Stopp; feste Haltedauern 21/63/126/252 Tage; Schattenportfolio.

## Bekannte Verzerrungen (müssen im Bericht stehen)
- **Survivorship:** Kostenlose Kurse enthalten keine delisteten Titel; der Bericht weist den Anteil aus. Für ein Urteil braucht es eine Quelle mit delisteten Titeln.
- **Ticker:** Zuordnung CIK → Ticker nur nach heutigem Stand; Tickerwechsel werden über die CIK aufgefangen, Delistings nicht.
- **Forderung:** regelbasierte Klassifikation (Item 4) mit Textbeleg; Vorbehaltsformulierungen zählen als „keine“.

## Entscheidung 2026-11-14: Deutschland und Österreich ausgeschlossen
Keine kostenlose zulässige Quelle (EQS-Nutzungsbedingungen, BaFin/Unternehmensregister robots.txt, APA-OTS TDM-Vorbehalt).
Region DACH umfasst daher nur die Schweiz (SIX). Parser für WpHG/BörseG-Mitteilungen bleiben im Code, ungenutzt.

## Änderung 2026-11-14: Deutschland wieder aufgenommen, SDAX im Universum
- Deutschland kommt über die IR-Seiten der Emittenten (Stimmrechtsmitteilungen, robots-konform, ohne EQS-Hosts)
  und über öffentliche Kampagnen auf den Websites der Aktivisten (Klasse C, getrennt ausgewertet) zurück.
- DACH-Obergrenze Marktkapitalisierung 2 Mrd. -> 8 Mrd. EUR, damit SDAX-Werte im Universum sind (Wunsch Nutzer).
  Bewusst Größengrenze statt Indexmitgliedschaft: die heutige SDAX-Zusammensetzung rückwirkend zu verwenden wäre
  ein Look-ahead-Fehler. Effekt auf die Ergebnisse wird im Bericht getrennt nach <= 2 Mrd. und 2–8 Mrd. ausgewiesen.

## Korrektur 2026-11-15: Aktivisten-Erkennung verschärft, Papierdepot zurückgesetzt
Der erste Tageslauf (2026-11-14) hat große Institutionen (Staatsfonds, Banken, Vermögensverwalter) als Aktivisten
gewertet, weil die Verhaltensregel (>= 3 frühere 13D) allein genügte. Neu: Klasse B nur für kuratierte Aktivisten;
Klasse A über die Verhaltensregel nur mit konkreter Forderung samt Textbeleg in Item 4. Zu diesem Zeitpunkt war keine
Order ausgeführt (0 Positionen, trades.csv leer); portfolio.json und shadow.csv wurden zurückgesetzt.

## Änderung 2026-11-15 (vor dem Einfrieren, nach externer Code-Prüfung)
- Limit-Ausführung konservativ: Ausführung zum Limit nur, wenn das Tagestief das Limit unterschreitet (bloße Berührung reicht nicht).
- Rechtszensierung: Der Alpha-Test wertet nur Kohorten, deren volle Haltedauer beobachtbar ist (Veröffentlichung ≥ 380 Tage vor Auswertung).
- Europa: Ohne bekannte Aktienzahl keine Qualifikation (Marktkapitalisierung muss prüfbar sein, gleiche Regel wie USA/Japan).
- Meldungen ohne Uhrzeit (SIX): Entscheidung frühestens am Folgetag statt Ausschluss (kein Look-ahead).
- Bewusst nicht übernommen: Faktormodell, Ridge-Regression, Portfolio-Simulator – zusätzliche Komplexität ohne Nutzen bei der aktuellen Fallzahl; die Bewertung gegen den regionalen Small-Cap-Index mit Kapazitätsgrenze bleibt.

## Änderung 2026-11-15 (vor dem Einfrieren, nach kritischem Repository-Audit)
Keine neuen Funktionen, Datenquellen oder Modelle – nur Korrekturen und Vereinfachungen.
- **Datenqualität verschärft:** „Ticker eindeutig“ gilt nur noch, wenn der Kurs am Entscheidungstag zum Ø-Einstand des
  Aktivisten passt (13D Item 5c) oder der Ticker aus damaligen SEC-Dokumenten des Emittenten bestätigt ist – der heutige
  Ticker allein genügt nicht. Der Kursabgleich zählt nur bei mindestens 20 gemeinsamen Tagen und 80 % Überdeckung;
  sonst gilt die Kampagne als ungeprüft.
- **Ein Regelwerk:** Die historisch bestimmbaren Ausschlussregeln stehen einmal in `risk/rules.candidate_reasons`
  (USA, Japan, Europa; Studie und Papierdepot). Das Papierdepot prüft jede Order über `risk/rules.check_portfolio`:
  Positionszahl, Aktivist, Doppelung, Positionsgröße, Branche (unbekannte Branche = eigener Topf je Region), Region (sobald
  3 Regionen bestanden haben) und Cash-Deckung inkl. Kosten und offener Orders.
  `earnings_blackout_days` am 2026-11-18 vom Eigentümer gestrichen; `max_major_holder` seit 2026-11-21 erzwungen.
- **Holdout:** Berichte bis 2026-11-15 haben Ergebnisse ab 2023 offen gezeigt; nach diesen Berichten wurden Regeln
  geändert. Der historische Holdout ab 2023 ist damit **vorbelastet**: Ein Bestehen ist ein Indiz, kein unabhängiger
  Nachweis. Unabhängig ist erst der Vorwärtstest (Kampagnen nach dem Einfrieren, Grundregel 2). Ab jetzt zeigen
  Erkundungstabellen nur den Entwicklungszeitraum (bis 2022); die Zeile „ab 2024“ entfällt.
- **Einfrieren vollständig:** Die Prüfsummen umfassen jetzt auch `sources/prices.py`, `learn/ranker.py`,
  `risk/rules.py`, `sources/secmeta.py`, `sources/hist_ticker.py`, `config/activists.yaml`.
- **Plausibilität:** Trades mit unplausibler Vergleichsindex-Reihe (Tagessprung > 25 %) zählen nicht. Ursache der
  Nettoüberrenditen unter −900 %: fehlerhafte Reihe des Japan-Index-ETF 1306, kein echter Verlust.
  Survivorship-Sensitivität rechnet nicht mehr mit fehlenden Werten (zuvor NaN) und ohne stillen Index-Fallback.
- **Abruf und Auswertung getrennt:** `study` lädt Kurse (mit dauerhaftem Cache, auch für unbekannte Ticker) und
  simuliert; `study --reuse` rechnet die Statistik nur aus den gespeicherten Trades neu (ohne Netz, Sekunden).
- **Betrieb:** Zustandsdateien werden atomar geschrieben. `cli health` setzt den Lauf auf Fehler, wenn EDGAR/EDINET
  ausfallen oder veraltet sind bzw. weniger als die Hälfte der aktiven Europa-Quellen liefert.
  Europa täglich nur noch SIX, FCA NSM, AMF; Nasdaq Nordic, Oslo, DE-IR-Seiten und Aktivisten-Websites sind
  deaktiviert (Code bleibt), weil sie bisher praktisch keine auswertbaren Kampagnen geliefert haben.
- **Sicherheit:** Fehlermeldungen der Kursquelle enthielten den Tiingo-Schlüssel und standen im Bericht
  (`price_check_note`). Die Meldungen werden jetzt bereinigt; der alte Schlüssel ist zu ersetzen.
- **Entwicklungsstopp:** Bis ein Studienlauf mit ausreichend geprüften Kampagnen vorliegt, keine neuen Datenquellen,
  Modelle oder Merkmale.

## Änderung 2026-11-17 (Branchen, Großaktionär, Quartalszahlen-Sperre)
`max_major_holder` und `earnings_blackout_days` werden jetzt erzwungen (`risk/rules.candidate_reasons`, alle Regionen).
- **Branchen:** Alle Regionen nutzen die 11 Yahoo-Sektornamen. USA: Abbildung der SIC-Nummer (`sources/sector.from_sic`);
  Japan/Europa: Yahoo-Suche je Symbol (`sector.yahoo`, Cache). Bekannte Grenze: Yahoo liefert den aktuellen, nicht den
  damaligen Sektor (nicht point-in-time). Unbekannt = eigener Topf je Region (unverändert).
- **Großaktionär:** Ausschluss („Großaktionär über Grenze“), wenn der größte bekannte Einzelaktionär (jeder Melder inkl.
  Aktivist) mehr als `max_major_holder` hält. Quellen: JP/EU aus den Ereignissen (`holding_ratio`, je Melder letzte Meldung
  vor dem Entscheidungstag, 3-Jahres-Fenster, Werte > 0,99 verworfen); USA aus den zuletzt geparsten 13D/13G-Meldungen
  (höchstens 10 Melder, `study/holders.py`). Fehlende Daten = kein Ausschluss.
- **Quartalszahlen-Sperre:** Keine neue Position, wenn die nächsten Quartalszahlen höchstens `earnings_blackout_days`
  Kalendertage nach dem Entscheidungstag erwartet werden. USA: Termine der 8-K Item 2.02 (nur Block `recent`), Projektion
  aus Vorjahrestermin (+364 Tage) bzw. letztem Termin (+91 Tage), nur Termine bis zum Entscheidungstag. JP/EU: keine freie
  historische Terminquelle -> kein Ausschluss, in `earnings_check` als „unbekannt (keine Datenquelle)“ markiert.
- **Kursprüfung Japan:** Es gibt keine freie zweite Kursreihe (Stooq bot-geschützt, J-Quants-Gratisplan nur 2 Jahre).
  Für qualifizierte JP-Kampagnen wird das Yahoo-Kursniveau gegen die eigenen Preisangaben des Meldenden geprüft
  (EDINET: Stückpreise außerbörslicher Aktienkäufe der letzten 60 Tage, ±15 %; sonst Ø-Einstand aus Erwerbsmitteln/Stückzahl,
  ±35 %). Bestätigung -> `price_check` wahr (`price_check_kind` = „EDINET-Einstand“). Widerlegen kann nur die
  Plausibilitätsprüfung der Kursreihe (Tagessprung > Faktor 3, split-bereinigt). Schwächer als die US-Prüfung, weil nur
  das Niveau am Einstieg und nicht die ganze Reihe verglichen wird; im Bericht ausgewiesen.

## Änderung 2026-11-18 (vor dem Einfrieren, auf ausdrückliche Anweisung des Eigentümers)
- Sperrfrist vor Quartalszahlen (`earnings_blackout_days`) gestrichen: keine kostenlose point-in-time-Quelle für Japan/Europa,
  Regel wurde nirgends angewendet; bei 252 Tagen Haltedauer unerheblich.
- Branche point-in-time: SIC aus dem SEC-Kopf der jeweiligen 13D-Meldung (Parser-Version 3), nicht die heutige Einstufung.
- Zweite Kursquelle (Tiingo) nur für qualifizierte US-Kampagnen; Nachlade-Modus füllt den Cache, bis alle geprüft sind.

## Änderung 2026-11-21 (nach dem Holdout, auf Anweisung des Eigentümers: Commit 97b8dba nachgezogen)
- Übernommen: Großaktionär-Regel (`max_major_holder`), einheitliche Yahoo-Sektornamen, Kursniveau-Prüfung Japan über EDINET.
- Branche USA bleibt point-in-time (SIC aus dem 13D-Kopf, abgebildet auf Yahoo-Sektor). JP/EU: heutiger Yahoo-Sektor (bekannte Grenze).
- Quartalszahlen-Sperre bleibt gestrichen: Regel greift nur, wenn `earnings_blackout_days` in `risk_limits.yaml` steht (derzeit nicht).
- Folge: Das Einfrieren vom 2026-11-18 wird ersetzt. Der Holdout ab 2023 ist für diese Regeln **verbraucht**; ein
  unabhängiger Nachweis kommt nur noch aus dem Vorwärtstest (Kampagnen nach dem neuen Einfrieren).

## Änderung 2026-11-21 (b) (Anweisung des Eigentümers): Finanzinstitute in Japan keine Aktivisten
- Japan: Melder mit Bank/Versicherer/Broker/Treuhand im Namen (`dataset.is_jp_financial`) bilden keine Kampagne, außer sie
  stehen in `activists.yaml`. Anlass: Papier-Käufe 2026-11-17 auf Meldungen der MUFG (595 von 2.198 JP-Kampagnen).
- `activists.yaml`: Symphony Financial Partners ergänzt (echter Aktivist, sonst vom Namensfilter erfasst).
- Neues Einfrieren; Nachweis nur vorwärts.
