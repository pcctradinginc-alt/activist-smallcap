"""Portfoliotor (rules.check_portfolio) und Anbindung in paper.portfolio.new_signals."""
import json
from datetime import date

import pandas as pd

from activist.paper import portfolio as pf
from activist.risk.rules import check_portfolio

LIM = {"portfolio": {"max_positions": 3, "max_position": 0.10, "max_sector": 0.25, "max_region_once_3_regions_pass": 0.50,
                     "max_per_activist": 2}}
V = 10000.0
CAND = {"ticker": "N", "activist": "A", "region": "US", "sector": "Tech", "position_eur": 500, "buy_cost": 0.01}


def held(ticker="H", activist="X", region="EU", sector="Energie", eur=500, **kw):
    return {"ticker": ticker, "activist": activist, "region": region, "sector": sector, "position_eur": eur, **kw}


def gate(h=(), cand=None, cash=10000.0, regions=0):
    return check_portfolio(list(h), {**CAND, **(cand or {})}, V, cash, LIM, regions)


def test_clean_candidate():
    assert gate([held()]) is None


def test_max_positions():
    assert gate([held(f"T{i}") for i in range(3)]) == "max. Positionen erreicht"


def test_max_per_activist():
    assert gate([held("T1", activist="A"), held("T2", activist="A")]) == "max. Positionen je Aktivist"


def test_duplicate_ticker():
    assert gate([held("N")]) == "Titel bereits im Depot/Orderbuch"


def test_position_size():
    assert gate(cand={"position_eur": 1001}) == "Positionsgröße über Grenze"
    assert gate(cand={"position_eur": 1000}) is None


def test_sector_limit_counts_fill_ratio():
    assert gate([held(sector="Tech", eur=2100)]) == "Branchengrenze"           # 2100 + 500 > 2500
    assert gate([held(sector="Tech", eur=4000, fill_ratio=0.5)]) is None       # 2000 + 500 = 2500
    assert gate([held(sector="Energie", eur=5000)]) is None


def test_unknown_sector_is_own_bucket():
    h = [held("T1", sector=None, eur=1100), held("T2", sector=float("nan"), eur=1100)]
    assert gate(h, {"sector": "", "region": "EU"}) == "Branchengrenze"      # unbekannt zählt je Region zusammen
    assert gate(h, {"sector": "", "region": "US"}) is None                   # andere Region = anderer Topf
    assert gate(h, {"sector": "Tech", "region": "EU"}) is None


def test_region_limit_only_with_3_regions():
    h = [held("T1", region="US", sector="A", eur=2400), held("T2", region="US", sector="B", eur=2400)]
    assert gate(h, regions=2) is None
    assert gate(h, regions=3) == "Regionsgrenze"                               # 4800 + 500 > 5000


def test_cash_counts_buy_cost():
    assert gate(cash=504.9) == "Cash reicht nicht"                             # 500 * 1,01 = 505
    assert gate(cash=505.0) is None
    assert gate(cash=500, cand={"buy_cost": 0}) is None


def test_save_state_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(pf, "STATE", tmp_path / "sub" / "portfolio.json")
    pf.save_state({"cash": 1.0, "positions": {}})
    assert json.loads(pf.STATE.read_text())["cash"] == 1.0
    assert not list(pf.STATE.parent.glob("*.tmp"))


def _signals(tmp_path, monkeypatch, st):
    j = tmp_path / "j"; j.mkdir(parents=True)
    for k, v in {"STATE": j / "portfolio.json", "TRADES": j / "trades.csv", "SHADOW": j / "shadow.csv", "JOURNAL": j,
                 "validated_combos": lambda: set(), "eur_per_unit": lambda *a: 1.0}.items():
        monkeypatch.setattr(pf, k, v)
    rows = pd.DataFrame([{"campaign_key": "K1", "status": "qualifiziert", "ticker": "T1", "region": "US", "signal_class": "A",
                          "activist": "Act", "published": "2026-01-02T20:00:00-05:00", "event_id": "E1", "price": 10.0,
                          "half_spread": 0.01}])
    pf.new_signals(st, date(2026, 1, 3), pf.params(), [], "2026-01-01", rows, fx=lambda *a: 1.0)
    return j / "shadow.csv"


def _state(cash, pos_value, orders=None):
    st = pf.load_state(); st["cash"] = cash; st["orders"] = orders or {}
    st["positions"] = {"P0": {"ticker": "POS", "activist": "Z", "region": "EU", "sector": "S", "position_eur": pos_value,
                              "qty": 1.0, "entry_eur": pos_value}}
    st["peak_value"] = cash + pos_value
    return st


def test_new_signals_cash_too_low(tmp_path, monkeypatch):
    pe = pf.params()["order"]["position_eur"]
    st = _state(0.5 * pe, 30 * pe)             # Depotwert groß genug für die Größengrenze, aber kaum Cash
    shadow = _signals(tmp_path, monkeypatch, st)
    assert not st["orders"] and "Cash reicht nicht" in shadow.read_text()


def test_new_signals_respects_reserved_cash(tmp_path, monkeypatch):
    pe = pf.params()["order"]["position_eur"]
    o0 = {"ticker": "OTHER", "activist": "Z", "region": "US", "sector": "S", "position_eur": pe, "buy_cost_est": 0.02}
    st = _state(1.5 * pe, 30 * pe, {"O0": o0})  # 1,5 pe Cash, davon 1,02 pe reserviert -> 0,48 pe frei
    shadow = _signals(tmp_path, monkeypatch, st)
    assert list(st["orders"]) == ["O0"] and "Cash reicht nicht" in shadow.read_text()
    st2 = _state(1.5 * pe, 30 * pe)             # ohne Reservierung reicht das Cash
    _signals(tmp_path / "x", monkeypatch, st2)
    (o,) = st2["orders"].values()
    assert o["buy_cost_est"] > 0 and "sector" in o
