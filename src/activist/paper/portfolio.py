"""Papierdepot (Phase 2/3, Schattenmodus).

Ablauf je Tageslauf (run_day):
  1. offene Orders ausführen   – dieselben Regeln wie in der Ereignisstudie (simulate_entry)
  2. Ausstiege prüfen          – Aktivist reduziert | 12 Monate | −30 % ; Verkauf zum nächsten Eröffnungskurs
  3. neue Signale              – neue Kampagnen -> Universum/Risikoregeln -> Portfoliogrenzen -> Order oder Schattenportfolio
  4. bewerten                  – Depotwert in EUR, Höchststand, Rückgang (Verlustbremse)

Schattenmodus: Solange eine Region/Klasse die Ereignisstudie nicht bestanden hat, wird gebucht, aber jede Position
trägt validated=False; es gehen keine Kauf-Mails raus (nur der Wochenbericht). Kein Zugang zu echtem Geld.
Journal-Dateien sind append-only; der Zustand liegt in journal/portfolio.json.
"""
from __future__ import annotations
import csv, hashlib, json
from datetime import date, datetime, timedelta, timezone
import pandas as pd
from ..common import EventStore, JOURNAL, file_hash, params, risk_limits
from ..risk.rules import portfolio_size_factor
from .execution import cost_one_side, simulate_entry

STATE = JOURNAL / "portfolio.json"
TRADES = JOURNAL / "trades.csv"
SHADOW = JOURNAL / "shadow.csv"
TRADE_COLS = ["trade_id", "event_id", "campaign_id", "ticker", "region", "side", "order_date", "fill_date", "price",
              "qty", "cost_pct", "journal_hash"]
SHADOW_COLS = ["candidate_id", "event_id", "ticker", "region", "reason", "date"]
EU = ("UK", "NORDIC", "BENELUX_FR", "DACH")
FX = {"US": "EURUSD=X", "JP": "EURJPY=X"}


# ------------------------------------------------------------------ Kurse und Währung
def load_prices(ticker: str, region: str, start: date, end: date) -> pd.DataFrame:
    from ..sources import prices
    if region in EU:
        from ..study.dataset_eu import load_px
        return load_px(ticker, start, end, cache=False)
    return prices.load(ticker, region, start, end, cache=False)[0]


def eur_per_unit(ticker: str, region: str, day: date) -> float:
    """Umrechnung lokale Währung -> EUR (Kurse sind in Landeswährung; .L bereits in Pfund)."""
    from ..sources import prices
    pair = FX.get(region)
    inverse = True
    if region in EU:
        from ..study.dataset_eu import SUFFIX_FX
        pair = next((v for k, v in SUFFIX_FX.items() if ticker.endswith(k)), None); inverse = False
    if not pair:
        return 1.0
    s = prices.yahoo(pair, day - timedelta(days=10), day)["close"]
    v = float(s.iloc[-1])
    return 1 / v if inverse else v


# ------------------------------------------------------------------ Zustand / Journal
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    cash = params()["study"]["econ_portfolio_eur"]
    return {"start_date": str(date.today()), "start_value": cash, "cash": cash, "peak_value": cash,
            "positions": {}, "orders": {}, "closed": [], "seen_campaigns": [], "history": [], "last_run": None}


def save_state(st: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1, default=str, ensure_ascii=False))


def _append(path, cols, row):
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k) for k in cols})


def book_trade(row: dict):
    row["journal_hash"] = file_hash(TRADES) if TRADES.exists() else ""
    _append(TRADES, TRADE_COLS, row)


def book_shadow(row: dict):
    _append(SHADOW, SHADOW_COLS, row)


def validated_combos() -> set[str]:
    """Region/Klasse mit bestandenem Nachweis laut letztem Studienbericht (reports/event_study_*.json)."""
    from ..common import REPORTS
    files = sorted(REPORTS.glob("event_study_*.json"))
    if not files:
        return set()
    res = json.loads(files[-1].read_text()).get("result", {}).get("combos", {})
    return {k for k, v in res.items() if v.get("qualifiziert:main", {}).get("status") == "bestanden"}


# ------------------------------------------------------------------ Bausteine
def portfolio_value(st: dict, day: date, quotes: dict | None = None) -> float:
    v = st["cash"]
    for pid, pos in st["positions"].items():
        q = (quotes or {}).get(pid) or pos.get("last_eur") or pos["entry_eur"]
        v += pos["qty"] * q
    return v


def check_limits(st: dict, cand: dict) -> str | None:
    """Portfoliogrenzen (nur manuell änderbar, config/risk_limits.yaml)."""
    P = risk_limits()["portfolio"]
    pos = list(st["positions"].values()) + list(st["orders"].values())
    if len(pos) >= P["max_positions"]:
        return "max. Positionen erreicht"
    if sum(1 for x in pos if x.get("activist") == cand.get("activist")) >= P["max_per_activist"]:
        return "max. Positionen je Aktivist"
    return None


