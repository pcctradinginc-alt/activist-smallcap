"""Transparenter Herausforderer: Scorecard aus Bucket-Mittelwerten (nur numpy/pandas, kein ML).

Der Herausforderer wird NIE aktiviert; `decide` liefert immer active=False.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from ..confidence.basic import shrink_to_mean

FEATURES = ["activist_prior_mean", "demand", "stake", "runup_20d", "adv_eur"]   # vorab festgelegt, max. 5
CATEGORICAL = {"demand"}
SHRINK_K_ACTIVIST = 10
SHRINK_K_BUCKET = 20
MIN_CAT_OBS = 10
ST_INSUFFICIENT = "zu wenig Daten"
ST_CHALLENGER = "Herausforderer besser auf Validierung (nicht aktiv – braucht Bestätigung auf zukünftigen Daten)"
ST_BASE = "Basisstrategie bleibt"


def _clean(o):
    """Rekursiv JSON-tauglich machen (numpy -> Python, NaN -> None)."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (float, np.floating)):
        return None if math.isnan(o) or math.isinf(o) else float(o)
    return o


def _dt(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True, errors="coerce")


def pit_activist_record(trades: pd.DataFrame) -> pd.DataFrame:
    """activist_prior_n / activist_prior_mean strikt punktgenau: nur Zeilen mit exit_date < published der Zeile."""
    df = trades.copy()
    pub, ex = _dt(df["published"]).to_numpy(), _dt(df["exit_date"]).to_numpy()
    y = df["net_excess"].to_numpy(float)
    act = df["activist"].to_numpy()
    ns, means = [], []
    for i in range(len(df)):
        done = ex < pub[i]                      # NaT -> False
        ng = int(done.sum())
        if ng == 0:
            ns.append(0); means.append(float("nan")); continue
        gm = float(y[done].mean())
        sel = done & (act == act[i])
        n = int(sel.sum())
        ns.append(n)
        means.append(float(shrink_to_mean(float(y[sel].mean()), n, gm, SHRINK_K_ACTIVIST)) if n else float("nan"))
    df["activist_prior_n"] = ns
    df["activist_prior_mean"] = means
    return df


def _shrunk(vals: np.ndarray, mu: float, k: int = SHRINK_K_BUCKET) -> float:
    return float((vals.sum() + k * mu) / (len(vals) + k))


def fit_scorecard(train_df: pd.DataFrame, quantile: float = 0.5) -> dict:
    y_all = train_df["net_excess"].to_numpy(float)
    mu = float(np.nanmean(y_all)) if len(y_all) else 0.0
    feats = {}
    for f in FEATURES:
        if f not in train_df:
            continue
        col = train_df[f]
        if f in CATEGORICAL:
            ok = col.notna().to_numpy()
            s = col[ok].astype(str)
            counts = s.value_counts()
            cats = sorted(c for c, n in counts.items() if n >= MIN_CAT_OBS)
            lab = s.where(s.isin(cats), "other")
            yy = pd.Series(y_all[ok], index=s.index)
            buckets = {}
            for b in cats + ["other"]:
                v = yy[lab == b].to_numpy()
                if len(v):
                    buckets[b] = {"value": _shrunk(v, mu), "n": int(len(v))}
            feats[f] = {"type": "categorical", "categories": cats, "buckets": buckets}
        else:
            x = pd.to_numeric(col, errors="coerce").to_numpy(float)
            ok = ~np.isnan(x)
            edges = []
            if ok.sum() >= 3:
                edges = sorted({float(e) for e in np.quantile(x[ok], [1 / 3, 2 / 3])})
            idx = np.searchsorted(np.array(edges), x[ok], side="right") if edges else np.zeros(ok.sum(), int)
            buckets = {}
            for b in range(len(edges) + 1):
                v = y_all[ok][idx == b]
                if len(v):
                    buckets[str(b)] = {"value": _shrunk(v, mu), "n": int(len(v))}
            feats[f] = {"type": "numeric", "edges": edges, "buckets": buckets}
    card = {"features": feats, "train_mean": mu, "n_train": int(len(train_df)), "quantile": quantile,
            "shrink_k": SHRINK_K_BUCKET, "fitted_on": {
                "published_min": _rng(train_df, "published")[0], "published_max": _rng(train_df, "published")[1]},
            "threshold": None}
    sc = score(train_df, card)
    card["threshold"] = float(sc.quantile(quantile)) if len(sc) else float("nan")
    return _clean(card)


def _rng(df, col):
    if col not in df or df[col].dropna().empty:
        return (None, None)
    d = _dt(df[col]).dropna()
    return (str(d.min().date()), str(d.max().date()))


