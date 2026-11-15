"""Tageskurse aus zwei kostenlosen Quellen (Yahoo, Stooq) mit Abgleich (Regel: >2 % Abweichung => gesperrt)."""
from __future__ import annotations
import io
from datetime import date, timedelta
import pandas as pd
import requests
from ..common import DATA

HDR = {"User-Agent": "Mozilla/5.0 (research)"}
YAHOO_SUFFIX = {"US": "", "JP": ".T", "UK": ".L", "DACH": ".DE"}


def yahoo(ticker: str, start: date, end: date) -> pd.DataFrame:
    import calendar
    p1, p2 = calendar.timegm(start.timetuple()), calendar.timegm(end.timetuple()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    r = requests.get(url, params={"period1": p1, "period2": p2, "interval": "1d", "events": "split,div"}, headers=HDR, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
    df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"], "close": q["close"],
                       "volume": q["volume"]}, index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    df["close_raw"] = df["close"]                     # Yahoo 'close' ist split-, aber nicht dividendenbereinigt
    # tatsächlich gehandelter Kurs am Tag (für Niveau-Filter: Mindestkurs, Marktkapitalisierung, Aktivisten-Einstand):
    # spätere Splits zurückrechnen, sonst Look-ahead über Reverse-Splits
    splits = (res.get("events") or {}).get("splits") or {}
    f_unadj = pd.Series(1.0, index=df.index)
    for sp in splits.values():
        d = pd.to_datetime(sp["date"], unit="s").normalize()
        ratio = float(sp["numerator"]) / float(sp["denominator"])
        if ratio > 0:
            f_unadj[df.index < d] *= ratio
    df["close_unadj"] = df["close"] * f_unadj
    if adj:  # Split-/Dividendenbereinigung auf OHLC übertragen
        f = pd.Series(adj, index=df.index) / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
    return df.dropna(subset=["close"])


def nasdaq(ticker: str, start: date, end: date) -> pd.DataFrame:
    for cls in ("stocks", "etf"):
        try:
            return _nasdaq(ticker, start, end, cls)
        except ValueError:
            continue
    raise ValueError(f"Nasdaq: keine Daten für {ticker}")


def _nasdaq(ticker: str, start: date, end: date, assetclass: str) -> pd.DataFrame:
    """Zweitquelle USA (unbereinigte Kurse). Stooq verlangt inzwischen eine Browser-Prüfung und entfällt."""
    r = requests.get(f"https://api.nasdaq.com/api/quote/{ticker}/historical",
                     params={"assetclass": assetclass, "fromdate": f"{start}", "todate": f"{end}", "limit": 9999},
                     headers={**HDR, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    rows = ((r.json().get("data") or {}).get("tradesTable") or {}).get("rows") or []
    if not rows:
        raise ValueError(f"Nasdaq: keine Daten für {ticker}")
    num = lambda x: float(str(x).replace("$", "").replace(",", "")) if x not in (None, "", "N/A") else None
    df = pd.DataFrame([{"date": pd.to_datetime(x["date"]), "open": num(x["open"]), "high": num(x["high"]),
                        "low": num(x["low"]), "close": num(x["close"]), "volume": num(x["volume"])} for x in rows])
    return df.set_index("date").sort_index()


def tiingo_cached(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Tiingo-Rohkurse mit Datei-Cache (Gratis-Tarif: begrenzte Zahl Symbole/Monat -> jede Reihe nur einmal laden)."""
    path = DATA / "cache" / "tiingo" / f"{ticker.upper()}.csv"
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index.min().date() <= start and df.index.max().date() >= min(end, date.today() - timedelta(days=7)):
            return df.loc[str(start):str(end)]
    df = tiingo(ticker, min(start, date(2014, 1, 1)), date.today())
    path.parent.mkdir(parents=True, exist_ok=True); df.to_csv(path)
    return df.loc[str(start):str(end)]


def _second_us(t: str, s: date, e: date) -> pd.DataFrame:
    """Zweite unabhängige Quelle USA: Nasdaq (nur ca. letzte 12 Monate), sonst Tiingo (Historie, inkl. delisteter Titel).
    Verglichen werden unbereinigte Schlusskurse."""
    if s >= date.today() - timedelta(days=330):
        try:
            return nasdaq(t, s, e)
        except ValueError:
            pass
    df = tiingo_cached(t, s, e)
    return df.assign(close=df["close_raw"])


SECOND_SOURCE = {"US": _second_us}


def cross_check(a: pd.DataFrame, b: pd.DataFrame, max_dev: float = 0.02) -> dict:
    """Vergleicht unbereinigte Schlusskurse beider Quellen."""
    a = a.assign(close=a["close_unadj"]) if "close_unadj" in a else (a.assign(close=a["close_raw"]) if "close_raw" in a else a)
    j = a[["close"]].join(b[["close"]], how="inner", lsuffix="_a", rsuffix="_b")
    if j.empty:
        return {"ok": False, "overlap": 0, "max_dev": None}
    dev = (j.close_a / j.close_b - 1).abs()
    return {"ok": bool(dev.max() <= max_dev), "overlap": len(j), "max_dev": float(dev.max())}


def load(ticker: str, region: str, start: date, end: date, cache: bool = True) -> tuple[pd.DataFrame, dict]:
    """Primärquelle Yahoo, Zweitquelle je Region (USA: Nasdaq/Tiingo). Gibt Kurse und Abgleichsergebnis zurück.
    ok=True: beide Quellen stimmen überein · ok=False: Abweichung > 2 % (gesperrt) · ok=None: ungeprüft.
    Das Prüfergebnis wird mit den Kursen gespeichert – ein Cache-Treffer gilt nie stillschweigend als geprüft."""
    import json
    path = DATA / "prices" / f"{region}_{ticker}.csv"
    chk_path = path.with_suffix(".check.json")
    if cache and path.exists() and chk_path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        chk = json.loads(chk_path.read_text())
        covered = df.index.min().date() <= start and df.index.max().date() >= min(end, date.today() - timedelta(days=7))
        if covered and (chk.get("ok") is not None or region not in SECOND_SOURCE):   # ungeprüft + Zweitquelle vorhanden -> neu prüfen
            return df.loc[str(start):str(end)], {**chk, "cached": True}
    a = yahoo(ticker + YAHOO_SUFFIX.get(region, ""), start, end)
    try:
        if region not in SECOND_SOURCE:
            raise ValueError(f"keine Zweitquelle für Region {region}")
        chk = cross_check(a, SECOND_SOURCE[region](ticker, start, end))
    except Exception as e:
        chk = {"ok": None, "error": str(e)[:200]}   # Zweitquelle nicht verfügbar => ungeprüft (zählt nicht als Nachweis)
    if cache and len(a):
        path.parent.mkdir(parents=True, exist_ok=True); a.to_csv(path); chk_path.write_text(json.dumps(chk))
    return a, chk


TIINGO_MIN_GAP_S, TIINGO_RETRIES, TIINGO_429_WAIT_S = 75.0, 4, 900.0
_TIINGO_LAST = [0.0]


def tiingo(ticker: str, start: date, end: date, key: str | None = None) -> pd.DataFrame:
    """Kostenloser Tiingo-Tarif (API-Schlüssel nötig) – enthält auch delistete US-Titel.
    Achtung Ticker-Wiederverwendung: Aufrufer muss das Ergebnis plausibilisieren (validate_against)."""
    import os
    key = key or os.environ.get("TIINGO_API_KEY")
    if not key:
        raise ValueError("TIINGO_API_KEY fehlt")
    import time
    for attempt in range(TIINGO_RETRIES + 1):   # Gratis-Tarif: ca. 50 Abrufe/Stunde -> Abstand halten, bei 429 warten
        wait = _TIINGO_LAST[0] + TIINGO_MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        r = requests.get(f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices",
                         params={"startDate": f"{start}", "endDate": f"{end}", "token": key}, headers=HDR, timeout=30)
        _TIINGO_LAST[0] = time.monotonic()
        if r.status_code != 429 or attempt == TIINGO_RETRIES:
            break
        time.sleep(TIINGO_429_WAIT_S)
    if r.status_code == 404:
        raise ValueError(f"Tiingo: {ticker} unbekannt")
    r.raise_for_status()
    rows = r.json()
    if not rows:
        raise ValueError(f"Tiingo: keine Daten für {ticker}")
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    out = df[["adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume"]].copy()
    out.columns = ["open", "high", "low", "close", "volume"]
    out["close_raw"] = df["close"].values
    return out


def validate_against(px: pd.DataFrame, ref_date, ref_price: float | None, tol: float = 0.35) -> bool:
    """Ticker-Wiederverwendung erkennen: unbereinigter Kurs am Stichtag muss nahe an einem Referenzkurs
    (z. B. Ø-Kaufkurs des Aktivisten laut 13D Item 5c) liegen. Ohne Referenz: nicht validierbar -> False."""
    if not ref_price or px.empty:
        return False
    col = "close_raw" if "close_raw" in px else "close"
    v = px[col].asof(pd.Timestamp(ref_date).tz_localize(None) if pd.Timestamp(ref_date).tzinfo else pd.Timestamp(ref_date))
    return bool(pd.notna(v) and abs(v / ref_price - 1) <= tol)
