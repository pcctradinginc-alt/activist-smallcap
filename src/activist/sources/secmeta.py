"""SEC-Metadaten: Stammdaten (submissions) und Point-in-Time-Fundamentaldaten (companyfacts, XBRL).

PIT-Regel: Es zaehlen nur Fakten mit "filed" <= Stichtag; spaetere Restatements werden nie verwendet.
Cache: DATA/cache/sec/*.json (TTL, nur fuer Rohdaten; die PIT-Filterung passiert beim Lesen).
"""
from __future__ import annotations
import json, time
from datetime import date, datetime
from pathlib import Path
from .. import common
from . import edgar

CACHE_TTL_DAYS = 7


def _cik10(cik) -> str:
    return str(int(str(cik).strip())).zfill(10)


def _d(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def _cache_path(kind: str, cik10: str) -> Path:
    p = common.DATA / "cache" / "sec"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{kind}_{cik10}.json"


def _fetch_json(url: str) -> dict:
    """404 = für diese CIK gibt es keine Daten (z. B. kein XBRL) -> leeres Dict, wird ebenfalls gecacht."""
    try:
        return edgar._get(url).json()
    except Exception as e:
        if getattr(getattr(e, "response", None), "status_code", None) == 404:
            return {}
        raise


def _cached(kind: str, url: str, cik10: str, ttl_days: float = CACHE_TTL_DAYS) -> dict:
    path = _cache_path(kind, cik10)
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl_days * 86400:
        return json.loads(path.read_text(encoding="utf-8"))
    data = _fetch_json(url)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
    time.sleep(0.12)  # SEC-Limit 10 req/s
    return data


def _submissions(cik) -> dict:
    c = _cik10(cik)
    return _cached("submissions", f"https://data.sec.gov/submissions/CIK{c}.json", c)


def _facts(cik) -> dict:
    c = _cik10(cik)
    return _cached("companyfacts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", c)


# ---------------------------------------------------------------- Stammdaten
def company(cik) -> dict:
    s = _submissions(cik)
    return {
        "cik": _cik10(cik),
        "name": s.get("name"),
        "tickers": list(s.get("tickers") or []),
        "exchanges": list(s.get("exchanges") or []),
        "sic": str(s["sic"]) if s.get("sic") else None,
        "sic_description": s.get("sicDescription") or None,
        "former_names": list(s.get("formerNames") or []),
    }


def ticker_asof(cik, asof) -> dict | None:
    """Aktueller Ticker laut submissions (KEIN historischer Ticker; submissions kennt nur den Ist-Stand).
    Ohne Ticker (typisch bei delisteten Firmen) -> None; der Aufrufer zaehlt das als Survivorship-Luecke."""
    _d(asof)  # Validierung
    c = company(cik)
    if not c["tickers"]:
        return None
    exch = c["exchanges"][0] if c["exchanges"] else None
    exch = exch or None
    return {"ticker": c["tickers"][0], "exchange": exch, "otc": exch in ("OTC", None),
            "current_not_historical": True}


# ---------------------------------------------------------------- Fakten (PIT)
def _entries(facts: dict, tax: str, concept: str, units: tuple[str, ...]) -> list[dict]:
    node = facts.get("facts", {}).get(tax, {}).get(concept)
    if not node:
        return []
    out = []
    for u in units:
        out += node.get("units", {}).get(u, [])
    return out


def _pit_latest(entries: list[dict], asof: date, pred=None) -> dict | None:
    """Neueste Veroeffentlichung (filed) <= asof; bei Gleichstand das spaeteste Periodenende."""
    best, key = None, None
    for e in entries:
        if "filed" not in e or "end" not in e or e.get("val") is None:
            continue
        if _d(e["filed"]) > asof or (pred and not pred(e)):
            continue
        k = (e["filed"], e["end"])
        if key is None or k > key:
            best, key = e, k
    return best


def shares_outstanding_asof(cik, asof) -> tuple[float, str, str] | None:
    """(Aktien, filed, fact_end) laut zuletzt bis asof veroeffentlichtem Fakt."""
    asof, f = _d(asof), _facts(cik)
    for tax, concept in (("dei", "EntityCommonStockSharesOutstanding"),
                         ("us-gaap", "CommonStockSharesOutstanding")):
        e = _pit_latest(_entries(f, tax, concept, ("shares",)), asof)
        if e:
            return e["val"], e["filed"], e["end"]
    return None


def _item(e: dict | None) -> dict | None:
    return None if e is None else {"value": e["val"], "filed": e["filed"], "end": e["end"], "form": e.get("form")}


def _is_annual(e: dict) -> bool:
    if e.get("form") not in ("10-K", "10-K/A", "10-KT") or e.get("fp") != "FY":
        return False
    if e.get("start"):
        days = (_d(e["end"]) - _d(e["start"])).days
        return 340 <= days <= 380
    return True


def fundamentals_asof(cik, asof) -> dict:
    """Point-in-time-Kennzahlen. Fehlende Posten sind None. debt_partial=True, wenn nur ein Teil
    (LongTermDebt bzw. LongTermDebtCurrent) vorliegt; debt_value=None, wenn beide fehlen.
    Hinweis: LongTermDebt enthaelt bei manchen Emittenten bereits den kurzfristigen Teil (Doppelzaehlung moeglich)."""
    asof, f = _d(asof), _facts(cik)
    usd = ("USD",)
    g = lambda c, pred=None: _pit_latest(_entries(f, "us-gaap", c, usd), asof, pred)
    cash, equity = g("CashAndCashEquivalentsAtCarryingValue"), g("StockholdersEquity")
    ltd, ltdc = g("LongTermDebt"), g("LongTermDebtCurrent")
    opinc = g("OperatingIncomeLoss", _is_annual)
    debt = None if (ltd is None and ltdc is None) else (
        (ltd["val"] if ltd else 0) + (ltdc["val"] if ltdc else 0))
    bs = cash or equity
    return {
        "cash": _item(cash),
        "debt": {"value": debt, "partial": (ltd is None) != (ltdc is None),
                 "long_term_debt": _item(ltd), "long_term_debt_current": _item(ltdc)} if debt is not None
        else {"value": None, "partial": True, "long_term_debt": None, "long_term_debt_current": None},
        "equity": _item(equity),
        "operating_income": _item(opinc),
        "age_days": (asof - _d(bs["filed"])).days if bs else None,
    }


# ---------------------------------------------------------------- Ausschluss nach Branche
def excluded_industry(sic) -> str | None:
    """Banken, Versicherer, Maentel (Blank-Check/Shells) sind fuer die Strategie ausgeschlossen."""
    try:
        n = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    if 6020 <= n <= 6199:
        return "Banken"
    if 6300 <= n <= 6411:
        return "Versicherer"
    if n == 6770:
        return "Maentel (Blank-Check/Shell)"
    return None
