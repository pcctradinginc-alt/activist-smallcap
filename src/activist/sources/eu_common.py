"""Gemeinsame Helfer der EU-Adapter: gedrosselter HTTP-Client (>=1 s Abstand, feste User-Agent) und Event-Builder."""
from __future__ import annotations
import re, time
from datetime import date, datetime, timedelta, timezone
import requests
from ..common import effective_decision_time, event_id

UA = "activist-smallcap research contact@example.org"
MIN_INTERVAL = 1.0
FORM = "MajorHolding"
_S = requests.Session(); _S.headers.update({"User-Agent": UA})
_last = [0.0]


def _throttle() -> None:
    wait = MIN_INTERVAL - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.monotonic()


def request(method: str, url: str, retries: int = 4, **kw) -> requests.Response:
    kw.setdefault("timeout", 60)
    for i in range(retries):
        _throttle()
        r = _S.request(method, url, **kw)
        if r.status_code in (429, 500, 502, 503, 504) and i < retries - 1:
            time.sleep(2 ** (i + 1)); continue
        r.raise_for_status()
        return r
    raise RuntimeError("unreachable")


def pct_to_fraction(s: str | float | None) -> float | None:
    """'5,001864 %' / '5.0%' -> 0.05001864 ; None bei Unlesbarem."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return round(float(s) / 100, 10)
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(s))
    return round(float(m.group(1).replace(",", ".")) / 100, 10) if m else None


def make_event(*, source: str, native_id: str, region: str, published: str | None, date_only: str | None,
               has_time: bool, issuer_name, issuer_id, filer_name, filer_id, url: str,
               holding_ratio: float | None = None, **extra) -> dict:
    """Baut das gemeinsame Event-Schema. Ohne Uhrzeit (has_time False) gilt Folgetag 00:00 UTC (Regel 1)."""
    pub = published if has_time else None
    dt = effective_decision_time(pub, date_only or (published or "")[:10])
    ev = {
        "event_id": event_id(source, str(native_id)), "source": source, "region": region, "form": FORM,
        "published": dt.isoformat(), "has_time": bool(has_time),
        "filing_date": (date_only or (published or "")[:10]),
        "issuer_name": issuer_name, "issuer_id": issuer_id,
        "filer_name": filer_name, "filer_id": filer_id, "url": url,
    }
    if holding_ratio is not None:
        ev["holding_ratio"] = holding_ratio
    ev.update({k: v for k, v in extra.items() if v is not None})
    return ev


def date_windows(start: date, end: date, days: int):
    d = start
    while d <= end:
        e = min(d + timedelta(days=days - 1), end)
        yield d, e
        d = e + timedelta(days=1)


def in_range(ev: dict, start: date, end: date) -> bool:
    """Filter auf Veröffentlichungs-Kalendertag (UTC-Datum der effektiven Entscheidungszeit minus ggf. Folgetag-Regel nicht nötig)."""
    return start.isoformat() <= ev["filing_date"] <= end.isoformat()
