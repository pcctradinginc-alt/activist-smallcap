"""Phase 1: Ereignisstudie. Haupttest = tatsächliche Handelsstrategie (Order-, Ausstiegs-, Kostenregeln)
auf qualifizierten Kampagnen; verworfene Kampagnen laufen als Schattenportfolio mit."""
from __future__ import annotations
import json
from datetime import date, timedelta
import pandas as pd
from ..common import REPORTS, params
from ..paper.execution import cost_one_side, simulate_entry, simulate_exit
from .stats import verdict

OPEN = ("keine Folgekurse", "offen (Datenende)")


def run_campaign(c: dict, px: pd.DataFrame, bench: pd.DataFrame, p: dict, stop=True, max_days=None,
                 exit_events=()) -> dict:
    fill = simulate_entry(px, pd.Timestamp(c["published"]), p["order"], p["order"]["position_eur"])
    if not fill.filled:
        return {"executed": False, "reason": fill.reason}
    xd, xp, why = simulate_exit(px, fill.fill_date, fill.price, p["exit"], exit_events, stop, max_days)
    gross = xp / fill.price - 1
    b = bench["close"]; b0, b1 = b.asof(fill.fill_date), b.asof(xd)
    bret = float(b1 / b0 - 1) if pd.notna(b0) and pd.notna(b1) else 0.0
    cost = sum(cost_one_side(c.get("half_spread"), p["costs"], c["region"], s) for s in ("buy", "sell"))
    return {"executed": True, "complete": why not in OPEN, "fill_ratio": fill.fill_ratio,
            "entry_date": fill.fill_date, "entry": fill.price, "exit_date": xd, "exit": xp, "exit_reason": why,
            "gross": gross, "bench": bret, "cost": cost, "net_excess": gross - bret - cost}


def run(ds: pd.DataFrame, price_loader, bench_loader, variants: bool = True) -> tuple[dict, pd.DataFrame]:
    p = params()
    names = ["main"] + (["no_stop"] + [f"hold_{d}" for d in p["study"]["explore_holding_days"]] if variants else [])
    trades = []
    for _, c in ds[ds.status.str.startswith(("qualifiziert", "verworfen"), na=False)].iterrows():
        c = c.to_dict()
        d0 = date.fromisoformat(c["published"][:10])
        try:
            px = price_loader(c["ticker"], c["region"], d0 - timedelta(days=45), d0 + timedelta(days=420))
            bench = bench_loader(c["region"], d0 - timedelta(days=45), d0 + timedelta(days=420))
        except Exception:
            continue
        ex = [pd.Timestamp(x) for x in (c.get("exit_events") or [])]
        for n in names:
            stop = n == "main"
            md = int(n.split("_")[1]) if n.startswith("hold_") else None
            r = run_campaign(c, px, bench, p, stop, md, ex if not md else ())
            trades.append({**{k: c[k] for k in ("campaign_key", "activist", "filer_id", "region", "signal_class",
                                                 "status", "ticker", "published", "demand")},
                           "variant": n, "qualified": c["status"] == "qualifiziert", **r})
    t = pd.DataFrame(trades)
    res = {"combos": {}, "execution": {}}
    if t.empty:
        return res, t
    for n in names:
        tv = t[t.variant == n]
        for label, sub in (("qualifiziert", tv[tv.qualified]), ("schatten", tv[~tv.qualified])):
            for (reg, cls), g in sub.groupby(["region", "signal_class"]):
                key = f"{reg}/{cls}"
                done = g[(g.executed == True) & (g.complete == True)]
                if n == "main" and label == "qualifiziert":
                    res["execution"][key] = {"signale": len(g), "ausgeführt": int((g.executed == True).sum()),
                                             "offen": int(((g.executed == True) & (g.complete == False)).sum())}
                if done.empty:
                    continue
                d = pd.to_datetime(g.published, utc=True)
                years = max((d.max() - d.min()).days / 365.25, 1.0)
                res["combos"].setdefault(key, {})[f"{label}:{n}"] = verdict(done, years, p)
    return res, t


