"""Regressionstests für die Befunde des Quant-Reviews (2026-11-14)."""
import numpy as np
import pandas as pd
import pytest
from activist.features.universe import local_day, price_features
from activist.paper.execution import simulate_entry
from activist.study import dataset, stats
from activist.features.register import Register

IDX = pd.bdate_range("2024-01-01", periods=60)


def px(close=10.0):
    c = np.full(len(IDX), close)
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=IDX)


def test_after_close_filing_uses_same_local_day_for_features_and_entry():
    """Befund 1: 13D um 20:00 New York am Tag D -> Merkmale bis D, Einstieg D+1 (nicht UTC-Datum D+1)."""
    p = px()
    D = IDX[30]
    pub = f"{D.date()}T20:00:00-05:00"
    p.loc[IDX[31], ["open", "high", "low", "close"]] = [10.0, 30.0, 10.0, 30.0]   # Sprung am Einstiegstag
    assert local_day(pub) == D
    f = price_features(p, pub)
    assert f["runup_20d"] == pytest.approx(0.0)               # Einstiegstag-Sprung nicht in den Merkmalen
    fill = simulate_entry(p, pd.Timestamp(pub), {"limit_above_last_close": 0.03, "max_fill_days": 3,
                                                 "max_share_of_daily_volume": 0.05}, 1000)
    assert fill.fill_date == IDX[31]


def test_unadjusted_price_used_for_level_filters():
    """Befund 5: späterer 1:10-Reverse-Split darf den damaligen Kurs nicht verändern."""
    p = px(0.5)
    p["close_unadj"] = p["close"] * 1.0
    p["close"] = p["close"] * 10            # bereinigte Reihe nach Reverse-Split
    f = price_features(p, f"{IDX[40].date()}T12:00:00-05:00")
    assert f["price"] == pytest.approx(0.5) and f["price_adj"] == pytest.approx(5.0)


def test_nan_volume_means_no_fill():
    p = px(); p["volume"] = np.nan
    f = simulate_entry(p, pd.Timestamp(f"{IDX[30].date()}T20:00:00-05:00"),
                       {"limit_above_last_close": 0.03, "max_fill_days": 3, "max_share_of_daily_volume": 0.05}, 1000)
    assert not f.filled


def test_ci_with_single_cluster_fails_check():
    df = pd.DataFrame({"net_excess": [0.1] * 5, "entry_date": IDX[:5], "activist": ["A"] * 5})
    s = stats.summarize(df, reps=50)
    assert np.isnan(s["ci_low"])


def ev(i, form, pub, filer="1517137", issuer="100"):
    return {"event_id": f"E{i}", "source": "EDGAR", "region": "US", "form": form, "published": pub,
            "filer_id": filer, "issuer_id": issuer, "filer_name": "X", "url": f"u{i}"}


def test_amendment_first_is_not_an_entry():
    evs = [ev(1, "SC 13D/A", "2023-01-05T10:00:00-05:00")]
    assert dataset.campaigns(evs, Register(evs)) == []


def test_wolf_pack_same_issuer_within_30_days_counts_once():
    evs = [ev(1, "SC 13D", "2023-01-05T10:00:00-05:00", "1517137"),
           ev(2, "SC 13D", "2023-01-20T10:00:00-05:00", "1791786")]     # Elliott, gleicher Emittent
    assert len(dataset.campaigns(evs, Register(evs))) == 1
