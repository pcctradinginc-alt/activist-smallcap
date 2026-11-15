import json
from collections import Counter
import pandas as pd
import pytest
from activist.study import event_study as es

PUB = "2022-03-01T10:00:00+00:00"


def px_for(ticker, start, end):
    idx = pd.bdate_range(start, end)
    c = pd.Series(10.0, index=idx)
    if ticker == "CRASH":
        c[idx >= "2022-03-10"] = 6.0
        c[idx >= "2022-04-15"] = 13.0
    if ticker == "BOOM":
        c[idx >= "2022-04-01"] = 12.0
    if ticker == "SHORT":
        c = c[c.index <= "2022-03-20"]
    return pd.DataFrame({"open": c, "high": c * 1.003, "low": c * 0.997, "close": c, "volume": 100000.0})


def price_loader(ticker, region, start, end):
    if ticker == "ERR":
        raise RuntimeError("no data")
    return px_for(ticker, start, end)


def bench_loader(region, start, end):
    idx = pd.bdate_range(start, end)
    return pd.DataFrame({"close": 100.0}, index=idx)


def row(i, ticker, status="qualifiziert", activist="A1", exit_events=None):
    return dict(campaign_key=f"k{i}", activist=activist, filer_id=activist, region="US", signal_class="A", status=status,
                ticker=ticker, published=PUB, demand="x", half_spread=0.005, exit_events=exit_events or [])


@pytest.fixture
def ds():
    return pd.DataFrame([row(1, "CRASH"), row(2, "BOOM", activist="A2"), row(3, "BOOM", activist="A3"),
                         row(4, "SHORT", activist="A4"),
                         row(5, "BOOM", status="verworfen: Anstieg 20T", activist="A5"),
                         row(6, "BOOM", status="außerhalb Universum: OTC"), row(7, "ERR"),
                         row(8, "BOOM", status="kein Ticker")])


def test_run_variants_main_uses_stop_and_trades(ds):
    res, t = es.run(ds, price_loader, bench_loader)
    assert set(t.variant) == {"main", "no_stop", "hold_21", "hold_63", "hold_126", "hold_252"}
    assert set(t.campaign_key) == {"k1", "k2", "k3", "k4", "k5", "k7"}   # Ladefehler wird als nicht ausgeführtes Signal gezählt
    assert not t[t.campaign_key == "k7"].executed.any()
    crash = t[t.campaign_key == "k1"].set_index("variant")
    assert crash.loc["main", "exit_reason"] == "Stopp"
    assert crash.loc["main", "gross"] == pytest.approx(-0.4)
    assert crash.loc["no_stop", "exit_reason"] == "Haltedauer"
    assert crash.loc["no_stop", "gross"] == pytest.approx(0.3)
    assert crash.loc["hold_21", "exit_reason"] == "Haltedauer"
    main = t[t.variant == "main"].set_index("campaign_key")
    assert bool(main.loc["k1", "complete"]) and not main.loc["k4", "complete"]
    assert main.loc["k4", "exit_reason"] == "offen (Datenende)"
    from activist.common import params as _p
    fee = _p()["costs"]["fixed_fee_eur_per_order"] / _p()["order"]["position_eur"]
    cost = 2 * (0.005 + 0.0025 + fee)
    assert crash.loc["main", "net_excess"] == pytest.approx(-0.4 - cost)


def test_run_results_count_only_complete_trades(ds):
    res, t = es.run(ds, price_loader, bench_loader)
    c = res["combos"]["US/A"]
    assert c["qualifiziert:main"]["n"] == 3          # k1, k2, k3 complete; k4 open (Datenende)
    assert res["execution"]["US/A"] == {"signale": 5, "ausgeführt": 4, "offen": 1}
    assert c["schatten:main"]["n"] == 1
    assert "qualifiziert:no_stop" in c
    assert c["qualifiziert:no_stop"]["mean"] > c["qualifiziert:main"]["mean"]   # stop cut k1 at -40 %
    assert c["qualifiziert:main"]["status"] == "nicht beurteilbar"             # n < min_campaigns


def test_run_no_variants_and_empty(ds):
    res, t = es.run(ds, price_loader, bench_loader, variants=False)
    assert set(t.variant) == {"main"}
    res2, t2 = es.run(ds.iloc[5:6], price_loader, bench_loader)
    assert t2.empty and res2 == {"combos": {}, "execution": {}}


def test_run_exit_events_used_for_main_not_hold_variants():
    d = pd.DataFrame([row(1, "BOOM", exit_events=["2022-03-15T10:00:00+00:00"])])
    res, t = es.run(d, price_loader, bench_loader)
    v = t.set_index("variant")
    assert v.loc["main", "exit_reason"] == "Aktivist reduziert"
    assert v.loc["no_stop", "exit_reason"] == "Aktivist reduziert"
    assert v.loc["hold_21", "exit_reason"] == "Haltedauer"


def test_write_report_files(ds, tmp_path):
    res, t = es.run(ds, price_loader, bench_loader)
    funnel = Counter({"kampagnen": 10, "Survivorship: Endwert unknown": 2, "qualifiziert": 4})
    out = es.write_report(res, funnel, ds, t, path=tmp_path / "sub" / "r.md")
    assert out == str(tmp_path / "sub" / "r.md")
    md = (tmp_path / "sub" / "r.md").read_text(encoding="utf-8")
    assert "20%" in md and "## US/A" in md and "qualifiziert:main" in md and "- kampagnen: 10" in md
    assert len(pd.read_csv(tmp_path / "sub" / "r_trades.csv")) == len(t)
    assert len(pd.read_csv(tmp_path / "sub" / "r_dataset.csv")) == len(ds)
    js = json.loads((tmp_path / "sub" / "r.json").read_text())
    assert js["funnel"]["kampagnen"] == 10 and "US/A" in js["result"]["combos"]
