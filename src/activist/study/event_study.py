"""Phase 1: Ereignisstudie. Haupttest = tatsächliche Handelsstrategie (Order-, Ausstiegs-, Kostenregeln)
auf qualifizierten Kampagnen; verworfene Kampagnen laufen als Schattenportfolio mit."""
from __future__ import annotations
import json
from datetime import date, timedelta
import pandas as pd
from ..common import REPORTS, atomic_write_text, params
from ..paper.execution import cost_one_side, simulate_entry, simulate_exit
from .alpha import dev_only, main_trades, run_alpha
from .stats import verdict

OPEN = ("keine Folgekurse", "offen (Datenende)")
BENCH_MAX_DAILY_MOVE = 0.25    # ein breiter Index bewegt sich nie um 25 % an einem Tag -> sonst fehlerhafte Reihe


def run_campaign(c: dict, px: pd.DataFrame, bench: pd.DataFrame, p: dict, stop=True, max_days=None,
                 exit_events=(), fx_local: float = 1.0, fx_at=None, bench_fx_at=None) -> dict:
    """fx_local = Einheiten Landeswährung je EUR am Entscheidungstag (Volumen-Obergrenze in Landeswährung)."""
    pos_local = p["order"]["position_eur"] * fx_local
    fill = simulate_entry(px, pd.Timestamp(c["published"]), p["order"], pos_local)
    if not fill.filled:
        return {"executed": False, "reason": fill.reason}
    import math
    entry_fx = fx_at(fill.fill_date.date()) if fx_at else fx_local
    if not math.isfinite(entry_fx) or entry_fx <= 0:
        return {"executed": False, "reason": "Einstiegswechselkurs fehlt"}
    local_value = pos_local * fill.fill_ratio
    value = local_value / entry_fx
    if c["region"] == "JP":
        lot = int(p["universe"]["JP"].get("lot_size", 100))
        qty = int(local_value / fill.price / lot) * lot
        if qty <= 0:
            return {"executed": False, "reason": "Mindestorder am Fill nicht erreicht"}
        local_value = qty * fill.price
        value = local_value / entry_fx
    xd, xp, why = simulate_exit(px, fill.fill_date, fill.price, p["exit"], exit_events, stop, max_days)
    gross = xp / fill.price - 1
    buy = cost_one_side(c.get("half_spread"), p["costs"], c["region"], "buy", value)
    buy += p["costs"].get("fixed_fee_eur_per_order", 0.0) * max(fill.fill_days - 1, 0) / max(value, 1e-9)
    entry_fields = {"invested_eur": value, "buy_cost": buy, "entry_fx_local_per_eur": entry_fx,
                    "entry": fill.price, "fill_ratio": fill.fill_ratio}
    # Vergleichsindex zeitgleich zu den Aktienkursen: Einstieg zur Eröffnung, Ausstieg zur Eröffnung (Meldung/Stopp)
    # bzw. zum Schluss (Haltedauer/Datenende)
    bo = bench["open"] if "open" in bench else bench["close"]
    b0 = bo.get(fill.fill_date, float("nan"))
    b1 = (bo if why in ("Aktivist reduziert", "Stopp") else bench["close"]).get(xd, float("nan"))
    if pd.isna(b0) or pd.isna(b1):
        return {"executed": True, "complete": False, "reason": "Vergleichsindex fehlt", "entry_date": fill.fill_date,
                "exit_date": xd, "exit_reason": why, **entry_fields}
    if b0 <= 0 or b1 <= 0 or bench["close"].loc[fill.fill_date:xd].pct_change().abs().max() > BENCH_MAX_DAILY_MOVE:
        return {"executed": True, "complete": False, "reason": "Vergleichsindex unplausibel", "entry_date": fill.fill_date,
                "exit_date": xd, "exit_reason": why, **entry_fields}
    bret = float(b1 / b0 - 1)
    exit_fx = fx_at(xd.date()) if fx_at else fx_local
    if not math.isfinite(exit_fx) or exit_fx <= 0:
        return {"executed": True, "complete": False, "reason": "Ausstiegswechselkurs fehlt",
                "entry_date": fill.fill_date, "exit_date": xd, "invested_eur": value, "buy_cost": buy}
    fx_ratio = entry_fx / exit_fx
    exit_value_eur = value * (1 + gross) * fx_ratio
    sell = cost_one_side(c.get("half_spread"), p["costs"], c["region"], "sell", exit_value_eur)
    cost = buy + sell
    cash_net = (1 + gross) * (1 - sell) / (1 + buy) - 1
    net_eur = exit_value_eur * (1 - sell) / (value * (1 + buy)) - 1
    bfx0 = bench_fx_at(fill.fill_date.date()) if bench_fx_at else entry_fx
    bfx1 = bench_fx_at(xd.date()) if bench_fx_at else exit_fx
    bench_eur = (1 + bret) * bfx0 / bfx1 - 1 if all(math.isfinite(x) and x > 0 for x in (bfx0,bfx1)) else None
    net_excess = net_eur - bench_eur if bench_eur is not None else None
    return {"executed": True, "complete": why not in OPEN and bench_eur is not None, "fill_ratio": fill.fill_ratio,
            "entry_date": fill.fill_date, "entry": fill.price, "exit_date": xd, "exit": xp, "exit_reason": why,
            "gross": gross, "bench": bret, "cost": cost, "net_excess": net_excess,
            "legacy_net_excess_local_approx": gross - bret - cost, "benchmark_eur": bench_eur,
            "benchmark_entry_fx": bfx0, "benchmark_exit_fx": bfx1,
            "buy_cost": buy, "sell_cost": sell, "invested_eur": value, "cash_net_local": cash_net,
            "cash_net_excess_local": cash_net - bret,
            "entry_fx_local_per_eur": entry_fx, "exit_fx_local_per_eur": exit_fx,
            "exit_value_eur": exit_value_eur, "net_eur": net_eur,
            "net_excess_eur_same_currency_benchmark": net_eur - ((1 + bret) * fx_ratio - 1),
            "hold_days": int((px.index > fill.fill_date).sum() if why in OPEN else ((px.index > fill.fill_date) & (px.index <= xd)).sum())}


