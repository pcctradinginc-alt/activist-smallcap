# EU-Quellen: Phase-0 Daten-Audit (Stand 2026-11-14)

Kriterien: (1) kostenlos und offiziell, (2) Veröffentlichungszeitstempel (idealerweise mit Uhrzeit), (3) >= 3 Jahre Historie,
(4) programmatischer Zugriff ohne Bot-Check/CAPTCHA. User-Agent `activist-smallcap research contact@example.org`, >= 1 s Abstand.
Alle Endpunkte per curl real verifiziert; nicht dokumentierte Endpunkte (SPA-Backends) können sich ohne Ankündigung ändern.

| Land / Quelle | Endpoint | Format | Zeitstempel (Uhrzeit) | Historie (älteste gefundene) | Filer / Emittent-ID | Anteil % | Limits / ToS | Verdikt |
|---|---|---|---|---|---|---|---|---|
| UK, FCA NSM (TR-1) | `POST https://api.data.fca.org.uk/search?index=nsm-search` (Backend der NSM-Web-App; Dokumente `https://data.fca.org.uk/artefacts/<download_link>`) | JSON (ES-Stil), Dokumente HTML | `publication_date` UTC, sekundengenau (ja); Altbestand vor 09/2017 nur Datum | 2010-09-30 (120k Treffer "Holding(s) in Company") | Emittent: LEI immer, ISIN nur Altbestand/Dokument; Filer: `related_org` (nicht immer), sicher nur im TR-1-Dokument | nur im TR-1-HTML (Ziff. 7) | robots.txt `Allow: /` (Content-Signal: search=yes). Seite 500 Treffer, from+size <= 10000. Kein Key. Backend nicht offiziell dokumentiert; Dokumentabruf 1 Request/Meldung. | **PASS** |
| NL, AFM Substantiële deelnemingen | `https://www.afm.nl/export.aspx?type=1331d46f-3fb6-4a36-b903-9584972675af&format=csv` (auch xml) | CSV (; ISO-8859, 114 MB Voll-Export, 308k Zeilen) | nur `Datum meldingsplicht` = Datum der Schwellenberührung, KEIN Veröffentlichungsdatum/-zeit; Meldung kommt bis zu Tage später | 2006 | Filer-Name + KvK, Emittent nur Name (keine ISIN/LEI) | ja (Kapitalbelang/Stemrecht) | robots Allow; kein Datumsfilter in der Export-URL gefunden; jeder Abruf 114 MB | **FAIL** (2): Stichtag statt Publikationszeit -> Look-ahead-Gefahr; (zusätzlich ID-Mangel). Als Ergänzung für Filer/% nutzbar, nicht für Timing |
| NO, Oslo Børs NewsWeb | `GET https://api3.oslo.oslobors.no/v1/newsreader/list?category=1006&fromDate=&toDate=` (`POST .../categories` für Kategorien) | JSON | `publishedTime` UTC, ms (ja) | 2006-03 (Liste), Stichprobe 2006/2009/2012/2016/2019/2022 ok | Emittent: Name + Ticker (`issuerSign`), keine ISIN; Filer + % nur im PDF-Anhang (`/attachment?messageId=&attachmentId=`) | nur im PDF | Kein Key, kein Bot-Check, robots der SPA nicht restriktiv; Backend-API der NewsWeb-App (nicht als Open-Data-API dokumentiert); ~600 Treffer/Anfrage | **PASS** (Ereignis-Ebene; Filer/% nicht ohne PDF-Parser) |
| FR, AMF BDIF | `GET https://bdif.amf-france.org/back/api/v1/informations?TypesInformation=SPDE&TypesDocument=Declarations&DateDebut=&DateFin=&From=&Size=` (PDF: `/back/api/v1/documents/<path>`) | JSON, PDF | `datePublication` Europe/Paris, sekundengenau (ja) **aber** Altbestand vor 2022 = Migrationsstempel Dez 2021; dann nur `dateMiseEnLigne` (teils 00:00) | Daten ab 2010 (numero 210C..), verlässliche Zeitstempel ab 2022-01 (~4,8 Jahre) | Emittent: Name + `jeton` (RS...), keine ISIN; Filer/% nur im PDF (`titre` leer) | nur PDF | robots ohne Sperre (Angular-SPA), kein Key; Backend nicht dokumentiert; enthält Schwellen UND Absichtserklärungen (Declarations) | **PASS** (Ereignis-Ebene; Altbestand flagged `ts_migrated`) |
| DE, BaFin Stimmrechtsmitteilungen (MVP-Portal) | `https://portal.mvp.bafin.de/database/...` | HTML-Datenbank | nur Datum | n/a | n/a | n/a | `robots.txt: Disallow: /` für alle Agents, kein Export/API | **FAIL** (4)/ToS: robots verbietet |
| DE, Unternehmensregister | `unternehmensregister.de` | HTML-Suche | n/a | n/a | n/a | n/a | robots: `/de/suche`, `/de/veroeffentlichung`, `/de/register*` disallow; Volltext kostenpflichtig/Suche mit Bot-Schutz | **FAIL** (1)/(4) |
| SE FI / FI FIN-FSA | fi.se, finanssivalvonta.fi | HTML | - | - | - | - | Kein offenes Flaggning-Register mit API gefunden (Meldungen laufen über Emittenten-Pressemitteilungen) -> über Nasdaq Nordic abgedeckt | **FAIL** (kein maschinenlesbarer Endpunkt gefunden) |
| DK Finanstilsynet OAM | `oam.finanstilsynet.dk` (React-SPA) | unklar | - | - | - | - | API-Endpunkt nach mehreren Probes nicht ermittelbar -> abgebrochen; Dänemark über Nasdaq Copenhagen abgedeckt | **FAIL** (unklar, Zeitbudget) |
| Nordics (SE/DK/FI/IS), Nasdaq Nordic Nachrichten | `GET https://api.news.eu.nasdaq.com/news/query.action?globalGroup=exchangeNotice&cnsCategory=Major+shareholder+announcements&timeZone=UTC&limit=200&start=&callback=cb` | JSONP | `published` sekundengenau (ja; `timeZone=UTC` wird akzeptiert) | start < 10000 -> ca. 2017-10 (8k Einträge); Datumsfilter existiert nicht, nur Paging | Emittent: nur Name (keine ISIN im Feed; ISIN teils im Text); Filer teils in Headline, sonst im Text/Anhang | im Meldetext (`view`-Seite HTML, Freitext mehrsprachig) | robots.txt leer (keine Sperre), kein Key; limit max 200; Nutzung der Nachrichten laut Nasdaq-Bedingungen nur für eigenen Gebrauch prüfen | **PASS** |
| CH, SIX Exchange Regulation (Offenlegung) | RSS `https://www.ser-ag.com/itf-data/significant-shareholders/rss-en.xml`; Suche = JS-SPA | RSS | `pubDate` immer 12:00 (Pseudo-Zeit, nicht echt) | nur 123 Items / ~1 Monat | Emittent-Name; kein Filer, kein % | nein | Such-Backend nicht ermittelbar | **FAIL** (3) Historie, (2) Pseudo-Zeit |

