"""Größter bekannter Einzelaktionär eines Emittenten zum Entscheidungstag (point-in-time, ohne Look-ahead).
JP/EU: aus den Ereignissen (holding_ratio); USA: EDGAR-Ereignisse tragen keinen Anteil -> 13D/13G-Meldungen werden geparst."""
from __future__ import annotations
import re, unicodedata
from datetime import date, timedelta

EU_REGIONS = {"UK", "NORDIC", "BENELUX_FR", "DACH"}
US_FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A", "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}


def _issuer_key(e: dict):
    if e.get("source") == "EDINET":
        return ("JP", e.get("issuer_sec_code")) if e.get("issuer_sec_code") else None
    if e.get("region") in EU_REGIONS:
        from .dataset_eu import _issuer_key as isin         # dieselbe ISIN-Logik wie der EU-Datensatz
        k = isin(e)
        return (e.get("region"), k) if k else None
    return None


def _filer_key(e: dict) -> str | None:
    if e.get("filer_id"):
        return str(e["filer_id"])
    n = re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", e.get("filer_name") or "").casefold())
    return n or None


def build_index(events: list[dict]) -> dict:
    """issuer_key -> [(published, filer_key, ratio)] nach Zeit sortiert. Nur Anteile in (0, 0.99] (1.0 = Lesefehler)."""
    idx: dict = {}
    for e in events:
        r, k, f = e.get("holding_ratio"), _issuer_key(e), _filer_key(e)
        if k and f and isinstance(r, (int, float)) and r == r and 0 < r <= 0.99 and e.get("published"):
            idx.setdefault(k, []).append((e["published"], f, float(r)))
    for v in idx.values():
        v.sort()
    return idx


def _window(before: str, years: float) -> tuple[str, str]:
    d = date.fromisoformat(before[:10])
    return (d - timedelta(days=round(365.25 * years))).isoformat(), d.isoformat()


def major_holder(index: dict, issuer_key, before: str, years: float = 3) -> float | None:
    """Je Melder die letzte Meldung mit Datum < before (Kalendertag) und innerhalb von `years`; Maximum über Melder.
    Eine letzte Meldung unter 5 % (Ausstieg) zählt mit ihrem kleinen Wert. None = nichts bekannt."""
    lo, hi = _window(before, years)
    last: dict = {}
    for pub, f, r in index.get(issuer_key, []):
        if lo <= pub[:10] < hi:
            last[f] = r                                  # sortiert -> spätere überschreiben frühere
    return max(last.values()) if last else None


def major_holder_us(events_by_issuer: dict, issuer_id: str, before: str, parse, years: float = 3,
                    max_filers: int = 10) -> float | None:
    """USA: je Melder letzte 13D/13G-Meldung vor `before` im Fenster, höchstens die `max_filers` jüngsten Melder.
    parse(url) -> dict mit 'percent_of_class' in PROZENT (Parser-Konvention); Rückgabe als Bruchteil (0..1)."""
    lo, hi = _window(before, years)
    last: dict = {}
    for e in sorted(events_by_issuer.get(str(issuer_id).lstrip("0"), []), key=lambda e: e["published"]):
        if e.get("form") in US_FORMS and e.get("filer_id") and e.get("url") and lo <= e["published"][:10] < hi:
            last[str(e["filer_id"]).lstrip("0")] = e
    recent = sorted(last.values(), key=lambda e: e["published"], reverse=True)[:max_filers]
    vals = []
    for e in recent:
        try:
            pc = parse(e["url"]).get("percent_of_class")
        except Exception:
            continue
        if isinstance(pc, (int, float)) and pc == pc and 0 < pc <= 99:
            vals.append(pc / 100)
    return max(vals) if vals else None
