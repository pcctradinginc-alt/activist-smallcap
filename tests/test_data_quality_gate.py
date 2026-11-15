from datetime import date
import pandas as pd
from activist.sources import hist_ticker
from activist.study import dataset


def _px(v=10.0):
    return pd.DataFrame({"close": v, "close_unadj": v}, index=pd.bdate_range("2020-01-01", periods=30))


D0 = date(2020, 1, 15)


def test_ticker_certain_survivorship(monkeypatch):
    row = {"ticker_source": "hist:10-K", "price_source": "tiingo"}
    assert dataset._ticker_certain(row, _px(), D0, "1", "ABC") is True and row["ticker_check"] == "hist+einstand"


def test_ticker_certain_einstand(monkeypatch):
    monkeypatch.setattr(hist_ticker, "ticker_hist", lambda *a: (_ for _ in ()).throw(AssertionError("kein Abruf nötig")))
    row = {"activist_cost": 10.5, "price_source": "yahoo"}
    assert dataset._ticker_certain(row, _px(), D0, "1", "ABC") is True and row["ticker_check"] == "einstand"


def test_ticker_certain_hist_normalised(monkeypatch):
    monkeypatch.setattr(hist_ticker, "ticker_hist", lambda *a: {"ticker": "brk.b"})
    row = {"activist_cost": 99.0}
    assert dataset._ticker_certain(row, _px(), D0, "1", "BRK-B") is True and row["ticker_check"] == "hist"


def test_ticker_certain_false(monkeypatch):
    monkeypatch.setattr(hist_ticker, "ticker_hist", lambda *a: {"ticker": "OLD"})
    row = {}
    assert dataset._ticker_certain(row, _px(), D0, "1", "NEW") is False and row["ticker_check"] is None
    def boom(*a): raise RuntimeError("net")
    monkeypatch.setattr(hist_ticker, "ticker_hist", boom)
    assert dataset._ticker_certain({}, _px(), D0, "1", "NEW") is False


def test_validate_against_prefers_unadj():
    px = pd.DataFrame({"close": 1.0, "close_raw": 2.0, "close_unadj": 10.0}, index=pd.bdate_range("2020-01-01", periods=30))
    from activist.sources import prices
    assert prices.validate_against(px, D0, 10.0) and not prices.validate_against(px, D0, 1.0)


def test_data_quality_requires_checked_prices():
    row = {"price_check": None, "mcap": 1e9}
    dq = dataset.data_quality(row, ticker_certain=True, activist_evidence=True)
    assert not dq["verified"] and "Kurse doppelt geprüft" in dq["dq_missing"]
    assert dataset.data_quality({**row, "price_check": True}, True, True)["verified"]
    assert not dataset.data_quality({**row, "price_check": True}, False, True)["verified"]
