"""Branchen (SIC/Yahoo), Großaktionär und Quartalszahlen-Sperre – ohne Netz."""
from datetime import date
import pytest
from activist.risk.rules import candidate_reasons, check_portfolio, next_earnings
from activist.sources import sector, secmeta
from activist.study import holders

EARNINGS_DATES = secmeta.earnings_dates       # Original vor dem Netz-Stub der conftest

LIM = {"max_spread": 0.05, "max_runup_20d": 0.2, "max_premium_over_activist_cost": 0.3, "max_position_share_of_adv": 0.05,
       "max_major_holder": 0.5, "earnings_blackout_days": 3}


@pytest.mark.parametrize("sic,exp", [("2834", "Healthcare"), ("2860", "Basic Materials"), ("3571", "Technology"),
                                     ("3711", "Consumer Cyclical"), ("3510", "Industrials"), ("6798", "Real Estate"),
                                     ("6199", "Financial Services"), ("7372", "Technology"), ("7380", "Industrials"),
                                     ("3841", "Healthcare"), ("3670", "Technology"), ("4911", "Utilities"),
                                     ("1311", "Energy"), ("4833", "Communication Services"), ("9995", None),
                                     (None, None), ("", None), ("abc", None)])
def test_from_sic(sic, exp):
    assert sector.from_sic(sic) == exp


class Resp:
    def __init__(self, quotes): self.quotes = quotes
    def raise_for_status(self): pass
    def json(self): return {"quotes": self.quotes}


def test_yahoo_parsing_and_cache(monkeypatch):
    calls = []
    def get(url, params=None, headers=None, timeout=None):
        calls.append(params["q"])
        return Resp([{"symbol": "OTHER.T", "sector": "Energy"}, {"symbol": "7203.t", "sector": "Consumer Cyclical"}])
    monkeypatch.setattr(sector.requests, "get", get)
    assert sector.yahoo("7203.T") == "Consumer Cyclical"
    assert sector.yahoo("7203.T") == "Consumer Cyclical" and calls == ["7203.T"]       # Treffer aus dem Cache


