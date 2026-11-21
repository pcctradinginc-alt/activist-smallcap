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
from ..common import EventStore, JOURNAL, atomic_write_text, file_hash, params
from ..risk.rules import check_portfolio, portfolio_size_factor
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
    if pair not in _FX:                       # einmal je Währungspaar die ganze Historie laden
        _FX[pair] = prices.yahoo(pair, date(2014, 1, 1), date.today())["close"]
    v = float(_FX[pair].asof(pd.Timestamp(day)))
    if pd.isna(v):
        raise ValueError(f"kein Wechselkurs {pair} am {day}")
    return 1 / v if inverse else v


_FX: dict = {}


# ------------------------------------------------------------------ Zustand / Journal
def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    cash = params()["study"]["econ_portfolio_eur"]
    return {"start_date": str(date.today()), "start_value": cash, "cash": cash, "peak_value": cash,
            "positions": {}, "orders": {}, "closed": [], "seen_campaigns": [], "history": [], "last_run": None}


def save_state(st: dict):
    atomic_write_text(STATE, json.dumps(st, indent=1, default=str, ensure_ascii=False))


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
    # nur der bestätigende Holdout-Test bei eingefrorenem Protokoll zählt (keine Entwicklungsergebnisse)
    a = json.loads(files[-1].read_text()).get("alpha") or {}
    return set(a.get("passed") or []) if a.get("frozen") else set()


# ------------------------------------------------------------------ Bausteine
def portfolio_value(st: dict, day: date, quotes: dict | None = None) -> float:
    v = st["cash"]
    for pid, pos in st["positions"].items():
        q = (quotes or {}).get(pid) or pos.get("last_eur") or pos["entry_eur"]
        v += pos["qty"] * q
    return v


def process_orders(st: dict, today: date, p: dict, loader=load_prices, fx=eur_per_unit):
    for oid, o in list(st["orders"].items()):
        d0 = date.fromisoformat(o["published"][:10])
        if (today - date.fromisoformat(o["order_date"])).days > 10:      # hängende Order (keine Kurse) verfällt
            book_shadow({"candidate_id": oid, "event_id": o["event_id"], "ticker": o["ticker"], "region": o["region"],
                         "reason": "Order verfallen (keine Kurse)", "date": str(today)})
            del st["orders"][oid]; continue
        try:
            px = loader(o["ticker"], o["region"], d0 - timedelta(days=40), today)
        except Exception:
            continue
        px = px[px.index < pd.Timestamp(today)] if o.get("exclude_today") else px
        from ..features.universe import local_day
        after = px[px.index > local_day(o["published"])]
        if len(after) == 0:
            continue                                   # noch kein Handelstag nach Veröffentlichung
        fill = simulate_entry(px, pd.Timestamp(o["published"]), p["order"], o["position_eur_local"])
        days_seen = len(after.head(p["order"]["max_fill_days"]))
        complete = fill.filled and fill.fill_ratio >= 0.999
        if fill.filled and (complete or days_seen >= p["order"]["max_fill_days"]):
            # wie in der Studie: Teilausführungen über bis zu 3 Tage, gebucht wird erst das Endergebnis
            rate = fx(o["ticker"], o["region"], fill.fill_date.date())
            value_eur = o["position_eur"] * fill.fill_ratio
            cost = cost_one_side(o.get("half_spread"), p["costs"], o["region"], "buy", value_eur)
            cost += p["costs"].get("fixed_fee_eur_per_order", 0.0) * max(fill.fill_days - 1, 0) / max(value_eur, 1e-9)
            st["cash"] -= value_eur * (1 + cost)
            st["positions"][oid] = {**o, "entry_date": str(fill.fill_date.date()), "entry_local": fill.price,
                                    "ref_close": float(px.close.asof(fill.fill_date)), "entry_rate": rate,
                                    "entry_eur": fill.price * rate, "qty": value_eur / (fill.price * rate),
                                    "buy_cost": cost, "fill_ratio": fill.fill_ratio, "last_eur": fill.price * rate,
                                    "bench_entry": _bench_close(o["region"], fill.fill_date.date(), "open")}
            book_trade({"trade_id": oid, "event_id": o["event_id"], "campaign_id": o["campaign_key"], "ticker": o["ticker"],
                        "region": o["region"], "side": "BUY", "order_date": o["order_date"],
                        "fill_date": str(fill.fill_date.date()), "price": round(fill.price, 4),
                        "qty": round(value_eur / (fill.price * rate), 4), "cost_pct": round(cost, 5)})
            del st["orders"][oid]
        elif not fill.filled and days_seen >= p["order"]["max_fill_days"]:
            book_shadow({"candidate_id": oid, "event_id": o["event_id"], "ticker": o["ticker"], "region": o["region"],
                         "reason": "Order verfallen (Limit nicht erreicht)", "date": str(today)})
            del st["orders"][oid]


