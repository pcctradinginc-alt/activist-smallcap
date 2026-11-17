"""Branchen für das Portfoliorisiko, einheitlich in den 11 Yahoo-Sektornamen (alle Regionen).
USA: Abbildung der SIC-Nummer (SEC-Stammdaten). Japan/Europa: Yahoo-Suche je Symbol (Cache DATA/cache/sector/).
Bekannte Grenze: Yahoo liefert den AKTUELLEN Sektor, nicht den zum Entscheidungstag (nicht point-in-time)."""
from __future__ import annotations
import json, time
from datetime import datetime, timedelta
import requests
from ..common import DATA, atomic_write_text
from .prices import HDR

SECTORS = ("Basic Materials", "Communication Services", "Consumer Cyclical", "Consumer Defensive", "Energy",
           "Financial Services", "Healthcare", "Industrials", "Real Estate", "Technology", "Utilities")
URL = "https://query2.finance.yahoo.com/v1/finance/search"
NEG_TTL_DAYS = 30
MIN_GAP_S = 0.5
_LAST = [0.0]

_SIC2 = {**dict.fromkeys(("01", "02", "20", "21", "51", "54", "82"), "Consumer Defensive"),
         **dict.fromkeys(("08", "10", "14", "24", "26", "28", "30", "32", "33"), "Basic Materials"),
         **dict.fromkeys(("12", "13", "29"), "Energy"),
         **dict.fromkeys(("15", "16", "17", "34", "35", "37", "40", "41", "42", "43", "44", "45", "46", "47", "50", "87", "73"),
                         "Industrials"),
         **dict.fromkeys(("22", "23", "25", "31", "39", "52", "53", "55", "56", "57", "58", "59", "70", "72", "75", "79"),
                         "Consumer Cyclical"),
         **dict.fromkeys(("36", "38"), "Technology"), "80": "Healthcare",
         **dict.fromkeys(("27", "48", "78"), "Communication Services"), "49": "Utilities",
         **dict.fromkeys(("60", "61", "62", "63", "64", "67"), "Financial Services"), "65": "Real Estate"}


def from_sic(sic) -> str | None:
    """US-SIC (2 bis 4 Stellen) -> Yahoo-Sektor; unbekannt/99/None -> None."""
    s = str(sic).strip() if sic is not None else ""
    if not s.isdigit() or len(s) < 2:
        return None
    if s[:3] == "283":
        return "Healthcare"
    if s[:3] == "357" or s[:3] == "737":
        return "Technology"
    if s[:3] == "371":
        return "Consumer Cyclical"
    if s[:3] == "384":
        return "Healthcare"
    if s == "6798":
        return "Real Estate"
    return _SIC2.get(s[:2])


def _path(symbol: str):
    return DATA / "cache" / "sector" / (symbol.replace("/", "_") + ".json")


def yahoo(symbol: str) -> str | None:
    """Aktueller Yahoo-Sektor eines Symbols (Suchendpunkt, ohne Crumb/Cookies). Treffer ewig gecacht, 'unbekannt' 30 Tage,
    Netzfehler nie (-> None)."""
    p = _path(symbol)
    if p.exists():
        try:
            c = json.loads(p.read_text(encoding="utf-8"))
            if c.get("sector") or datetime.fromisoformat(c["ts"]) > datetime.now() - timedelta(days=NEG_TTL_DAYS):
                return c.get("sector")
        except Exception:
            pass
    wait = _LAST[0] + MIN_GAP_S - time.time()
    if wait > 0:
        time.sleep(wait)
    try:
        r = requests.get(URL, params={"q": symbol, "quotesCount": 1, "newsCount": 0}, headers=HDR, timeout=30)
        r.raise_for_status()
        quotes = r.json().get("quotes") or []
    except Exception:
        return None
    finally:
        _LAST[0] = time.time()
    q = next((x for x in quotes if str(x.get("symbol", "")).upper() == symbol.upper()), None)
    sec = q.get("sector") if q else None
    sec = sec if sec in SECTORS else None
    atomic_write_text(p, json.dumps({"sector": sec, "ts": datetime.now().isoformat()}))
    return sec
