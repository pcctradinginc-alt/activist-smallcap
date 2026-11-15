import json
import numpy as np
import pandas as pd
from activist.learn import ranker as R


def _mk(n, seed, stake_effect=0.0, start="2020-01-01"):
    rng = np.random.default_rng(seed)
    pub = pd.Timestamp(start) + pd.to_timedelta(np.arange(n) * 3, unit="D")
    stake = rng.uniform(0.01, 0.15, n)
    y = stake_effect * (stake - 0.08) + rng.normal(0, 0.15, n)
    return pd.DataFrame({
        "campaign_key": [f"c{seed}_{i}" for i in range(n)],
        "published": pub.strftime("%Y-%m-%d"),
        "entry_date": (pub + pd.Timedelta(days=2)).strftime("%Y-%m-%d"),
        "exit_date": (pub + pd.Timedelta(days=100)).strftime("%Y-%m-%d"),
        "net_excess": y, "region": "US", "signal_class": "A",
        "activist": [f"act{i}" for i in rng.integers(0, 40, n)],
        "demand": rng.choice(["board", "sale", "buyback"], n),
        "stake": stake, "runup_20d": rng.normal(0, 0.1, n), "adv_eur": rng.lognormal(12, 1, n),
        "activist_prior_n": 0, "activist_prior_mean": np.nan})


def test_pit_no_leak():
    df = pd.DataFrame({
        "published": ["2020-01-01", "2020-06-01", "2021-01-01"],
        "exit_date": ["2020-03-01", "2020-09-01", "2021-03-01"],
        "net_excess": [0.1, 0.2, 5.0],  # last row: future winner
        "activist": ["A", "A", "A"]})
    out = R.pit_activist_record(df)
    assert list(out["activist_prior_n"]) == [0, 1, 2]
    assert np.isnan(out["activist_prior_mean"][0])
    # row 1: one prior (0.1), global mean 0.1
    assert abs(out["activist_prior_mean"][1] - 0.1) < 1e-12
    # row 2 shrunk (n=2,k=10) toward global 0.15 -> 0.15 + 0.2*(0.15-0.15)
    assert abs(out["activist_prior_mean"][2] - 0.15) < 1e-12
    # changing the future row must not change earlier rows
    df2 = df.copy(); df2.loc[2, "net_excess"] = -9.0
    out2 = R.pit_activist_record(df2)
    assert out.loc[:1, "activist_prior_mean"].equals(out2.loc[:1, "activist_prior_mean"]) or \
        np.allclose(out.loc[1:1, "activist_prior_mean"], out2.loc[1:1, "activist_prior_mean"])


def test_card_depends_only_on_train():
    tr, va = _mk(300, 1), _mk(150, 2)
    c1 = R.fit_scorecard(tr)
    va2 = va.assign(net_excess=va["net_excess"] + 9, stake=va["stake"] * 3)
    assert R.fit_scorecard(tr) == c1
    d1 = R.decide(tr, va, reps=200); d2 = R.decide(tr, va2, reps=200)
    assert d1["card"] == d2["card"]
    json.dumps(c1); json.dumps(d1)


def test_categorical_other_and_missing():
    tr = _mk(300, 3)
    tr.loc[:4, "demand"] = "rare"
    card = R.fit_scorecard(tr)
    assert "rare" not in card["features"]["demand"]["categories"]
    assert "other" in card["features"]["demand"]["buckets"]
    te = tr.head(3).copy(); te["stake"] = np.nan
    s1 = R.score(te, card)
    te2 = te.drop(columns=["stake"]); assert np.allclose(s1, R.score(te2, card))


def test_insufficient():
    d = R.decide(_mk(80, 1), _mk(50, 2))
    assert d["status"] == R.ST_INSUFFICIENT and d["active"] is False
    d = R.decide(_mk(90, 1), _mk(300, 2))
    assert d["status"] == R.ST_INSUFFICIENT


def test_stake_matters():
    d = R.decide(_mk(600, 11, stake_effect=1.5), _mk(600, 12, stake_effect=1.5, start="2025-01-01"), reps=500)
    assert d["status"] == R.ST_CHALLENGER, d["evaluation"]
    assert d["evaluation"]["diff"] > 0 and d["evaluation"]["p_value"] < 0.1
    assert d["active"] is False


def test_noise_keeps_base():
    d = R.decide(_mk(400, 21), _mk(400, 22, start="2025-01-01"), reps=500, seed=3)
    assert d["status"] == R.ST_BASE
    assert d["active"] is False


def test_select_quantile_share():
    tr = _mk(300, 5)
    card = R.fit_scorecard(tr)
    assert 0.4 < R.select(tr, card).mean() < 0.7


def test_missing_feature_is_neutral():
    import numpy as np, pandas as pd
    from activist.learn import ranker
    rng = np.random.default_rng(1)
    n = 150
    df = pd.DataFrame({"stake": rng.uniform(0.05, 0.2, n), "net_excess": rng.normal(0.02, 0.1, n),
                       "activist": [f"a{i % 30}" for i in range(n)],
                       "published": pd.date_range("2015-01-01", periods=n, freq="7D").astype(str)})
    card = ranker.fit_scorecard(df)
    assert ranker.score(pd.DataFrame({"stake": [np.nan]}), card).iloc[0] == 0.0


def _fa_df(effect, n=240, seed=2):
    import numpy as np, pandas as pd
    rng = np.random.default_rng(seed)
    stake = rng.uniform(0.05, 0.25, n)
    return pd.DataFrame({"stake": stake, "net_excess": effect * (stake - 0.15) * 10 + rng.normal(0, 0.05, n),
                         "activist": [f"a{i % 40}" for i in range(n)],
                         "demand": rng.choice(["none", "sale"], n)})


def test_feature_analysis_detects_real_effect_only():
    from activist.learn import ranker
    assert ranker.feature_analysis(_fa_df(0.3))["stake"]["status"] == "belastbar"
    assert ranker.feature_analysis(_fa_df(0.0))["stake"]["status"] == "nicht belastbar"


def test_feature_analysis_small_sample_and_fixed_demand_contrast():
    from activist.learn import ranker
    r = ranker.feature_analysis(_fa_df(0.3, n=40))
    assert r["stake"]["status"] == "zu wenig Daten"
    r2 = ranker.feature_analysis(_fa_df(0.0))
    assert r2["demand"].get("compare") in ("Forderung − keine", None)


def test_challenger_holdout_needs_min_sample():
    from activist.learn import ranker
    assert ranker.challenger_holdout({}, _fa_df(0.0, n=10))["status"] == ranker.ST_INSUFFICIENT
