import json
from datetime import date, datetime
from pathlib import Path
import pytest
from activist.sources import eu_de, eu_ch

FX = Path(__file__).parent / "fixtures" / "eu_de_ch"
SCHEMA = {"event_id", "source", "region", "form", "published", "has_time", "filing_date", "issuer_name",
          "issuer_id", "filer_name", "filer_id", "url"}


def load(n): return json.loads((FX / n).read_text(encoding="utf-8"))


def check(ev):
    assert SCHEMA <= set(ev) and ev["form"] == "MajorHolding" and ev["region"] == "DACH"
    assert datetime.fromisoformat(ev["published"]).tzinfo is not None


def test_parse_notification_wphg_english_eqs():
    d = eu_de.parse_notification((FX / "eqs_wphg_en.txt").read_text(encoding="utf-8"))
    assert d == {"issuer_name": "Bilfinger SE", "isin": "DE0005909006", "lei": "529900H0HULEN2BZ4604",
                 "filer_name": "Morgan Stanley", "holding_ratio": 0.0658, "previous_ratio": 0.068, "threshold_date": "2026-11-10"}


def test_parse_notification_wphg_german_synthetic():
    # synthetisch nach dem deutschen BaFin-Formular (Beschriftungen), nicht live verifiziert
    txt = """Stimmrechtsmitteilung
1. Angaben zum Emittenten
Name:
Beispiel AG
Legal Entity Identifier (LEI):
529900H0HULEN2BZ4604
2. Grund der Mitteilung
3. Angaben zum Mitteilungspflichtigen
Juristische Person: Muster Capital GmbH
Sitz, Staat: Frankfurt, Deutschland
4. Namen der Aktionäre
5. Datum der Schwellenberührung:
3 Mär 2026
6. Gesamtstimmrechtsanteile
Neu
5,01 %
0,10 %
5,11 %
10000000
Letzte Mitteilung
4,98 %
0,00 %
4,98 %
/
7. Details zu den Stimmrechtsanteilen
DE0005909006 0 500000 0 % 5,01 %
"""
    d = eu_de.parse_notification(txt)
    assert d["issuer_name"] == "Beispiel AG" and d["filer_name"] == "Muster Capital GmbH"
    assert d["holding_ratio"] == 0.0511 and d["previous_ratio"] == 0.0498
    assert d["threshold_date"] == "2026-03-03" and d["isin"] == "DE0005909006"


def test_parse_notification_austria_both_formats():
    new = eu_de.parse_notification(eu_de.article_text(load("ots_pr_eqs_at.json")))
    assert new["issuer_name"] == "Wienerberger AG" and new["filer_name"] == "Erste Asset Management GmbH"
    assert new["isin"] == "AT0000831706" and new["holding_ratio"] == 0.0382 and new["previous_ratio"] == 0.0422
    assert new["threshold_date"] == "2026-11-10"
    old = eu_de.parse_notification(eu_de.article_text(load("ots_pr_eans_old.json")))   # euro-adhoc, Pipe-Tabellen
    assert old["filer_name"] == "Marathon Asset Management Limited" and old["isin"] == "AT0000831706"
    assert old["holding_ratio"] == 0.0398 and old["previous_ratio"] == 0.0401 and old["threshold_date"] == "2021-11-30"


def test_isin_checksum_rejects_garbage():
    assert eu_de._isin_ok("DE0005909006") and not eu_de._isin_ok("DE0005909007")
    assert eu_de.parse_notification("nothing here")["isin"] is None


def test_parse_list_dedupes_english_twin_and_flags_correction():
    stubs = eu_de.parse_list(load("ots_search.json"))
    assert stubs and all("Release according" not in s["title"] for s in stubs)
    first = stubs[0]
    assert first["key"] == "EQS_20261111_EQS0015" and first["published"].startswith("2026-11-11T17:16:13+01:00")
    assert any(s["correction"] for s in stubs)
    assert any(s["key"].startswith("OTS_") for s in stubs)       # alte EANS-Meldungen werden ebenfalls erkannt


def test_build_event_schema_and_values():
    stub = eu_de.parse_list(load("ots_search.json"))[0]
    det = eu_de.parse_notification(eu_de.article_text(load("ots_pr_eqs_at.json")))
    ev = eu_de.build_event(stub, det)
    check(ev)
    assert ev["source"] == "OTS_PVR" and ev["has_time"] is True and ev["issuer_id"] == "AT0000831706"
    assert ev["holding_ratio"] == 0.0382 and ev["filer_name"] == "Erste Asset Management GmbH"
    bare = eu_de.build_event(stub)                              # ohne Detail: Fallback Slug
    assert bare["issuer_id"] == "wienerberger-ag" and "holding_ratio" not in bare


def test_fetch_events_requires_tdm_license():
    with pytest.raises(PermissionError):
        eu_de.fetch_events(date(2026, 11, 1), date(2026, 11, 7))


def test_six_parse_overview():
    evs = eu_ch.parse_overview(load("six_overview.json"))
    assert len(evs) == 3
    for e in evs: check(e)
    a = evs[0]
    assert a["source"] == "SIX_SER" and a["has_time"] is False and a["filing_date"] == "2026-11-14"
    assert a["published"].startswith("2026-11-15T00:00:00")        # Folgetag-Regel
    assert a["issuer_name"] == "Barry Callebaut AG" and a["issuer_id"] == "CH0009002962"
    assert a["filer_name"] == "BlackRock, Inc." and a["holding_ratio"] == 0.04966 and a["threshold_date"] == "2026-11-11"
    c = evs[1]                                                    # Unterschreitung, keine ISIN -> SER-Kürzel
    assert c["holding_ratio"] is None if "holding_ratio" in c else True
    assert c["issuer_id"] == "SER:CEMBRA" and c["below_threshold"] == 0.03