def simulate(ds: pd.DataFrame, price_loader, bench_loader, variants: bool = True, fx_loader=None, bench_fx_loader=None) -> pd.DataFrame:
    """Datenbeschaffung + Handelssimulation (langsam, Netzwerk); die Auswertung steht getrennt in evaluate()."""
    p = params()
    names = ["main"] + (["no_stop"] + [f"hold_{d}" for d in p["study"]["explore_holding_days"]] if variants else [])
    trades = []
    for _, c in ds[ds.status.str.startswith(("qualifiziert", "verworfen"), na=False)].iterrows():
        c = c.to_dict()
        d0 = date.fromisoformat(c["published"][:10])
        try:
            if c.get("price_source") == "tiingo":      # Survivorship: dieselbe Kursquelle wie im Datensatz
                from ..sources import prices
                px = prices.tiingo_cached(c["ticker"], d0 - timedelta(days=45), d0 + timedelta(days=420))
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
            def rate_at(day):
                try:
                    return float(fx_loader(c["ticker"], c["region"], day))
                except Exception:
                    return float("nan")
            def bench_rate_at(day):
                try:
                    return float(bench_fx_loader(c["region"], day))
                except Exception:
                    return float("nan")
            r = run_campaign(c, px, bench, p, stop, md, ex if not md else (), fxl, rate_at if fx_loader else None,
                             bench_rate_at if bench_fx_loader else None)
            trades.append({**{k: c[k] for k in ("campaign_key", "activist", "filer_id", "region", "signal_class",
                                                 "status", "ticker", "published", "demand")},
                           **{k: c.get(k) for k in ("event_id", "issuer_id", "issuer_name", "filer_name", "url", "demand_evidence", "activist_evidence_kind", "prior_proposal_count", "identity_status", "identity_issues", "identity_verified")},
                           "mcap": c.get("mcap"), "verified": c.get("verified") is True, "dq_missing": c.get("dq_missing"),
                           "stake": c.get("stake"), "runup_20d": c.get("runup_20d"),
                           "adv_eur": (c.get("adv") / fxl) if c.get("adv") and fxl else None,
                           "price_source": c.get("price_source"),
                           "variant": n, "qualified": c["status"] == "qualifiziert", **r})
    return pd.DataFrame(trades)


