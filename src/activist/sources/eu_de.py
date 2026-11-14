"""DACH: Stimmrechtsmitteilungen (DE §§ 33ff WpHG / AT §§ 130ff BörseG) aus der EU-weiten Medienverbreitung.

Befund (Audit 2026-11-14, siehe docs/EU_SOURCES.md): Es gibt KEINE Quelle für Deutschland, die robots.txt UND Nutzungsbedingungen
erfüllt (BaFin-MVP: robots Disallow /; EQS News: Listing nur über /wp-admin/admin-ajax.php (robots-gesperrt), AGB verbieten
Text-/Data-Mining ohne Zustimmung; Unternehmensregister: robots + Bot-Schutz). Einziger technisch sauberer Zugang mit Historie
und Zeitstempel: APA-OTS (ots.at) spiegelt die EQS-PVR-/euro-adhoc-Meldungen österreichischer Emittenten (seit 2009; vor 2021-12
über 'EANS-Stimmrechte' auch einige deutsche Emittenten, z. B. TUI AG). ots.at deklariert in robots.txt/tdmrep.json aber
`tdm-reservation: 1` (Text-und-Data-Mining-Vorbehalt). Deshalb liefert `fetch_events` NUR mit `tdm_licensed=True` (= der Betreiber
hat eine Lizenz/Zustimmung der APA-OTS eingeholt) Daten und wirft sonst PermissionError. Die reinen Parser (`parse_list`,
`parse_notification`, `build_event`) sind frei verwendbar, auch für EQS-Text (WpHG-Formular, de/en).

Zeitstempel: OTS `timestamp` (Epoche, sekundengenau) = EQS/euro-adhoc-Verteilzeit; Anzeige in Europa/Wien (= Berlin).
issuer_id = ISIN (aus Abschnitt 7.A der Meldung, sonst aus dem euro-adhoc-Fußblock), Fallback LEI bzw. OTS-Emittent-Slug.
"""
from __future__ import annotations
import re
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from .eu_common import make_event, request, date_windows, in_range

TZ = ZoneInfo("Europe/Berlin")
SOURCE, REGION = "OTS_PVR", "DACH"
API = "https://core2023.ots.at/api/v1"
SITE = "https://www.ots.at/presseaussendung/"
QUERIES = ("EQS-PVR", "EANS-Stimmrechte")          # ab 2021-12 / 2009 bis 2021-12
PAGE = 10
_TITLE_OK = re.compile(r"^\s*(EQS-PVR|EANS-Stimmrechte|EQS-Stimmrechtsmitteilung)\b", re.I)
_TITLE_EN = re.compile(r"Release according to|Announcement according to|EANS-Voting Rights", re.I)   # englische Zwilling-Fassung
_CORR = re.compile(r"\b(Korrektur|Correction)\b", re.I)

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
           "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12}


