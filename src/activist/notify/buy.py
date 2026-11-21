"""Papier-Kauf-Mail aus einer neuen Order. Fehler beim Versand brechen den Tageslauf nie ab."""
from __future__ import annotations
import glob, json, math
from datetime import timedelta
from ..common import JOURNAL, file_hash
from .mail import send
from .templates import buy_mail


def _pct(v, signed=True):
    return "–" if v is None or (isinstance(v, float) and math.isnan(v)) else (f"{v:+.1%}" if signed else f"{v:.1%}")


def _stats(combo: str) -> dict:
    """Kennzahlen der Vergleichsfälle aus dem neuesten Studienbericht (Entwicklung, sonst Holdout)."""
    files = sorted(glob.glob("reports/event_study_????????.json"))
    if not files:
        return {}
    a = json.load(open(files[-1])).get("alpha", {})
    return (a.get("development") or {}).get(combo) or (a.get("holdout") or {}).get(combo) or {}


def _val(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else x


def compose(o: dict, r: dict, combo: str, today) -> tuple[str, str, str]:
    s = _stats(combo)
    against = []
    if not o.get("validated"):
        against.append(f"Kombination {combo} hat den Alpha-Nachweis NICHT bestanden (Status: {s.get('status', 'keine Daten')}).")
    if s.get("mean") is not None and s["mean"] < 0:
        against.append(f"Vergleichsfälle im Schnitt negativ ({_pct(s['mean'])}).")
    if not r.get("verified"):
        against.append("Kurse nicht mit zweiter Quelle bestätigt.")
    if (r.get("demand") or "none") == "none":
        against.append("Keine öffentliche Forderung des Melders erkennbar (evtl. kein echter Aktivist).")
    against.append("Small Caps: hohe Schwankung, Spread und geringe Liquidität.")
    stake = _val(r.get("stake"))
    d = {"ticker": o["ticker"], "region": o["region"], "cls": o.get("signal_class") or "?",
         "activist": o.get("activist") or o.get("filer_name") or "?", "stake": _pct(stake, False) if stake else "?",
         "company": r.get("issuer_name") or o["ticker"], "when": str(o.get("published"))[:16], "form": r.get("form") or "?",
         "demand": o.get("demand") or "none", "evidence": _val(r.get("demand_evidence")) or "kein Textbeleg",
         "url": o.get("url") or "", "campaign": o.get("campaign_key") or "",
         "limit": f"{o['limit_hint']:.2f}" if o.get("limit_hint") else "–", "valid": f"{o.get('max_fill_days', 3)} Handelstage ab {today}",
         "size": f"{o['position_eur']:.0f} € (≈ {o.get('position_eur_local') or 0:.0f} Stück)",
         "size_short": f"{o['position_eur']:.0f} €", "costs": f"≈ {o.get('buy_cost_est', 0):.2%} je Seite",
         "conf": {"mean": _pct(s.get("mean")), "ci": f"{_pct(s.get('ci_low'))} bis {_pct(s.get('ci_high'))}",
                  "n": str(s.get("n", 0)), "q25": _pct(s.get("worst_quartile")), "worst": _pct(s.get("worst")),
                  "pos": _pct(s.get("p_positive"), False)},
         "risk": "Portfolioregeln bestanden.", "after": f"{o.get('n_after', '?')} Positionen/Orders",
         "against": " ".join(against), "hash": file_hash(JOURNAL / "trades.csv"),
         "banner": None if o.get("validated") else "Nicht validiert – nur zur Beobachtung"}
    return buy_mail(d)


def notify(o: dict, r: dict, combo: str, today) -> bool:
    try:
        subj, text, html = compose(o, r, combo, today)
        return send(subj, text, html=html)
    except Exception as e:      # Mailfehler dürfen den Papierhandel nicht stoppen
        print(f"Kauf-Mail nicht versendet: {e}")
        return False
