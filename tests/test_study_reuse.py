"""Trennung Simulation/Auswertung, --reuse, Entwicklungszeitraum, Vergleichsindex-Plausibilität, Survivorship ohne NaN."""
import json
from argparse import Namespace
from collections import Counter
from datetime import date
import numpy as np
import pandas as pd
import pytest
from activist import cli, common
from activist.common import params
from activist.sources import prices
from activist.study import alpha, event_study as es
from test_event_study import bench_loader, price_loader, px_for, row


def _ds():
    r = [row(1, "CRASH"), row(2, "BOOM", activist="A2"), row(3, "BOOM", activist="A3", status="verworfen: Anstieg 20T"), row(4, "ERR")]
    r.append({**row(5, "BOOM", activist="A5"), "published": "2023-03-01T10:00:00+00:00"})   # Holdout-Zeitraum
    return pd.DataFrame(r)


def _j(x):
    return json.dumps(x, default=str, sort_keys=True)


def test_csv_roundtrip_same_evaluation(tmp_path):
    t = es.simulate(_ds(), price_loader, bench_loader)
    t["verified"] = True
    t.to_csv(tmp_path / "t.csv", index=False)
    a, b = es.evaluate(t), es.evaluate(es.load_trades(tmp_path / "t.csv"))
    assert a["combos"] and _j(a["combos"]) == _j(b["combos"]) and _j(a["execution"]) == _j(b["execution"])
    assert _j(a["alpha"]["data_quality"]) == _j(b["alpha"]["data_quality"])


def test_dev_only_and_exploration_excludes_holdout():
    t = pd.DataFrame({"published": ["2022-12-31T23:00:00+00:00", "2023-01-01T00:00:00+00:00", None]})
    assert len(alpha.dev_only(t)) == 1
    x = pd.DataFrame({"a": [1]})
    assert alpha.dev_only(x) is x
    res, tr = es.run(_ds(), price_loader, bench_loader)
    assert "erkundung:main_ab2024" not in json.dumps(res["combos"])
    assert res["combos"]["US/A"]["qualifiziert:main"]["n"] == 2          # k5 (Holdout) nicht in der Erkundung
    assert res["execution"]["US/A"]["signale"] == 4                      # Zählung bleibt auf allen Daten


def test_protocol_files_complete():
    assert "src/activist/sources/prices.py" in alpha.PROTOCOL_FILES and "src/activist/learn/ranker.py" in alpha.PROTOCOL_FILES


def test_benchmark_plausibility_guard():
    px = px_for("CRASH", date(2022, 1, 1), date(2022, 6, 30))
    bench = bench_loader("US", date(2022, 1, 1), date(2022, 6, 30))
    c = row(1, "CRASH")
    ok = es.run_campaign(c, px, bench, params())
    assert ok["complete"] and ok["bench"] == 0
    bench.loc[bench.index >= "2022-03-08", "close"] = 1100.0       # ungeteilter Split im Index
    bad = es.run_campaign(c, px, bench, params())
    assert bad["executed"] and bad["complete"] is False and bad["reason"] == "Vergleichsindex unplausibel"
    t = pd.DataFrame([{**c, "variant": "main", "qualified": True, **bad}])
    md = "\n".join(es.extremes(t))
    assert "Wegen unplausiblem Vergleichsindex ausgeschlossen: 1" in md and "Keine abgeschlossenen" in md


def test_extremes_lists_large_excess():
    t = pd.DataFrame([dict(variant="main", executed=True, complete=True, ticker="X", region="JP", published="2022-01-02", gross=9.0,
                           bench=0.1, net_excess=8.9)])
    assert "| X | JP | 2022-01-02 |" in "\n".join(es.extremes(t))


def test_survivorship_nan_regression():
    ds = pd.DataFrame([
        dict(status="nur Endwert", terminal_type="cash_merger", activist_cost=np.nan, terminal_cash=np.nan, published="2022-03-01T10:00:00+00:00"),
        dict(status="nur Endwert", terminal_type="going_private", activist_cost=10.0, terminal_cash=np.nan, published="2022-03-01T10:00:00+00:00"),
        dict(status="nur Endwert", terminal_type="cash_merger", activist_cost=10.0, terminal_cash=13.0, published="2022-03-01T10:00:00+00:00",
             terminal_date="2022-05-02"),
        dict(status="nur Endwert", terminal_type="bankruptcy", published="2022-03-01T10:00:00+00:00"),
        dict(status="qualifiziert", published="2022-03-01T10:00:00+00:00")])
    tr = pd.DataFrame([dict(variant="main", qualified=True, executed=True, complete=True, published="2022-03-01", net_excess=v)
                       for v in (0.1, -0.2, np.nan, 0.3)])
    L = es.survivorship(ds, tr, bench_loader, params())
    txt = "\n".join(L)
    assert L and "nan" not in txt.lower()
    lo, hi = [float(x.strip("%+").replace(" ", "")) for x in txt.split("**")[1].split(" bis ")]
    assert np.isfinite([lo, hi]).all() and lo <= hi
    assert es.survivorship(ds, tr.assign(net_excess=np.nan), bench_loader, params()) == []
    def broken(*a):
        raise RuntimeError("kein Index")
    assert "übersprungen: 2" in "\n".join(es.survivorship(ds, tr, broken, params()))


def test_cmd_study_reuse(tmp_path, monkeypatch):
    for mod in (common, es):
        monkeypatch.setattr(mod, "REPORTS", tmp_path)
    def no_net(*a, **k):
        raise AssertionError("Netzwerkzugriff")
    monkeypatch.setattr(prices, "load", no_net); monkeypatch.setattr(prices, "yahoo", no_net)
    args = Namespace(reuse=True, main_only=False, since=None, no_parse=False)
    with pytest.raises(SystemExit) as e:
        cli.cmd_study(args)
    assert e.value.code == 1
    ds = _ds()
    res, t = es.run(ds, price_loader, bench_loader)
    es.write_report(res, Counter({"kampagnen": 5}), ds, t, path=tmp_path / "event_study_20200101.md")
    cli.cmd_study(args)
    out = tmp_path / f"event_study_{date.today():%Y%m%d}.md"
    assert out.exists() and "- kampagnen: 5" in out.read_text(encoding="utf-8")
    assert len(pd.read_csv(tmp_path / f"event_study_{date.today():%Y%m%d}_trades.csv")) == len(t)
