"""SEC EDGAR: Schedule 13D/13G aus dem täglichen Formular-Index, Zeitstempel aus dem Filing-Header."""
from __future__ import annotations
import re, time
from datetime import date
import requests
from ..common import USER_AGENT, effective_decision_time, event_id

FORMS = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
         "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
_S = requests.Session(); _S.headers.update({"User-Agent": USER_AGENT})


def _get(url: str) -> requests.Response:
    for i in range(4):
        r = _S.get(url, timeout=30)
        if r.status_code == 200:
            return r
        if r.status_code in (403, 429, 503):
            time.sleep(2 ** i); continue
        r.raise_for_status()
    r.raise_for_status(); return r


def daily_index(d: date) -> list[dict]:
    if d.weekday() >= 5:              # Wochenende: kein Index (SEC antwortet teils mit 403 statt 404)
        return []
    q = (d.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{d.year}/QTR{q}/form.{d:%Y%m%d}.idx"
    for i in range(4):
        r = _S.get(url, timeout=30)
        if r.status_code == 404:      # Feiertag / Index noch nicht erstellt
            return []
        if r.status_code in (403, 429, 503):
            time.sleep(2 ** i); continue
        r.raise_for_status()
        return parse_form_index(r.text)
    print(f"::warning::EDGAR-Index {d} nicht abrufbar (HTTP {r.status_code}) – Feiertag oder Sperre, wird beim nächsten Lauf nachgeholt")
    return []


def parse_form_index(text: str) -> list[dict]:
    rows, started = [], False
    for line in text.splitlines():
        if line.startswith("----"):
            started = True; continue
        if not started or not line.strip():
            continue
        m = re.match(r"^(.+?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8}|\d{4}-\d{2}-\d{2})\s+(\S+)\s*$", line)
        if not m:
            continue
        form, company, cik, fdate, path = m.groups()
        if form.strip() in FORMS:
            fdate = fdate if "-" in fdate else f"{fdate[:4]}-{fdate[4:6]}-{fdate[6:]}"
            rows.append({"form": form.strip(), "company": company.strip(), "cik": cik, "date": fdate, "path": path})
    return rows


def parse_header(text: str) -> dict:
    """Liest Annahmezeitpunkt, Subject Company und Filer aus dem SGML-Header."""
    def grab(pat, s=text):
        m = re.search(pat, s); return m.group(1).strip() if m else None
    acc = grab(r"<ACCEPTANCE-DATETIME>(\d{14})")
    def role_block(role):
        # Never let a missing field fall through into the next entity section.
        m = re.search(r"(?m)^\s*" + re.escape(role) + r"\s*:\s*$", text)
        if not m:
            return ""
        tail = text[m.end():]
        return re.split(r"(?m)^\s*(?:SUBJECT COMPANY|FILED BY|REPORTING OWNER|ISSUER|<DOCUMENT>)\s*:?", tail, maxsplit=1)[0]
    subj = role_block("SUBJECT COMPANY")
    filer = role_block("FILED BY")
    published = None
    if acc:  # EDGAR-Zeit ist US/Eastern
        from zoneinfo import ZoneInfo
        from datetime import datetime
        published = datetime.strptime(acc, "%Y%m%d%H%M%S").replace(tzinfo=ZoneInfo("America/New_York")).isoformat()
    return {
        "published": published,
        "subject_name": grab(r"COMPANY CONFORMED NAME:\s*(.+)", subj),
        "subject_cik": grab(r"CENTRAL INDEX KEY:\s*(\d+)", subj),
        "filer_name": grab(r"COMPANY CONFORMED NAME:\s*(.+)", filer),
        "filer_cik": grab(r"CENTRAL INDEX KEY:\s*(\d+)", filer),
        "accession": grab(r"ACCESSION NUMBER:\s*([\d-]+)"),
    }


def fetch_events(d: date, with_headers: bool = True, limit: int | None = None) -> list[dict]:
    from .edgar_backfill import activist_name_keys, select
    out = []
    rows = list(select(daily_index(d), activist_name_keys()).values())   # 13D immer, 13G nur von Aktivisten
    for row in rows[:limit]:
        hdr = {}
        if with_headers:
            try:
                txt = _get("https://www.sec.gov/Archives/" + row["path"]).text[:20000]
                hdr = parse_header(txt); time.sleep(0.12)   # SEC-Limit 10 req/s
            except Exception as e:  # Datenqualität: Ereignis bleibt, aber ohne Uhrzeit
                hdr = {"error": str(e)}
        acc = hdr.get("accession") or row["path"].rsplit("/", 1)[-1].replace(".txt", "")
        pub = hdr.get("published")
        out.append({
            "event_id": event_id("EDGAR", acc), "source": "EDGAR", "region": "US",
            "form": row["form"], "published": effective_decision_time(pub, row["date"]).isoformat(),
            "has_time": bool(pub), "filing_date": row["date"],
            "issuer_name": hdr.get("subject_name") or row["company"], "issuer_id": hdr.get("subject_cik") or row["cik"],
            "filer_name": hdr.get("filer_name"), "filer_id": hdr.get("filer_cik"),
            "url": "https://www.sec.gov/Archives/" + row["path"],
        })
    return out


def ticker_map() -> dict[str, str]:
    """CIK -> Ticker aus SEC company_tickers.json (aktueller Stand; historische Zuordnung prüfen)."""
    j = _get("https://www.sec.gov/files/company_tickers.json").json()
    return {str(v["cik_str"]).zfill(10): v["ticker"] for v in j.values()}
