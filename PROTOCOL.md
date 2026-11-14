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