def load_trades(path) -> pd.DataFrame:
    """Trades-CSV wieder einlesen (für evaluate): Datumsangaben als Text, Wahrheitswerte ohne Lücken."""
    t = pd.read_csv(path, low_memory=False)
    if "published" in t:
        t["published"] = t["published"].astype(str)
    for c in ("qualified", "executed"):
        if c in t:
            t[c] = t[c].fillna(False).astype(bool)
    return t



# Only relax one named candidate filter; never relax universe, data quality,
# minimum lot, missing FX, or execution capacity. No holdout exploration.
ABLATION_FILTERS = ("Spread", "Anstieg 20T", "Kurs über Aktivisten-Einstand",
                    "Großaktionär über Grenze", "Quartalszahlen-Sperre", "Liquidität")


def policy_comparisons(t: pd.DataFrame) -> dict:
    """Event-level comparisons, not a cash-constrained portfolio backtest.
    Use only development cohorts with observed net excess. A filter's
    marginal admissions must have exactly that one rejection reason.
    Exit comparisons are paired on identical campaigns with complete outcomes.
    """
    required = {"variant", "qualified", "executed", "complete", "status", "net_excess",
                "campaign_key", "region", "signal_class", "published"}
    if t.empty or not required.issubset(t.columns):
        return {}
    import numpy as np
    from .alpha import split_of
    d = dev_only(t).copy()
    d["split"] = d.published.map(split_of)
    d = d[(d.executed == True) & (d.complete == True) &
          np.isfinite(pd.to_numeric(d.net_excess, errors="coerce"))]
    out = {}
    for (region, cls, split), g in d.groupby(["region", "signal_class", "split"]):
        main = g[g.variant == "main"]
        if main.campaign_key.duplicated().any():
            continue
        base = main[main.qualified == True]
        def summary(x):
            y = x.net_excess
            return {"n": int(len(y)), "mean": float(y.mean()) if len(y) else None,
                    "worst": float(y.min()) if len(y) else None}
        base_summary = summary(base)
        filters = {}
        for f in ABLATION_FILTERS:
            admitted = main[main.status == "verworfen: " + f]
            relaxed = pd.concat([base, admitted])
            a, b = summary(admitted), summary(relaxed)
            filters[f] = {"added": a, "relaxed": b,
                          "delta_mean": b["mean"] - base_summary["mean"]
                          if a["n"] and base_summary["n"] else None}
        exits = {}
        for variant in sorted(set(g.variant) - {"main"}):
            alt = g[(g.variant == variant) & (g.qualified == True)]
            if alt.campaign_key.duplicated().any():
                continue
            pair = base[["campaign_key", "net_excess"]].merge(
                alt[["campaign_key", "net_excess"]], on="campaign_key", suffixes=("_main", "_alt"))
            delta = pair.net_excess_alt - pair.net_excess_main
            exits[variant] = {"n_paired": int(len(pair)),
                              "delta_mean": float(delta.mean()) if len(pair) else None,
                              "main_worst": float(pair.net_excess_main.min()) if len(pair) else None,
                              "alt_worst": float(pair.net_excess_alt.min()) if len(pair) else None}
        out[f"{region}/{cls}/{split}"] = {"base": base_summary, "filters": filters,
                                           "exits": exits, "status": "exploratory_only"}
    return out


