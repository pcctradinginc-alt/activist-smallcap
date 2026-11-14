import json
from pathlib import Path
from activist.sources import openfigi as o

FX = Path(__file__).parent / "fixtures" / "eu"
ISINS = ["GB0007980591", "SE0000108656", "XX0000000000"]


def _parsed():
    return o.parse_response(ISINS, json.loads((FX / "openfigi_resp.json").read_text()))


def test_parse_drops_non_equity_and_handles_warning():
    p = _parsed()
    assert len(p["GB0007980591"]) == 2 and len(p["SE0000108656"]) == 2 and p["XX0000000000"] == []


def test_primary_prefers_home_exchange_and_builds_yahoo_ticker():
    p = _parsed()
    assert o.yahoo_ticker(o.primary_listing("GB0007980591", p["GB0007980591"])) == "BP.L"
    l = o.primary_listing("SE0000108656", p["SE0000108656"])
    assert l["exch"] == "SS"                       # Heimatmarkt statt Helsinki-Zweitlisting
    assert o.yahoo_ticker(l) == "ERIC-B.ST"        # Gattung B -> Yahoo-Schreibweise


def test_suffix_mapping_and_unsupported_exchange():
    for exch, suf in {"LN": ".L", "NO": ".OL", "SS": ".ST", "DC": ".CO", "FH": ".HE", "FP": ".PA", "NA": ".AS", "BB": ".BR"}.items():
        assert o.yahoo_ticker({"exch": exch, "ticker": "ABC", "name": "ABC PLC"}) == "ABC" + suf
    assert o.yahoo_ticker({"exch": "ZZ", "ticker": "ABC", "name": ""}) is None
    assert o.yahoo_ticker({"exch": "LN", "ticker": "BT/A", "name": ""}) == "BT-A.L"
    assert o.primary_listing("GB1", [{"exch": "ZZ", "ticker": "X", "type": "Common Stock"}]) is None


def test_map_isins_caches_and_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(o, "CACHE", tmp_path / "openfigi.json")
    calls = []

    def fake(batch):
        calls.append(list(batch))
        return {i: [] for i in batch} | _parsed()
    monkeypatch.setattr(o, "_post", fake)
    many = [f"GB{k:010d}" for k in range(23)]
    o.map_isins(many)
    assert [len(c) for c in calls] == [10, 10, 3]
    o.map_isins(many[:5])                           # komplett aus dem Cache
    assert len(calls) == 3
    assert o.map_isins(["GB9999999999"], use_network=False)["GB9999999999"] == []