## Implementierte Adapter (nur PASS)

Alle: `fetch_events(start_date, end_date)` -> Event-Dicts (Schema wie EDGAR/EDINET), `form="MajorHolding"`, `published` ISO mit TZ via
`effective_decision_time` (ohne Uhrzeit: Folgetag 00:00 UTC), `holding_ratio` als Bruch (0.05 = 5 %), plus reine `parse_*`-Funktionen.
Gemeinsamer gedrosselter Client (>= 1 s, UA) in `eu_common.py`.

| Modul | Region | source | Parser | issuer_id | filer | holding_ratio |
|---|---|---|---|---|---|---|
| `sources/eu_nsm.py` | UK | NSM | `parse_hits`, `parse_tr1` | ISIN (nach `enrich_docs=True` oder Altbestand), sonst LEI | `related_org`, mit enrich TR-1 "Details of person subject..." | nur mit `enrich_docs=True` (1 Request/Meldung) |
| `sources/eu_oslo.py` | NORDIC | OSLO_NEWSWEB | `parse_list` | Ticker (`issuerSign`), kein ISIN | leer (PDF) | leer (PDF) |
| `sources/eu_nasdaq_nordic.py` | NORDIC | NASDAQ_NORDIC | `parse_jsonp`, `parse_items`, `parse_body`, `filer_from_headline` | Firmenname (Fallback), ISIN wenn im Text | aus Headline wenn vorhanden | nur mit `enrich_docs=True`, best effort |
| `sources/eu_amf.py` | BENELUX_FR | AMF_BDIF | `parse_informations` | AMF-`jeton` (RS...) | leer (PDF) | leer (PDF) |

Tests: `tests/test_eu_sources.py` (offline, Fixtures in `tests/fixtures/eu/`, je < 25 KB; echte, gekürzte Antworten vom 2026-11-14).

## Limitierungen

- EU-Stimmrechtsmitteilungen (TR-1, AMF-Franchissements, Flagging) enthalten KEINE Absicht (anders als 13D Item 4). Aktivismus ist nur über
  Namensabgleich des Filers gegen das Aktivisten-Register (Klasse B) erkennbar. Dafür ist der Filer nötig: vorhanden nur UK (mit enrich),
  Nasdaq Nordic (teils Headline/Text). Norwegen und Frankreich liefern ohne PDF-Parser (kein pdftotext/pypdf installiert) nur Emittent + Zeit.
- Schwellen: UK 3 % (dann je 1 %), FR 5/10/15/20/25/30/33/50/66/90/95 % u. Absicht, NO/SE/DK/FI 5 % aufwärts: kein 5 %-Gate wie bei 13D,
  Fonds/Banken (Morgan Stanley, JPM) erzeugen massiv Rauschen -> Filter Pflicht.
- Alle Backends (NSM-API, NewsWeb-API, BDIF-API, Nasdaq-Query) sind nicht offiziell als öffentliche API dokumentiert, nur von den
  offiziellen Web-Apps genutzt: Schema-/URL-Änderungen jederzeit möglich; Tests laufen offline und fangen das nicht ab. Vor Produktivnutzung ToS prüfen.
- Nasdaq: kein Datumsfilter, Historie nur bis ~10 000 Einträge zurück (~2017); Backfill = Paging ab heute. Mehrsprachige Duplikate werden
  per (Firma, Zeitpunkt) zusammengefasst.
- AMF Altbestand: `datePublication` = Migrationsdatum (Dez 2021); Adapter nimmt `dateMiseEnLigne` oder Datum + Folgetag-Regel, Event trägt `ts_migrated`.
- Der AFM-Export (NL) taugt wegen fehlender Publikationszeit nicht für Timing, könnte aber offline Filer/% nachtragen (Fuzzy-Join auf Emittent + Datum).
- DACH ist nicht abgedeckt (BaFin/Unternehmensregister FAIL, SIX FAIL); Region "DACH" bleibt ohne Adapter. UK-NSM enthält teils DE/CH-Emittenten nicht.
- Nicht verifiziert: Verhalten bei Korrekturmeldungen (NSM `latest_flag`, Oslo `correctedByMessageId` werden mitgeführt/gefiltert, aber nicht gemerged).
