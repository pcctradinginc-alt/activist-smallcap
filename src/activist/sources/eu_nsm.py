"""UK: FCA National Storage Mechanism (NSM), TR-1 'Holding(s) in Company'.

Suche: POST https://api.data.fca.org.uk/search?index=nsm-search (JSON/Elasticsearch-Stil, kein Key, keine Bot-Prüfung).
Dokumente: https://data.fca.org.uk/artefacts/<download_link> (HTML des TR-1-Formulars, optional via enrich=True).
publication_date hat Sekunden-Genauigkeit (UTC) für RNS-Quellen; Altbestand 'Portal (pre-Sept 2017)' nur Datum.
"""
from __future__ import annotations
import html, re
from datetime import date
from .eu_common import make_event, request, pct_to_fraction, date_windows, in_range

SEARCH = "https://api.data.fca.org.uk/search"
ARTEFACTS = "https://data.fca.org.uk/artefacts/"
SOURCE, REGION = "NSM", "UK"
PAGE = 500


def build_query(start: date, end: date, frm: int = 0, size: int = PAGE) -> dict:
    return {"from": frm, "size": size, "sort": "submitted_date", "sortorder": "asc",
            "criteriaObj": {"criteria": [{"name": "headline", "value": "Holding(s) in Company"}],
                            "dateCriteria": [{"name": "submitted_date",
                                              "value": {"from": f"{start.isoformat()}T00:00:00Z",
                                                        "to": f"{end.isoformat()}T23:59:59Z"}}]}}


def parse_hits(resp: dict) -> list[dict]:
    out = []
    for h in (resp.get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        if "holding" not in (s.get("headline") or "").lower() or s.get("latest_flag", "Y") != "Y":
            continue
        pub = s.get("publication_date") or s.get("submitted_date")
        if not pub:
            continue
        has_time = not pub.endswith("T00:00:00Z")          # Altbestand: nur Datum
        rel = (s.get("related_org") or [{}])[0] if s.get("related_org") else {}
        isin, lei = s.get("isin"), s.get("lei")
        out.append(make_event(
            source=SOURCE, native_id=s.get("disclosure_id") or h["_id"], region=REGION,
            published=pub.replace("Z", "+00:00"), date_only=pub[:10], has_time=has_time,
            issuer_name=s.get("company"), issuer_id=isin or lei,
            filer_name=rel.get("company"), filer_id=rel.get("lei"),
            url=ARTEFACTS + s["download_link"] if s.get("download_link") else None,
            issuer_lei=lei, issuer_isin=isin, nsm_source=s.get("source")))
    return out


def _text(raw: str) -> str:
    raw = re.sub(r"<style.*?</style>", "", raw, flags=re.S)
    raw = re.sub(r"</(p|tr|div|h\d)>", "\n", raw)
    raw = re.sub(r"</t[dh]>", " | ", raw)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(re.sub(r"<[^>]+>", "", raw)))


def parse_tr1(raw: str) -> dict:
    """Liest aus dem TR-1-Formular (HTML): Emittenten-ISIN/-Name, Meldepflichtigen, Gesamtanteil (Ziff. 7)."""
    t = _text(raw)
    lines = [l.strip(" |") for l in t.splitlines()]
    lines = [l for l in lines if l]

    def after(label: str, n: int = 1):
        for i, l in enumerate(lines):
            if l.lower().startswith(label.lower()):
                return lines[i + n] if i + n < len(lines) else None
        return None
    isin = after("ISIN")
    isin = isin if isin and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin) else None
    if not isin:                                    # Formate variieren: erste ISIN-förmige Zeile im Abschnitt 1
        for l in lines[:40]:
            if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", l):
                isin = l; break
    # Meldepflichtiger: erste Zeile nach "3. Details of person subject" -> "Name"
    filer = None
    for i, l in enumerate(lines):
        if re.match(r"3\.\s*Details of person", l, re.I):
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].lower() == "name" and j + 1 < len(lines) and lines[j + 1].lower() != "name":
                    filer = lines[j + 1]; break
            break
    total = None
    for i, l in enumerate(lines):
        if l.lower().startswith("resulting situation on the date"):
            nums = [x for x in lines[i + 1:i + 8] if re.fullmatch(r"\d+[.,]\d+\s*%?|\d+\s*%", x)]
            if len(nums) >= 3:
                total = pct_to_fraction(nums[2])          # "Total of both in %"
            elif nums:
                total = pct_to_fraction(nums[0])
            break
    return {"isin": isin, "issuer_name": after("Issuer Name"), "filer_name": filer, "holding_ratio": total}


def enrich(ev: dict) -> dict:
    """Holt das TR-1 (1 Request) und ergänzt ISIN, Filer, holding_ratio. Fehler -> Event unverändert."""
    if not ev.get("url"):
        return ev
    try:
        d = parse_tr1(request("GET", ev["url"]).text)
    except Exception:
        return ev
    if d["isin"]:
        ev["issuer_id"] = ev["issuer_isin"] = d["isin"]
    if d["filer_name"]:
        ev["filer_name"] = d["filer_name"]
    if d["holding_ratio"] is not None:
        ev["holding_ratio"] = d["holding_ratio"]
    return ev


def fetch_events(start_date: date, end_date: date, enrich_docs: bool = False) -> list[dict]:
    out: list[dict] = []
    for a, b in date_windows(start_date, end_date, 7):
        frm = 0
        while True:
            r = request("POST", SEARCH, params={"index": "nsm-search"}, json=build_query(a, b, frm)).json()
            hits = (r.get("hits") or {}).get("hits", [])
            out += parse_hits(r)
            frm += len(hits)
            if not hits or frm >= r["hits"]["total"]["value"] or frm >= 10000:
                break
    out = [e for e in out if in_range(e, start_date, end_date)]
    return [enrich(e) for e in out] if enrich_docs else out
