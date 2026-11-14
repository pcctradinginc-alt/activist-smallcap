"""Universum deutscher Aktien, die Privatanleger handeln können (Frankfurt, München m:access, Xetra-Freiverkehr).

Quellen (frei, offiziell; Abruf nur nach robots.txt, >= 1 s Abstand über eu_common):
  1. Deutsche Börse 'Listed companies' XLSX (Blätter Prime Standard, General Standard, Scale, Basic Board). Die Blob-URL ist
     gehasht -> wird von der Seite .../statistics/listed-companies gelesen. Prime/General = regulierter Markt (regulated=True).
  2. Börse München m:access 'Gelistete Unternehmen' (HTML-Tabelle, ISIN steckt im Link /aktie/<ISIN>/).
  3. Deutsche Börse T7-Instrumentenlisten XFRA (Börse Frankfurt) und XETR: Instrument Type 'CS' mit ISIN-Präfix DE, die in 1./2.
     fehlen, gelten als Open Market / Freiverkehr (regulated=False).
Nur Emittenten mit ISIN DE (immer) sowie AT/CH/LU, wenn sie in den Listen 1./2. (Primärlisting in Deutschland) stehen.
Ticker: OpenFIGI-Listing GY/GR -> '.DE', sonst GF -> '.F' (optional, resolve_tickers=True; Cache openfigi.json).
Nicht abgedeckt: Düsseldorf/Hamburg (keine freie maschinenlesbare Liste; regulierter Markt dort ist fast immer Teilmenge von Frankfurt),
Stuttgart (Cloudflare-Challenge -> nicht abrufbar). Snapshot von heute, nicht point-in-time (Survivorship).
Cache: DATA/cache/de_universe.json
"""
from __future__ import annotations
import csv, html, io, json, re, time, unicodedata, zipfile
from collections import Counter
from datetime import date
from urllib import robotparser
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET
from ..common import DATA
from . import eu_common

CACHE = DATA / "cache" / "de_universe.json"
LISTED_PAGE = "https://cashmarket.deutsche-boerse.com/cash-en/Data-Tech/statistics/listed-companies"
DOWNLOADS_PAGE = "https://cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/Downloads"
FRANKFURT_PAGE = "https://cashmarket.deutsche-boerse.com/cash-en/trading/Tradable-Instruments-Xetra/boersefrankfurt"
MACCESS_PAGE = "https://www.boerse-muenchen.de/maccess/gelistete-unternehmen/"
SHEETS = {"Prime Standard": True, "General Standard": True, "Scale": False, "Basic Board": False}
COUNTRY = {"Germany": "DE", "Netherlands": "NL", "Luxembourg": "LU", "Austria": "AT", "Switzerland": "CH"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
FIGI_SUFFIX = {"GY": ".DE", "GR": ".DE", "GF": ".F"}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}

_robots: dict[str, robotparser.RobotFileParser | None] = {}


