"""Papierdepot: run_day / weekly offline (keine Netzwerkzugriffe, Journal in tmp_path)."""
import csv
import json
from datetime import date

import pandas as pd
import pytest

from activist import common
from activist.common import file_hash, params
from activist.paper import portfolio as pf
from activist.paper import report
from activist.paper.execution import cost_one_side

IDX = pd.bdate_range("2024-01-02", periods=320)
D0, D1, D2, D3, D4 = (IDX[i].date() for i in range(5))
PUB = "2024-01-02T12:00:00"
COSTS = params()["costs"]
BUY_COST = 0.01 + 0.0025 + 10.0 / 1000.0          # Spanne 1 % + 0,25 % + 10 EUR / 1000 EUR


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    j, r = tmp_path / "journal", tmp_path / "reports"
    j.mkdir(); r.mkdir()
    for mod in (pf, report):
        monkeypatch.setattr(mod, "SHADOW", j / "shadow.csv")
        monkeypatch.setattr(mod, "TRADES", j / "trades.csv")
    monkeypatch.setattr(pf, "STATE", j / "portfolio.json")
    monkeypatch.setattr(pf, "JOURNAL", j)
    monkeypatch.setattr(report, "JOURNAL", j)
    monkeypatch.setattr(common, "JOURNAL", j)
    monkeypatch.setattr(common, "REPORTS", r)
    monkeypatch.setattr(report, "REPORTS", r)
    monkeypatch.setattr(pf, "validated_combos", lambda: set())
    monkeypatch.setattr(pf, "_bench_close", lambda region, day, field="close": 100.0)
    monkeypatch.setattr(pf, "eur_per_unit", lambda *a: 1.0)   # new_signals ruft eur_per_unit direkt (nicht fx)
    return tmp_path


def frame(rows, index=None):
    idx = IDX[:len(rows)] if index is None else index
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


def flat(n=320, px=100.0):
    return frame([(px, px + 1, px - 1, px, 1e7)] * n)


def loader_for(px):
    return lambda t, region, s, e: px[px.index <= pd.Timestamp(e)]


FX = lambda *a: 1.0


def cand(key="C1", **kw):
    base = dict(campaign_key=key, status="qualifiziert", ticker="AAA", region="US", signal_class="A",
                activist="ACT1", filer_id="F1", filer_name="Filer One", issuer_id="ISS1", published=PUB,
                event_id=f"E-{key}", half_spread=0.01, price=100.0, demand=None, url=None)
    base.update(kw)
    return base


def rows_of(*cs):
    return pd.DataFrame(list(cs))


EMPTY = pd.DataFrame()


def run(day, px, rows=EMPTY, events=()):
    return pf.run_day(day, events=list(events), rows=rows, loader=loader_for(px), fx=FX)


def trades():
    p = pf.TRADES
    return list(csv.DictReader(open(p))) if p.exists() else []


def shadow():
    p = pf.SHADOW
    return list(csv.DictReader(open(p))) if p.exists() else []


def state():
    return json.loads(pf.STATE.read_text())


def open_position(px, **kw):
    """Tag 0 Order, Tag 1 Fill (Eröffnung 100 -> Einstieg 100)."""
    run(D0, px, rows_of(cand(**kw)))
    run(D1, px)
    return state()


def price_path(extra):
    """idx0/1 = 100 (Schluss 100; Fill zu 100); extra ersetzt ab idx2."""
    rows = [(100, 101, 99, 100, 1e7)] * 2 + list(extra)
    rows += [(rows[-1][3],) * 3 + (rows[-1][3], 1e7)] * (320 - len(rows))
    return frame(rows)


# ------------------------------------------------------------------ 1. Order -> Fill
def test_qualified_creates_order_then_buy_booked(monkeypatch):
    monkeypatch.setattr(pf, "validated_combos", lambda: {"US/A"})
    px = flat()
    res = run(D0, px, rows_of(cand()))
    st = state()
    assert res["orders"] == 1 and res["positions"] == 0
    (oid, o), = st["orders"].items()
    assert o["position_eur"] == 1000 and o["validated"] is True and st["cash"] == 20000
    assert not pf.TRADES.exists()
    assert (pf.JOURNAL / "decisions" / f"{oid}.json").exists()

    run(D1, px)
    st = state()
    assert st["orders"] == {} and oid in st["positions"]
    pos = st["positions"][oid]
    assert pos["entry_date"] == str(D1) and pos["entry_local"] == 100.0 and pos["qty"] == pytest.approx(10.0)
    assert pos["buy_cost"] == pytest.approx(BUY_COST)
    assert st["cash"] == pytest.approx(20000 - 1000 * (1 + BUY_COST))
    (t,) = trades()
    assert t["side"] == "BUY" and t["trade_id"] == oid and float(t["price"]) == 100.0
    assert float(t["cost_pct"]) == pytest.approx(BUY_COST, abs=1e-5)
    assert "journal_hash" in t and t["fill_date"] == str(D1)


