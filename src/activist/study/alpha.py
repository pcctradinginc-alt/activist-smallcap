"""Bestätigender Alpha-Test (Research Protocol v1).

Zeitliche Trennung nach Veröffentlichungsdatum der Kampagne (kein Mischen über die Zeit):
  Training    bis 2019-12-31        – Entwicklung, Ranking-Anpassung
  Validierung 2020-01-01 – 2022-12-31 – Auswahl/Prüfung von Herausforderern
  Holdout     ab 2023-01-01         – unangetastet; wird NUR ausgewertet, wenn das Protokoll eingefroren ist und
                                       seit dem Einfrieren keine protokollrelevante Datei geändert wurde.

Nachweis je Region/Klasse (eigene Urteile, nie zusammengerechnet), nur geprüfte Daten (verified) und nur der
Haupttest (Orderregeln, Ausstieg mit Stopp, Kosten, Index):
  n >= min_campaigns · Ø >= 3 % · untere 90-%-Grenze > 0 · beide Zeithälften positiv · Wirtschaftlichkeit inkl. Kapazität
  · einseitiger p-Wert (gebündelter Bootstrap, Aktivist und Monat, der größere zählt) nach Holm-Korrektur < alpha.
"""
from __future__ import annotations
import hashlib, json, subprocess
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from ..common import ROOT, atomic_write_text, params
from .stats import verdict

SPLITS = {"train": (None, "2019-12-31"), "valid": ("2020-01-01", "2022-12-31"), "holdout": ("2023-01-01", None)}
FREEZE = ROOT / "PROTOCOL_FREEZE.json"
PROTOCOL_FILES = ["config/params.yaml", "config/risk_limits.yaml", "PROTOCOL.md", "src/activist/study/alpha.py",
                  "src/activist/study/stats.py", "src/activist/study/event_study.py", "src/activist/study/dataset.py",
                  "src/activist/study/dataset_eu.py", "src/activist/paper/execution.py", "src/activist/features/universe.py",
                  "src/activist/features/register.py", "src/activist/parse/filing13d.py",
                  "src/activist/sources/prices.py", "src/activist/learn/ranker.py", "src/activist/risk/rules.py",
                  "src/activist/sources/secmeta.py", "src/activist/sources/sector.py",
                  "src/activist/study/holders.py", "src/activist/sources/hist_ticker.py", "config/activists.yaml",
                  "src/activist/study/control_portfolios.py", "src/activist/paper/portfolio.py",
                  "src/activist/sources/edinet_docs.py", "src/activist/sources/edinet_backfill.py",
                  "src/activist/sources/edgar_backfill.py", "src/activist/common.py", "src/activist/cli.py", "src/activist/study/identity.py",
                  "src/activist/sources/edgar.py", "src/activist/sources/openfigi.py", "src/activist/sources/fx.py",
                  "src/activist/study/funnel.py", "src/activist/sources/campaigns_c.py", "config/identity_evidence.json"]
FAMILY_ALPHA = 0.10
COHORT_LAG_DAYS = 380


# ------------------------------------------------------------------ Einfrieren
def _hashes() -> dict[str, str]:
    out = {}
    for f in PROTOCOL_FILES:
        p = ROOT / f
        out[f] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "fehlt"
    return out


def current_artifact(report_dir, name="event_study_*.json"):
    """Consume only the most recent on-disk result with the current protocol fingerprint."""
    files = sorted(report_dir.glob(name))
    if not files:
        return {}
    try:
        result = json.loads(files[-1].read_text(encoding="utf-8"))
        return result if result.get("protocol_hashes") == _hashes() else {}
    except (OSError, ValueError, AttributeError):
        return {}


def freeze(note: str = "") -> dict:
    """Friert das Protokoll ein (manuell, einmalig vor dem bestätigenden Test). Danach ist jede Änderung an
    protokollrelevanten Dateien sichtbar und sperrt den Holdout, bis neu eingefroren wird (mit Begründung)."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:
        commit = None
    rec = {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "git_commit": commit,
           "splits": SPLITS, "family_alpha": FAMILY_ALPHA, "note": note, "hashes": _hashes()}
    atomic_write_text(FREEZE, json.dumps(rec, indent=1, ensure_ascii=False))
    return rec


def freeze_state() -> dict:
    if not FREEZE.exists():
        return {"frozen": False, "reason": "Protokoll nicht eingefroren"}
    rec = json.loads(FREEZE.read_text())
    changed = [f for f, h in _hashes().items() if rec.get("hashes", {}).get(f) != h]
    if changed:
        return {"frozen": False, "reason": "Protokoll nach dem Einfrieren geändert: " + ", ".join(changed),
                "frozen_at": rec.get("frozen_at")}
    return {"frozen": True, "frozen_at": rec.get("frozen_at"), "git_commit": rec.get("git_commit")}


# ------------------------------------------------------------------ Statistik
def split_of(published: str) -> str:
    d = str(published)[:10]
    if d <= SPLITS["train"][1]:
        return "train"
    if d <= SPLITS["valid"][1]:
        return "valid"
    return "holdout"


def dev_only(trades: pd.DataFrame) -> pd.DataFrame:
    """Nur Entwicklungszeitraum (Veröffentlichung bis Ende Validierung); der Holdout bleibt der Erkundung verschlossen."""
    if "published" not in trades:
        return trades
    return trades[trades.published.astype(str).str[:10] <= SPLITS["valid"][1]]


def bootstrap_p(df: pd.DataFrame, col: str = "net_excess", reps: int = 2000, seed: int = 11) -> float:
    """Einseitiger p-Wert für H0: Mittelwert <= 0. Gebündelt nach Aktivist und nach Monat; der größere zählt.
    Bootstrap unter H0: Werte um den Stichprobenmittelwert zentriert."""
    if len(df) < 2:
        return float("nan")
    d = df.assign(month=pd.to_datetime(df["entry_date"]).dt.to_period("M").astype(str))
    obs = d[col].mean()
    ps = []
    for cl in ("activist", "month"):
        groups = [g[col].to_numpy() - obs for _, g in d.groupby(cl)]
        if len(groups) < 2:
            return float("nan")
        rng = np.random.default_rng(seed)
        sims = np.array([np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))]).mean()
                         for _ in range(reps)])
        ps.append(float((sims >= obs).mean()))
    return max(ps)


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm-Bonferroni-adjustierte p-Werte (monoton)."""
    items = sorted(((k, v) for k, v in pvals.items() if v == v), key=lambda kv: kv[1])
    m, out, run = len(items), {}, 0.0
    for i, (k, v) in enumerate(items):
        run = max(run, min(1.0, (m - i) * v))
        out[k] = run
    for k, v in pvals.items():
        out.setdefault(k, float("nan"))
    return out