def _bench(region: str, day: date, field: str = "close") -> float | None:
    from ..sources import prices
    b = params()["universe"][region]["benchmark"]
    try:
        df = (prices.yahoo(b, day - timedelta(days=10), day + timedelta(days=1)) if b.startswith("^")
              else prices.load(b, region, day - timedelta(days=10), day + timedelta(days=1), cache=False)[0])
        v = df[field].get(pd.Timestamp(day))
        return float(v) if v is not None and pd.notna(v) else None
    except Exception:
        return None


_bench_close = _bench   # Rückwärtskompatibilität (Tests)


def process_exits(st: dict, today: date, p: dict, events: list[dict], loader=load_prices, fx=eur_per_unit):
    """Exakt dieselben Ausstiegsregeln wie die Ereignisstudie (simulate_exit): Aktivist reduziert -> nächste Eröffnung,
    Stopp −30 % -> am Auslösetag zu min(Eröffnung, Stopp), 252 Handelstage -> Schlusskurs. Kurse werden bei jedem Lauf
    neu geladen und sind nachträglich split-/dividendenbereinigt; der Einstieg wird deshalb auf die aktuelle Reihe
    umgerechnet (ref_close), damit Splits keinen falschen Stopp auslösen."""
    from .execution import simulate_exit
    from ..study.event_study import OPEN
    for pid, pos in list(st["positions"].items()):
        try:
            px = loader(pos["ticker"], pos["region"], date.fromisoformat(pos["entry_date"]) - timedelta(days=5), today)
        except Exception:
            continue
        ed = pd.Timestamp(pos["entry_date"])
        ref = px.close.asof(ed)
        if pd.isna(ref) or not pos.get("ref_close"):
            continue
        entry_adj = pos["entry_local"] * float(ref) / pos["ref_close"]
        path = px[px.index > ed]
        if path.empty:
            continue
        rate = fx(pos["ticker"], pos["region"], today)
        er = pos.get("entry_rate") or rate
        pos["last_eur"] = pos["entry_eur"] * float(path.close.iloc[-1]) / entry_adj * rate / er
        pos["last_date"] = str(path.index[-1].date())
        ex = [pd.Timestamp(t) for t in (pos.get("exit_events") or [])]
        red = _activist_reduced(pos, events)
        if red:
            ex.append(pd.Timestamp(red))
        xd, xp, why = simulate_exit(px, ed, entry_adj, p["exit"], ex, stop=True)
        if why in OPEN:
            continue                                   # kein Auslöser -> Position bleibt offen
        why = {"Stopp": "Stopp −30 %", "Haltedauer": "12 Monate"}.get(why, why)
        rx = fx(pos["ticker"], pos["region"], xd.date())
        value = pos["qty"] * pos["entry_eur"] * (xp / entry_adj) * (rx / er)
        cost = cost_one_side(pos.get("half_spread"), p["costs"], pos["region"], "sell", value)
        st["cash"] += value * (1 - cost)
        gross = xp / entry_adj - 1
        b1 = _bench_close(pos["region"], xd.date(), "close" if why == "12 Monate" else "open")
        b0 = pos.get("bench_entry")
        bret = (b1 / b0 - 1) if b1 and b0 else None
        st["closed"].append({**{k: pos.get(k) for k in ("ticker", "region", "activist", "entry_date", "signal_class",
                                                        "validated", "challenger_score", "challenger_selected")},
                             "exit_date": str(xd.date()), "reason": why, "gross": gross, "bench": bret or 0.0,
                             "bench_missing": bret is None,
                             "net_excess": gross - (bret or 0.0) - pos["buy_cost"] - cost})
        book_trade({"trade_id": pid + "-S", "event_id": pos["event_id"], "campaign_id": pos["campaign_key"],
                    "ticker": pos["ticker"], "region": pos["region"], "side": "SELL", "order_date": str(today),
                    "fill_date": str(xd.date()), "price": round(xp, 4), "qty": round(pos["qty"], 4),
                    "cost_pct": round(cost, 5)})
        del st["positions"][pid]


AMEND_OR_13G = {"SC 13D/A", "SCHEDULE 13D/A", "SC 13G/A", "SCHEDULE 13G/A"}


def _activist_reduced(pos: dict, events: list[dict]) -> str | None:
    """Früheste spätere Meldung desselben Melders zum selben Emittenten, die eine Reduktion unter die Schwelle zeigt.
    EU/JP: holding_ratio aus der Meldung; USA: 13D/A bzw. 13G/A wird gelesen (is_exit)."""
    thr = 0.03 if pos["region"] in ("UK", "DACH") else 0.05
    who = str(pos.get("filer_id") or pos.get("filer_name") or "").lstrip("0")
    iss = {str(pos.get("issuer_id") or "").lstrip("0"), str(pos.get("ticker"))}
    for e in sorted(events, key=lambda e: e["published"]):
        if e["published"] <= pos["published"]:
            continue
        if str(e.get("filer_id") or e.get("filer_name") or "").lstrip("0") != who:
            continue
        if str(e.get("issuer_id") or e.get("issuer_sec_code") or "").lstrip("0") not in iss:
            continue
        if e.get("holding_ratio") is not None and e["holding_ratio"] < thr:
            return e["published"]
        if e.get("form") in AMEND_OR_13G and e.get("url"):
            try:
                from ..study.dataset import _parse, _parsed_cache
                if _parse(e["url"], _parsed_cache()).get("is_exit"):
                    return e["published"]
            except Exception:
                pass
    return None