def evaluate(t: pd.DataFrame) -> dict:
    """Reine Auswertung der Trades-Tabelle (kein Netzwerk). Erkundungstabellen nur mit Entwicklungszeitraum (dev_only)."""
    p = params()
    res = {"combos": {}, "execution": {}}
    if t.empty:
        return res
    names = sorted(t.variant.unique(), key=lambda v: v != "main")
    # Bestätigender Test (Train/Validierung/Holdout, nur geprüfte Daten, Holm) + Lernstatus
    from ..learn import ranker
    res["alpha"] = run_alpha(t)
    res["data_quality"] = res["alpha"]["data_quality"]
    res["policy_comparisons"] = policy_comparisons(t)
    from .control_portfolios import compare
    res["control_portfolios"] = compare(t, start_cash=p["study"]["econ_portfolio_eur"])
    mt = main_trades(t)
    if len(mt):
        mt = ranker.pit_activist_record(mt)
        # Purge outcomes not yet known at the next evaluation boundary.
        exit_day = mt.exit_date.astype(str).str[:10]
        tr = mt[(mt.split == "train") & (exit_day < "2020-01-01")]
        va = mt[(mt.split == "valid") & (exit_day < "2023-01-01")]
        ho = mt[mt.split == "holdout"]
        res["learning"] = ranker.decide(tr, va)
        # Merkmalsanalyse nur auf Trainingsdaten (Validierung und Holdout bleiben unangetastet)
        res["learning"]["features"] = ranker.feature_analysis(tr) if len(tr) else {}
        if res["learning"]["status"] != ranker.ST_INSUFFICIENT:
            res["learning"]["card_full"] = ranker.card_full(tr)
            if res["alpha"]["frozen"] and res["learning"]["status"] == ranker.ST_CHALLENGER:
                res["learning"]["holdout"] = ranker.challenger_holdout(res["learning"]["card_full"], ho)
        res["outcomes"] = mt
    else:
        res["learning"] = {"status": ranker.ST_INSUFFICIENT, "active": False, "n_train": 0, "n_valid": 0}
    res["learning"]["closed_campaigns"] = int(len(mt))
    res["learning"]["active"] = False          # nie automatisch aktiv: Änderung der Handelsregeln nur manuell
    for n in names:
        tv = t[t.variant == n]
        if "executed" not in tv:
            continue
        for label, sub in (("qualifiziert", tv[tv.qualified]), ("schatten", tv[~tv.qualified])):
            for (reg, cls), g in sub.groupby(["region", "signal_class"]):
                key = f"{reg}/{cls}"
                if n == "main" and label == "qualifiziert":     # reine Zählung ohne Renditen: alle Daten
                    res["execution"][key] = {"signale": len(g), "ausgeführt": int((g.executed == True).sum()),
                                             "offen": int(((g.executed == True) & (g.complete == False)).sum())}
                g = dev_only(g)
                done = g[(g.executed == True) & (g.complete == True)]
                if done.empty:
                    continue
                d = pd.to_datetime(g.published, utc=True, format="ISO8601")
                years = max((d.max() - d.min()).days / 365.25, 1.0)
                res["combos"].setdefault(key, {})[f"{label}:{n}"] = verdict(done, years, p)
                if n == "main" and label == "qualifiziert" and "mcap" in done:   # Erkundung: Größenklassen
                    for nm, msk in (("bis_2Mrd", done.mcap <= 2e9), ("2_bis_8Mrd", done.mcap > 2e9)):
                        if msk.sum():
                            res["combos"][key][f"erkundung:main_{nm}"] = verdict(done[msk], years, p)
    return res


def run(ds: pd.DataFrame, price_loader, bench_loader, variants: bool = True, fx_loader=None) -> tuple[dict, pd.DataFrame]:
    t = simulate(ds, price_loader, bench_loader, variants, fx_loader)
    return evaluate(t), t


