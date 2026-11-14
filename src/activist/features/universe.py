"""Universums- und Merkmalsberechnung je Ereignis, ausschließlich mit Daten bis zum Entscheidungstag."""
from __future__ import annotations
import numpy as np
import pandas as pd


def local_day(decision_time) -> pd.Timestamp:
    """Börsenlokales Kalenderdatum der Veröffentlichung. Zeitstempel werden mit ihrem Original-Offset gespeichert
    (EDGAR: New York, EDINET: Tokio), daher Ortszeit = Wanduhrzeit. Muss identisch zu simulate_entry sein:
    Merkmale nur aus Kursen bis einschließlich diesem Tag, Einstieg frühestens am Folgehandelstag."""
    d = pd.Timestamp(decision_time)
    return (d.tz_localize(None) if d.tzinfo else d).normalize()


def price_features(px: pd.DataFrame, decision_time) -> dict:
    """Kurs, 20T-Median-Dollarumsatz, 20T-Anstieg, Spread-Schätzer – nur Kurse bis zum Vortag/Entscheidungstag."""
    d = local_day(decision_time)
    hist = px[px.index <= d].tail(21)
    if len(hist) < 21:
        return {"ok": False, "reason": "zu wenig Kurshistorie"}
    last = hist.iloc[-1]
    dv = (hist.close * hist.volume).tail(20)
    raw = float(last["close_unadj"]) if "close_unadj" in hist and pd.notna(last.get("close_unadj")) else float(last.close)
    return {"ok": True, "price": raw, "price_adj": float(last.close), "adv": float(dv.median()),
            "runup_20d": float(hist.close.iloc[-1] / hist.close.iloc[0] - 1),
            "half_spread": corwin_schultz(hist) / 2}


def corwin_schultz(hist: pd.DataFrame) -> float:
    """Spread-Schätzer aus Hoch/Tief (Corwin & Schultz 2012), Median über 20 Tage, Untergrenze 0."""
    h, l = np.log(hist.high.values), np.log(hist.low.values)
    beta = (h[1:] - l[1:]) ** 2 + (h[:-1] - l[:-1]) ** 2
    gamma = (np.maximum(h[1:], h[:-1]) - np.minimum(l[1:], l[:-1])) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    s = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    s = s[np.isfinite(s)]
    return float(max(np.median(np.clip(s, 0, None)), 0.0)) if len(s) else 0.02


def universe_check(region_cfg: dict, pf: dict, shares: float | None, sic_reason: str | None, otc: bool) -> list[str]:
    """Gibt die verletzten Universumsregeln zurück (leer = im Universum)."""
    v = []
    if not pf.get("ok"):
        return [pf.get("reason", "Kurse")]
    if pf["price"] < region_cfg["min_price"]:
        v.append("Kurs unter Mindestkurs")
    if pf["adv"] < region_cfg["min_adv"]:
        v.append("Tagesumsatz zu gering")
    if shares is None:
        v.append("Aktienzahl unbekannt")
    else:
        mcap = shares * pf["price"]
        if not (region_cfg["mcap_min"] <= mcap <= region_cfg["mcap_max"]):
            v.append("Marktkapitalisierung außerhalb")
    if sic_reason:
        v.append(sic_reason)
    if otc:
        v.append("OTC")
    return v