def test_cost_includes_fixed_fee_per_1000_eur():
    base = cost_one_side(0.01, {**COSTS, "fixed_fee_eur_per_order": 0.0}, "US", "buy", 1000)
    assert cost_one_side(0.01, COSTS, "US", "buy", 1000) - base == pytest.approx(0.01)
    assert cost_one_side(0.01, COSTS, "US", "buy", 1000) == pytest.approx(BUY_COST)


def test_limit_above_open_fills_at_open_below_limit():
    px = frame([(100, 101, 99, 100, 1e7), (102, 104, 101, 103, 1e7)] + [(103, 104, 102, 103, 1e7)] * 5)
    open_position(px)
    (t,) = trades()
    assert float(t["price"]) == 102.0


def test_journal_hash_chains_trades():
    px = flat()
    run(D0, px, rows_of(cand("C1", ticker="AAA"), cand("C2", ticker="BBB")))
    run(D1, px)
    t = trades()
    assert len(t) == 2
    # zweiter Eintrag trägt den Hash der Datei vor seiner Zeile (= Datei nur mit erster Zeile)
    lines = pf.TRADES.read_bytes().splitlines(keepends=True)
    import hashlib
    assert t[1]["journal_hash"] == hashlib.sha256(b"".join(lines[:2])).hexdigest()[:16]


# ------------------------------------------------------------------ 2. Verfall
def test_order_expires_after_three_days_into_shadow():
    rows = [(100, 101, 99, 100, 1e7)] + [(110, 112, 106, 111, 1e7)] * 6   # Limit 103, nie erreicht
    px = frame(rows)
    run(D0, px, rows_of(cand()))
    run(D1, px); run(D2, px)
    assert len(state()["orders"]) == 1 and shadow() == []
    run(D3, px)
    st = state()
    assert st["orders"] == {} and st["positions"] == {} and st["cash"] == 20000
    (s,) = shadow()
    assert "verfallen" in s["reason"] and s["ticker"] == "AAA"
    assert trades() == []


# ------------------------------------------------------------------ 3. Shadow / Dubletten
def test_non_qualified_goes_to_shadow_only_and_no_duplicates():
    px = flat()
    r = cand("C9", status="Universum: Marktkapitalisierung")
    run(D0, px, rows_of(r, r))                  # doppelt im selben Datensatz
    st = state()
    assert st["orders"] == {} and st["positions"] == {}
    (s,) = shadow()
    assert s["reason"] == "Universum: Marktkapitalisierung" and s["region"] == "US"
    run(D1, px, rows_of(r))                     # an Folgetag erneut gesehen
    assert len(shadow()) == 1 and state()["seen_campaigns"] == ["C9"]


def test_qualified_campaign_seen_twice_gives_single_order():
    px = flat()
    run(D0, px, rows_of(cand("C1")))
    run(D1, px, rows_of(cand("C1", published="2024-01-03T12:00:00")))
    assert len(trades()) == 1 and shadow() == []
    assert len(state()["positions"]) == 1 and state()["orders"] == {}


def test_row_without_campaign_key_ignored():
    px = flat()
    run(D0, px, rows_of(cand(campaign_key=None)))
    assert state()["orders"] == {} and shadow() == []


# BUG portfolio.py:177-186: Position ohne Ausstiegssignal fällt nach dem `if not trigger`-Block in Zeile 186
# (KeyError 'trigger_date'); run_day stürzt bei jeder offenen Position ohne Trigger ab.
BUG_NO_TRIGGER = pytest.mark.xfail(raises=KeyError, strict=True, reason="portfolio.py:186 KeyError trigger_date")

