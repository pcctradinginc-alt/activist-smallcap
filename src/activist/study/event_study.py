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
                 exit_events=(), fx_local: float = 1.0) -> dict:
    """fx_local = Einheiten Landeswährung je EUR am Entscheidungstag (Volumen-Obergrenze in Landeswährung)."""
    pos_local = p["order"]["position_eur"] * fx_local
    fill = simulate_entry(px, pd.Timestamp(c["published"]), p["order"], pos_local)
    if not fill.filled:
        return {"executed": False, "reason": fill.reason}
    xd, xp, why = simulate_exit(px, fill.fill_date, fill.price, p["exit"], exit_events, stop, max_days)
    gross = xp / fill.price - 1
    # Vergleichsindex zeitgleich zu den Aktienkursen: Einstieg zur Eröffnung, Ausstieg zur Eröffnung (Meldung/Stopp)
    # bzw. zum Schluss (Haltedauer/Datenende)
    bo = bench["open"] if "open" in bench else bench["close"]
    b0 = bo.get(fill.fill_date, float("nan"))
    b1 = (bo if why in ("Aktivist reduziert", "Stopp") else bench["close"]).get(xd, float("nan"))
    if pd.isna(b0) or pd.isna(b1):
        return {"executed": True, "complete": False, "reason": "Vergleichsindex fehlt", "entry_date": fill.fill_date,
                "exit_date": xd, "exit_reason": why}
    bret = float(b1 / b0 - 1)
    value = p["order"]["position_eur"] * fill.fill_ratio
    buy = cost_one_side(c.get("half_spread"), p["costs"], c["region"], "buy", value)
    buy += p["costs"].get("fixed_fee_eur_per_order", 0.0) * max(fill.fill_days - 1, 0) / max(value, 1e-9)
    cost = buy + cost_one_side(c.get("half_spread"), p["costs"], c["region"], "sell", value)
    return {"executed": True, "complete": why not in OPEN, "fill_ratio": fill.fill_ratio,
            "entry_date": fill.fill_date, "entry": fill.price, "exit_date": xd, "exit": xp, "exit_reason": why,
            "gross": gross, "bench": bret, "cost": cost, "net_excess": gross - bret - cost,
            "hold_days": int((px.index > fill.fill_date).sum() if why in OPEN else ((px.index > fill.fill_date) & (px.index <= xd)).sum())}


def run(ds: pd.DataFrame, price_loader, bench_loader, variants: bool = True, fx_loader=None) -> tuple[dict, pd.DataFrame]:
    p = params()
    names = ["main"] + (["no_stop"] + [f"hold_{d}" for d in p["study"]["explore_holding_days"]] if variants else [])
    trades = []
    for _, c in ds[ds.status.str.startswith(("qualifiziert", "verworfen"), na=False)].iterrows():
        c = c.to_dict()
        d0 = date.fromisoformat(c["published"][:10])
        try:
            if c.get("price_source") == "tiingo":      # Survivorship: dieselbe Kursquelle wie im Datensatz
                from ..sources import prices
                px = prices.tiingo(c["ticker"], d0 - timedelta(days=45), d0 + timedelta(days=420))
            else:
                px = price_loader(c["ticker"], c["region"], d0 - timedelta(days=45), d0 + timedelta(days=420))
            bench = bench_loader(c["region"], d0 - timedelta(days=45), d0 + timedelta(days=420))
        except Exception:
            trades.append({"campaign_key": c.get("campaign_key"), "region": c.get("region"), "signal_class": c.get("signal_class"),
                           "variant": "main", "qualified": c["status"] == "qualifiziert", "executed": False,
                           "reason": "Kurse/Index nicht ladbar", "published": c.get("published")})
            continue
        try:
            fxl = fx_loader(c["ticker"], c["region"], d0) if fx_loader else 1.0
        except Exception:                         # kein stiller Fallback: ohne Wechselkurs keine Volumengrenze
            trades.append({"campaign_key": c.get("campaign_key"), "region": c.get("region"), "signal_class": c.get("signal_class"),
                           "variant": "main", "qualified": c["status"] == "qualifiziert", "executed": False,
                           "reason": "Wechselkurs fehlt", "published": c.get("published")})
            continue
        ex = [pd.Timestamp(x) for x in (c.get("exit_events") or [])]
        for n in names:
            stop = n == "main"
            md = int(n.split("_")[1]) if n.startswith("hold_") else None
            r = run_campaign(c, px, bench, p, stop, md, ex if not md else (), fxl)
            trades.append({**{k: c[k] for k in ("campaign_key", "activist", "filer_id", "region", "signal_class",
                                                 "status", "ticker", "published", "demand")},
                           "mcap": c.get("mcap"), "verified": bool(c.get("verified")), "dq_missing": c.get("dq_missing"),
                           "stake": c.get("stake"), "runup_20d": c.get("runup_20d"),
                           "adv_eur": (c.get("adv") / fxl) if c.get("adv") and fxl else None,
                           "price_source": c.get("price_source"),
                           "variant": n, "qualified": c["status"] == "qualifiziert", **r})
    t = pd.DataFrame(trades)
    res = {"combos": {}, "execution": {}}
    if t.empty:
        return res, t
    # Bestätigender Test (Train/Validierung/Holdout, nur geprüfte Daten, Holm) + Lernstatus
    from .alpha import main_trades, run_alpha
    from ..learn import ranker
    res["alpha"] = run_alpha(t)
    res["data_quality"] = res["alpha"]["data_quality"]
    mt = main_trades(t)
    if len(mt):
        mt = ranker.pit_activist_record(mt)
        res["learning"] = ranker.decide(mt[mt.split == "train"], mt[mt.split == "valid"])
    else:
        res["learning"] = {"status": ranker.ST_INSUFFICIENT, "active": False, "n_train": 0, "n_valid": 0}
    res["learning"]["closed_campaigns"] = int(len(mt))
    for n in names:
        tv = t[t.variant == n]
        if "executed" not in tv:
            continue
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
                if n == "main" and label == "qualifiziert" and "mcap" in done:   # Erkundung: Größenklassen
                    for nm, msk in (("bis_2Mrd", done.mcap <= 2e9), ("2_bis_8Mrd", done.mcap > 2e9)):
                        if msk.sum():
                            res["combos"][key][f"erkundung:main_{nm}"] = verdict(done[msk], years, p)
                if n == "main" and label == "qualifiziert":   # Erkundung: neue 13D-Fristen ab 2024
                    late = done[pd.to_datetime(done.published, utc=True) >= pd.Timestamp("2024-01-01", tz="UTC")]
                    if len(late):
                        res["combos"][key]["erkundung:main_ab2024"] = verdict(
                            late, max((pd.Timestamp.now(tz="UTC") - pd.Timestamp("2024-01-01", tz="UTC")).days / 365.25, 1.0), p)
    return res, t