def score(df: pd.DataFrame, card: dict) -> pd.Series:
    """Summe der Abweichungen der Bucket-Werte vom Trainingsmittel -> fehlendes Merkmal zählt neutral (0)."""
    mu = card["train_mean"]
    total = pd.Series(0.0, index=df.index)
    for f, spec in card["features"].items():
        if f not in df:
            continue
        b = spec["buckets"]
        if spec["type"] == "categorical":
            s = df[f]
            lab = s.astype(str).where(s.notna())
            lab = lab.where(lab.isin(spec["categories"]), "other").where(s.notna())
            vals = lab.map(lambda c: b[c]["value"] - mu if c in b else 0.0)
        else:
            x = pd.to_numeric(df[f], errors="coerce")
            idx = np.searchsorted(np.array(spec["edges"]), x.to_numpy(float), side="right") if spec["edges"] else np.zeros(len(x), int)
            vals = pd.Series([b[str(i)]["value"] - mu if str(i) in b else 0.0 for i in idx], index=df.index)
            vals = vals.where(x.notna())
        total = total + vals.fillna(0.0)
    return total


def select(df: pd.DataFrame, card: dict, quantile: float | None = None) -> pd.Series:
    """Maske: score >= in Train gespeicherte Schwelle (quantile nur zur Neuberechnung, sonst aus card)."""
    thr = card["threshold"]
    if quantile is not None and quantile != card.get("quantile"):
        raise ValueError("quantile ist beim Fit fixiert: fit_scorecard(train, quantile=...) verwenden")
    if thr is None:
        return pd.Series(False, index=df.index)
    return score(df, card) >= thr


def evaluate(df: pd.DataFrame, mask, reps: int = 2000, seed: int = 7, cluster: str = "activist") -> dict:
    mask = np.asarray(mask, bool)
    y = df["net_excess"].to_numpy(float)
    base = float(y.mean()) if len(y) else float("nan")
    sel = float(y[mask].mean()) if mask.any() else float("nan")
    out = {"n_selected": int(mask.sum()), "n_all": int(len(y)), "mean_selected": sel, "mean_all": base,
           "diff": sel - base if mask.any() else float("nan"), "p_value": float("nan"), "reps": reps, "seed": seed}
    if not mask.any() or len(y) == 0:
        return _clean(out)
    codes, _ = pd.factorize(df[cluster].astype(str))
    g = codes.max() + 1
    s_all = np.bincount(codes, weights=y, minlength=g); n_all = np.bincount(codes, minlength=g).astype(float)
    s_sel = np.bincount(codes, weights=y * mask, minlength=g); n_sel = np.bincount(codes, weights=mask.astype(float), minlength=g)
    if g < 2:
        return _clean(out)
    rng = np.random.default_rng(seed)
    diffs = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, g, g), minlength=g)
        ns = (w * n_sel).sum()
        if ns == 0:
            continue
        diffs.append((w * s_sel).sum() / ns - (w * s_all).sum() / (w * n_all).sum())
    diffs = np.array(diffs)
    if len(diffs):
        out["p_value"] = float(((diffs <= 0).sum() + 1) / (len(diffs) + 1))   # H0: selected - baseline <= 0
    return _clean(out)


def _summary(card: dict) -> dict:
    return {"n_train": card["n_train"], "train_mean": card["train_mean"], "threshold": card["threshold"],
            "fitted_on": card["fitted_on"],
            "features": {f: {"type": s["type"], "edges": s.get("edges"),
                             "buckets": {b: {"value": v["value"], "n": v["n"]} for b, v in s["buckets"].items()}}
                         for f, s in card["features"].items()}}


def decide(train_df: pd.DataFrame, valid_df: pd.DataFrame, min_campaigns: int = 200, reps: int = 2000, seed: int = 7,
           quantile: float = 0.5) -> dict:
    n_tr, n_va = len(train_df), len(valid_df)
    base = {"active": False, "n_train": n_tr, "n_valid": n_va, "min_campaigns": min_campaigns}
    if n_tr + n_va < min_campaigns or n_tr < 100:
        return _clean({**base, "status": ST_INSUFFICIENT})
    card = fit_scorecard(train_df, quantile=quantile)
    ev = evaluate(valid_df, select(valid_df, card), reps=reps, seed=seed)
    p, d = ev["p_value"], ev["diff"]
    better = d is not None and p is not None and d > 0 and p < 0.10
    return _clean({**base, "status": ST_CHALLENGER if better else ST_BASE, "evaluation": ev, "card": _summary(card)})