# ------------------------------------------------------------------ 4. Stopp
def test_stop_loss_sells_on_trigger_day_at_min_open_stop():
    """Wie in der Studie: ruhende Stopp-Order -> Verkauf am Auslösetag zu min(Eröffnung, Stopp-Niveau)."""
    px = price_path([(90, 91, 65, 66, 1e7), (80, 81, 79, 80, 1e7)])   # Tag 2: Tief 65 < 70
    st = open_position(px)
    (pid,) = st["positions"]
    cash_before = st["cash"]
    run(D2, px)
    st = state()
    assert st["positions"] == {}
    (c,) = st["closed"]
    assert c["reason"].startswith("Stopp") and c["exit_date"] == str(D2) and c["entry_date"] == str(D1)
    sell_cost = cost_one_side(0.01, COSTS, "US", "sell", 10 * 70.0)
    assert c["gross"] == pytest.approx(-0.30)
    assert c["net_excess"] == pytest.approx(-0.30 - BUY_COST - sell_cost)
    assert c["validated"] is False
    assert st["cash"] == pytest.approx(cash_before + 10 * 70.0 * (1 - sell_cost))
    buy, sell_row = trades()
    assert sell_row["side"] == "SELL" and sell_row["trade_id"] == pid + "-S"
    assert float(sell_row["price"]) == pytest.approx(70.0) and sell_row["fill_date"] == str(D2)


def test_stop_gap_down_sells_at_open():
    px = price_path([(60, 61, 59, 60, 1e7)])                 # Eröffnung unter Stopp -> Verkauf zu 60
    open_position(px)
    run(D2, px)
    (c,) = state()["closed"]
    assert c["gross"] == pytest.approx(-0.40)


def test_no_stop_when_drop_is_only_20_percent():
    px = price_path([(90, 91, 80, 82, 1e7), (82, 83, 81, 82, 1e7)])
    open_position(px)
    run(D2, px); run(D3, px)
    st = state()
    assert len(st["positions"]) == 1 and st["closed"] == []
    assert not next(iter(st["positions"].values())).get("exit_trigger")


# ------------------------------------------------------------------ 5. Aktivist reduziert
def reduction(ratio, **kw):
    return {"event_id": "E-red", "published": "2024-01-03T09:00:00", "filer_id": "F1", "issuer_id": "ISS1",
            "holding_ratio": ratio, **kw}


def test_activist_reduction_triggers_exit_and_sells_next_open():
    """Meldung am Einstiegstag 09:00 -> Verkauf zur nächsten Eröffnung (Tag 2, 105)."""
    px = price_path([(105, 106, 104, 105, 1e7), (108, 109, 107, 108, 1e7)])
    open_position(px)
    run(D2, px, events=[reduction(0.02)])
    st = state()
    (c,) = st["closed"]
    assert c["reason"] == "Aktivist reduziert" and c["exit_date"] == str(D2)
    assert c["gross"] == pytest.approx(0.05)
    assert [t["side"] for t in trades()] == ["BUY", "SELL"] and float(trades()[1]["price"]) == 105.0


@pytest.mark.parametrize("ev", [reduction(0.08),                               # über Schwelle
                                reduction(0.02, filer_id="OTHER"),             # anderer Meldender
                                reduction(0.02, issuer_id="OTHER"),            # anderer Emittent
                                reduction(0.02, published="2024-01-02T11:00:00")])  # vor der Position
def test_no_exit_for_unrelated_or_non_reducing_events(ev):
    px = price_path([(101, 102, 100, 101, 1e7), (101, 102, 100, 101, 1e7)])
    open_position(px)
    run(D2, px, events=[ev])
    assert state()["positions"] and not state()["closed"]


# ------------------------------------------------------------------ 6. Haltedauer
def test_holding_period_252_days_exit():
    """Ausstieg zum Schlusskurs des 252. Handelstags nach Einstieg (wie in der Studie)."""
    px = flat()
    open_position(px)
    run(IDX[1 + 251].date(), px)
    assert state()["positions"] and not state()["closed"]
    run(IDX[1 + 252].date(), px)
    st = state()
    (c,) = st["closed"]
    assert c["reason"] == "12 Monate" and st["positions"] == {}
    assert c["exit_date"] == str(IDX[1 + 252].date())


# ------------------------------------------------------------------ 7. Limits
def test_max_positions_16th_candidate_to_shadow():
    px = flat()
    cs = [cand(f"K{i}", ticker=f"T{i}", activist=f"ACT{i}") for i in range(16)]
    res = run(D0, px, rows_of(*cs))
    assert res["orders"] == 15
    (s,) = shadow()
    assert s["reason"] == "max. Positionen erreicht" and s["ticker"] == "T15"


