import numpy as np
import pandas as pd
from activist.study.stats import verdict, summarize

P = {"study": {"min_campaigns": 100, "ci_level": 0.90, "bootstrap_reps": 300, "pass_min_mean": 0.03,
               "econ_portfolio_eur": 10000, "econ_min_excess_eur": 300},
     "order": {}}


def make(n, loc, seed=1, scale=0.05):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "net_excess": rng.normal(loc, scale, n),
        "activist": [f"A{i % 12}" for i in range(n)],
        "entry_date": pd.date_range("2020-01-01", periods=n, freq="9D"),
    })


def test_verdict_pass():
    v = verdict(make(120, 0.12), years=3.0, p=P)
    assert v["status"] == "bestanden", v
    assert all(v["checks"].values())


def test_verdict_not_assessable_small_n():
    v = verdict(make(30, 0.12), years=1.0, p=P)
    assert v["status"] == "nicht beurteilbar"
    assert v["thin_data"] is False  # 30 >= 20


def test_verdict_fail_negative():
    v = verdict(make(120, -0.10), years=3.0, p=P)
    assert v["status"] == "nicht bestanden"
    assert not v["checks"]["mean>=3%"]
    assert not v["checks"]["ci_low>0"]


def test_verdict_fail_second_half_negative():
    df = make(120, 0.12)
    df = df.sort_values("entry_date").reset_index(drop=True)
    df.loc[60:, "net_excess"] = -0.5
    df.loc[:59, "net_excess"] += 0.6
    v = verdict(df, 3.0, P)
    assert v["status"] == "nicht bestanden"
    assert not v["checks"]["both_halves_positive"]


def test_verdict_empty():
    v = verdict(make(0, 0.0), 1.0, P)
    assert v["status"] == "keine Daten"


def test_summarize_basic():
    s = summarize(make(40, 0.05), reps=200)
    assert s["n"] == 40 and s["ci_low"] <= s["mean"] <= s["ci_high"]
    assert s["worst"] <= s["worst_quartile"]
    assert summarize(make(10, 0.05), reps=100)["thin_data"] is True