def survivorship(ds: pd.DataFrame, trades: pd.DataFrame, bench_loader, p: dict) -> list[str]:
    """Behandlung verschwundener Titel: (1) Imputation aus Endwert (Übernahmepreis bzw. Totalverlust) mit dem
    Ø-Einstand des Aktivisten als Einstiegsnäherung, (2) Manski-Spannweite für den Rest. Kein Nachweis – Sensitivität."""
    m = trades[(trades.variant == "main") & (trades.qualified == True) & (trades.executed == True) & (trades.complete == True)]
    miss = ds[ds.status == "nur Endwert"] if "status" in ds else ds.iloc[0:0]
    if miss.empty or m.empty:
        return []
    share_in = len(m) / max(len(ds[ds.status.str.startswith(("qualifiziert", "verworfen", "außerhalb"), na=False)]), 1)
    cost_rt = 2 * (p["costs"]["min_half_spread"] + p["costs"]["fx_and_fees"]
                   + p["costs"]["fixed_fee_eur_per_order"] / p["order"]["position_eur"])
    imp = []
    for _, r in miss.iterrows():
        entry, typ = r.get("activist_cost"), r.get("terminal_type")
        if typ == "bankruptcy":
            g = -1.0
        elif typ in ("cash_merger", "going_private", "mixed") and entry and r.get("terminal_cash"):
            g = float(r["terminal_cash"]) / (float(entry) * 1.03) - 1   # Einstieg ~ Limit über Aktivisten-Einstand
        else:
            continue
        try:
            b = bench_loader("US", date.fromisoformat(r["published"][:10]) - timedelta(days=5),
                             date.fromisoformat(str(r.get("terminal_date") or r["published"])[:10]) + timedelta(days=5))["close"]
            br = float(b.iloc[-1] / b.iloc[0] - 1)
        except Exception:
            br = 0.0
        imp.append(max(g, -1.0) - br - cost_rt)
    n_c, mu = len(m), m.net_excess.mean()
    n_m = max(len(miss) - len(imp), 0) * share_in   # nur nicht imputierte Fälle zählen als offen
    lo_v = -1.0 - cost_rt                          # echte Untergrenze: Totalverlust (Kurslücke durch den Stopp)
    hi_v = float(m.net_excess.max())               # echte Obergrenze: bester beobachteter Fall
    base_n = n_c + len(imp) * share_in
    base_sum = n_c * mu + sum(imp) * share_in
    lo = (base_sum + n_m * lo_v) / (base_n + n_m); hi = (base_sum + n_m * hi_v) / (base_n + n_m)
    L = ["## Survivorship (Sensitivität, kein Nachweis)", "",
         f"- Kampagnen nur mit Endwert: {len(miss)}; davon nach Universumsquote erwartet handelbar: {n_m:.0f}",
         f"- Endwert-Typen: " + ", ".join(f"{k} {v}" for k, v in miss.terminal_type.value_counts().items())]
    if imp:
        comb = (n_c * mu + sum(imp) * share_in) / (n_c + len(imp) * share_in)
        L.append(f"- Imputiert aus Endwert: {len(imp)} Fälle, Ø {pd.Series(imp).mean():+.1%} → kombiniert Ø {comb:+.1%}")
    L += [f"- Manski-Spannweite des Haupttests: **{lo:+.1%} bis {hi:+.1%}** (offene Fälle alle Totalverlust bzw. alle bester Fall)",
          f"- Urteil robust gegen Survivorship nur, wenn beide Grenzen auf derselben Seite der 3 %-Schwelle liegen.", ""]
    return L