# ------------------------------------------------------------------ Test
def main_trades(trades: pd.DataFrame, verified_only: bool = True) -> pd.DataFrame:
    if trades.empty or not {"executed", "complete", "qualified", "variant", "published"}.issubset(trades.columns):
        return trades.iloc[0:0].assign(split=pd.Series(dtype=str))
    # Rechtszensierung vermeiden: nur Kohorten, deren volle Haltedauer (252 Handelstage + Einstiegsfenster) beobachtbar ist
    cutoff = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=COHORT_LAG_DAYS)).strftime("%Y-%m-%d")
    trades = trades[trades.published.astype(str).str[:10] <= cutoff]
    t = trades[(trades.variant == "main") & (trades.qualified == True) & (trades.executed == True) & (trades.complete == True)]
    if verified_only:
        t = t[t.get("verified", pd.Series(False, index=t.index)) == True]
    return t.assign(split=t.published.map(split_of))


def run_alpha(trades: pd.DataFrame, study_years: dict | None = None) -> dict:
    p = params()
    fz = freeze_state()
    t_all = main_trades(trades, verified_only=False)
    t = main_trades(trades, verified_only=True)
    dq = {"n_campaigns_valid": int(len(t)), "n_campaigns_unverified": int(len(t_all) - len(t)),
          "verified_share": float(len(t) / len(t_all)) if len(t_all) else 0.0}
    out = {"frozen": fz["frozen"], "freeze": fz, "data_quality": dq, "development": {}, "holdout": {}}
    years_of = lambda g: max((pd.to_datetime(g.entry_date).max() - pd.to_datetime(g.entry_date).min()).days / 365.25, 1.0)
    for (reg, cls), g in t.groupby(["region", "signal_class"]):
        key = f"{reg}/{cls}"
        dev = g[g.split != "holdout"]
        if len(dev):
            out["development"][key] = {**verdict(dev, years_of(dev), p), "note": "Entwicklung (Training+Validierung), kein Nachweis"}
    if not fz["frozen"]:
        out["status"] = "nicht beurteilbar"
        out["holdout_note"] = "Holdout versiegelt: " + fz["reason"]
        return out
    # The prior protocol already published a US/A holdout analysis on 2026-11-18.
    # A fresh hash cannot make those same historical campaigns unseen again.
    from datetime import date
    eligible_start = date.fromisoformat(fz["frozen_at"][:10])
    hold = t[(t.split == "holdout") & (t.published.str[:10] > str(eligible_start))]
    pv, res = {}, {}
    for (reg, cls), g in hold.groupby(["region", "signal_class"]):
        key = f"{reg}/{cls}"
        v = verdict(g, years_of(g), p)
        res[key] = v
        if v.get("n", 0) >= p["study"]["min_campaigns"]:
            pv[key] = bootstrap_p(g, reps=p["study"]["bootstrap_reps"])
    adj = holm(pv)
    for key, v in res.items():
        v["p_raw"], v["p_holm"] = pv.get(key), adj.get(key)
        ok_stat = v.get("p_holm") is not None and v["p_holm"] == v["p_holm"] and v["p_holm"] < FAMILY_ALPHA
        if v["status"] == "bestanden" and not ok_stat:
            v["status"] = "nicht bestanden (Mehrfachtest)"
        v["checks"] = {**v.get("checks", {}), f"Holm p<{FAMILY_ALPHA}": bool(ok_stat)}
        out["holdout"][key] = v
    passed = [k for k, v in out["holdout"].items() if v["status"] == "bestanden"]
    judged = [k for k, v in out["holdout"].items() if v["status"] != "nicht beurteilbar"]
    out["status"] = "nachgewiesen" if passed else ("nicht nachgewiesen" if judged else "nicht beurteilbar")
    out["passed"] = passed
    return out
