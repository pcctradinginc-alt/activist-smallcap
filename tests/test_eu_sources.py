import json
from datetime import date, datetime
from pathlib import Path
from activist.sources import eu_nsm, eu_oslo, eu_nasdaq_nordic, eu_amf

FX = Path(__file__).parent / "fixtures" / "eu"
SCHEMA = {"event_id", "source", "region", "form", "published", "has_time", "filing_date", "issuer_name",
          "issuer_id", "filer_name", "filer_id", "url"}


def load(n): return json.loads((FX / n).read_text(encoding="utf-8"))


def check(ev, region):
    assert SCHEMA <= set(ev) and ev["form"] == "MajorHolding" and ev["region"] == region
    assert datetime.fromisoformat(ev["published"]).tzinfo is not None


def test_nsm_parse_hits():
    evs = eu_nsm.parse_hits(load("nsm_search.json"))
    assert len(evs) == 4
    for e in evs: check(e, "UK")
    a = evs[0]
    assert a["source"] == "NSM" and a["has_time"] and a["published"].startswith("2026-11-13T18:18:15")
    assert a["issuer_name"] == "NEWRIVER REIT PLC" and a["filer_name"] == "MORGAN STANLEY"
    assert a["issuer_id"] == "2138004GX1VAUMH66L31"          # LEI als Fallback
    old = evs[-1]                                            # Altbestand: nur Datum, ISIN vorhanden
    assert old["issuer_id"] == "IE00BYTBXV33" and old["has_time"] is False
    assert old["published"].startswith("2010-10-01T00:00:00")  # Folgetag-Regel


def test_nsm_parse_tr1():
    d = eu_nsm.parse_tr1((FX / "nsm_tr1_newriver.html").read_text(encoding="utf-8"))
    assert d["isin"] == "GB00BD7XPJ64" and d["issuer_name"] == "NEWRIVER REIT PLC"
    assert d["filer_name"] == "Morgan Stanley"
    assert abs(d["holding_ratio"] - 0.05001864) < 1e-9


def test_oslo_parse_list():
    evs = eu_oslo.parse_list(load("oslo_list.json"))
    assert len(evs) == 4
    for e in evs: check(e, "NORDIC")
    e = next(x for x in evs if x["event_id"] == "OSLO_NEWSWEB:686499")
    assert e["issuer_name"] == "Nordic Mining ASA" and e["issuer_id"] == "NOM"
    assert e["published"].startswith("2026-11-13T13:23:04") and e["has_time"]


def test_nasdaq_parse_items_dedups_languages():
    items = eu_nasdaq_nordic.parse_jsonp("cb(" + json.dumps(load("nasdaq_list.json")) + ")")
    evs = eu_nasdaq_nordic.parse_items(items)
    for e in evs: check(e, "NORDIC")
    assert len(evs) < len(items)                              # fi/en bzw. da/en Duplikate zusammengefasst
    cm = next(e for e in evs if e["issuer_name"] == "ChemoMetec")
    assert cm["filer_name"] == "BlackRock, Inc." and cm["published"].startswith("2026-11-11T18:45:44")


def test_nasdaq_parse_body():
    d = eu_nasdaq_nordic.parse_body((FX / "nasdaq_view_chemometec.html").read_text(encoding="utf-8"))
    assert abs(d["holding_ratio"] - 0.1524) < 1e-9


def test_amf_parse_informations_and_migrated_timestamp():
    evs = eu_amf.parse_informations(load("amf_list.json"))
    assert len(evs) == 3
    for e in evs: check(e, "BENELUX_FR")
    new = next(e for e in evs if e["event_id"] == "AMF_BDIF:226C2066")
    assert new["issuer_name"] == "NEXANS" and new["has_time"] and new["published"].startswith("2026-11-13T16:24:30")
    old = next(e for e in evs if e["event_id"] == "AMF_BDIF:218C2062")
    assert old["has_time"] and old["published"].startswith("2018-12-28T08:20:33") and old["ts_migrated"]
    assert new["url"].endswith(".pdf")
