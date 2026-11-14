"""Schweiz: SIX Exchange Regulation, Offenlegung bedeutender Aktionäre (FinfraG Art. 120ff).

Backend der Such-Webseite (ser-ag.com/en/resources/notifications-market-participants/significant-shareholders.html, React-SPA):
  GET https://www.ser-ag.com/sheldon/significant_shareholders/v1/overview.json
      ?pageSize=&pageNumber=(0-basiert)&sortAttribute=byDate&fromDate=YYYYMMDD&toDate=YYYYMMDD[&notificationId=][&submitterId=]
  GET .../issuers.json   (Id, Name aller Emittenten)
Kein Key, kein Bot-Check, robots.txt erlaubt /sheldon (nur /bin, /*/shared/component/ und listing-archive gesperrt). Historie
mindestens bis 1997 (Stichprobe 1999-12-31). Veröffentlichungsdatum nur als Datum (publicationDate) -> Folgetag-Regel (has_time False).

Feldzuordnung: notificationSubmitter = Emittent (Name), notificationSubmitterId = SER-Emittentenkürzel, beneficialNames = Meldepflichtiger
(wirtschaftlich Berechtigter), purchaseTotalVotingRate = Erwerbsanteil in % (Aktien + Erwerbspositionen), saleTotalVotingRate =
Veräußerungspositionen in %, belowThresholdVotingRate = Schwelle, unter die gefallen wurde (dann purchase = 0). ISIN nur in den
Positionslisten und nicht immer befüllt; Fallback issuer_id = 'SER:<Kürzel>'.
Nutzungsbedingungen (SIX-Disclaimer Ziff. 10): Nutzung für öffentliche/kommerzielle Zwecke ohne schriftliche Zustimmung untersagt ->
nur interne Auswertung, keine Weitergabe der Rohdaten.
"""
from __future__ import annotations
from datetime import date
from .eu_common import make_event, request, date_windows, in_range

BASE = "https://www.ser-ag.com/sheldon/significant_shareholders/v1/overview.json"
SOURCE, REGION = "SIX_SER", "DACH"
PAGE = 100
_POS = ("positionsEquity", "positionsRights", "positionsSale", "positionsLending", "positionsLendingEquity",
        "positionsLendingRights", "positionsTender", "positionsDiscretionaryVoting")


def _iso(d) -> str | None:
    s = str(d or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() and s != "00000000" else None


def _frac(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x / 100, 10) if x > 0 else None


def _isin(item: dict) -> str | None:
    for k in _POS:
        for p in item.get(k) or []:
            if p.get("isin"):
                return p["isin"]
    return None


def parse_overview(resp: dict) -> list[dict]:
    out = []
    for it in resp.get("itemList") or []:
        p = it.get("publication") or {}
        pub = _iso(p.get("publicationDate"))
        if not pub or not p.get("notificationId"):
            continue
        owners = list(dict.fromkeys(it.get("beneficialNames") or [])) or list(dict.fromkeys(it.get("shareholderNames") or []))
        isin = _isin(it)
        out.append(make_event(
            source=SOURCE, native_id=p["notificationId"], region=REGION, published=None, date_only=pub, has_time=False,
            issuer_name=p.get("notificationSubmitter"), issuer_id=isin or f"SER:{p.get('notificationSubmitterId')}",
            filer_name="; ".join(owners) or None, filer_id=None, url=f"{BASE}?notificationId={p['notificationId']}",
            holding_ratio=_frac(p.get("purchaseTotalVotingRate")), issuer_isin=isin, ser_issuer_id=p.get("notificationSubmitterId"),
            sale_ratio=_frac(p.get("saleTotalVotingRate")), below_threshold=_frac(p.get("belowThresholdVotingRate")),
            threshold_date=_iso(p.get("transactionDate")), ser_category=p.get("category"),
            corrects=p.get("correcteeId") or None, deletes=p.get("deleteeId") or None, owner_group=it.get("groupRepresentative") or None))
    return out


def fetch_events(start_date: date, end_date: date) -> list[dict]:
    out: list[dict] = []
    for a, b in date_windows(start_date, end_date, 31):
        page = 0
        while True:
            r = request("GET", BASE, params={"pageSize": PAGE, "pageNumber": page, "sortAttribute": "byDate",
                                             "fromDate": a.strftime("%Y%m%d"), "toDate": b.strftime("%Y%m%d")}).json()
            items = r.get("itemList") or []
            out += parse_overview(r)
            page += 1
            if not items or page * PAGE >= int(r.get("totalCount") or 0):
                break
    seen, uniq = set(), []
    for e in out:
        if e["event_id"] not in seen and in_range(e, start_date, end_date):
            seen.add(e["event_id"]); uniq.append(e)
    return sorted(uniq, key=lambda e: (e["filing_date"], e["event_id"]))