def process_orders(st: dict, today: date, p: dict, loader=load_prices, fx=eur_per_unit):
    for oid, o in list(st["orders"].items()):
        d0 = date.fromisoformat(o["published"][:10])
        try:
            px = loader(o["ticker"], o["region"], d0 - timedelta(days=40), today)
        except Exception:
            continue
        after = px[px.index > pd.Timestamp(d0)]
        if len(after) == 0:
            continue                                   # noch kein Handelstag nach Veröffentlichung
        fill = simulate_entry(px, pd.Timestamp(o["published"]), p["order"], o["position_eur_local"])
        days_seen = len(after.head(p["order"]["max_fill_days"]))
        if fill.filled:
            rate = fx(o["ticker"], o["region"], fill.fill_date.date())
            value_eur = o["position_eur"] * fill.fill_ratio
            qty = value_eur / (fill.price * rate)
            cost = cost_one_side(o.get("half_spread"), p["costs"], o["region"], "buy", value_eur)
            st["cash"] -= value_eur * (1 + cost)
            st["positions"][oid] = {**o, "entry_date": str(fill.fill_date.date()), "entry_local": fill.price,
                                    "entry_eur": fill.price * rate, "qty": qty, "buy_cost": cost,
                                    "fill_ratio": fill.fill_ratio, "last_eur": fill.price * rate,
                                    "bench_entry": _bench_close(o["region"], fill.fill_date.date())}
            book_trade({"trade_id": oid, "event_id": o["event_id"], "campaign_id": o["campaign_key"], "ticker": o["ticker"],
                        "region": o["region"], "side": "BUY", "order_date": o["order_date"],
                        "fill_date": str(fill.fill_date.date()), "price": round(fill.price, 4), "qty": round(qty, 4),
                        "cost_pct": round(cost, 5)})
            del st["orders"][oid]
        elif days_seen >= p["order"]["max_fill_days"]:
            book_shadow({"candidate_id": oid, "event_id": o["event_id"], "ticker": o["ticker"], "region": o["region"],
                         "reason": "Order verfallen (Limit nicht erreicht)", "date": str(today)})
            del st["orders"][oid]


def _bench_close(region: str, day: date) -> float | None:
    from ..sources import prices
    b = params()["universe"][region]["benchmark"]
    try:
        s = (prices.yahoo(b, day - timedelta(days=10), day + timedelta(days=1)) if b.startswith("^")
             else prices.load(b, region, day - timedelta(days=10), day + timedelta(days=1), cache=False)[0])["close"]
        return float(s.asof(pd.Timestamp(day)))
    except Exception:
        return None


def process_exits(st: dict, today: date, p: dict, events: list[dict], loader=load_prices, fx=eur_per_unit):
    """Regeln: Aktivist reduziert (spätere Meldung derselben Kampagne mit Anteil unter Schwelle oder 13D-Exit) |
    Haltedauer 252 Handelstage | Stopp −30 %. Verkauf zum nächsten Eröffnungskurs nach dem Auslöser."""
    for pid, pos in list(st["positions"].items()):
        try:
            px = loader(pos["ticker"], pos["region"], date.fromisoformat(pos["entry_date"]) - timedelta(days=5), today)
        except Exception:
            continue
        path = px[px.index > pd.Timestamp(pos["entry_date"])]
        if path.empty:
            continue
        rate = fx(pos["ticker"], pos["region"], today)
        pos["last_eur"] = float(path.close.iloc[-1]) * rate
        pos["last_date"] = str(path.index[-1].date())
        trigger = pos.get("exit_trigger")
        if not trigger:
            if pos.get("exit_event_time") or _activist_reduced(pos, events):
                trigger = "Aktivist reduziert"
            elif path.low.min() <= pos["entry_local"] * (1 + p["exit"]["stop_loss"]):
                trigger = "Stopp −30 %"
            elif len(path) >= p["exit"]["max_holding_days"]:
                trigger = "12 Monate"
            if trigger:
                pos["exit_trigger"], pos["trigger_date"] = trigger, pos["last_date"]
                continue                             # Verkauf am nächsten Handelstag zur Eröffnung
            continue                                 # kein Auslöser -> Position bleibt offen
        nxt = path[path.index > pd.Timestamp(pos["trigger_date"])]
        if nxt.empty:
            continue
        sell = float(nxt.open.iloc[0])
        if pos["exit_trigger"].startswith("Stopp"):
            sell = min(sell, pos["entry_local"] * (1 + p["exit"]["stop_loss"]))
        value = pos["qty"] * sell * rate
        cost = cost_one_side(pos.get("half_spread"), p["costs"], pos["region"], "sell", value)
        st["cash"] += value * (1 - cost)
        gross = sell / pos["entry_local"] - 1
        b1 = _bench_close(pos["region"], nxt.index[0].date())
        bret = (b1 / pos["bench_entry"] - 1) if b1 and pos.get("bench_entry") else 0.0
        st["closed"].append({**{k: pos[k] for k in ("ticker", "region", "activist", "entry_date", "signal_class",
                                                    "validated")},
                             "exit_date": str(nxt.index[0].date()), "reason": pos["exit_trigger"], "gross": gross,
                             "bench": bret, "net_excess": gross - bret - pos["buy_cost"] - cost})
        book_trade({"trade_id": pid + "-S", "event_id": pos["event_id"], "campaign_id": pos["campaign_key"],
                    "ticker": pos["ticker"], "region": pos["region"], "side": "SELL", "order_date": pos["trigger_date"],
                    "fill_date": str(nxt.index[0].date()), "price": round(sell, 4), "qty": round(pos["qty"], 4),
                    "cost_pct": round(cost, 5)})
        del st["positions"][pid]


