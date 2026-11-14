"""Norwegen: Oslo Børs NewsWeb (api3.oslo.oslobors.no/v1/newsreader), Kategorie 1006 'FLAGGING' (Major shareholdings notification).

Die Liste liefert Zeitstempel (ms, UTC), Emittent (Name, Ticker 'issuerSign'), Meldungs-ID. Melder und Prozentsatz stehen NUR im
PDF-Anhang (kein ISIN, kein Filer in den Metadaten) -> filer_name/holding_ratio bleiben leer.
Liste ist pro Anfrage auf ~600 Treffer begrenzt -> Fenster werden bei Überlauf halbiert.
"""
from __future__ import annotations
from datetime import date, timedelta
from .eu_common import make_event, request, in_range, date_windows

LIST = "https://api3.oslo.oslobors.no/v1/newsreader/list"
FLAGGING = 1006
SOURCE, REGION = "OSLO_NEWSWEB", "NORDIC"
CAP = 590


def parse_list(resp: dict) -> list[dict]:
    out = []
    for m in (resp.get("data") or {}).get("messages", []):
        if m.get("test") or not any(c.get("id") == FLAGGING for c in m.get("category", [])):
            continue
        pub = m["publishedTime"]                                    # '2026-11-13T13:23:04.715Z'
        out.append(make_event(
            source=SOURCE, native_id=m["messageId"], region=REGION,
            published=pub.replace("Z", "+00:00"), date_only=pub[:10], has_time=True,
            issuer_name=m.get("issuerName"), issuer_id=m.get("issuerSign"),
            filer_name=None, filer_id=None, url=f"https://newsweb.oslobors.no/message/{m['messageId']}",
            title=m.get("title"), num_attachments=m.get("numbAttachments"),
            corrected_by=m.get("correctedByMessageId") or None, correction_for=m.get("correctionForMessageId") or None))
    return out


def _window(a: date, b: date) -> list[dict]:
    r = request("GET", LIST, params={"category": FLAGGING, "fromDate": a.isoformat(), "toDate": (b + timedelta(days=1)).isoformat()}).json()
    n = len((r.get("data") or {}).get("messages", []))
    if n >= CAP and a < b:                                          # abgeschnitten -> teilen
        mid = a + (b - a) // 2
        return _window(a, mid) + _window(mid + timedelta(days=1), b)
    return parse_list(r)


def fetch_events(start_date: date, end_date: date) -> list[dict]:
    seen, out = set(), []
    for a, b in date_windows(start_date, end_date, 14):
        for ev in _window(a, b):
            if ev["event_id"] not in seen and in_range(ev, start_date, end_date):
                seen.add(ev["event_id"]); out.append(ev)
    return sorted(out, key=lambda e: e["published"])
