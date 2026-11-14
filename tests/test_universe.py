import numpy as np
import pandas as pd
import pytest
from activist.features import universe as u

CFG = {"min_price": 1.0, "min_adv": 100000, "mcap_min": 50e6, "mcap_max": 2e9}


def make_px(n=40, start="2024-01-01", seed=0):
    idx = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(seed)
    c = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({"open": c, "high": c * 1.02, "low": c * 0.98, "close": c, "volume": 50000.0}, index=idx)


def test_price_features_ignores_future_spike():
    px = make_px()
    dec = px.index[30] + pd.Timedelta(hours=15)
    a = u.price_features(px, dec)
    px2 = px.copy()
    px2.iloc[31:, px2.columns.get_indexer(["high", "close", "volume", "low", "open"])] *= 1000
    b = u.price_features(px2, dec)
    assert a["ok"] and a == b
    assert a["price"] == pytest.approx(px.close.iloc[30])


def test_price_features_tz_aware_and_insufficient_history():
    px = make_px()
    a = u.price_features(px, pd.Timestamp(px.index[30], tz="UTC"))
    assert a["ok"]
    assert u.price_features(px, px.index[10])["ok"] is False


def test_price_features_values():
    px = make_px()
    r = u.price_features(px, px.index[25])
    h = px.iloc[5:26]
    assert r["runup_20d"] == pytest.approx(h.close.iloc[-1] / h.close.iloc[0] - 1)
    assert r["adv"] == pytest.approx(float((h.close * h.volume).tail(20).median()))


@pytest.mark.parametrize("seed", range(5))
def test_corwin_schultz_nonneg_finite(seed):
    s = u.corwin_schultz(make_px(21, seed=seed))
    assert np.isfinite(s) and s >= 0


def test_corwin_schultz_flat_and_wide():
    idx = pd.bdate_range("2024-01-01", periods=21)
    flat = pd.DataFrame({"high": 10.0, "low": 10.0}, index=idx)
    assert u.corwin_schultz(flat) == 0.0
    wide = pd.DataFrame({"high": 12.0, "low": 8.0}, index=idx)
    assert np.isfinite(u.corwin_schultz(wide)) and u.corwin_schultz(wide) >= 0


GOOD = {"ok": True, "price": 10.0, "adv": 200000.0}


def test_universe_check_ok_and_each_violation():
    assert u.universe_check(CFG, GOOD, 10e6, None, False) == []
    assert u.universe_check(CFG, {"ok": False, "reason": "zu wenig Kurshistorie"}, 1, None, False) == ["zu wenig Kurshistorie"]
    assert u.universe_check(CFG, {"ok": False}, 1, None, False) == ["Kurse"]
    assert "Kurs unter Mindestkurs" in u.universe_check(CFG, {**GOOD, "price": 0.5}, 1e9, None, False)
    assert u.universe_check(CFG, {**GOOD, "adv": 1.0}, 10e6, None, False) == ["Tagesumsatz zu gering"]
    assert u.universe_check(CFG, GOOD, None, None, False) == ["Aktienzahl unbekannt"]
    assert u.universe_check(CFG, GOOD, 1e6, None, False) == ["Marktkapitalisierung außerhalb"]   # 10m < 50m
    assert u.universe_check(CFG, GOOD, 1e9, None, False) == ["Marktkapitalisierung außerhalb"]   # 10bn > 2bn
    assert u.universe_check(CFG, GOOD, 10e6, "Bank", False) == ["Bank"]
    assert u.universe_check(CFG, GOOD, 10e6, None, True) == ["OTC"]


def test_universe_check_boundaries_and_multiple():
    assert u.universe_check(CFG, {**GOOD, "price": 1.0, "adv": 100000}, 50e6, None, False) == []  # mcap 50m exactly
    v = u.universe_check(CFG, {**GOOD, "price": 0.5, "adv": 1}, None, "X", True)
    assert len(v) == 5