def test_yahoo_negative_cache_and_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(sector.requests, "get", lambda *a, **k: calls.append(1) or Resp([{"symbol": "X", "sector": "Bogus"}]))
    assert sector.yahoo("X") is None and sector.yahoo("X") is None and len(calls) == 1   # None 30 Tage gecacht
    import json
    p = sector._path("X")
    p.write_text(json.dumps({"sector": None, "ts": "2000-01-01T00:00:00"}))
    assert sector.yahoo("X") is None and len(calls) == 2                                   # abgelaufen -> neu
    monkeypatch.setattr(sector.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    assert sector.yahoo("Y") is None and not sector._path("Y").exists()                    # Netzfehler nie gecacht


def ev(i, pub, filer, ratio, issuer="1234", src="EDINET", **kw):
    return {"event_id": str(i), "source": src, "region": "JP" if src == "EDINET" else "UK", "published": pub, "filer_id": filer,
            "filer_name": f"F{filer}", "issuer_sec_code": issuer, "issuer_isin": issuer, "holding_ratio": ratio, **kw}


def test_holders_latest_per_filer_window_and_exit():
    idx = holders.build_index([ev(1, "2021-01-05T10:00:00", "A", 0.60), ev(2, "2022-01-05T10:00:00", "A", 0.02),   # A stieg aus
                               ev(3, "2021-06-01T10:00:00", "B", 0.30), ev(4, "2021-07-01T10:00:00", "C", 1.0),    # 1.0 = Fehler
                               ev(5, "2017-01-01T10:00:00", "D", 0.90)])                                           # außerhalb 3 Jahre
    assert holders.major_holder(idx, ("JP", "1234"), "2023-01-01T00:00:00") == 0.30
    assert holders.major_holder(idx, ("JP", "1234"), "2021-12-31T00:00:00") == 0.60                                # A noch nicht ausgestiegen
    assert holders.major_holder(idx, ("JP", "1234"), "2021-01-05T10:00:00") is None                               # gleicher Tag zählt nicht
    assert holders.major_holder(idx, ("JP", "9999"), "2023-01-01") is None


def test_holders_eu_key_and_filer_name_fallback():
    e = ev(1, "2022-01-01T00:00:00", None, 0.4, issuer="GB0001234567", src="NSM", filer_id=None, filer_name="Big Holder Ltd.")
    idx = holders.build_index([e, dict(e, filer_name="Nobody", holding_ratio=None), dict(e, filer_name=None, filer_id=None)])
    assert holders.major_holder(idx, ("UK", "GB0001234567"), "2023-01-01") == 0.4 and len(idx[("UK", "GB0001234567")]) == 1


def test_major_holder_us_fake_parse():
    mk = lambda i, pub, f, form="SC 13G/A": {"published": pub, "filer_id": f, "form": form, "url": f"u{i}", "issuer_id": "0000123"}
    evs = {"123": [mk(1, "2022-01-01T10:00:00", "1"), mk(2, "2022-06-01T10:00:00", "1"), mk(3, "2022-03-01T10:00:00", "2"),
                   mk(4, "2022-04-01T10:00:00", "3", "8-K"), mk(5, "2023-02-01T10:00:00", "4"), mk(6, "2015-01-01T10:00:00", "5")]}
    pc = {"u1": 70.0, "u2": 6.0, "u3": 12.5, "u4": 99.0, "u5": 80.0, "u6": 60.0}
    assert holders.major_holder_us(evs, "0000123", "2023-01-01T00:00:00", lambda u: {"percent_of_class": pc[u]}) == 0.125
    assert holders.major_holder_us(evs, "123", "2023-01-01", lambda u: {"percent_of_class": None}) is None
    assert holders.major_holder_us(evs, "123", "2023-01-01", lambda u: 1 / 0) is None
    seen = []
    holders.major_holder_us(evs, "123", "2023-01-01", lambda u: seen.append(u) or {}, max_filers=1)
    assert seen == ["u2"]                                                                   # nur der jüngste Melder


def test_next_earnings():
    d = date
    assert next_earnings([], d(2023, 5, 1)) is None
    assert next_earnings([d(2023, 6, 1)], d(2023, 5, 1)) is None                             # nur Termine <= d0 (kein Look-ahead)
    assert next_earnings([d(2022, 5, 3), d(2023, 2, 1)], d(2023, 5, 1)) == d(2023, 5, 2)     # Vorjahrestermin +364
    assert next_earnings([d(2023, 2, 1)], d(2023, 4, 1)) == d(2023, 5, 3)                    # +91-Rückfall
    assert next_earnings([d(2023, 2, 1)], d(2024, 6, 1)) is None                            # Projektion liegt vor d0


def test_earnings_dates_from_submissions(monkeypatch):
    rec = {"form": ["8-K", "8-K", "10-Q", "8-K"], "items": ["2.02,9.01", "5.02", "", "7.01, 2.02"],
           "filingDate": ["2023-02-01", "2023-03-01", "2023-05-01", "2022-11-01"]}
    monkeypatch.setattr(secmeta, "_submissions", lambda cik: {"filings": {"recent": rec}})
    assert EARNINGS_DATES(5) == [date(2022, 11, 1), date(2023, 2, 1)]


def test_candidate_reasons_new_rules_and_order():
    row = {"price": 10, "adv": 1e9, "major_holder": 0.51, "days_to_earnings": 3, "activist_cost": 5}
    assert candidate_reasons(row, 1000, LIM) == ["Kurs über Aktivisten-Einstand", "Großaktionär über Grenze", "Quartalszahlen-Sperre"]
    assert candidate_reasons({**row, "activist_cost": None, "major_holder": 0.5, "days_to_earnings": 4}, 1000, LIM) == []
    assert candidate_reasons({**row, "activist_cost": None, "major_holder": None, "days_to_earnings": -1}, None, LIM) == ["Wechselkurs fehlt"]
    nan = float("nan")
    assert candidate_reasons({**row, "activist_cost": None, "major_holder": nan, "days_to_earnings": nan}, 1000, LIM) == []


def test_portfolio_unknown_sector_none_is_per_region_bucket():
    P = {"portfolio": {"max_positions": 9, "max_per_activist": 9, "max_position": 0.5, "max_sector": 0.25,
                       "max_region_once_3_regions_pass": 1.0}}
    held = [{"ticker": "H", "activist": "X", "region": "JP", "sector": None, "position_eur": 200}]
    cand = {"ticker": "N", "activist": "A", "region": "JP", "sector": None, "position_eur": 100}
    assert check_portfolio(held, cand, 1000, 1000, P) == "Branchengrenze"
    assert check_portfolio(held, dict(cand, region="US"), 1000, 1000, P) is None