def test_fourth_candidate_of_same_activist_to_shadow():
    px = flat()
    cs = [cand(f"K{i}", ticker=f"T{i}", activist="SAME") for i in range(4)]
    res = run(D0, px, rows_of(*cs))
    assert res["orders"] == 3
    (s,) = shadow()
    assert s["reason"] == "max. Positionen je Aktivist" and s["ticker"] == "T3"


def _seed(cash, peak=20000.0):
    st = pf.load_state()
    st.update(cash=cash, peak_value=peak)
    pf.save_state(st)


def test_drawdown_25_percent_blocks_new_trades():
    _seed(15000.0)                                  # 1 - 15000/20000 = 25 %
    res = run(D0, flat(), rows_of(cand()))
    assert res["orders"] == 0
    (s,) = shadow()
    assert s["reason"].startswith("Verlustbremse")


def test_drawdown_between_15_and_25_halves_size():
    _seed(16000.0)                                  # 20 %
    run(D0, flat(), rows_of(cand()))
    (o,) = state()["orders"].values()
    assert o["position_eur"] == pytest.approx(500.0)


def test_drawdown_below_15_full_size():
    _seed(17500.0)                                  # 12,5 %
    run(D0, flat(), rows_of(cand()))
    (o,) = state()["orders"].values()
    assert o["position_eur"] == pytest.approx(1000.0)


# ------------------------------------------------------------------ 8. Doppellauf
def test_second_run_same_day_skipped_and_books_nothing():
    px = flat()
    run(D0, px, rows_of(cand("C1")))
    before = state()
    res = run(D0, px, rows_of(cand("C2", ticker="BBB")))
    assert res == {"skipped": "bereits gelaufen"}
    assert state() == before and len(state()["orders"]) == 1 and shadow() == []
    run(D1, px)
    n = len(trades())
    assert run(D1, px) == {"skipped": "bereits gelaufen"} and len(trades()) == n == 1


# ------------------------------------------------------------------ 9. Wochenbericht
def test_weekly_renders_positions_cash_hash_and_writes_file():
    px = flat()
    run(D0, px, rows_of(cand("C1", ticker="AAA"), cand("C2", ticker="BBB", status="Universum: Spanne")))
    run(D1, px)
    pf.validated_combos = lambda: {"US/A"}
    st = state()
    pid = next(iter(st["positions"]))
    st["positions"][pid]["validated"] = False
    st["positions"][pid]["last_eur"] = 110.0
    pf.save_state(st)

    subject, body = report.weekly(D1)
    val = st["cash"] + 10 * 110.0
    assert "[nicht nachgewiesen]" in body and "AAA" in body and "+10,0%" in body
    de = lambda x: f"{x:,.0f}".replace(",", ".")
    assert f"Cash: {de(st['cash'])} €" in body and f"Depotwert: {de(val)} €" in body
    assert f"Journal-Prüfwert: {file_hash(pf.TRADES)}" in body
    assert "BBB (US): Universum: Spanne" in body
    assert subject.startswith("[Wochenbericht]") and "1 Positionen" in subject
    f = common.REPORTS / f"weekly_{D1:%G-W%V}.md"
    assert f.exists() and f.read_text(encoding="utf-8") == body


def test_weekly_validated_position_has_no_flag_and_empty_state_ok():
    _, body = report.weekly(D1)
    assert "keine" in body and "Journal-Prüfwert: -" in body
    px = flat()
    run(D0, px, rows_of(cand()))
    run(D1, px)
    st = state()
    next(iter(st["positions"].values()))["validated"] = True
    pf.save_state(st)
    _, body = report.weekly(D1)
    assert "AAA" in body and not [l for l in body.splitlines() if l.startswith("- AAA") and "[nicht nachgewiesen]" in l]


def test_limit_touch_only_does_not_fill():
    import pandas as pd
    from activist.paper.execution import simulate_entry
    idx = pd.bdate_range("2025-01-01", periods=6)
    px = pd.DataFrame({"open": [10, 10, 11, 11, 11, 11], "high": 11.0, "low": [10, 10, 10.3, 10.3, 10.3, 10.3],
                       "close": 10.0, "volume": 1e7}, index=idx)
    cfg = {"limit_above_last_close": 0.03, "max_fill_days": 3, "max_share_of_daily_volume": 0.05}
    f = simulate_entry(px, pd.Timestamp("2025-01-02 22:00", tz="UTC"), cfg, 1000.0)
    assert not f.filled
