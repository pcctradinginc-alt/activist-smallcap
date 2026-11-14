import json
from pathlib import Path
import pytest
from activist import common
from activist.sources import secmeta

FIX = Path(__file__).parent / "fixtures" / "secmeta"


@pytest.fixture
def net(tmp_path, monkeypatch):
    """Offline: Cache in tmp, _fetch_json liefert Fixtures; calls zaehlt mit."""
    monkeypatch.setattr(common, "DATA", tmp_path)
    store = {"calls": []}

    def fake(url):
        store["calls"].append(url)
        for key, doc in store.get("docs", {}).items():
            if key in url:
                return doc
        raise AssertionError("unexpected url " + url)
    monkeypatch.setattr(secmeta, "_fetch_json", fake)
    store["docs"] = {}
    return store


def F(end, val, filed, form="10-K", fp="FY", start=None):
    e = {"end": end, "val": val, "filed": filed, "form": form, "fp": fp, "accn": "x"}
    if start:
        e["start"] = start
    return e


def facts(**concepts):
    out = {"cik": 1, "facts": {"dei": {}, "us-gaap": {}}}
    for name, (tax, unit, ents) in concepts.items():
        out["facts"][tax][name] = {"units": {unit: ents}}
    return out


def test_company_real_fixture_and_cache(net):
    net["docs"]["submissions"] = json.loads((FIX / "submissions_1589094.json").read_text())
    c = secmeta.company(1589094)
    assert c["cik"] == "0001589094" and c["name"] == "RIGHTSIDE GROUP, LTD."
    assert c["sic"] == "7389" and c["tickers"] == []
    secmeta.company("0001589094")
    assert len(net["calls"]) == 1                      # zweiter Aufruf aus dem Cache
    assert (common.DATA / "cache" / "sec" / "submissions_0001589094.json").exists()


def test_ticker_none_for_delisted(net):
    net["docs"]["submissions"] = json.loads((FIX / "submissions_1589094.json").read_text())
    assert secmeta.ticker_asof(1589094, "2020-01-01") is None


@pytest.mark.parametrize("exch,otc", [(["Nasdaq"], False), (["OTC"], True), ([], True), ([None], True)])
def test_ticker_otc_flag(net, exch, otc):
    net["docs"]["submissions"] = {"name": "X", "tickers": ["XX"], "exchanges": exch, "sic": "3600"}
    t = secmeta.ticker_asof(5, "2024-01-01")
    assert t["ticker"] == "XX" and t["otc"] is otc


def test_shares_pit_ignores_later_filings(net):
    net["docs"]["companyfacts"] = facts(EntityCommonStockSharesOutstanding=("dei", "shares", [
        F("2022-12-31", 100, "2023-03-01"), F("2023-03-31", 110, "2023-05-10", "10-Q", "Q1"),
        F("2023-12-31", 999, "2024-03-01")]))
    assert secmeta.shares_outstanding_asof(5, "2023-04-01") == (100, "2023-03-01", "2022-12-31")
    assert secmeta.shares_outstanding_asof(5, "2023-05-10")[0] == 110     # filed == Stichtag zaehlt
    assert secmeta.shares_outstanding_asof(5, "2023-02-28") is None


def test_shares_fallback_gaap(net):
    net["docs"]["companyfacts"] = facts(CommonStockSharesOutstanding=("us-gaap", "shares", [
        F("2022-12-31", 70, "2023-03-01")]))
    assert secmeta.shares_outstanding_asof(5, "2023-06-01") == (70, "2023-03-01", "2022-12-31")


def test_restatement_after_date_not_used(net):
    net["docs"]["companyfacts"] = facts(
        CashAndCashEquivalentsAtCarryingValue=("us-gaap", "USD", [
            F("2022-12-31", 50, "2023-03-01"),
            F("2022-12-31", 5, "2023-11-01", "10-K/A")]),            # Restatement spaeter
        OperatingIncomeLoss=("us-gaap", "USD", [
            F("2022-12-31", 10, "2023-03-01", start="2022-01-01"),
            F("2022-12-31", 7, "2023-11-01", "10-K/A", start="2022-01-01")]))
    r = secmeta.fundamentals_asof(5, "2023-06-30")
    assert r["cash"]["value"] == 50 and r["operating_income"]["value"] == 10
    assert r["age_days"] == 121
    assert secmeta.fundamentals_asof(5, "2023-12-01")["cash"]["value"] == 5


def test_comparative_same_filing_picks_current_period(net):
    net["docs"]["companyfacts"] = facts(StockholdersEquity=("us-gaap", "USD", [
        F("2021-12-31", 80, "2023-03-01"), F("2022-12-31", 90, "2023-03-01")]))
    assert secmeta.fundamentals_asof(5, "2023-04-01")["equity"]["end"] == "2022-12-31"


def test_debt_partial_and_missing(net):
    net["docs"]["companyfacts"] = facts(LongTermDebt=("us-gaap", "USD", [F("2022-12-31", 30, "2023-03-01")]))
    d = secmeta.fundamentals_asof(5, "2023-04-01")["debt"]
    assert d["value"] == 30 and d["partial"] is True
    net["docs"]["companyfacts"] = facts(
        LongTermDebt=("us-gaap", "USD", [F("2022-12-31", 30, "2023-03-01")]),
        LongTermDebtCurrent=("us-gaap", "USD", [F("2022-12-31", 5, "2023-03-01")]))
    secmeta._cache_path("companyfacts", "0000000005").unlink()
    d = secmeta.fundamentals_asof(5, "2023-04-01")["debt"]
    assert d["value"] == 35 and d["partial"] is False
    net["docs"]["companyfacts"] = facts()
    secmeta._cache_path("companyfacts", "0000000005").unlink()
    r = secmeta.fundamentals_asof(5, "2023-04-01")
    assert r["debt"]["value"] is None and r["age_days"] is None and r["cash"] is None


def test_operating_income_annual_only(net):
    net["docs"]["companyfacts"] = facts(OperatingIncomeLoss=("us-gaap", "USD", [
        F("2022-12-31", 10, "2023-03-01", start="2022-01-01"),
        F("2023-03-31", 99, "2023-05-01", "10-Q", "Q1", start="2023-01-01")]))
    assert secmeta.fundamentals_asof(5, "2023-06-01")["operating_income"]["value"] == 10


def test_real_companyfacts_fixture(net):
    net["docs"]["companyfacts"] = json.loads((FIX / "companyfacts_1589094.json").read_text())
    early, late = secmeta.fundamentals_asof(1589094, "2015-01-01"), secmeta.fundamentals_asof(1589094, "2016-12-31")
    assert (early["cash"] is None) or early["cash"]["filed"] <= "2015-01-01"
    assert late["cash"] is not None and late["cash"]["filed"] <= "2016-12-31"
    s = secmeta.shares_outstanding_asof(1589094, "2016-12-31")
    assert s is None or s[1] <= "2016-12-31"


@pytest.mark.parametrize("sic,reason", [
    (6020, "Banken"), ("6199", "Banken"), (6200, None), (6300, "Versicherer"), (6411, "Versicherer"),
    (6412, None), (6770, "Maentel (Blank-Check/Shell)"), (3600, None), (None, None), ("", None)])
def test_excluded_industry(sic, reason):
    assert secmeta.excluded_industry(sic) == reason
