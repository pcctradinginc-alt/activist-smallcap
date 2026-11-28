import json
from pathlib import Path
import pytest
from activist import common
from activist.sources import hist_ticker as h, secmeta

FIX = Path(__file__).parent / "fixtures" / "hist_ticker"
# CIK -> Submissions-Fixture; Dokumente nach Accession
SUBS = {"1087423": "sub_1087423.json", "98246": "sub_98246.json", "1589094": "sub_1589094.json",
        "1591877": "sub_1591877.json"}
DOCS = {"0001087423-18-000010": "rht_2018_10k.htm", "0001193125-08-095150": "rht_2008_10k.htm",
        "0000098246-20-000042": "tif_2020_10k.htm", "0001564590-17-004423": "rightside_2016_10k.htm",
        "0001178913-16-004662": "sizmek_2015_10k.htm"}


@pytest.fixture
def net(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "DATA", tmp_path)
    monkeypatch.setattr(secmeta.time, "sleep", lambda s: None)
    calls = {"json": [], "docs": []}

    def fake_json(url):
        calls["json"].append(url)
        if "submissions-001" in url:
            return json.loads((FIX / "sub_1087423_p1.json").read_text())
        for cik, fn in SUBS.items():
            if f"CIK{int(cik):010d}.json" in url:
                return json.loads((FIX / fn).read_text())
        raise AssertionError(url)

    def fake_doc(url):
        calls["docs"].append(url)
        acc = [a for a in DOCS if a.replace("-", "") in url]
        if not acc:
            raise AssertionError(url)
        return (FIX / DOCS[acc[0]]).read_text(encoding="utf-8")
    monkeypatch.setattr(secmeta, "_fetch_json", fake_json)
    monkeypatch.setattr(h, "_fetch_doc", fake_doc)
    return calls


def test_text_pattern_pre_ixbrl_red_hat(net):
    r = h.ticker_hist(1087423, "2019-01-15")
    assert (r["ticker"], r["exchange"], r["source"]) == ("RHT", "NYSE", "text_pattern")
    assert r["filing"] == "0001087423-18-000010"
    assert "RHT" in r["evidence"]


def test_ixbrl_dei_tiffany(net):
    r = h.ticker_hist("0000098246", "2020-06-30")
    assert (r["ticker"], r["exchange"], r["source"]) == ("TIF", "NYSE", "ixbrl_dei")
    assert r["filing"] == "0000098246-20-000042"


def test_delisted_rightside_and_sizmek(net):
    r = h.ticker_hist(1589094, "2017-06-01")
    assert (r["ticker"], r["exchange"]) == ("NAME", "NASDAQ")
    r = h.ticker_hist(1591877, "2016-06-01")
    assert (r["ticker"], r["exchange"]) == ("SZMK", "NASDAQ")


def test_extra_submission_pages_loaded(net):
    r = h.ticker_hist(1087423, "2008-09-01")
    assert r["ticker"] == "RHT" and r["filing"] == "0001193125-08-095150"
    assert any("submissions-001" in u for u in net["json"])


def test_window_rules(net):
    # 10-K 2018-04-26; Stichtag 2017-01-01 liegt davor (<200d nach) -> nicht; 2016-10-xx hat 10-K 2016-04 (<400d)
    rows = h._all_filings(1087423, h.secmeta._d("2016-01-01"), h.secmeta._d("2019-12-31"))
    c = h._candidates(rows, h.secmeta._d("2018-05-10"))
    assert c[0]["accession"] == "0001087423-18-000010"            # davor, naechster 10-K
    assert h._candidates(rows, h.secmeta._d("2025-01-01")) == []  # weit nach allem
    after = h._candidates(rows, h.secmeta._d("2018-02-01"))       # 10-K 2018-04-26 liegt 84d danach, 2017-04 395d davor
    assert after[0]["accession"] == "0001193125-17-139562"        # Vorlauf hat Vorrang


def test_none_when_no_filing_and_cache(net):
    r = h.ticker_hist(98246, "2030-01-01")
    assert r["ticker"] is None and r["source"] == "none"
    n = len(net["docs"])
    assert h.ticker_hist(98246, "2030-01-20") == r      # unabhängiger Tagescache
    assert len(net["docs"]) == n
    cache = json.loads((common.DATA / "cache" / "hist_ticker" / "0000098246.json").read_text())
    assert "pit-v2:2030-01-01" in cache and "pit-v2:2030-01-20" in cache


def test_cache_hit_skips_network(net):
    a = h.ticker_hist(98246, "2020-06-30")
    nd = len(net["docs"])
    assert h.ticker_hist(98246, "2020-06-30") == a
    assert len(net["docs"]) == nd


def test_network_error_not_cached(net, monkeypatch):
    def boom(url):
        raise RuntimeError("503")
    monkeypatch.setattr(h, "_fetch_doc", boom)
    r = h.ticker_hist(98246, "2020-06-30")
    assert r["ticker"] is None
    assert not (common.DATA / "cache" / "hist_ticker" / "0000098246.json").exists()


# ---------------------------------------------------------------- Extraktor (synthetisch)
def test_cover_table():
    raw = ("<table><tr><td>Title of each class</td><td>Trading Symbol(s)</td><td>Name of each exchange on which registered</td></tr>"
           "<tr><td>Common Stock, $0.01 par value</td><td>ABCD</td><td>The NASDAQ Stock Market LLC</td></tr>"
           "<tr><td>Warrants to purchase common stock</td><td>ABCDW</td><td>The NASDAQ Stock Market LLC</td></tr></table>")
    r = h.extract_ticker(raw)
    assert (r["ticker"], r["exchange"], r["source"]) == ("ABCD", "NASDAQ", "cover_table")


def test_units_warrants_skipped_in_ixbrl():
    raw = ('<ix:nonNumeric name="dei:TradingSymbol" contextRef="a">XYZ.U</ix:nonNumeric>'
           '<ix:nonNumeric name="dei:TradingSymbol" contextRef="b">XYZ</ix:nonNumeric>'
           '<ix:nonNumeric name="dei:TradingSymbol" contextRef="c">XYZ WS</ix:nonNumeric>')
    assert h.extract_ticker(raw)["ticker"] == "XYZ"


def test_colon_pattern_and_false_positives():
    assert h.extract_ticker("<p>Acme Corp. (Nasdaq: ACME) announced</p>")["ticker"] == "ACME"
    r = h.extract_ticker("Our 5% notes are listed on the New York Stock Exchange under the symbol \"ACN\". "
                         "Our common stock trades on the NYSE American under the symbol \"ACM\".")
    assert r["ticker"] == "ACM" and r["exchange"] == "NYSE American"
    assert h.extract_ticker("see the symbol of the company and the other things") is None


def test_no_future_or_same_day_ticker_evidence():
    rows = [{"form":"10-K","date":d,"doc":"x","accession":d} for d in
            ("2020-01-01","2020-02-01","2020-02-02")]
    assert [x["date"] for x in h._candidates(rows,h.secmeta._d("2020-02-01"))] == ["2020-01-01"]
