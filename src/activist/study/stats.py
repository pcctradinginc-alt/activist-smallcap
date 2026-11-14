"""Statistik: gebündelter Bootstrap (nach Aktivist und nach Monat) und Nachweiskriterien."""
from __future__ import annotations
import numpy as np
import pandas as pd


def cluster_bootstrap_ci(df: pd.DataFrame, col: str, cluster: str, level=0.9, reps=2000, seed=7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    groups = [g[col].to_numpy() for _, g in df.groupby(cluster)]
    if len(groups) < 2:
        return (float("nan"), float("nan"))
    means = []
    for _ in range(reps):
        pick = rng.integers(0, len(groups), len(groups))
        v = np.concatenate([groups[i] for i in pick]); means.append(v.mean())
    a = (1 - level) / 2
    return float(np.quantile(means, a)), float(np.quantile(means, 1 - a))


def summarize(df: pd.DataFrame, col: str = "net_excess", level=0.9, reps=2000) -> dict:
    """Vier Zahlen des Konfidenzfaktors + konservativster Unsicherheitsbereich (Aktivist- vs. Monats-Cluster)."""
    if df.empty:
        return {"n": 0}
    df = df.assign(month=pd.to_datetime(df["entry_date"]).dt.to_period("M").astype(str))
    ci_a = cluster_bootstrap_ci(df, col, "activist", level, reps)
    ci_m = cluster_bootstrap_ci(df, col, "month", level, reps)
    lows = [v for v in (ci_a[0], ci_m[0]) if not np.isnan(v)]
    highs = [v for v in (ci_a[1], ci_m[1]) if not np.isnan(v)]
    lo, hi = (min(lows), max(highs)) if lows else (float("nan"), float("nan"))
    x = df[col]
    return {"n": int(len(df)), "mean": float(x.mean()), "ci_low": float(lo), "ci_high": float(hi),
            "worst_quartile": float(x.quantile(0.25)), "worst": float(x.min()), "p_positive": float((x > 0).mean()),
            "thin_data": len(df) < 20}


def verdict(df: pd.DataFrame, years: float, p: dict) -> dict:
    s = p["study"]; o = p["order"]
    summ = summarize(df, reps=s["bootstrap_reps"], level=s["ci_level"])
    if summ["n"] == 0:
        return {"status": "keine Daten", **summ}
    d = df.sort_values("entry_date"); half = len(d) // 2
    h1, h2 = d.iloc[:half]["net_excess"].mean(), d.iloc[half:]["net_excess"].mean()
    trades_per_year = summ["n"] / max(years, 1e-9)
    pos_frac = 0.05  # Basis-Positionsgröße
    exp_eur = trades_per_year * pos_frac * s["econ_portfolio_eur"] * summ["mean"]
    checks = {
        "n>=min_campaigns": summ["n"] >= s["min_campaigns"],
        "mean>=3%": summ["mean"] >= s["pass_min_mean"],
        "ci_low>0": summ["ci_low"] > 0,
        "both_halves_positive": bool(h1 > 0 and h2 > 0),
        "economics": exp_eur >= s["econ_min_excess_eur"],
    }
    status = "nicht beurteilbar" if not checks["n>=min_campaigns"] else ("bestanden" if all(checks.values()) else "nicht bestanden")
    return {"status": status, **summ, "half1": float(h1), "half2": float(h2),
            "trades_per_year": trades_per_year, "expected_eur_per_year": exp_eur, "checks": checks}