def robots_ok(url: str) -> bool:
    """robots.txt je Host (einmal geladen, gedrosselt). 401/403 oder Netzfehler -> verboten; sonstige 4xx -> erlaubt."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            eu_common._throttle()
            r = eu_common._S.get(base + "/robots.txt", timeout=30)
            if r.status_code in (401, 403) or r.status_code >= 500:
                rp.disallow_all = True
            elif r.status_code >= 400:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
        except Exception:
            rp.disallow_all = True
        _robots[base] = rp
    return _robots[base].can_fetch("activist-smallcap", url)


def polite_get(url: str, **kw):
    if not robots_ok(url):
        raise PermissionError(f"robots.txt verbietet {url}")
    return eu_common.request("GET", url, **kw)


# ------------------------------------------------------------------ XLSX (stdlib, openpyxl nicht installiert)

def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(data: bytes) -> dict[str, list[list[str]]]:
    """{Blattname: Zeilen (Listen von Strings, '' für leer)} ohne externe Abhängigkeit."""
    z = zipfile.ZipFile(io.BytesIO(data))
    sst = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            sst.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    target = {r.get("Id"): r.get("Target") for r in rels}
    out = {}
    for sh in wb.find("m:sheets", NS):
        rid = sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        path = "xl/" + target[rid].lstrip("/").removeprefix("xl/")
        rows = []
        for row in ET.fromstring(z.read(path)).iter(f"{{{NS['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", NS):
                v = c.find("m:v", NS)
                if c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{{{NS['m']}}}t"))
                elif v is None or v.text is None:
                    continue
                else:
                    val = sst[int(v.text)] if c.get("t") == "s" else v.text
                cells[_col(c.get("r"))] = val.strip()
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        out[sh.get("name")] = rows
    return out


def parse_listed_companies(sheets: dict[str, list[list[str]]]) -> list[dict]:
    out = []
    for sheet, regulated in SHEETS.items():
        rows = sheets.get(sheet) or []
        hi = next((i for i, r in enumerate(rows) if "ISIN" in r), None)
        if hi is None:
            continue
        head = rows[hi]
        ix = {h: head.index(h) for h in ("ISIN", "Company", "Country") if h in head}
        for r in rows[hi + 1:]:
            get = lambda k: r[ix[k]].strip() if k in ix and ix[k] < len(r) else ""
            isin = get("ISIN")
            if not ISIN_RE.match(isin):
                continue
            out.append({"isin": isin, "name": html.unescape(get("Company")), "segment": sheet, "regulated": regulated,
                        "country": COUNTRY.get(get("Country"), isin[:2])})
    return out


def parse_maccess(page: str) -> list[dict]:
    out = []
    for tr in re.findall(r"(?s)<tr>(.*?)</tr>", page):
        m = re.search(r'href="/aktie/([A-Z]{2}[A-Z0-9]{9}\d)/"', tr)
        n = re.search(r'(?s)<td>\s*<a[^>]*>\s*(.*?)\s*</a>', tr)
        if m and n:
            out.append({"isin": m.group(1), "name": html.unescape(re.sub(r"\s+", " ", n.group(1))).strip(),
                        "segment": "m:access", "regulated": False, "country": m.group(1)[:2]})
    return out


def parse_t7_csv(text: str, home_only: bool = True) -> list[dict]:
    """T7 allTradableInstruments (Kopfzeilen 'Market:'/'Date Last Update:' dann Header): Aktien (CS) mit DE-ISIN."""
    lines = text.splitlines()
    hi = next(i for i, l in enumerate(lines) if l.startswith("Product Status"))
    out = {}
    for r in csv.DictReader(lines[hi:], delimiter=";"):
        isin = r.get("ISIN", "")
        if r.get("Instrument Type") == "CS" and ISIN_RE.match(isin) and (isin.startswith("DE") or not home_only):
            out.setdefault(isin, {"isin": isin, "name": r.get("Instrument", "").strip(), "segment": "Open Market",
                                  "regulated": False, "country": isin[:2], "wkn": r.get("WKN", "").lstrip("0")})
    return list(out.values())


def merge(listed: list[dict], maccess: list[dict], t7: list[dict]) -> list[dict]:
    """Priorität: Deutsche-Börse-Segmente > m:access > Open Market. AT/CH/LU/NL nur aus den Listen 1./2. (nicht aus T7)."""
    by: dict[str, dict] = {}
    for src in (listed, maccess, t7):
        for r in src:
            if r["isin"] in by:
                continue
            if r["country"] not in ("DE", "AT", "CH", "LU"):
                continue
            by[r["isin"]] = dict(r, yahoo_ticker=None)
    for r in by.values():
        r.pop("wkn", None)
    return sorted(by.values(), key=lambda r: (r["segment"], r["name"]))


def yahoo_from_listings(listings: list[dict]) -> str | None:
    """OpenFIGI-Listings -> Yahoo: GY/GR -> '.DE', sonst GF -> '.F'."""
    eq = [l for l in listings if l.get("ticker")]
    for exch in ("GY", "GR", "GF"):
        for l in eq:
            if l.get("exch") == exch:
                t = l["ticker"].strip().rstrip("/").replace("/", "-").replace(" ", "-")
                return t + FIGI_SUFFIX[exch]
    return None


def resolve_tickers(rows: list[dict], use_network: bool = True) -> None:
    from . import openfigi
    m = openfigi.map_isins([r["isin"] for r in rows], use_network)
    for r in rows:
        r["yahoo_ticker"] = yahoo_from_listings(m.get(r["isin"]) or [])


def counts(rows: list[dict]) -> dict:
    return dict(Counter(r["segment"] for r in rows))


# ------------------------------------------------------------------ Abruf

def _link(page: str, pat: str) -> str:
    m = re.search(rf'href="([^"]*{pat}[^"]*)"', page)
    if not m:
        raise LookupError(f"Link {pat} nicht gefunden")
    return m.group(1)


def fetch_universe(with_tickers: bool = True) -> list[dict]:
    lp = polite_get(LISTED_PAGE).text
    xlsx = polite_get(urljoin(LISTED_PAGE, _link(lp, r"Listed-companies\.xlsx"))).content
    listed = parse_listed_companies(read_xlsx(xlsx))
    maccess = parse_maccess(polite_get(MACCESS_PAGE).text)
    t7 = []
    for page, pat in ((DOWNLOADS_PAGE, r"t7-xetr-allTradableInstruments\.csv"), (FRANKFURT_PAGE, r"t7-xfra-BF-allTradableInstruments\.csv")):
        try:
            pg = polite_get(page).text
            t7 += parse_t7_csv(polite_get(urljoin(page, _link(pg, pat)), timeout=180).content.decode("utf-8-sig", "replace"))
        except Exception as e:                       # eine fehlende T7-Liste soll das Universum nicht verhindern
            print(f"WARN T7 {pat}: {e}")
    # m:access-Namen/ISINs sind verlässlich; T7-Namen sind abgekürzt (nur Open Market)
    rows = merge(listed, maccess, t7)
    if with_tickers:
        resolve_tickers(rows)
    return rows


def load_universe(refresh: bool = False, max_age_days: int = 30, with_tickers: bool = True) -> list[dict]:
    if CACHE.exists() and not refresh:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        if (date.today() - date.fromisoformat(d["asof"])).days <= max_age_days:
            return d["rows"]
    rows = fetch_universe(with_tickers)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"asof": date.today().isoformat(), "counts": counts(rows), "rows": rows},
                                ensure_ascii=False), encoding="utf-8")
    return rows


# ------------------------------------------------------------------ Namensnormalisierung (Abgleich in campaigns_c)
LEGAL = r"\b(?:ag|se|kgaa|gmbh|co|kg|sa|s a|n v|nv|plc|ltd|inc|holding|holdings|group|gruppe|international|o n|inh|na|vz|st|vzo|vz|ord|eo|nom|reg|namens|inhaber|aktien|stammaktien|vorzugsaktien)\b"


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").casefold())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("&", " und ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(?:eo|eur)\s*\d+(?:\s*\d+)?\b", " ", s)
    s = re.sub(LEGAL, " ", s)
    s = re.sub(r"(?:\s+und)+\s*$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()
