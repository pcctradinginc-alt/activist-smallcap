"""Challenger im Schattenmodus: nur Daten bis zum Entscheidungstag, kein Einfluss auf Handel/Regeln, Bericht zeigt 3 Angaben."""
import json
from datetime import date
import pandas as pd
import pytest
from activist.paper import portfolio as pf


@pytest.fixture
def reports(tmp_path, monkeypatch):
    import activist.common as common
    monkeypatch.setattr(common, "REPORTS", tmp_path)
    card = {"features": {"activist_prior_mean": {"type": "numeric", "edges": [0.0],
                                                 "buckets": {"0": {"value": -0.10, "n": 50}, "1": {"value": 0.20, "n": 50}}}},
            "train_mean": 0.02, "threshold": 0.05, "n_train": 100}
    (tmp_path / "scorecard.json").write_text(json.dumps({"status": "x", "card": card}))
    pd.DataFrame([{"activist": "Good", "exit_date": "2025-01-01", "net_excess": 0.5},
                  {"activist": "Bad", "exit_date": "2025-01-01", "net_excess": -0.5},
                  {"activist": "Bad", "exit_date": "2030-01-01", "net_excess": 9.9}]       # Zukunft: darf nicht zählen
                 ).to_csv(tmp_path / "campaign_outcomes.csv", index=False)
    return tmp_path


def test_challenger_uses_only_past_outcomes(reports):
    good = pf.challenger_view({"activist": "Good"}, date(2026, 1, 1), 1.0)
    bad = pf.challenger_view({"activist": "Bad"}, date(2026, 1, 1), 1.0)
    assert good["challenger_selected"] is True and bad["challenger_selected"] is False   # 9,9 aus 2030 ignoriert


def test_no_scorecard_means_no_challenger(tmp_path, monkeypatch):
    import activist.common as common
    monkeypatch.setattr(common, "REPORTS", tmp_path)
    assert pf.challenger_view({"activist": "X"}, date(2026, 1, 1), 1.0)["challenger_selected"] is None


def test_challenger_never_blocks_or_sizes_orders(reports, tmp_path, monkeypatch):
    """Auch ein vom Challenger abgelehnter Kandidat wird von der Basisstrategie normal geordert."""
    j = tmp_path / "j"; j.mkdir()
    monkeypatch.setattr(pf, "STATE", j / "portfolio.json"); monkeypatch.setattr(pf, "TRADES", j / "trades.csv")
    monkeypatch.setattr(pf, "SHADOW", j / "shadow.csv"); monkeypatch.setattr(pf, "JOURNAL", j)
    monkeypatch.setattr(pf, "validated_combos", lambda: set())
    monkeypatch.setattr(pf, "eur_per_unit", lambda *a: 1.0)
    rows = pd.DataFrame([{"campaign_key": "K1", "status": "qualifiziert", "ticker": "T1", "region": "US", "signal_class": "A",
                          "activist": "Bad", "published": "2026-01-02T20:00:00-05:00", "event_id": "E1", "price": 10.0}])
    st = pf.load_state()
    pf.new_signals(st, date(2026, 1, 3), pf.params(), [], "2026-01-01", rows, fx=lambda *a: 1.0)
    (o,) = st["orders"].values()
    assert o["challenger_selected"] is False and o["position_eur"] == pf.params()["order"]["position_eur"]
