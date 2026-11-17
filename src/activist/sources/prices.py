"""Tageskurse aus zwei kostenlosen Quellen (Yahoo, Stooq) mit Abgleich (Regel: >2 % Abweichung => gesperrt)."""
from __future__ import annotations
import io, json, re
from datetime import date, timedelta
import pandas as pd
import requests
from ..common import DATA, atomic_write_text

HDR = {"User-Agent": "Mozilla/5.0 (research)"}
YAHOO_SUFFIX = {"US": "", "JP": ".T", "UK": ".L", "DACH": ".DE"}
MIN_OVERLAP_DAYS, MIN_OVERLAP_SHARE = 20, 0.8   # verbindliche Mindestabdeckung des Quellenabgleichs


def _scrub(msg) -> str:
    """API-Schlüssel aus Fehlertexten entfernen (requests-Fehler enthalten die komplette URL inkl. token=...)."""
    return re.sub(r"token=[^&\s]+", "token=***", str(msg))


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
    """Tiingo-Rohkurse mit Datei-Cache (Gratis-Tarif: begrenzte Zahl Symbole/Monat -> jede Reihe nur einmal laden).
    Sidecar <TICKER>.meta.json: Abrufdatum + Fehler (unbekannter Ticker wird 30 Tage negativ gecacht: spart >= 75 s je Anfrage)."""
    path = DATA / "cache" / "tiingo" / f"{ticker.upper()}.csv"
    meta_path, today = path.with_suffix(".meta.json"), date.today()
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else None
    if meta and meta.get("error") and date.fromisoformat(meta["fetched"]) >= today - timedelta(days=30):
        raise ValueError(meta["error"])
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if meta and not meta.get("error"):
            fetched = date.fromisoformat(meta["fetched"])
            hit = start >= date(2014, 1, 1) and (fetched >= end or fetched >= today - timedelta(days=7))
        else:
            hit = df.index.min().date() <= start and df.index.max().date() >= min(end, today - timedelta(days=7))
        if hit:
            return df.loc[str(start):str(end)]
    try:
        df = tiingo(ticker, min(start, date(2014, 1, 1)), today)
    except ValueError as e:
        if "unbekannt" in str(e) or "keine Daten" in str(e):
            atomic_write_text(meta_path, json.dumps({"fetched": today.isoformat(), "error": str(e)}))
        raise
    atomic_write_text(path, df.to_csv()); atomic_write_text(meta_path, json.dumps({"fetched": today.isoformat(), "error": None}))
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
    """Vergleicht unbereinigte Schlusskurse beider Quellen. Zu geringe Überlappung = ungeprüft (ok=None), nie ok=True."""
    a = a.assign(close=a["close_unadj"]) if "close_unadj" in a else (a.assign(close=a["close_raw"]) if "close_raw" in a else a)
    j = a[["close"]].rename(columns={"close": "close_a"}).join(b[["close"]].rename(columns={"close": "close_b"}), how="inner")
    n = len(j)
    dev = (j.close_a / j.close_b - 1).abs()
    md = float(dev.max()) if n else None
    if n < max(MIN_OVERLAP_DAYS, MIN_OVERLAP_SHARE * len(a)):
        return {"ok": None, "overlap": n, "max_dev": md, "error": f"zu wenig Überlappung ({n} von {len(a)} Tagen)"}
    return {"ok": bool(md <= max_dev), "overlap": n, "max_dev": md}


def load(ticker: str, region: str, start: date, end: date, cache: bool = True, second: bool = True) -> tuple[pd.DataFrame, dict]:
    """Primärquelle Yahoo, Zweitquelle je Region (USA: Nasdaq/Tiingo). Gibt Kurse und Abgleichsergebnis zurück.
    ok=True: beide Quellen stimmen überein · ok=False: Abweichung > 2 % (gesperrt) · ok=None: ungeprüft.
    Das Prüfergebnis wird mit den Kursen gespeichert – ein Cache-Treffer gilt nie stillschweigend als geprüft."""
    path = DATA / "prices" / f"{region}_{ticker}.csv"
    chk_path = path.with_suffix(".check.json")
    if cache and path.exists() and chk_path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        chk = json.loads(chk_path.read_text())
        today = date.today()
        if chk.get("req_start"):   # Abdeckung nach angefragtem Zeitraum (delistete/junge Ticker haben nie Daten bis end)
            covered = date.fromisoformat(chk["req_start"]) <= start and \
                (date.fromisoformat(chk["req_end"]) >= end or date.fromisoformat(chk["req_end"]) >= today - timedelta(days=7))
        else:
            covered = df.index.min().date() <= start and df.index.max().date() >= min(end, today - timedelta(days=7))
        if chk.get("ok") and (chk.get("overlap") or 0) < MIN_OVERLAP_DAYS:   # alte Prüfdatei mit zu kleiner Überlappung = ungeprüft
            chk = {**chk, "ok": None}
        if covered and (chk.get("ok") is not None or region not in SECOND_SOURCE or not second):   # ungeprüft + Zweitquelle vorhanden -> neu prüfen
            return df.loc[str(start):str(end)], {**chk, "cached": True}
    a = yahoo(ticker + YAHOO_SUFFIX.get(region, ""), start, end)
    try:
        if region not in SECOND_SOURCE:
            raise ValueError(f"keine Zweitquelle für Region {region}")
        if not second:      # Zweitquelle (knappes Tiingo-Kontingent) erst für qualifizierte Kampagnen
            raise ValueError("Zweitprüfung zurückgestellt")
        chk = cross_check(a, SECOND_SOURCE[region](ticker, start, end))
    except Exception as e:
        chk = {"ok": None, "error": _scrub(e)[:200]}   # Zweitquelle nicht verfügbar => ungeprüft (zählt nicht als Nachweis)
    if cache and len(a):
        atomic_write_text(path, a.to_csv())
        atomic_write_text(chk_path, json.dumps({**chk, "req_start": start.isoformat(), "req_end": end.isoformat()}))
    return a, chk