def survivorship(ds: pd.DataFrame, trades: pd.DataFrame, bench_loader, p: dict) -> list[str]:
    """Behandlung verschwundener Titel: (1) Imputation aus Endwert (Übernahmepreis bzw. Totalverlust) mit dem
    Ø-Einstand des Aktivisten als Einstiegsnäherung, (2) Manski-Spannweite für den Rest. Kein Nachweis – Sensitivität."""
    m = dev_only(trades)
    m = m[(m.variant == "main") & (m.qualified == True) & (m.executed == True) & (m.complete == True)]
    nets = m.net_excess.dropna()
    miss = ds[ds.status == "nur Endwert"] if "status" in ds else ds.iloc[0:0]
    if miss.empty or nets.empty:
        return []
    share_in = len(nets) / max(len(ds[ds.status.str.startswith(("qualifiziert", "verworfen", "außerhalb"), na=False)]), 1)
    cost_rt = 2 * (p["costs"]["min_half_spread"] + p["costs"]["fx_and_fees"]
                   + p["costs"]["fixed_fee_eur_per_order"] / p["order"]["position_eur"])
    cases = []
    for _, r in miss.iterrows():
        entry, tc, typ = r.get("activist_cost"), r.get("terminal_cash"), r.get("terminal_type")
        if typ == "bankruptcy":
            g = -1.0
        elif typ in ("cash_merger", "going_private", "mixed") and pd.notna(entry) and pd.notna(tc) and float(entry) > 0 and float(tc) > 0:
            g = float(tc) / (float(entry) * 1.03) - 1   # Einstieg ~ Limit über Aktivisten-Einstand
        else:
            continue
        d0 = pd.Timestamp(str(r["published"])[:10]) - pd.Timedelta(days=5)
        cases.append((g, d0, pd.Timestamp(str(r.get("terminal_date") if pd.notna(r.get("terminal_date")) else r["published"])[:10])
                      + pd.Timedelta(days=5)))
    b, imp, skipped = None, [], 0
    if cases:                                       # Vergleichsindex einmal für den ganzen Zeitraum laden
        try:
            b = bench_loader("US", min(c[1] for c in cases).date(), max(c[2] for c in cases).date())["close"].dropna()
        except Exception:
            b = None
    for g, s0, e0 in cases:
        w = b.loc[s0:e0] if b is not None else []
        if len(w) < 2:                              # kein Index im Fenster: nicht mit 0 % imputieren
            skipped += 1
            continue
        imp.append(max(g, -1.0) - float(w.iloc[-1] / w.iloc[0] - 1) - cost_rt)
    n_c, mu = len(nets), nets.mean()
    n_m = max(len(miss) - len(imp), 0) * share_in   # nur nicht imputierte Fälle zählen als offen
    lo_v = -1.0 - cost_rt                          # echte Untergrenze: Totalverlust (Kurslücke durch den Stopp)
    hi_v = float(nets.max())               # echte Obergrenze: bester beobachteter Fall
    base_n = n_c + len(imp) * share_in
    base_sum = n_c * mu + sum(imp) * share_in
    lo = (base_sum + n_m * lo_v) / (base_n + n_m); hi = (base_sum + n_m * hi_v) / (base_n + n_m)
    L = ["## Survivorship (Sensitivität, kein Nachweis)", "",
         f"- Kampagnen nur mit Endwert: {len(miss)}; davon nach Universumsquote erwartet handelbar: {n_m:.0f}",
         f"- Endwert-Typen: " + ", ".join(f"{k} {v}" for k, v in miss.terminal_type.value_counts().items())]
    if imp:
        comb = (n_c * mu + sum(imp) * share_in) / (n_c + len(imp) * share_in)
        L.append(f"- Imputiert aus Endwert: {len(imp)} Fälle, Ø {pd.Series(imp).mean():+.1%} → kombiniert Ø {comb:+.1%}")
    if skipped:
        L.append(f"- Imputationen ohne Vergleichsindex übersprungen: {skipped}")
    L += [f"- Manski-Spannweite des Haupttests: **{lo:+.1%} bis {hi:+.1%}** (offene Fälle alle Totalverlust bzw. alle bester Fall)",
          f"- Urteil robust gegen Survivorship nur, wenn beide Grenzen auf derselben Seite der 3 %-Schwelle liegen.", ""]
    return L