# ---------------------------------------------------------------- Parser (rein, offline)
def _isin_ok(s: str) -> bool:
    """ISIN-Prüfsumme (Luhn über die in Ziffern expandierten Buchstaben)."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", s):
        return False
    digits = "".join(str(int(c, 36)) for c in s)
    tot = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        tot += n
    return tot % 10 == 0


def _isins(text: str) -> list[str]:
    return [m for m in re.findall(r"(?<![A-Z0-9])([A-Z]{2}[A-Z0-9]{9}\d)(?![A-Z0-9])", text) if _isin_ok(m)]


def _pct(s: str) -> float:
    return round(float(s.replace(",", ".")) / 100, 10)


def _parse_date(s: str) -> str | None:
    """'10.11.2026' | '1.12.2021' | '10 Nov 2026' | '10. Nov. 2026' | '2026-11-10' -> ISO-Datum."""
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m:
        return m.group(0)
    m = re.search(r"\b(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\b", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
    m = re.search(r"\b(\d{1,2})\.?[ -]+([A-Za-zäÄ]{3,9})\.?[ -]+(\d{4})\b", s)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1))).isoformat()
    return None


def _clean(v: str | None) -> str | None:
    v = re.sub(r"\s+", " ", v or "").strip(" |:-–")
    return v or None


def _section(text: str, num: int) -> str:
    """Text von '<num>.' (Zeilenanfang, gefolgt von Buchstabe) bis zur nächsten Hauptziffer."""
    m = re.search(rf"(?m)^[ |]*{num}\.\s+(?=\S)", text)
    if not m:
        return ""
    n = re.search(rf"(?m)^[ |]*{num + 1}\.\s+(?=\S)", text[m.end():])
    return text[m.start(): m.end() + n.start()] if n else text[m.start():]


def parse_notification(text: str) -> dict:
    """Standardformular (WpHG-Mitteilung DE/EN, BörseG-Beteiligungsmeldung AT, alte euro-adhoc-Variante mit Pipe-Tabellen).

    -> {issuer_name, isin, lei, filer_name, holding_ratio, previous_ratio, threshold_date}. holding_ratio = Gesamtanteil
    (Aktien + Instrumente, 'Total of both'/'Summe') als Bruch; previous_ratio = letzte Meldung. Fehlende Felder -> None.
    """
    t = text.replace("\r", "")
    sec1, sec3, sec5, sec6, sec7 = (_section(t, i) for i in (1, 3, 5, 6, 7))
    # Emittent
    issuer = None
    m = re.search(r"(?:^|\n)[ |]*1\.\s*(?:Emittent|Issuer)[^:\n]*:\s*(.+)", sec1, re.I)      # AT: '1. Emittent: X'
    if m:
        issuer = _clean(m.group(1))
    if not issuer:
        m = re.search(r"(?im)^[ |]*Name\s*:[ \t|]*\n?[ \t|]*(.+)$", sec1)                     # DE/EN: 'Name:' (Wert evtl. Folgezeile)
        issuer = _clean(m.group(1)) if m else None
    lei = None
    m = re.search(r"LEI\)?\s*:?\s*\n?\s*([A-Z0-9]{20})\b", sec1)
    if m:
        lei = m.group(1)
    # Meldepflichtiger
    filer = None
    for pat in (r"(?:Legal entity|Juristische Person|Rechtsträger)\s*:\s*(.+)", r"(?im)^[ |]*Name\s*:[ \t|]*\n?[ \t|]*(.+)$"):
        m = re.search(pat, sec3, re.I)
        if m:
            filer = _clean(re.split(r"\s+(?:City of registered|Sitz|Staat|Date of birth|Geburtsdatum)\b", m.group(1))[0])
            break
    if not filer:                                                         # natürliche Person: 'Surname/First name'
        nm = re.findall(r"(?im)^[ |]*(?:Surname|Nachname|Vorname|First name)\s*:[ \t|]*\n?[ \t|]*(\S.*)$", sec3)       # Wert evtl. in Folgezeile
        filer = _clean(" ".join(reversed(nm))) if nm else None
    # Schwellendatum (Wert evtl. in Folgezeile)
    thr = _parse_date(sec5[:300]) if sec5 else None
    # Anteile: Abschnitt 6 enthält Reihenfolge neu (Aktien, Instrumente, Summe), dann vorherige Meldung
    pcts = re.findall(r"(\d+(?:[.,]\d+)?)[ _]*%", sec6)
    new = _pct(pcts[2]) if len(pcts) >= 3 else (_pct(pcts[0]) if len(pcts) == 1 else None)
    prev = _pct(pcts[5]) if len(pcts) >= 6 else None
    # ISIN: Abschnitt 7.A, sonst irgendwo (z. B. euro-adhoc-Fußblock 'ISIN: AT...')
    isins = _isins(sec7) or _isins(t)
    return {"issuer_name": issuer, "isin": isins[0] if isins else None, "lei": lei, "filer_name": filer,
            "holding_ratio": new, "previous_ratio": prev, "threshold_date": thr}


def article_text(pressrelease: dict) -> str:
    """Fügt die 'article'-Blöcke einer OTS-Pressrelease zu Klartext zusammen."""
    res = pressrelease.get("result", pressrelease)
    return "\n".join(a.get("content", "") for a in res.get("article", []))


def parse_list(resp: dict) -> list[dict]:
    """OTS-Suchantwort -> Stubs (ohne Detail). Nur deutschsprachige Stimmrechtsmeldungen (EN-Zwilling entfällt)."""
    out = []
    for r in resp.get("result") or []:
        title = (r.get("title") or {}).get("content") or ""
        if not _TITLE_OK.search(title) or _TITLE_EN.search(title) or not r.get("key"):
            continue
        local = datetime.fromtimestamp(int(r["timestamp"]), TZ)
        out.append({"key": r["key"], "title": title, "issuer_name": r.get("emittentName"), "published": local.isoformat(),
                    "date": local.date().isoformat(), "issuer_slug": r.get("emittentSlug"),
                    "url": f"{SITE}{r['key']}/{r.get('titleSlug', '')}", "correction": bool(_CORR.search(title))})
    return out


def build_event(stub: dict, detail: dict | None = None) -> dict:
    d = detail or {}
    return make_event(
        source=SOURCE, native_id=stub["key"], region=REGION, published=stub["published"], date_only=stub["date"], has_time=True,
        issuer_name=d.get("issuer_name") or stub["issuer_name"], issuer_id=d.get("isin") or d.get("lei") or stub.get("issuer_slug"),
        filer_name=d.get("filer_name"), filer_id=None, url=stub["url"], holding_ratio=d.get("holding_ratio"),
        previous_ratio=d.get("previous_ratio"), threshold_date=d.get("threshold_date"), issuer_isin=d.get("isin"),
        issuer_lei=d.get("lei"), correction=True if stub.get("correction") else None, headline=stub["title"])


# ---------------------------------------------------------------- Live (nur mit Lizenz/Zustimmung, siehe Modul-Docstring)
def _require_license(tdm_licensed: bool) -> None:
    if not tdm_licensed:
        raise PermissionError("ots.at deklariert tdm-reservation=1 (robots.txt TDM-policy). Live-Abruf nur mit eingeholter Zustimmung "
                              "der APA-OTS: fetch_events(..., tdm_licensed=True). Siehe docs/EU_SOURCES.md.")


def fetch_events(start_date: date, end_date: date, enrich_docs: bool = True, tdm_licensed: bool = False) -> list[dict]:
    _require_license(tdm_licensed)
    frm = int(datetime.combine(start_date, dtime.min, TZ).timestamp())
    to = int(datetime.combine(end_date + timedelta(days=1), dtime.min, TZ).timestamp()) - 1
    stubs: dict[str, dict] = {}
    for q in QUERIES:
        for a, b in date_windows(start_date, end_date, 92):                 # Fenster, um die Zahl der Seiten klein zu halten
            wf = max(frm, int(datetime.combine(a, dtime.min, TZ).timestamp()))
            wt = min(to, int(datetime.combine(b + timedelta(days=1), dtime.min, TZ).timestamp()) - 1)
            page = 1
            while True:
                r = request("GET", f"{API}/search", params={"query": q, "channel[]": "index", "type": "", "from": wf, "to": wt,
                                                              "page": page, "maxCountPerPage": PAGE}).json()
                for s in parse_list(r):
                    stubs[s["key"]] = s
                if page >= int((r.get("meta") or {}).get("totalPages") or 0):
                    break
                page += 1
    out = []
    for s in sorted(stubs.values(), key=lambda s: s["published"]):
        detail = None
        if enrich_docs:
            try:
                detail = parse_notification(article_text(request("GET", f"{API}/pressrelease/{s['key']}").json()))
            except Exception:
                detail = None
        out.append(build_event(s, detail))
    return [e for e in out if in_range(e, start_date, end_date)]