def hypotheses(trades: pd.DataFrame) -> list[str]:
    """H2 (Forderungsart) und H3 (Stabilität der Aktivisten) – Erkundung auf qualifizierten Haupttrades."""
    t = trades[(trades.variant == "main") & (trades.executed == True) & (trades.complete == True)]
    if t.empty:
        return []
    L = ["## H2: Nettoüberrendite nach Forderung (Erkundung)", "", "| Forderung | n | Ø netto | positiv |", "|---|---|---|---|"]
    for dm, g in t.groupby(t.demand.fillna("unbekannt")):
        L.append(f"| {dm} | {len(g)} | {g.net_excess.mean():+.1%} | {(g.net_excess > 0).mean():.0%} |")
    d = pd.to_datetime(t.published, utc=True); mid = d.sort_values().iloc[len(d) // 2]
    a = t.assign(h=(d >= mid).astype(int)).groupby(["activist", "h"]).net_excess.mean().unstack()
    a = a.dropna()
    L += ["", "## H3: Stabilität der Aktivisten (Rangkorrelation erste vs. zweite Zeithälfte)", ""]
    if len(a) >= 5:
        rho = a[0].rank().corr(a[1].rank())
        L.append(f"Spearman ρ = {rho:+.2f} über {len(a)} Aktivisten mit Trades in beiden Hälften.")
    else:
        L.append(f"Nicht beurteilbar: nur {len(a)} Aktivisten mit Trades in beiden Hälften (mind. 5).")
    return L + [""]


def write_report(res: dict, funnel, ds: pd.DataFrame, trades: pd.DataFrame, path=None) -> str:
    path = path or REPORTS / f"event_study_{date.today():%Y%m%d}.md"
    L = ["# Ereignisstudie – Research Protocol v1", "",
         "Haupttest: **qualifiziert:main** (Orderregeln, Ausstieg inkl. −30 %-Stopp, Kosten, gegen Regionsindex). "
         "Alle anderen Zeilen sind Erkundung bzw. Schattenportfolio und zählen nicht als Nachweis.", "",
         "## Datentrichter", ""]
    L += [f"- {k}: {v}" for k, v in funnel.most_common()]
    gap = funnel.get("ohne Ticker (Survivorship-Lücke)", 0) / max(funnel.get("kampagnen", 1), 1)
    L += ["", f"**Survivorship-Lücke: {gap:.0%} der Kampagnen ohne heutigen Ticker** (übernommen, insolvent, delistet). "
          "Übernahmen sind oft erfolgreiche Kampagnen, Insolvenzen die schlechtesten – die Richtung der Verzerrung ist "
          "offen. Vor einem Nachweis ist eine Kursquelle mit delisteten Titeln nötig (z. B. CRSP, Norgate, EODHD).", "",
         "## Ausführung", ""]
    L += [f"- {k}: {v}" for k, v in res.get("execution", {}).items()]
    L.append("")
    for combo, vs in sorted(res["combos"].items()):
        L += [f"## {combo}", "", "| Variante | Urteil | n | Ø netto | 90 %-Bereich | schl. Viertel | schlechtester | positiv | Hälften |",
              "|---|---|---|---|---|---|---|---|---|"]
        for name, v in sorted(vs.items()):
            if not v.get("n"):
                continue
            L.append(f"| {name} | {v['status']} | {v['n']}{' ⚠︎ dünn' if v.get('thin_data') else ''} | {v['mean']:+.1%} | "
                     f"{v['ci_low']:+.1%} … {v['ci_high']:+.1%} | {v['worst_quartile']:+.1%} | {v['worst']:+.1%} | "
                     f"{v['p_positive']:.0%} | {v.get('half1', 0):+.1%} / {v.get('half2', 0):+.1%} |")
        L.append("")
    L += hypotheses(trades) if not trades.empty else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    ds.to_csv(path.with_name(path.stem + "_dataset.csv"), index=False)
    trades.to_csv(path.with_name(path.stem + "_trades.csv"), index=False)
    path.with_suffix(".json").write_text(json.dumps({"result": res, "funnel": dict(funnel)}, default=str, indent=1))
    return str(path)