def learning_section(res: dict) -> list[str]:
    lr = res.get("learning") or {}
    L = ["## Champion vs. Challenger und Merkmalsanalyse (Training ≤ 2019, feste Regeln)", ""]
    ev = lr.get("evaluation")
    if ev:
        L.append(f"- Validierung 2020–2022: Challenger Ø {ev['mean_selected']:+.1%} (n={ev['n_selected']}) vs. Champion "
                 f"Ø {ev['mean_all']:+.1%} (n={ev['n_all']}), Differenz {ev['diff']:+.1%}, p={ev['p_value']:.3f} → {lr['status']}")
    else:
        L.append(f"- {lr.get('status')} (n Training {lr.get('n_train', 0)}, Validierung {lr.get('n_valid', 0)}; "
                 f"Mindestumfang {lr.get('min_campaigns', 200)})")
    if lr.get("holdout"):
        L.append(f"- Holdout: {lr['holdout']['status']}")
    for f, v in (lr.get("features") or {}).items():
        if v.get("diff") is not None:
            L.append(f"- {f}: {v['compare']} = {v['diff']:+.1%} (90 %: {v['ci90'][0]:+.1%} … {v['ci90'][1]:+.1%}) → {v['status']}")
        else:
            L.append(f"- {f}: {v['status']}")
    return L + ["", "Die Basisstrategie bleibt aktiv; der Challenger handelt nicht und ändert keine Regeln.", ""]