def challenger_view(r: dict, today: date, fx_rate: float | None) -> dict:
    """Score des Challengers zum Entscheidungszeitpunkt (Schattenmodus: handelt nicht, ändert keine Regel).
    Aktivisten-Bilanz nur aus Kampagnen, deren Ausstieg vor heute lag (point-in-time)."""
    from ..common import REPORTS
    from ..confidence.basic import shrink_to_mean
    from ..learn import ranker
    sc = REPORTS / "scorecard.json"
    if not sc.exists():
        return {"challenger_score": None, "challenger_selected": None}
    card = json.loads(sc.read_text())["card"]
    prior = None
    oc = REPORTS / "campaign_outcomes.csv"
    if oc.exists():
        o = pd.read_csv(oc)
        o = o[pd.to_datetime(o.exit_date) < pd.Timestamp(today)]
        if len(o):
            own = o[o.activist == r.get("activist")]
            prior = shrink_to_mean(own.net_excess.mean() if len(own) else o.net_excess.mean(), len(own), o.net_excess.mean())
    feat = pd.DataFrame([{"activist_prior_mean": prior, "demand": r.get("demand"), "stake": r.get("stake"),
                          "runup_20d": r.get("runup_20d"),
                          "adv_eur": (r.get("adv") * fx_rate) if r.get("adv") and fx_rate else None}])
    sco = float(ranker.score(feat, card).iloc[0])
    return {"challenger_score": sco, "challenger_selected": bool(sco >= card["threshold"])}


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
        pos_eur = p["order"]["position_eur"] * factor
        buy_cost = cost_one_side(r.get("half_spread"), p["costs"], r["region"], "buy", pos_eur)
        cash_free = st["cash"] - sum(o["position_eur"] * (1 + o.get("buy_cost_est", 0)) for o in st["orders"].values())
        why = "Verlustbremse: keine neuen Trades" if factor == 0 else check_portfolio(
            list(st["positions"].values()) + list(st["orders"].values()), {**r, "position_eur": pos_eur, "buy_cost": buy_cost},
            portfolio_value(st, today), cash_free, regions_passed=len({c.split("/")[0] for c in ok}))
        if why:
            book_shadow({"candidate_id": oid, "event_id": r.get("event_id"), "ticker": r.get("ticker"),
                         "region": r.get("region"), "reason": why, "date": str(today)}); continue
        try:
            local = pos_eur / (fx or eur_per_unit)(r["ticker"], r["region"], today)
        except Exception:                         # kein stiller Fallback
            book_shadow({"candidate_id": oid, "event_id": r.get("event_id"), "ticker": r.get("ticker"),
                         "region": r.get("region"), "reason": "gesperrt: Wechselkurs fehlt", "date": str(today)}); continue
        combo = f"{r['region']}/{r.get('signal_class')}"
        try:
            chal = challenger_view(r, today, pos_eur / local if local else None)
        except Exception as e:
            chal = {"challenger_score": None, "challenger_selected": None, "challenger_error": str(e)[:120]}
        st["orders"][oid] = {"event_id": r.get("event_id"), "campaign_key": cid, "ticker": r["ticker"],
                             "region": r["region"], "signal_class": r.get("signal_class"), "activist": r.get("activist"),
                             "sector": r.get("sector") if isinstance(r.get("sector"), str) else None, "buy_cost_est": buy_cost,
                             "filer_id": r.get("filer_id"), "filer_name": r.get("filer_name"),
                             "issuer_id": r.get("issuer_id") or r.get("isin"), "published": r["published"],
                             "order_date": str(today), "position_eur": pos_eur, "position_eur_local": local,
                             "half_spread": r.get("half_spread"), "limit_hint": (r.get("price") or 0) * 1.03,
                             "validated": combo in ok, "demand": r.get("demand"), "url": r.get("url"),
                             "exit_events": [str(x) for x in (r.get("exit_events") or [])
                                             if isinstance(r.get("exit_events"), list)], **chal}
        atomic_write_text(JOURNAL / "decisions" / f"{oid}.json", json.dumps(
            {**r, "decision": "order", "combo": combo, "validated": combo in ok}, default=str, ensure_ascii=False, indent=1))
        from ..notify.buy import notify
        notify({**st["orders"][oid], "n_after": len(st["positions"]) + len(st["orders"]), "max_fill_days": p["order"].get("max_fill_days", 3)}, r, combo, today)


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