TIINGO_MIN_GAP_S, TIINGO_RETRIES, TIINGO_429_WAIT_S = 75.0, 1, 90.0
TIINGO_MAX_CALLS = 45            # Gratis-Tarif: ~50/Stunde; je Lauf begrenzt, Rest im nächsten Lauf (Cache)
_TIINGO_LAST = [0.0]
_TIINGO_STATE = {"calls": 0, "blocked": False}


def tiingo(ticker: str, start: date, end: date, key: str | None = None) -> pd.DataFrame:
    """Kostenloser Tiingo-Tarif (API-Schlüssel nötig) – enthält auch delistete US-Titel.
    Achtung Ticker-Wiederverwendung: Aufrufer muss das Ergebnis plausibilisieren (validate_against)."""
    import os
    key = key or os.environ.get("TIINGO_API_KEY")
    if not key:
        raise ValueError("TIINGO_API_KEY fehlt")
    import time
    if _TIINGO_STATE["blocked"] or _TIINGO_STATE["calls"] >= TIINGO_MAX_CALLS:
        raise ValueError("Tiingo-Kontingent für diesen Lauf erschöpft (nächster Lauf setzt fort)")
    _TIINGO_STATE["calls"] += 1
    for attempt in range(TIINGO_RETRIES + 1):   # Gratis-Tarif: ca. 50 Abrufe/Stunde -> Abstand halten, bei 429 warten
        wait = _TIINGO_LAST[0] + TIINGO_MIN_GAP_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(f"https://api.tiingo.com/tiingo/daily/{ticker.lower()}/prices",
                             params={"startDate": f"{start}", "endDate": f"{end}", "token": key}, headers=HDR, timeout=30)
        except requests.RequestException:     # Fehlertext enthält die URL samt token=... -> nie weiterreichen
            raise ValueError(f"Tiingo: Verbindungsfehler für {ticker}") from None
        _TIINGO_LAST[0] = time.monotonic()
        if r.status_code != 429:
            break
        if attempt == TIINGO_RETRIES:
            _TIINGO_STATE["blocked"] = True       # Limit erreicht: Rest des Laufs ohne Tiingo statt stundenlang warten
            raise ValueError("Tiingo 429: Abruflimit erreicht")
        time.sleep(TIINGO_429_WAIT_S)
    if r.status_code == 404:
        raise ValueError(f"Tiingo: {ticker} unbekannt")
    if r.status_code >= 400:      # nie r.raise_for_status(): der requests-Fehlertext enthält die URL samt token=...
        raise ValueError(f"Tiingo: HTTP {r.status_code} für {ticker}")
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
    col = next(c for c in ("close_unadj", "close_raw", "close") if c in px)   # tatsächlich gehandelter Kurs bevorzugt
    v = px[col].asof(pd.Timestamp(ref_date).tz_localize(None) if pd.Timestamp(ref_date).tzinfo else pd.Timestamp(ref_date))
    return bool(pd.notna(v) and abs(v / ref_price - 1) <= tol)


def reference_check(px: pd.DataFrame, refs: dict, tol: float = 0.15) -> tuple[bool | None, str]:
    """Kursniveau gegen eigene Preisangaben des Meldenden (EDINET) prüfen. False nur bei unplausibler Kursreihe, sonst True/None
    (Block-Trades können verhandelt sein, Ø-Einstand ist unzuverlässig -> beides darf nur bestätigen, nie widerlegen)."""
    if "error" in refs:
        return None, "EDINET: " + str(refs["error"])[:150]
    if px.empty:
        return None, "keine Kursdaten"
    r = px[next(c for c in ("close_raw", "close") if c in px)].dropna()   # split-bereinigt: ein Split ist kein Datenfehler
    jump = r.pct_change().add(1).pipe(lambda q: q[(q > 3) | (q < 1 / 3)])
    if len(jump):
        return False, f"Kursreihe unplausibel (Tagessprung > Faktor 3) am {pd.Timestamp(jump.index[0]).date()}"
    col = next(c for c in ("close_unadj", "close_raw", "close") if c in px)

    def at(d):
        t = pd.Timestamp(d)
        v = px[col].asof(t.tz_localize(None) if t.tzinfo else t)
        return float(v) if pd.notna(v) else None
    cmp_ = [(at(d), p) for d, p, _ in refs.get("trades") or [] if p > 0]
    cmp_ = [(c, p) for c, p in cmp_ if c]
    ok = sum(abs(c / p - 1) <= tol for c, p in cmp_)
    if cmp_ and ok * 2 >= len(cmp_):
        return True, f"EDINET-Einstand: {ok}/{len(cmp_)} Stückpreise passen"
    avg, bd = refs.get("avg_cost"), refs.get("base_date")
    c = at(bd) if avg and bd else None
    if c and abs(c / avg - 1) <= 0.35:
        return True, "EDINET-Einstand: Ø-Kaufkurs passt"
    why = f"{ok}/{len(cmp_)} Stückpreise passen" if cmp_ else ("Ø-Kaufkurs weicht ab" if c else "keine EDINET-Preisangaben")
    return None, "EDINET-Einstand nicht bestätigt: " + why if (cmp_ or c) else "keine EDINET-Preisangaben"