def alpha_section(res: dict) -> list[str]:
    a = res.get("alpha")
    if not a:
        return []
    dq = a["data_quality"]
    L = ["## Bestätigender Alpha-Test (Holdout ab 2023, nur geprüfte Daten, Holm-korrigiert)", "",
         f"**Ergebnis: {a['status'].upper()}**", "",
         f"- Geprüfte Kampagnen: {dq['n_campaigns_valid']} | ungeprüft (zählen nicht): {dq['n_campaigns_unverified']} "
         f"| Anteil geprüft {dq['verified_share']:.0%}",
         "- Kursprüfung: USA zweite Kursreihe (Tiingo); Japan nur Kursniveau gegen EDINET-Preisangaben des Meldenden "
         "(schwächer, eigene Zeilen JP/…)",
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
    t = dev_only(trades)
    t = t[(t.variant == "main") & (t.qualified == True) & (t.executed == True) & (t.complete == True)]
    if t.empty:
        return []
    L = ["## H2: Nettoüberrendite nach Forderung (Erkundung)", "", "| Forderung | n | Ø netto | positiv |", "|---|---|---|---|"]
    for dm, g in t.groupby(t.demand.fillna("unbekannt")):
        L.append(f"| {dm} | {len(g)} | {g.net_excess.mean():+.1%} | {(g.net_excess > 0).mean():.0%} |")
    d = pd.to_datetime(t.published, utc=True, format="ISO8601"); mid = d.sort_values().iloc[len(d) // 2]
    a = t.assign(h=(d >= mid).astype(int)).groupby(["activist", "h"]).net_excess.mean().unstack()
    a = a.dropna()
    L += ["", "## H3: Stabilität der Aktivisten (Rangkorrelation erste vs. zweite Zeithälfte)", ""]
    if len(a) >= 5:
        rho = a[0].rank().corr(a[1].rank())
        L.append(f"Spearman ρ = {rho:+.2f} über {len(a)} Aktivisten mit Trades in beiden Hälften.")
    else:
        L.append(f"Nicht beurteilbar: nur {len(a)} Aktivisten mit Trades in beiden Hälften (mind. 5).")
    return L + [""]


def extremes(trades: pd.DataFrame) -> list[str]:
    """Listing auffälliger Nettoüberrenditen zur Prüfung der Kursdaten (keine automatische Ausschließung)."""
    L = ["## Extremwerte (zur Prüfung der Kursdaten)", ""]
    if trades.empty or "executed" not in trades:
        return L + ["Keine Trades.", ""]
    mt = trades[trades.variant == "main"]
    n_bad = int((mt.reason == "Vergleichsindex unplausibel").sum()) if "reason" in mt else 0
    x = mt.assign(net_excess=mt.get("net_excess", float("nan")))
    x = x[(x.executed == True) & (x.complete == True) & (x.net_excess.abs() > 1.0)].sort_values("net_excess", key=abs, ascending=False).head(20)
    L.append(f"Wegen unplausiblem Vergleichsindex ausgeschlossen: {n_bad}")
    if x.empty:
        return L + ["", "Keine abgeschlossenen Haupttrades mit |Nettoüberrendite| > 100 %.", ""]
    L += ["", "| Ticker | Region | veröffentlicht | brutto | Index | netto |", "|---|---|---|---|---|---|"]
    L += [f"| {r.ticker} | {r.region} | {str(r.published)[:10]} | {r.gross:+.1%} | {r.bench:+.1%} | {r.net_excess:+.1%} |"
          for r in x.itertuples()]
    return L + [""]


def write_report(res: dict, funnel, ds: pd.DataFrame, trades: pd.DataFrame, path=None, bench_loader=None) -> str:
    path = path or REPORTS / f"event_study_{date.today():%Y%m%d}.md"
    L = ["# Ereignisstudie – Research Protocol v1", "",
         "Haupttest: **qualifiziert:main** (Orderregeln, Ausstieg inkl. −30 %-Stopp, Kosten, gegen Regionsindex). "
         "Alle anderen Zeilen sind Erkundung bzw. Schattenportfolio und zählen nicht als Nachweis. "
         "Die Erkundungstabellen enthalten nur den Entwicklungszeitraum (Veröffentlichung bis 2022-12-31); der Zeitraum ab 2023 "
         "wird ausschließlich im versiegelten bestätigenden Test ausgewertet.", "",
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
    L += extremes(trades)
    if bench_loader is not None and not trades.empty:
        L += survivorship(ds, trades, bench_loader, params())
    L += alpha_section(res)
    L += learning_section(res)
    L += hypotheses(trades) if not trades.empty else []
    atomic_write_text(path, "\n".join(L))
    atomic_write_text(path.with_name(path.stem + "_dataset.csv"), ds.to_csv(index=False))
    out = res.pop("outcomes", None)
    if out is not None and len(out):
        cols = [c for c in ("campaign_key", "published", "entry_date", "exit_date", "exit_reason", "region", "signal_class",
                            "activist", "demand", "stake", "runup_20d", "adv_eur", "activist_prior_n", "activist_prior_mean",
                            "gross", "bench", "cost", "net_excess", "split", "verified", "event_id", "issuer_id", "issuer_name",
                            "filer_id", "filer_name", "ticker", "url", "demand_evidence", "activist_evidence_kind",
                            "prior_proposal_count", "dq_missing") if c in out]
        atomic_write_text(REPORTS / "campaign_outcomes.csv", out[cols].to_csv(index=False))   # Ergebnis je abgeschlossener Kampagne
    card = (res.get("learning") or {}).get("card_full")
    if card:
        atomic_write_text(REPORTS / "scorecard.json", json.dumps({"status": res["learning"]["status"], "card": card},
                                                                 default=str, indent=1, ensure_ascii=False))
    atomic_write_text(path.with_name(path.stem + "_trades.csv"), trades.to_csv(index=False))
    from .alpha import _hashes
    atomic_write_text(path.with_suffix(".json"), json.dumps({"protocol_hashes": _hashes(), "result": res, "funnel": dict(funnel), "alpha": res.get("alpha"),
                                                              "data_quality": res.get("data_quality"), "learning": res.get("learning")},
                                                             default=str, indent=1))
    return str(path)