def _activist_reduced(pos: dict, events: list[dict]) -> bool:
    thr = 0.03 if pos["region"] in ("UK", "DACH") else 0.05
    for e in events:
        if e["published"] <= pos["published"]:
            continue
        same = (str(e.get("filer_id") or e.get("filer_name")) == str(pos.get("filer_id") or pos.get("filer_name"))
                and str(e.get("issuer_id") or e.get("issuer_sec_code")) in (str(pos.get("issuer_id")), str(pos.get("ticker"))))
        if same and e.get("holding_ratio") is not None and e["holding_ratio"] < thr:
            return True
    return False


def new_signals(st: dict, today: date, p: dict, events: list[dict], since: str, rows: pd.DataFrame | None = None,
                fx=None):
    """Neue Kampagnen seit 'since' bewerten. rows: vorbereiteter Datensatz (Tests); sonst aus den Buildern."""
    if rows is None:
        from ..study.dataset import build, build_jp
        from ..study.dataset_eu import build_eu
        parts = [build(events, since=since)[0], build_jp(events, since=since)[0], build_eu(events, since=since)[0]]
        rows = pd.concat([x for x in parts if not x.empty], ignore_index=True) if any(not x.empty for x in parts) else pd.DataFrame()
    if rows.empty:
        return
    ok = validated_combos()
    dd = 1 - portfolio_value(st, today) / st["peak_value"]
    factor = portfolio_size_factor(dd)
    for _, r in rows.iterrows():
        r = r.to_dict()
        cid = r.get("campaign_key")
        if not cid or cid in st["seen_campaigns"]:
            continue
        st["seen_campaigns"].append(cid)
        oid = f"{today:%Y%m%d}-{hashlib.sha1(cid.encode()).hexdigest()[:6]}"
        if r.get("status") != "qualifiziert":
            book_shadow({"candidate_id": oid, "event_id": r.get("event_id"), "ticker": r.get("ticker"),
                         "region": r.get("region"), "reason": r.get("status"), "date": str(today)})
            continue
        why = check_limits(st, r) or ("Verlustbremse: keine neuen Trades" if factor == 0 else None)
        if why:
            book_shadow({"candidate_id": oid, "event_id": r.get("event_id"), "ticker": r.get("ticker"),
                         "region": r.get("region"), "reason": why, "date": str(today)}); continue
        pos_eur = p["order"]["position_eur"] * factor
        try:
            local = pos_eur / (fx or eur_per_unit)(r["ticker"], r["region"], today)
        except Exception:
            local = pos_eur
        combo = f"{r['region']}/{r.get('signal_class')}"
        st["orders"][oid] = {"event_id": r.get("event_id"), "campaign_key": cid, "ticker": r["ticker"],
                             "region": r["region"], "signal_class": r.get("signal_class"), "activist": r.get("activist"),
                             "filer_id": r.get("filer_id"), "filer_name": r.get("filer_name"),
                             "issuer_id": r.get("issuer_id") or r.get("isin"), "published": r["published"],
                             "order_date": str(today), "position_eur": pos_eur, "position_eur_local": local,
                             "half_spread": r.get("half_spread"), "limit_hint": (r.get("price") or 0) * 1.03,
                             "validated": combo in ok, "demand": r.get("demand"), "url": r.get("url")}
        (JOURNAL / "decisions").mkdir(parents=True, exist_ok=True)
        (JOURNAL / "decisions" / f"{oid}.json").write_text(json.dumps({**r, "decision": "order", "combo": combo,
                                                                         "validated": combo in ok}, default=str,
                                                                        ensure_ascii=False, indent=1))


def run_day(today: date | None = None, events: list[dict] | None = None, rows: pd.DataFrame | None = None,
            loader=load_prices, fx=eur_per_unit) -> dict:
    today = today or date.today()
    p, st = params(), load_state()
    if st.get("last_run") == str(today):
        return {"skipped": "bereits gelaufen"}      # verspäteter/doppelter Lauf erzeugt keine zweite Order
    events = events if events is not None else EventStore().all()
    since = st.get("last_run") or str(today - timedelta(days=3))
    process_orders(st, today, p, loader, fx)
    process_exits(st, today, p, events, loader, fx)
    new_signals(st, today, p, events, since, rows, fx)
    val = portfolio_value(st, today)
    st["peak_value"] = max(st["peak_value"], val)
    st["history"].append({"date": str(today), "value": round(val, 2), "cash": round(st["cash"], 2),
                          "positions": len(st["positions"])})
    st["last_run"] = str(today)
    save_state(st)
    return {"value": val, "positions": len(st["positions"]), "orders": len(st["orders"])}