def alpha_section(res: dict) -> list[str]:
    a = res.get("alpha")
    if not a:
        return []
    dq = a["data_quality"]
    L = ["## Bestätigender Alpha-Test (Holdout ab 2023, nur geprüfte Daten, Holm-korrigiert)", "",
         f"**Ergebnis: {a['status'].upper()}**", "",
         f"- Geprüfte Kampagnen: {dq['n_campaigns_valid']} | ungeprüft (zählen nicht): {dq['n_campaigns_unverified']} "
         f"| Anteil geprüft {dq['verified_share']:.0%}",
         f"- Protokoll: {'eingefroren am ' + str(a['freeze'].get('frozen_at')) if a['frozen'] else a['freeze']['reason']}", ""]
    if a.get("holdout"):
        L += ["| Region/Klasse | Urteil | n | Ø netto | 90 %-Bereich | p (roh) | p (Holm) |", "|---|---|---|---|---|---|---|"]
        for k, v in sorted(a["holdout"].items()):
            if v.get("n"):
                fmt = lambda x: "–" if x is None or x != x else f"{x:.3f}"
                L.append(f"| {k} | {v['status']} | {v['n']} | {v['mean']:+.1%} | {v['ci_low']:+.1%} … {v['ci_high']:+.1%} | "
                         f"{fmt(v.get('p_raw'))} | {fmt(v.get('p_holm'))} |")
    else:
        L.append(a.get("holdout_note", ""))
    lr = res.get("learning", {})
    L += ["", f"**Lernstufe:** {lr.get('status')} · aktiv: {'JA' if lr.get('active') else 'NEIN'} · "
              f"abgeschlossene geprüfte Kampagnen {lr.get('closed_campaigns', 0)} / 200", ""]
    return L


def hypotheses(trades: pd.DataFrame) -> list[str]:
    """H2 (Forderungsart) und H3 (Stabilität der Aktivisten) – Erkundung auf qualifizierten Haupttrades."""
    t = trades[(trades.variant == "main") & (trades.qualified == True) & (trades.executed == True) & (trades.complete == True)]
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


def write_report(res: dict, funnel, ds: pd.DataFrame, trades: pd.DataFrame, path=None, bench_loader=None) -> str:
    path = path or REPORTS / f"event_study_{date.today():%Y%m%d}.md"
    L = ["# Ereignisstudie – Research Protocol v1", "",
         "Haupttest: **qualifiziert:main** (Orderregeln, Ausstieg inkl. −30 %-Stopp, Kosten, gegen Regionsindex). "
         "Alle anderen Zeilen sind Erkundung bzw. Schattenportfolio und zählen nicht als Nachweis.", "",
         "## Datentrichter", ""]
    L += [f"- {k}: {v}" for k, v in funnel.most_common()]
    gap = sum(v for k, v in funnel.items() if k.startswith("Survivorship: Endwert")) / max(funnel.get("kampagnen", 1), 1)
    rec = (ds.price_source == "tiingo").sum() if "price_source" in ds else 0
    L += ["", f"**Survivorship: {rec} verschwundene Titel über historischen Ticker + Tiingo zurückgeholt; "
          f"{gap:.0%} der Kampagnen weiterhin ohne Kursreihe** (nur Endwert, siehe Spannweite unten).", "",
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
    if bench_loader is not None and not trades.empty:
        L += survivorship(ds, trades, bench_loader, params())
    L += alpha_section(res)
    L += hypotheses(trades) if not trades.empty else []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(L), encoding="utf-8")
    ds.to_csv(path.with_name(path.stem + "_dataset.csv"), index=False)
    trades.to_csv(path.with_name(path.stem + "_trades.csv"), index=False)
    path.with_suffix(".json").write_text(json.dumps({"result": res, "funnel": dict(funnel), "alpha": res.get("alpha"),
                                                     "data_quality": res.get("data_quality"), "learning": res.get("learning")},
                                                    default=str, indent=1))
    return str(path)
