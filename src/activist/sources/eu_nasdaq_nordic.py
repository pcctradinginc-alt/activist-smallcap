"""Nordics (SE/DK/FI/IS): Nasdaq Nordic offizieller Nachrichten-Feed, Kategorie 'Major shareholder announcements'.

GET https://api.news.eu.nasdaq.com/news/query.action (JSONP, callback=cb) -- kein Key. Zeitstempel sekundengenau (timeZone=UTC angefordert).
Limit 200/Seite, start < 10000 (-> ca. 2017 ff.); es gibt keinen funktionierenden Datumsfilter, daher Paging ab neuestem Eintrag.
Mehrsprachige Duplikate (gleiche Firma + Zeitpunkt) werden zusammengefasst (en bevorzugt).
Flagger steht teils in der Überschrift, Prozentsatz im Meldungstext (view-Seite, optional enrich=True).
"""
from __future__ import annotations
import html, json, re
from datetime import date, datetime, timezone
from .eu_common import make_event, request, pct_to_fraction, in_range

API = "https://api.news.eu.nasdaq.com/news/query.action"
SOURCE, REGION = "NASDAQ_NORDIC", "NORDIC"
PAGE = 200
HEAD_FILER = re.compile(r"^(?:Major shareholder announcement|Storaktionærmeddelelse|Storaktionärsmeddelande|Flöggun|"
                        r"Flagging notification|Notification of major shareholding)\s*[-:–]\s*(.+)$", re.I)


def parse_jsonp(text: str) -> list[dict]:
    return json.loads(text[text.index("(") + 1:text.rindex(")")])["results"]["item"]


def filer_from_headline(h: str) -> str | None:
    m = HEAD_FILER.match((h or "").strip())
    return m.group(1).strip() if m else None


def parse_items(items: list[dict]) -> list[dict]:
    best: dict[tuple, dict] = {}
    for it in items:
        if "major shareholder" not in (it.get("cnsCategory") or "").lower():
            continue
        key = (it.get("company"), it["published"])
        if key in best and (best[key].get("language") == "en" or it.get("language") != "en"):
            continue
        best[key] = it
    out = []
    for it in best.values():
        pub = it["published"]                                       # 'YYYY-MM-DD HH:MM:SS' UTC (timeZone=UTC)
        iso = pub.replace(" ", "T") + "+00:00"
        out.append(make_event(
            source=SOURCE, native_id=it["disclosureId"], region=REGION, published=iso, date_only=pub[:10], has_time=True,
            issuer_name=it.get("company"), issuer_id=it.get("company"),      # kein ISIN im Feed -> Name als Fallback-ID
            filer_name=filer_from_headline(it.get("headline")), filer_id=None, url=it.get("messageUrl"),
            headline=it.get("headline"), market=it.get("market")))
    return sorted(out, key=lambda e: e["published"])


def parse_body(raw: str) -> dict:
    """Best-effort aus dem Meldungstext (Dänisch/Englisch/Finnisch variieren): Gesamtanteil und ISIN."""
    t = re.sub(r"<(script|style).*?</\1>", "", raw, flags=re.S)
    t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))
    m = re.search(r"total of both[^0-9]{0,20}(\d+(?:[.,]\d+)?)\s*%", t, re.I) or \
        re.search(r"voting rights[^%]{0,80}?(?:to|at|of)\s+(\d+(?:[.,]\d+)?)\s*%", t, re.I) or \
        re.search(r"(\d+(?:[.,]\d+)?)\s*%", t)
    i = re.search(r"ISIN[^A-Z0-9]{0,20}([A-Z]{2}[A-Z0-9]{9}\d)", t)
    return {"holding_ratio": pct_to_fraction(m.group(1)) if m else None, "isin": i.group(1) if i else None}


def enrich(ev: dict) -> dict:
    try:
        d = parse_body(request("GET", ev["url"]).text)
    except Exception:
        return ev
    if d["holding_ratio"] is not None:
        ev["holding_ratio"] = d["holding_ratio"]
    if d["isin"]:
        ev["issuer_id"] = ev["issuer_isin"] = d["isin"]
    return ev


def fetch_events(start_date: date, end_date: date, enrich_docs: bool = False) -> list[dict]:
    params = {"globalGroup": "exchangeNotice", "displayLanguage": "en", "timeZone": "UTC",
              "dateMask": "yyyy-MM-dd HH:mm:ss", "dir": "DESC", "globalName": "NordicAllMarkets",
              "callback": "cb", "limit": PAGE, "cnsCategory": "Major shareholder announcements"}
    items, start = [], 0
    while start < 10000:
        page = parse_jsonp(request("GET", API, params={**params, "start": start}).text)
        items += page
        if not page or page[-1]["published"][:10] < start_date.isoformat():
            break
        start += PAGE
    out = [e for e in parse_items(items) if in_range(e, start_date, end_date)]
    return [enrich(e) for e in out] if enrich_docs else out
