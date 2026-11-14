"""Tageskurse aus zwei kostenlosen Quellen (Yahoo, Stooq) mit Abgleich (Regel: >2 % Abweichung => gesperrt)."""
from __future__ import annotations
import io
from datetime import date
import pandas as pd
import requests
from ..common import DATA

HDR = {"User-Agent": "Mozilla/5.0 (research)"}
STOOQ_SUFFIX = {"US": ".us", "JP": ".jp", "UK": ".uk", "DACH": ".de"}
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
    df["close_raw"] = df["close"]
    if adj:  # Split-/Dividendenbereinigung auf OHLC übertragen
        f = pd.Series(adj, index=df.index) / df["close"]
        for c in ("open", "high", "low", "close"):
            df[c] = df[c] * f
    return df.dropna(subset=["close"])


def stooq(ticker: str, start: date, end: date) -> pd.DataFrame:
    r = requests.get("https://stooq.com/q/d/l/", params={"s": ticker.lower(), "i": "d",
                     "d1": f"{start:%Y%m%d}", "d2": f"{end:%Y%m%d}"}, headers=HDR, timeout=30)
    r.raise_for_status()
    if not r.text.startswith("Date"):
        raise ValueError(f"Stooq: keine Daten für {ticker}")
    df = pd.read_csv(io.StringIO(r.text), parse_dates=["Date"]).set_index("Date")
    return df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]


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


SECOND_SOURCE = {"US": lambda t, s, e: nasdaq(t, s, e)}


def cross_check(a: pd.DataFrame, b: pd.DataFrame, max_dev: float = 0.02) -> dict:
    """Vergleicht unbereinigte Schlusskurse beider Quellen."""
    a = a.assign(close=a["close_raw"]) if "close_raw" in a else a
    j = a[["close"]].join(b[["close"]], how="inner", lsuffix="_a", rsuffix="_b")
    if j.empty:
        return {"ok": False, "overlap": 0, "max_dev": None}
    dev = (j.close_a / j.close_b - 1).abs()
    return {"ok": bool(dev.max() <= max_dev), "overlap": len(j), "max_dev": float(dev.max())}


def load(ticker: str, region: str, start: date, end: date, cache: bool = True) -> tuple[pd.DataFrame, dict]:
    """Primärquelle Yahoo, Zweitquelle je Region (USA: Nasdaq). Gibt Kurse und Abgleichsergebnis zurück.
    ok=None heißt: keine Zweitquelle verfügbar -> Ereignis gilt als ungeprüft (Datenqualität)."""
    path = DATA / "prices" / f"{region}_{ticker}.csv"
    if cache and path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.index.min().date() <= start and df.index.max().date() >= end:
            return df.loc[str(start):str(end)], {"ok": True, "cached": True}
    a = yahoo(ticker + YAHOO_SUFFIX.get(region, ""), start, end)
    try:
        if region not in SECOND_SOURCE:
            raise ValueError(f"keine Zweitquelle für Region {region}")
        chk = cross_check(a, SECOND_SOURCE[region](ticker, start, end))
    except Exception as e:
        chk = {"ok": None, "error": str(e)}   # Zweitquelle nicht verfügbar => als ungeprüft markieren
    if cache and len(a):
        path.parent.mkdir(parents=True, exist_ok=True); a.to_csv(path)
    return a, chk


def tiingo(ticker: str, start: date, end: date, key: str | None = None) -> pd.DataFrame:
    """Kostenloser Tiingo-Tarif (API-Schlüssel nötig) – enthält auch delistete US-Titel.
    Achtung Ticker-Wiederverwendung: Aufrufer muss das Ergebnis plausibilisieren (validate_against)."""
    import os
    key = key or os.environ.get("TIINGO_API_KEY")
    if not key:
        raise ValueError("TIINGO_API_KEY fehlt")
    r = requests.get(f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices",
                     params={"startDate": f"{start}", "endDate": f"{end}", "token": key}, headers=HDR, timeout=30)
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
