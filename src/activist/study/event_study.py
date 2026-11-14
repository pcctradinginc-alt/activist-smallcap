"""Phase 1: Internationale Ereignisstudie. Haupttest = tatsächliche Handelsstrategie (Order-, Ausstiegs-, Kostenregeln)."""
from __future__ import annotations
import json
from datetime import date, timedelta
import pandas as pd
from ..common import EventStore, REPORTS, params
from ..paper.execution import cost_one_side, simulate_entry, simulate_exit
from ..parse.campaigns import assign
from .stats import verdict

EXIT_FORMS_HINT = ("reduz", "exit")


def run_campaign(c: dict, px: pd.DataFrame, bench: pd.DataFrame, p: dict, stop=True, max_days=None,
                 exit_events=()) -> dict | None:
    fill = simulate_entry(px, pd.Timestamp(c["published"]), p["order"], p["order"]["position_eur"])
    if not fill.filled:
        return {"campaign_id": c["campaign_id"], "executed": False, "reason": fill.reason}
    xd, xp, why = simulate_exit(px, fill.fill_date, fill.price, p["exit"], exit_events, stop, max_days)
    gross = xp / fill.price - 1
    b = bench["close"]
    b0, b1 = b.asof(fill.fill_date), b.asof(xd)
    bret = (b1 / b0 - 1) if b0 and b1 else 0.0
    cost = cost_one_side(c.get("half_spread"), p["costs"], c["region"], "buy") + \
           cost_one_side(c.get("half_spread"), p["costs"], c["region"], "sell")
    return {"campaign_id": c["campaign_id"], "executed": True, "fill_ratio": fill.fill_ratio,
            "entry_date": fill.fill_date, "entry": fill.price, "exit_date": xd, "exit": xp, "exit_reason": why,
            "gross": gross, "bench": bret, "cost": cost, "net_excess": gross - bret - cost,
            "complete": why not in ("keine Folgekurse", "offen (Datenende)"),
            "activist": c.get("filer_name") or c.get("filer_id"), "region": c["region"], "class": c["signal_class"]}


def run(price_loader, bench_loader, events: list[dict] | None = None, variants: bool = True) -> dict:
    """price_loader(ticker, region, start, end) -> DataFrame; bench_loader(region, start, end) -> DataFrame."""
    p = params()
    evs = assign(events if events is not None else EventStore().all())
    firsts = [e for e in evs if e.get("is_first_event") and e.get("ticker")]
    rows = {"main": [], "no_stop": []} | {f"hold_{d}": [] for d in p["study"]["explore_holding_days"]}
    skipped = []
    for c in firsts:
        start = date.fromisoformat(c["published"][:10]) - timedelta(days=40)
        end = start + timedelta(days=420)
        try:
            px = price_loader(c["ticker"], c["region"], start, end)
            bench = bench_loader(c["region"], start, end)
        except Exception as e:
            skipped.append({"campaign_id": c["campaign_id"], "reason": f"Kurse: {e}"}); continue
        exits = [pd.Timestamp(e["published"]) for e in evs if e["campaign_id"] == c["campaign_id"]
                 and e.get("event_type") == "exit"]
        r = run_campaign(c, px, bench, p, True, None, exits)
        if not r or not r["executed"]:
            skipped.append(r or {"campaign_id": c["campaign_id"]}); continue
        rows["main"].append(r)
        if variants:
            rows["no_stop"].append(run_campaign(c, px, bench, p, False, None, exits))
            for d in p["study"]["explore_holding_days"]:
                rows[f"hold_{d}"].append(run_campaign(c, px, bench, p, False, d, ()))
    result = {"skipped": len(skipped), "combos": {}}
    for name, rs in rows.items():
        # nur abgeschlossene Trades zählen; offene Positionen gehen nicht in die Auswertung ein
        df = pd.DataFrame([r for r in rs if r and r.get("executed") and r.get("complete")])
        result.setdefault("open_trades", {})[name] = sum(1 for r in rs if r and r.get("executed") and not r.get("complete"))
        if df.empty:
            continue
        years = max((pd.to_datetime(df.entry_date).max() - pd.to_datetime(df.entry_date).min()).days / 365.25, 1.0)
        for (reg, cls), g in df.groupby(["region", "class"]):
            result["combos"].setdefault(f"{reg}/{cls}", {})[name] = verdict(g, years, p)
    return result


def write_report(result: dict, path=None) -> str:
    path = path or REPORTS / f"event_study_{date.today():%Y%m%d}.md"
    lines = ["# Ereignisstudie (Research Protocol v1)", "",
             "Haupttest = 'main' (Orderregeln, Ausstieg inkl. Stopp, Kosten). Übrige Varianten sind Erkundung.", "",
             f"Übersprungene Kampagnen: {result['skipped']} | offene Trades (nicht gewertet): "
             f"{result.get('open_trades', {}).get('main', 0)}", ""]
    for combo, variants in sorted(result["combos"].items()):
        lines.append(f"## {combo}")
        for name, v in variants.items():
            if v.get("n", 0) == 0:
                continue
            thin = " (dünne Datenlage)" if v.get("thin_data") else ""
            lines.append(f"- **{name}**: {v['status']} | n={v['n']}{thin} | Ø {v['mean']:+.2%} "
                         f"(90 %: {v['ci_low']:+.2%} bis {v['ci_high']:+.2%}) | schlechtestes Viertel {v['worst_quartile']:+.2%} "
                         f"| schlechtester {v['worst']:+.2%} | positiv {v['p_positive']:.0%}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    (path.with_suffix(".json")).write_text(json.dumps(result, default=str, indent=1), encoding="utf-8")
    return str(path)
