"""Bestätigender Alpha-Test: Holdout-Versiegelung, Holm, nur geprüfte Daten, Protokolländerung sperrt."""
import numpy as np
import pandas as pd
import pytest
from activist.study import alpha


def trades(n_per_split=150, effect=0.06, verified=True, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for start in ("2016-01-01", "2020-06-01", "2023-03-01"):
        d = pd.date_range(start, periods=n_per_split, freq="5D")
        for i, x in enumerate(d):
            rows.append({"variant": "main", "qualified": True, "executed": True, "complete": True, "verified": verified,
                         "published": str(x.date()), "entry_date": x + pd.Timedelta(days=1), "region": "US",
                         "signal_class": "A", "activist": f"a{i % 40}", "net_excess": rng.normal(effect, 0.15),
                         "hold_days": 120})
    return pd.DataFrame(rows)


@pytest.fixture
def root(tmp_path, monkeypatch):
    for f in alpha.PROTOCOL_FILES:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_text("x")
    monkeypatch.setattr(alpha, "ROOT", tmp_path)
    monkeypatch.setattr(alpha, "FREEZE", tmp_path / "PROTOCOL_FREEZE.json")
    return tmp_path


def test_splits():
    assert alpha.split_of("2019-12-31") == "train" and alpha.split_of("2020-01-01") == "valid"
    assert alpha.split_of("2023-01-01") == "holdout"


def test_holdout_sealed_until_frozen(root):
    r = alpha.run_alpha(trades())
    assert r["status"] == "nicht beurteilbar" and r["holdout"] == {} and "versiegelt" in r["holdout_note"]


def test_strong_effect_passes_after_freeze(root):
    alpha.freeze("test")
    r = alpha.run_alpha(trades(effect=0.08))
    assert r["frozen"] and r["status"] == "nachgewiesen" and r["holdout"]["US/A"]["p_holm"] < 0.10


def test_protocol_change_after_freeze_reseals(root):
    alpha.freeze("test")
    (root / "config/params.yaml").write_text("geändert")
    r = alpha.run_alpha(trades(effect=0.08))
    assert not r["frozen"] and r["status"] == "nicht beurteilbar" and "geändert" in r["freeze"]["reason"]


def test_unverified_data_never_proves_alpha(root):
    alpha.freeze("test")
    r = alpha.run_alpha(trades(effect=0.10, verified=False))
    assert r["status"] != "nachgewiesen" and r["data_quality"]["n_campaigns_valid"] == 0


def test_noise_not_proven(root):
    alpha.freeze("test")
    r = alpha.run_alpha(trades(effect=0.0, seed=9))
    assert r["status"] == "nicht nachgewiesen"


def test_holm_adjustment():
    adj = alpha.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03) and adj["c"] == pytest.approx(0.06) and adj["b"] == pytest.approx(0.06)
