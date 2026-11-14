"""DE: Stimmrechtsmitteilungen (§§ 33ff WpHG) von den eigenen Investor-Relations-Seiten der Emittenten (source 'DE_IR').

Warum: BaFin (robots Disallow /), EQS/DGAP (ToS: kein TDM), Unternehmensregister (robots + Bot-Schutz) und ots.at (TDM-Vorbehalt) sind
gesperrt (docs/EU_SOURCES.md). Die Emittenten veroeffentlichen die Meldungen aber selbst (§ 40 WpHG: auch auf der eigenen Website
nach Art. 3 TranspRL-Umsetzung ueblich). Dieses Modul liest nur diese Seiten, hoeflich und konform:

* robots.txt je Host (urllib.robotparser, Token 'activist-smallcap') VOR jedem Abruf; Disallow -> Seite wird uebersprungen;
  robots.txt 4xx/5xx/Netzfehler (ausser 404/410) -> alles gesperrt (konservativ). Crawl-delay wird beachtet (max. 10 s).
* Nie Abruf von eqs-news.com / eqs.com / dgap.de / ots.at / bafin.de / unternehmensregister.de (auch nicht per Redirect).
  Bettet eine IR-Seite nur ein EQS-Widget (iframe/script) ein, wird 'eqs_widget' vermerkt und uebersprungen.
* UA 'activist-smallcap research contact@example.org', >= 1 s Abstand je Host, kein Login, kein CAPTCHA-Umgehen.

Pipeline: Universum (Deutsche Boerse 'Listed companies' XLSX: Prime/General Standard/Scale; eigener Mini-XLSX-Leser, kein openpyxl noetig)
-> Website je ISIN (Wikidata P946->P856) -> IR-Seiten-Erkennung (Homepage, typische Pfade, sitemap.xml; Cache DATA/cache/de_ir/sites.json)
-> Eintraege (Datum + Link) -> Detail (HTML/PDF) -> eu_de.parse_notification -> Event (form MajorHolding).
Datum ohne Uhrzeit => Folgetag-Regel (common.effective_decision_time); steht eine Uhrzeit im Listeneintrag, wird sie (Europe/Berlin) genutzt.
"""
from __future__ import annotations
import hashlib, io, json, re, threading, time, zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlsplit, urldefrag
from zoneinfo import ZoneInfo
import requests, yaml
from .. import common
from . import eu_de
from .eu_common import UA, make_event, in_range

SOURCE, REGION = "DE_IR", "DACH"
TZ = ZoneInfo("Europe/Berlin")
ROBOT_TOKEN = "activist-smallcap"
MIN_INTERVAL = 1.0
BLOCKED_HOSTS = ("eqs-news.com", "eqs.com", "dgap.de", "ots.at", "bafin.de", "unternehmensregister.de")
LISTED_PAGE = "https://www.cashmarket.deutsche-boerse.com/cash-en/Data-Tech/statistics/listed-companies"
SEGMENTS = ("Prime Standard", "General Standard", "Scale")
# query.wikidata.org/sparql ist per robots.txt (User-agent *) gesperrt -> QLever-Mirror der Wikidata-Daten (robots.txt dort 404 = erlaubt).
WIKIDATA = "https://qlever.dev/api/wikidata"
MAX_HTML, MAX_PDF = 10_000_000, 12_000_000
IR_PATHS = ("/investor-relations", "/ir", "/en/investor-relations", "/de/investor-relations", "/investoren", "/investor-relations/stimmrechtsmitteilungen",
            "/en/investor-relations/voting-rights", "/de/investor-relations/stimmrechtsmitteilungen", "/investor-relations/voting-rights")
VOTING_RE = re.compile(r"stimmrecht(?!svertret|sausue?b|sausüb|sberechtig|sformular|svollmacht)|voting[\s_-]*rights?|wphg|major[\s_-]*shareholder|stimmrechtsmeldung|bedeutende[\s_-]+beteiligung", re.I)
IR_RE = re.compile(r"investor|/ir\b|anleger|aktion[aä]r|shareholder|finanz|publikation|regulat|pflichtver|mitteilung", re.I)
STRONG_RE = re.compile(r"stimmrechtsmitteilung|stimmrechtsmeldung|stimmrechtsanteil|voting[\s_-]*rights?[\s_-]*(notification|announcement|release)|"
                       r"(release|veroeffentlichung|ver\u00f6ffentlichung)[^.]{0,40}(sec\.?|section|art\.?|article|\u00a7)\s*40|major[\s_-]*holding|beteiligungsmeldung|stimmrechte[\s_-]+(?!gesamt)", re.I)
NOT_NOTIF_RE = re.compile(r"gesamt(zahl|stimmrecht)|total[\s_-]*number|aktienverkauf|directors|managers|15a|(?<!\d)43[\s_-]*wphg|stimmrechtsvertreter|insider|total[\s_-]*voting", re.I)
CURATED_PATH = Path(__file__).resolve().parents[3] / "config" / "de_ir_urls.yaml"
CURATED_FAIL = ("eqs_widget", "js_only", "bot_protection", "none_found")
EQS_RE = re.compile(r"eqs-news\.com|eqs\.com|eqs-cockpit|dgap\.de|eqs-adhoc", re.I)
MONTHS_EN = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


class Skip(Exception):
    """Abruf nicht erlaubt (robots/Blockliste) oder nicht moeglich."""


# ---------------------------------------------------------------- Cache
def cache_dir() -> Path:
    d = common.DATA / "cache" / "de_ir"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_json(name: str) -> dict:
    p = cache_dir() / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_json(name: str, obj: dict) -> None:
    (cache_dir() / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------- HTTP (robots + Blockliste + Drosselung je Host)
def host_blocked(url: str) -> bool:
    h = (urlsplit(url).hostname or "").lower()
    return any(h == b or h.endswith("." + b) for b in BLOCKED_HOSTS)


class Client:
    """Hoeflicher Client: robots.txt je Host, Blockliste, >= 1 s je Host, manuelle Redirect-Pruefung. Thread-sicher."""

    def __init__(self, interval: float = MIN_INTERVAL):
        self.interval = interval
        self.s = requests.Session(); self.s.headers.update({"User-Agent": UA})
        self._lock = threading.Lock()
        self._next: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}   # None = alles gesperrt
        self._rlock: dict[str, threading.Lock] = {}
        self.sitemaps: dict[str, list[str]] = {}

    def _wait(self, host: str, extra: float = 0.0) -> None:
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next.get(host, 0.0))
            self._next[host] = t + max(self.interval, extra)
        if t > now:
            time.sleep(t - now)

    def _raw(self, url: str, stream_cap: int, **kw) -> requests.Response:
        self._wait(urlsplit(url).netloc.lower())
        r = self.s.request(kw.pop("method", "GET"), url, timeout=30, allow_redirects=False, stream=True, **kw)
        try:
            chunks, n = [], 0
            for c in r.iter_content(65536):
                chunks.append(c); n += len(c)
                if n > stream_cap:
                    raise Skip(f"zu gross: {url}")
            r._content = b"".join(chunks)
        finally:
            r.close()
        return r

    def robots(self, url: str):
        sp = urlsplit(url)
        key = f"{sp.scheme}://{sp.netloc.lower()}"
        lk = self._rlock.setdefault(key, threading.Lock())
        with lk:
            if key in self._robots:
                return self._robots[key]
            rp: robotparser.RobotFileParser | None
            try:
                r = self._raw(key + "/robots.txt", 1_000_000)
                hops = 0
                while r.status_code in (301, 302, 303, 307, 308) and hops < 3:     # robots darf umgeleitet werden (z. B. http->https)
                    loc = urljoin(r.url, r.headers.get("Location", ""))
                    if host_blocked(loc):
                        raise Skip("robots-Redirect auf gesperrten Host")
                    r = self._raw(loc, 1_000_000); hops += 1
                if r.status_code in (404, 410):
                    rp = robotparser.RobotFileParser(); rp.parse([])
                elif r.status_code == 200:
                    rp = robotparser.RobotFileParser(); rp.parse(r.text.splitlines())
                    self.sitemaps[key] = [ln.split(":", 1)[1].strip() for ln in r.text.splitlines() if ln.lower().startswith("sitemap:")]
                else:
                    rp = None
            except Exception:
                rp = None
            self._robots[key] = rp
            return rp

    def allowed(self, url: str) -> bool:
        if host_blocked(url):
            return False
        rp = self.robots(url)
        return bool(rp and rp.can_fetch(ROBOT_TOKEN, url))

    def get(self, url: str, cap: int = MAX_HTML) -> requests.Response:
        """GET mit robots-/Blockpruefung je Hop. Skip bei Disallow/Blockliste/Fehlerstatus."""
        for _ in range(6):
            if host_blocked(url):
                raise Skip(f"gesperrter Host: {url}")
            rp = self.robots(url)
            if rp is None:
                raise Skip(f"robots.txt nicht lesbar/gesperrt: {urlsplit(url).netloc}")
            if not rp.can_fetch(ROBOT_TOKEN, url):
                raise Skip(f"robots Disallow: {url}")
            cd = rp.crawl_delay(ROBOT_TOKEN)
            if cd and cd > self.interval:
                self._wait(urlsplit(url).netloc.lower(), min(float(cd), 10.0))
            r = self._raw(url, cap)
            if r.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, r.headers.get("Location", "")); continue
            if r.status_code >= 400:
                raise Skip(f"HTTP {r.status_code}: {url}")
            return r
        raise Skip("zu viele Redirects")


# ---------------------------------------------------------------- Universum
def _xlsx_sheets(blob: bytes) -> dict[str, list[list[str]]]:
    """Minimaler XLSX-Leser (zipfile + ElementTree): {Blattname: Zeilen (Liste von Zellwerten, '' fuer leer)}."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    z = zipfile.ZipFile(io.BytesIO(blob))
    ss = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    out = {}
    for sh in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns):
        target = rels[sh.get(f"{{{ns['r']}}}id")].lstrip("/")
        root = ET.fromstring(z.read(target if target.startswith("xl/") else "xl/" + target))
        rows = []
        for row in root.iter(f"{{{ns['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", ns):
                col = 0
                for ch in re.match(r"[A-Z]+", c.get("r")).group(0):
                    col = col * 26 + ord(ch) - 64
                v = c.find("m:v", ns)
                is_ = c.find("m:is", ns)
                if c.get("t") == "s" and v is not None:
                    cells[col - 1] = ss[int(v.text)]
                elif c.get("t") == "inlineStr" and is_ is not None:
                    cells[col - 1] = "".join(t.text or "" for t in is_.iter(f"{{{ns['m']}}}t"))
                elif v is not None:
                    cells[col - 1] = v.text or ""
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
        out[sh.get("name")] = rows
    return out


def parse_universe(blob: bytes, segments=SEGMENTS) -> list[dict]:
    """Deutsche-Boerse-'Listed companies'-XLSX -> [{isin, name, segment, symbol, country, sector, index}] (je ISIN einmal)."""
    sheets = _xlsx_sheets(blob)
    out, seen = [], set()
    for seg in segments:
        rows = sheets.get(seg) or []
        hdr = next((i for i, r in enumerate(rows) if r and r[0].strip() == "ISIN"), None)
        if hdr is None:
            continue
        head = [h.strip() for h in rows[hdr]]
        idx_cols = [i for i, h in enumerate(head) if h in ("TecDAX", "DAX", "MDAX", "SDAX")]
        main_col = head.index("Index") if "Index" in head else None       # Spalte 'Index' traegt DAX/MDAX/SDAX bzw. '-'
        for r in rows[hdr + 1:]:
            if not r or not eu_de._isin_ok((r[0] or "").strip()) or r[0].strip() in seen:
                continue
            seen.add(r[0].strip())
            g = lambda i: (r[i].strip() if len(r) > i else "")
            ix = [g(main_col)] if main_col is not None and g(main_col) not in ("", "-") else []
            ix += [head[i] for i in idx_cols if g(i) not in ("", "-") and head[i] not in ix]
            out.append({"isin": r[0].strip(), "symbol": g(1), "name": re.sub(r"\s+", " ", g(2)), "sector": g(3), "country": g(5),
                        "segment": seg, "index": ix})
    return out


def load_universe(client: Client | None = None, refresh: bool = False) -> list[dict]:
    p = cache_dir() / "universe.xlsx"
    if not p.exists() or refresh or time.time() - p.stat().st_mtime > 30 * 86400:
        c = client or Client()
        page = c.get(LISTED_PAGE).text
        m = re.search(r'href="([^"]*Listed-companies\.xlsx[^"]*)"', page)
        if not m:
            raise RuntimeError("Listed-companies.xlsx-Link auf der Deutsche-Boerse-Seite nicht gefunden")
        p.write_bytes(c.get(urljoin(LISTED_PAGE, m.group(1)), 20_000_000).content)
    return parse_universe(p.read_bytes())


# ---------------------------------------------------------------- Website je ISIN (Wikidata)
def parse_wikidata(resp: dict) -> dict[str, str]:
    best: dict[str, str] = {}
    for b in (resp.get("results") or {}).get("bindings", []):
        isin, site = b.get("isin", {}).get("value"), b.get("site", {}).get("value")
        if not isin or not site or host_blocked(site) or "web.archive.org" in site:
            continue
        if isin not in best or len(site) < len(best[isin]):
            best[isin] = site
    return best


def wikidata_sites(isins: list[str], client: Client, batch: int = 200) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(isins), batch):
        vals = " ".join(f'"{x}"' for x in isins[i:i + batch])
        q = f"PREFIX wdt: <http://www.wikidata.org/prop/direct/> SELECT ?isin ?site WHERE {{ VALUES ?isin {{ {vals} }} ?item wdt:P946 ?isin . OPTIONAL {{ ?item wdt:P856 ?site }} }}"
        if not client.allowed(WIKIDATA):
            raise Skip("Wikidata robots Disallow")
        for attempt in range(5):                                       # 429/5xx: Retry-After bzw. exponentielles Warten
            client._wait(urlsplit(WIKIDATA).netloc)
            r = client.s.post(WIKIDATA, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=90)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(min(float(r.headers.get("Retry-After", 0) or 0) or 15 * 2 ** attempt, 120)); continue
            r.raise_for_status(); break
        out.update(parse_wikidata(r.json()))
    return out


# ---------------------------------------------------------------- HTML-Helfer
def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html, "lxml")


def html_text(html: str) -> str:
    s = _soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    txt = s.get_text("\n")
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    return re.sub(r"\n\s*\n+", "\n", txt).strip()


def detect_eqs_widget(html: str) -> bool:
    """iframe/script/embed/object, dessen src auf EQS/DGAP zeigt (reine <a>-Links zaehlen nicht)."""
    s = _soup(html)
    for t in s.find_all(["iframe", "script", "embed", "object", "link"]):
        for attr in ("src", "data", "href", "data-src"):
            if EQS_RE.search(t.get(attr) or ""):
                return True
    return False


def find_date(text: str) -> str | None:
    d = None
    try:
        d = eu_de._parse_date(text)
    except ValueError:
        d = None
    if d:
        return d
    m = re.search(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text)      # 'November 4, 2025'
    if m and m.group(1)[:3].lower() in MONTHS_EN:
        try:
            return date(int(m.group(3)), MONTHS_EN[m.group(1)[:3].lower()], int(m.group(2))).isoformat()
        except ValueError:
            return None
    return None


def find_date_url(url: str) -> str | None:
    """Datum im Dateinamen/Pfad: '...-24-10-2025.pdf', '2025-10-24', '20251024'."""
    path = urlsplit(url).path
    for pat, order in ((r"(?<!\d)(\d{2})[-_.](\d{2})[-_.]((?:19|20)\d{2})(?!\d)", "dmy"), (r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)", "ymd")):
        for m in re.finditer(pat, path):
            a, b, c = (int(x) for x in m.groups())
            y, mo, d = (c, b, a) if order == "dmy" else (a, b, c)
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                continue
    return None


def find_time(text: str) -> str | None:
    m = re.search(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?::[0-5]\d)?\s*(?:Uhr|h\b|CET|CEST|MEZ|MESZ)", text) or \
        re.search(r"(?<![\d.])([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?(?![\d%])", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


def extract_items(html: str, base_url: str) -> list[dict]:
    """Datierte Linkeintraege (Liste/Tabelle/Karten): [{date, time, title, url}]. Datum im kleinsten Vorfahren (<= 500 Zeichen) des Links."""
    s = _soup(html)
    for t in s(["script", "style", "noscript"]):
        t.decompose()
    items, seen = [], set()
    for a in s.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        node, d, txt = a, None, ""
        for _ in range(5):
            txt = node.get_text(" ", strip=True)
            tm = node.find("time", attrs={"datetime": True}) if hasattr(node, "find") else None
            d = find_date(txt) or (find_date(tm["datetime"][:10]) if tm else None)
            if d or len(txt) > 500 or node.parent is None or node.parent.name in ("body", "html", "[document]"):
                break
            node = node.parent
        if not d or len(txt) > 500:
            d, txt = find_date_url(url), a.get_text(" ", strip=True)
            if not d:
                continue
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True)) or txt
        key = (url, d)
        if key in seen:
            continue
        seen.add(key)
        items.append({"date": d, "time": find_time(txt.replace(d, "")) if re.search(r"\d{1,2}[:.]\d{2}", txt) else None,
                      "title": label[:200], "context": txt[:300], "url": url})
    return items


def voting_links(html: str, base_url: str, pattern=VOTING_RE) -> list[tuple[str, str]]:
    s = _soup(html)
    out, seen = [], set()
    for a in s.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urldefrag(urljoin(base_url, href))[0]
        text = a.get_text(" ", strip=True)
        if url in seen or url.lower().endswith((".pdf", ".zip", ".xlsx", ".jpg", ".png")) or not re.match(r"https?://", url):
            continue
        if pattern.search(text) or pattern.search(urlsplit(url).path):
            seen.add(url); out.append((url, text))
    return out


def _same_site(a: str, b: str) -> bool:
    ha, hb = (urlsplit(a).hostname or "").removeprefix("www."), (urlsplit(b).hostname or "").removeprefix("www.")
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


# ---------------------------------------------------------------- IR-Seiten-Erkennung
def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """-> (page_urls, child_sitemaps)"""
    try:
        root = ET.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml)
    except ET.ParseError:
        return [], []
    locs = [e.text.strip() for e in root.iter() if e.tag.endswith("}loc") or e.tag == "loc" if e.text]
    if root.tag.endswith("sitemapindex"):
        return [], locs
    return locs, []


def _rank(urls: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Kuerzere Pfade und Texte mit 'Stimmrecht' zuerst (Index-Seite vor Einzelmeldung)."""
    return sorted(urls, key=lambda ut: (0 if re.search(r"stimmrecht|voting", ut[1], re.I) else 1, urlsplit(ut[0]).path.count("/"), len(ut[0])))


_SECOND_RE = re.compile(r"aktie|share|aktion[aä]r|publikation|publication|mitteilung|regulat|pflicht|disclosure|ad-?hoc|news|governance|corporate|bekanntmachung|capital-market|kapitalmarkt", re.I)


SKIP_PATH_RE = re.compile(r"/arch[ie]v|/karriere|/career|/jobs?(/|$)|/produkt|/product|/newsroom/|/presse(?!mitteilung)|/press-release|/blog|/datenschutz|/privacy|/impressum|/imprint|/cookie|/kontakt|/contact|/login|/shop|\.(pdf|zip|xlsx?|jpe?g|png)$", re.I)
SOCIAL = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "xing.com", "kununu.com", "glassdoor.com")


def page_is_voting(html: str, url: str) -> bool:
    """Seite listet >= 2 datierte Eintraege, deren Titel/Link/Dateiname nach Stimmrechtsmitteilung aussehen."""
    return sum(1 for it in extract_items(html, url) if VOTING_RE.search(it["title"] + " " + it["url"] + " " + it["context"])) >= 2


def _score(url: str, text: str) -> int:
    s = 0
    blob = url + " " + text
    if VOTING_RE.search(blob):
        s += 100
    if re.search(r"investor|/ir\b|anleger", blob, re.I):
        s += 3
    if re.search(r"aktie|share|aktion[aä]r", blob, re.I):
        s += 5
    if _SECOND_RE.search(blob):
        s += 4
    return s - urlsplit(url).path.count("/") // 3


def discover(website: str, client: Client, max_requests: int = 40) -> dict:
    """Best-first-Suche der Stimmrechts-Seite. -> {status, robots_ok, home, ir_urls, eqs_widget, n_requests, reason}.
    status: ok | robots_blocked | robots_unreadable | unreachable | eqs_widget | no_ir_page."""
    res = {"status": "no_ir_page", "robots_ok": True, "home": None, "ir_urls": [], "eqs_widget": False, "n_requests": 0, "reason": None}
    if not re.match(r"https?://", website):
        website = "https://" + website
    if not urlsplit(website).path:
        website += "/"
    if not client.allowed(website):
        rp = client.robots(website)
        res.update(status="robots_blocked" if rp is not None else "robots_unreadable", robots_ok=False)
        return res
    budget = [max_requests]
    pages: dict[str, str | None] = {}
    finals: dict[str, str] = {}

    def fetch(u: str) -> str | None:
        if u in pages:
            return pages[u]
        if budget[0] <= 0:
            return None
        budget[0] -= 1
        try:
            r = client.get(u)
            ct = r.headers.get("Content-Type", "").lower()
            if ct and "html" not in ct and "xml" not in ct:
                pages[u] = None
            else:
                pages[u] = r.text; finals[u] = r.url
        except (Skip, requests.RequestException) as e:
            pages[u] = None
            if u == website:
                res["reason"] = str(e)[:120]
        return pages[u]

    home_html = fetch(website)
    if home_html is None:
        res["status"] = "unreachable"; return res
    home = finals.get(website, website)
    res["home"] = home
    cand: list[tuple[str, str]] = voting_links(home_html, home)
    # Best-first ueber IR-/Aktien-/Publikations-Links (beliebige Hosts, robots wird je Abruf geprueft)
    frontier: dict[str, int] = {}
    seen = {website, home}

    def push(html: str, base: str) -> None:
        for u, t in voting_links(html, base, re.compile(IR_RE.pattern + "|" + _SECOND_RE.pattern, re.I)):
            if u not in seen and not host_blocked(u) and not any((urlsplit(u).hostname or "").endswith(x) for x in SOCIAL) and not SKIP_PATH_RE.search(urlsplit(u).path):
                frontier[u] = max(frontier.get(u, -99), _score(u, t))
    push(home_html, home)
    base = f"{urlsplit(home).scheme}://{urlsplit(home).netloc}"

    def sitemap_candidates() -> list[tuple[str, str]]:
        key = f"{urlsplit(base).scheme}://{urlsplit(base).netloc.lower()}"
        client.robots(base + "/")
        sms = [u for u in client.sitemaps.get(key, []) if _same_site(u, base) and not host_blocked(u)][:2] or [base + "/sitemap.xml"]
        urls: list[str] = []
        todo = list(sms)
        n = 0
        while todo and n < 6 and budget[0] > 8:
            sm = fetch(todo.pop(0)); n += 1
            if sm:
                pu, kids = parse_sitemap(sm)
                urls += pu
                todo += [k for k in kids if _same_site(k, base) and not host_blocked(k) and not re.search(r"product|produkt|blog|news(?!letter)|job|karriere|image|media", k, re.I)][:4]
        return [(u, "sitemap") for u in urls if VOTING_RE.search(urlsplit(u).path) and not u.lower().endswith((".pdf", ".xml"))]
    if not cand:
        cand += sitemap_candidates()
    fetched = 0
    while not cand and frontier and budget[0] > 6 and fetched < 18:
        u = max(frontier, key=frontier.get); frontier.pop(u); seen.add(u)
        h = fetch(u)
        fetched += 1
        if h:
            cand += voting_links(h, finals.get(u, u))
            if not cand and page_is_voting(h, finals.get(u, u)):
                cand.append((u, "self"))
            if not cand:
                push(h, finals.get(u, u))
    if not cand:                                                           # typische Pfade
        for pth in IR_PATHS[:5]:
            h = fetch(base + pth)
            if h:
                cand += voting_links(h, finals.get(base + pth, base + pth))
                if not cand and page_is_voting(h, finals.get(base + pth, base + pth)):
                    cand.append((base + pth, "self"))
            if cand or budget[0] <= 4:
                break
    if not cand and budget[0] > 3:                                         # IR-Subdomains
        reg = (urlsplit(home).hostname or "").removeprefix("www.")
        for sub in ("ir", "investor", "investors"):
            u = f"https://{sub}.{reg}/"
            if budget[0] > 3 and fetch(u):
                cand += voting_links(pages[u], finals.get(u, u))
                if not cand:
                    for u2, _ in sorted(voting_links(pages[u], finals.get(u, u), _SECOND_RE), key=lambda x: -_score(*x))[:2]:
                        h2 = fetch(u2)
                        if h2:
                            cand += voting_links(h2, finals.get(u2, u2))
                break
    cand = [c for c in _rank(cand) if not host_blocked(c[0])][:4]
    verified, widget = [], False
    for u, _ in cand:
        h = fetch(u)
        if h is None:
            continue
        if detect_eqs_widget(h):
            widget = True
        if extract_items(h, finals.get(u, u)) or VOTING_RE.search(urlsplit(u).path):
            if u not in verified:
                verified.append(u)
        if len(verified) >= 2:
            break
    res["n_requests"] = max_requests - budget[0]
    res["ir_urls"] = [finals.get(u, u) for u in verified]
    res["eqs_widget"] = widget
    if widget and not any(extract_items(pages[u], finals.get(u, u)) for u in verified if pages.get(u)):
        res["status"] = "eqs_widget"
    elif verified:
        res["status"] = "ok"
    return res



# ---------------------------------------------------------------- Detail + Event
_TOTAL_RE = re.compile(r"(?:Gesamtzahl der Stimmrechte|Total number of voting rights)"
                       r"(?:(?!\n\s*\n)[\s\S]){0,500}?(?<![\d.,])(\d{1,3}(?:[.\s\xa0']\d{3}){2,}|\d{6,})(?![\d,.]*\s*%)", re.I)
_STAMP_RE = re.compile(r"(\d{1,2}\.\d{1,2}\.\d{4})\s*/\s*([01]?\d|2[0-3]):([0-5]\d)(?::\d{2})?\s*(?:CET|CEST|MEZ|MESZ)", re.I)


def total_voting_rights(text: str) -> int | None:
    m = _TOTAL_RE.search(text)
    if not m:
        return None
    n = int(re.sub(r"\D", "", m.group(1)))
    return n if n >= 100_000 else None          # Plausibilitaet: Stimmrechtszahlen sind sechs- bis zehnstellig


def dissemination_stamp(text: str) -> str | None:
    """'05.06.2025 / 15:48 CET/CEST' (EQS-Verteilzeit im Meldungskopf, Ortszeit Berlin) -> ISO-Zeitstempel mit Offset."""
    m = _STAMP_RE.search(text[:3000])
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%d.%m.%Y").date()
    except ValueError:
        return None
    return datetime.combine(d, dtime(int(m.group(2)), int(m.group(3))), TZ).isoformat()


def chain_text(text: str) -> str | None:
    """Rohtext Abschnitt 3 (Meldepflichtiger) + 4 (Kette der kontrollierten Unternehmen) fuer das Alias-Matching."""
    t = text.replace("\r", "")
    parts = [eu_de._section(t, 3), eu_de._section(t, 4)]
    s = "\n".join(p.strip() for p in parts if p.strip())
    return re.sub(r"[ \t]+", " ", s)[:4000] or None


def parse_detail(text: str) -> dict:
    d = eu_de.parse_notification(text)
    d["total_voting_rights"] = total_voting_rights(text)
    d["chain_text"] = chain_text(text)
    d["stamp"] = dissemination_stamp(text)
    return d


# ---------------------------------------------------------------- Handelskalender + konservative Veroeffentlichungsregel
def easter(y: int) -> date:
    """Ostersonntag (anonymer gregorianischer Algorithmus)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, dy = divmod(h + l - 7 * m + 114, 31)
    return date(y, mo, dy + 1)


def de_holidays(y: int) -> set[date]:
    """Feste Boersenfeiertage (Frankfurt): 1.1., Karfreitag, Ostermontag, 1.5., 24./25./26./31.12."""
    e = easter(y)
    return {date(y, 1, 1), e - timedelta(days=2), e + timedelta(days=1), date(y, 5, 1), date(y, 12, 24), date(y, 12, 25), date(y, 12, 26), date(y, 12, 31)}


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in de_holidays(d.year)


def add_trading_days(d: date, n: int) -> date:
    """d + n deutsche Handelstage (n >= 0; d selbst zaehlt nicht mit)."""
    while n > 0:
        d += timedelta(days=1)
        if is_trading_day(d):
            n -= 1
    return d


NOTIFIER_DAYS, ISSUER_DAYS = 4, 3          # § 33 Abs. 1 WpHG (Melder -> Emittent/BaFin: 4 Handelstage), § 40 Abs. 1 (Emittent -> Veroeffentlichung: 3 Handelstage)


def conservative_publication(list_date: str, threshold_date: str | None) -> tuple[str, str]:
    """Ohne expliziten Verteilstempel: published-Tag = max(Listendatum + 1 Kalendertag, Schwellendatum + 7 Handelstage). -> (ISO-Datum, Regelname).
    Keine Look-ahead-Verzerrung: spaetestens an diesem Tag war die Meldung nach den Fristen oeffentlich (Obergrenze der Frist, nicht Schaetzung)."""
    ld = date.fromisoformat(list_date)
    cand = ld + timedelta(days=1)
    rule = "list_date+1d"
    if threshold_date:
        try:
            td = add_trading_days(date.fromisoformat(threshold_date), NOTIFIER_DAYS + ISSUER_DAYS)
        except ValueError:
            td = None
        if td and td > cand:
            cand, rule = td, f"threshold_date+{NOTIFIER_DAYS + ISSUER_DAYS}td"
    return cand.isoformat(), rule


def build_event(issuer: dict, item: dict, detail: dict | None) -> dict:
    d = detail or {}
    # Uhrzeit nur aus dem Meldungskopf ('TT.MM.JJJJ / HH:MM CET/CEST', EQS-Verteilzeit); Uhrzeiten in IR-Listen haben unklare Zeitzone
    # (United Internet zeigt UTC) und werden ignoriert. Stempel gilt, wenn er nicht mehr als 1 Tag VOR dem Listendatum liegt (Listendatum kann das
    # Schwellen-/Meldedatum sein; die Verteilzeit ist der spaetere, massgebliche Zeitpunkt). Ohne Stempel: conservative_publication (kein Look-ahead).
    pub, day, rule, pub_day = None, item["date"], None, None
    st = d.get("stamp")
    if st and (date.fromisoformat(st[:10]) - date.fromisoformat(day)).days >= -1:
        pub, day, rule = st, st[:10], "eqs_stamp"
    else:
        pub_day, rule = conservative_publication(day, d.get("threshold_date"))
    has_time = pub is not None
    nid = hashlib.sha1(f"{item['url']}|{item['date']}|{item['title']}".encode()).hexdigest()[:16]
    ev = make_event(
        source=SOURCE, native_id=f"{issuer['isin']}:{nid}", region=REGION, published=pub, date_only=day, has_time=has_time,
        issuer_name=issuer.get("name") or d.get("issuer_name"), issuer_id=issuer["isin"], filer_name=d.get("filer_name"), filer_id=None,
        url=item["url"], holding_ratio=d.get("holding_ratio"), previous_ratio=d.get("previous_ratio"), threshold_date=d.get("threshold_date"),
        issuer_isin=issuer["isin"], issuer_lei=d.get("lei"), chain_text=d.get("chain_text"), total_voting_rights=d.get("total_voting_rights"),
        headline=item["title"], segment=issuer.get("segment"), publication_rule=rule)
    if pub_day:                                                        # explizit: Tag 00:00 UTC (statt Standard-Folgetag des Listendatums)
        ev["published"] = datetime.fromisoformat(pub_day).replace(tzinfo=timezone.utc).isoformat()
    return ev


def _detail_text(client: Client, url: str) -> str | None:
    try:
        r = client.get(url, MAX_PDF)
    except (Skip, requests.RequestException):
        return None
    ct = r.headers.get("Content-Type", "").lower()
    if "pdf" in ct or url.lower().split("?")[0].endswith(".pdf") or r.content[:4] == b"%PDF":
        from .eu_pdf import extract_text
        try:
            return extract_text(r.content)
        except Exception:
            return None
    return html_text(r.text)


def _is_notification(it: dict, detail: dict | None) -> bool:
    """Behalten, wenn das Detail Anteil/Melder liefert; sonst nur bei eindeutigem Titel/Link (z. B. gescanntes PDF)."""
    blob = it["title"] + " " + it["context"] + " " + it["url"]
    if detail and (detail.get("holding_ratio") is not None or detail.get("filer_name")):
        return True
    return bool(STRONG_RE.search(blob)) and not NOT_NOTIF_RE.search(blob)


def extract_events(issuer: dict, ir_urls: list[str], client: Client, start: date, end: date, max_details: int = 60, render: bool = False) -> list[dict]:
    """Eintraege der IR-Seiten im Zeitraum -> Events. Detailfehler lassen das Event mit Datum/Titel (ohne Felder) bestehen."""
    items: dict[tuple, dict] = {}
    queue, visited = list(ir_urls), set()
    while queue and len(visited) < 6:
        u = queue.pop(0)
        if u in visited:
            continue
        visited.add(u)
        try:
            r = client.get(u)
        except (Skip, requests.RequestException):
            continue
        html = r.text
        if render and not extract_items(html, r.url):                  # js_only: Liste fehlt im statischen HTML -> headless rendern (nur robots-erlaubt)
            try:
                from . import js_render
                html = js_render.render_html(r.url, client)
            except (Skip, ImportError):
                continue
        for it in extract_items(html, r.url):
            if (it["url"] in visited) or not (VOTING_RE.search(it["title"] + it["context"] + it["url"]) or it["url"].lower().endswith(".pdf")):
                continue
            items.setdefault((it["url"], it["date"]), it)
        # Jahres-Reiter / Paginierung derselben Seite
        s = _soup(html)
        for a in s.find_all("a", href=True):
            tx = a.get_text(" ", strip=True)
            nu = urldefrag(urljoin(r.url, a["href"]))[0]
            if nu not in visited and nu not in queue and _same_site(nu, r.url) and not nu.lower().endswith(".pdf") and \
               (re.fullmatch(r"(19|20)\d\d", tx) and start.year <= int(tx) <= end.year or re.fullmatch(r"\d{1,2}|›|»|weiter|next|more", tx, re.I)) and \
               urlsplit(nu).path.rstrip("/").rsplit("/", 1)[0] == urlsplit(r.url).path.rstrip("/").rsplit("/", 1)[0]:
                queue.append(nu)
    out = []
    todo = [it for it in items.values() if start.isoformat() <= it["date"] <= end.isoformat()][:max_details]
    for it in todo:
        text = _detail_text(client, it["url"])
        detail = parse_detail(text) if text else None
        if not _is_notification(it, detail):
            continue                                                   # keine Stimmrechtsmitteilung (Gesamtzahl, §43, Directors' Dealings ...)
        out.append(build_event(issuer, it, detail))
    return out


# ---------------------------------------------------------------- Kuratierte IR-URLs (config/de_ir_urls.yaml)
def load_curated(path: Path | None = None) -> dict[str, dict]:
    """ISIN -> {name, voting_rights_url, verified, note, status, index, segment}. Fehlende Datei -> {}."""
    p = path or CURATED_PATH
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def save_curated(cur: dict[str, dict], path: Path | None = None) -> None:
    (path or CURATED_PATH).write_text(
        "# ISIN -> {name, voting_rights_url, verified, status, note, ...}; erzeugt/gepflegt von eu_de_ir.curate (siehe docs/EU_SOURCES.md).\n"
        "# status: ok | js_only (URL gesetzt, Liste nur per Rendering) | eqs_widget | bot_protection | none_found (voting_rights_url null).\n" +
        yaml.safe_dump(cur, allow_unicode=True, sort_keys=True, width=200), encoding="utf-8")


def sdax_coverage(issuers: list[dict] | None = None, curated: dict | None = None, records: list[dict] | None = None, index: str = "SDAX") -> list[dict]:
    """Abdeckung je Index-Mitglied (Default SDAX): [{isin, name, status, voting_rights_url, verified, note, events?}].
    status = kuratierter Status (ok/js_only/eqs_widget/bot_protection/none_found) oder 'uncurated'. Mitglieder aus issuers (load_universe),
    sonst aus den 'index'-Feldern der kuratierten Datei (offline nutzbar). records (process_issuer/run) ergaenzen 'events'."""
    cur = load_curated() if curated is None else curated
    members = ([{"isin": i["isin"], "name": i["name"]} for i in issuers if index in (i.get("index") or [])] if issuers is not None
               else [{"isin": k, "name": v.get("name")} for k, v in cur.items() if index in (v.get("index") or [])])
    ev = {r["isin"]: len(r.get("events", [])) for r in (records or [])}
    out = []
    for mbr in sorted(members, key=lambda x: x["name"] or ""):
        c = cur.get(mbr["isin"]) or {}
        row = {"isin": mbr["isin"], "name": mbr["name"], "status": c.get("status", "uncurated" if not c else ("ok" if c.get("voting_rights_url") else "none_found")),
               "voting_rights_url": c.get("voting_rights_url"), "verified": c.get("verified"), "note": c.get("note")}
        if records is not None:
            row["events"] = ev.get(mbr["isin"], 0)
        out.append(row)
    return out


VR_STRICT_RE = re.compile(r"stimmrecht(?!svertret|sausue?b|sausüb|sberechtig|sformular|svollmacht)|voting[\s_-]*rights?|major[\s_-]*holding|beteiligungsmeldung|"
                          r"(?:§|sec\.?|section|art\.?|article|abs\.?)\s*40|40[\s_-]*(?:abs|wphg|para)", re.I)
EQS_TEXT_RE = re.compile(r"\bEQS\b|DGAP|eqs-news|hugin|euro-?adhoc", re.I)


def _voting_items(html: str, url: str, blocked: bool = False) -> list[dict]:
    """Datierte Stimmrechts-Eintraege der Seite (Titel/Link/Kontext nennt Stimmrechte bzw. § 40; Ziel auf derselben Site oder PDF).
    blocked=True liefert stattdessen die, die nur auf gesperrte Hosts (EQS ...) verlinken."""
    horizon = (date.today() + timedelta(days=31)).isoformat()                      # Datumsparser-Ausreisser (z. B. 2923) verwerfen
    its = [it for it in extract_items(html, url) if it["date"] <= horizon and VR_STRICT_RE.search(it["title"] + " " + it["url"] + " " + it["context"])
           and not NOT_NOTIF_RE.search(it["title"] + " " + it["url"])]
    if blocked:
        return [it for it in its if host_blocked(it["url"])]
    return [it for it in its if not host_blocked(it["url"]) and (_same_site(it["url"], url) or it["url"].lower().split("?")[0].endswith(".pdf"))]


def curate_issuer(issuer: dict, website: str | None, client: Client, today: date | None = None, js_ok: bool = True) -> dict:
    """Findet + verifiziert die Stimmrechts-Seite eines Emittenten (Discovery, dann Pruefung: >= 1 datierter Stimmrechts-Eintrag; sonst Rendering).
    -> Kurationsdatensatz {name, segment, index, website, voting_rights_url, verified, status, note}."""
    today = today or date.today()
    rec = {"name": issuer["name"], "segment": issuer.get("segment"), "index": issuer.get("index") or [], "website": website,
           "voting_rights_url": None, "verified": today.isoformat(), "status": "none_found", "note": ""}
    if not website:
        rec["note"] = "keine Website (Wikidata)"; return rec
    try:
        d = discover(website, client, max_requests=30)
    except Exception as e:
        rec["note"] = f"Discovery-Fehler {repr(e)[:80]}"; return rec
    st = d["status"]
    if st in ("robots_blocked", "robots_unreadable"):
        rec.update(status="bot_protection", note=("robots.txt sperrt" if st == "robots_blocked" else "robots.txt unlesbar/Bot-Schutz") + f" ({website})"); return rec
    if st == "unreachable":
        rec["note"] = f"Website nicht erreichbar: {d.get('reason') or ''}"[:160]; return rec
    widget_url = None
    for u in d["ir_urls"]:
        try:
            r = client.get(u)
        except (Skip, requests.RequestException) as e:
            rec["note"] = f"{u}: {str(e)[:80]}"; continue
        its = _voting_items(r.text, r.url)
        if its:
            rec.update(status="ok", voting_rights_url=r.url, note=f"{len(its)} datierte Stimmrechtseintraege, juengster {max(i['date'] for i in its)}")
            return rec
        if detect_eqs_widget(r.text) or _voting_items(r.text, r.url, blocked=True):
            widget_url = widget_url or r.url; continue
        if js_ok and VOTING_RE.search(r.url + " " + html_text(r.text)[:6000]):          # Seite handelt von Stimmrechten, Liste fehlt -> JS?
            try:
                from . import js_render
                its = _voting_items(js_render.render_html(r.url, client), r.url) if js_render.available() else []
            except (Skip, ImportError) as e:
                rec["note"] = f"Rendering nicht moeglich: {str(e)[:80]}"; its = []
            if its:
                rec.update(status="js_only", voting_rights_url=r.url, note=f"nur per Rendering: {len(its)} Eintraege, juengster {max(i['date'] for i in its)}")
                return rec
            if EQS_TEXT_RE.search(r.text):
                widget_url = widget_url or r.url; rec["note"] = "Stimmrechtsseite verweist nur auf EQS/DGAP (nicht abgerufen)"; continue
            rec["note"] = f"{r.url}: Stimmrechtsseite ohne auslesbare datierte Eintraege (auch gerendert)"
    if widget_url or st == "eqs_widget":
        rec.update(status="eqs_widget", voting_rights_url=widget_url or (d["ir_urls"] or [None])[0], note=rec["note"] or "Liste nur als EQS-Widget/EQS-Links (nicht abgerufen)")
    elif not rec["note"]:
        rec["note"] = "keine Stimmrechts-Seite gefunden" if not d["ir_urls"] else "Kandidatenseiten ohne Stimmrechtseintraege"
    return rec


def curate(issuers: list[dict], client: Client | None = None, workers: int = 6, force: bool = False, path: Path | None = None,
           websites: dict | None = None, priority=("SDAX", "MDAX", "DAX"), log=print) -> dict:
    """Kuratiert die Liste (Reihenfolge SDAX, MDAX, DAX, Rest), speichert die YAML nach JEDEM Emittenten. Bereits kuratierte (status ok/js_only) werden uebersprungen."""
    from concurrent.futures import ThreadPoolExecutor
    client = client or Client()
    cur = load_curated(path)
    lock = threading.Lock()
    if websites is None:
        sites = _load_json("sites.json")
        websites = resolve(issuers, client, sites); _save_json("sites.json", sites)
    rank = lambda i: next((n for n, ix in enumerate(priority) if ix in (i.get("index") or [])), len(priority))
    todo = sorted((i for i in issuers if force or (cur.get(i["isin"]) or {}).get("status") not in ("ok", "js_only")), key=lambda i: (rank(i), i["name"]))

    def one(i):
        site = websites.get(i["isin"]) or (cur.get(i["isin"]) or {}).get("website")
        try:
            rec = curate_issuer(i, site, client)
        except Exception as e:
            rec = {"name": i["name"], "segment": i.get("segment"), "index": i.get("index") or [], "website": site, "voting_rights_url": None,
                   "verified": date.today().isoformat(), "status": "none_found", "note": f"Fehler {repr(e)[:80]}"}
        with lock:
            cur[i["isin"]] = rec
            save_curated(cur, path)
            log(f"{rec['status']:15s} {i['isin']} {i['name'][:30]:30s} {rec.get('voting_rights_url') or rec['note']}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, todo))
    return cur


# ---------------------------------------------------------------- Orchestrierung + Abdeckung
def process_issuer(issuer: dict, website: str | None, client: Client, start: date, end: date, sites: dict | None = None,
                   curated: dict | None = None) -> dict:
    """-> Datensatz je Emittent {isin, name, segment, website, status, robots_ok, ir_urls, eqs_widget, events}. status siehe discover + no_website."""
    rec = {"isin": issuer["isin"], "name": issuer["name"], "segment": issuer.get("segment"), "website": website, "status": "no_website",
           "robots_ok": None, "ir_urls": [], "eqs_widget": False, "events": []}
    cur = (curated or {}).get(issuer["isin"]) or {}
    render = False
    if cur.get("voting_rights_url") and cur.get("status", "ok") in ("ok", "js_only"):           # kuratierte URL zuerst, Discovery nur als Fallback
        render = cur.get("status") == "js_only"
        rec.update(status=cur.get("status", "ok"), robots_ok=True, ir_urls=[cur["voting_rights_url"]], curated=True, website=website or cur.get("voting_rights_url"))
        try:
            rec["events"] = extract_events(issuer, rec["ir_urls"], client, start, end, render=render)
        except Exception as e:
            rec["error"] = repr(e)[:120]
        if rec["events"] or cur.get("status") == "js_only":
            return rec
        # kuratierte Seite liefert nichts (z. B. URL umgezogen): Discovery als Fallback
    if cur.get("status") in ("eqs_widget", "bot_protection") and not website:
        rec.update(status=cur["status"], eqs_widget=cur["status"] == "eqs_widget", robots_ok=cur["status"] != "bot_protection")
        return rec
    if not website:
        return rec
    cached = (sites or {}).get(issuer["isin"])
    if cached and cached.get("website") == website and time.time() - cached.get("checked", 0) < 14 * 86400:
        disc = cached
    else:
        try:
            disc = discover(website, client)
        except Exception as e:                                         # eine kaputte Seite darf den Lauf nicht beenden
            disc = {"status": "unreachable", "robots_ok": True, "ir_urls": [], "eqs_widget": False, "error": repr(e)[:120]}
        disc["checked"] = time.time(); disc["website"] = website
        if sites is not None:
            sites[issuer["isin"]] = {k: disc.get(k) for k in ("website", "status", "robots_ok", "home", "ir_urls", "eqs_widget", "checked")}
    rec.update(status=disc["status"], robots_ok=disc.get("robots_ok"), ir_urls=disc.get("ir_urls", []), eqs_widget=bool(disc.get("eqs_widget")))
    if rec["ir_urls"] and rec["status"] == "ok":
        try:
            rec["events"] = extract_events(issuer, rec["ir_urls"], client, start, end)
        except Exception as e:
            rec["error"] = repr(e)[:120]
    return rec


def coverage(records: list[dict]) -> dict:
    """Abdeckungszaehler ueber process_issuer-Datensaetze."""
    evs = [e for r in records for e in r.get("events", [])]
    dates = [e["filing_date"] for e in evs]
    return {"issuers": len(records),
            "with_website": sum(1 for r in records if r.get("website")),
            "robots_ok": sum(1 for r in records if r.get("website") and r.get("robots_ok")),
            "ir_page_found": sum(1 for r in records if r.get("ir_urls")),
            "eqs_widget": sum(1 for r in records if r.get("eqs_widget")),
            "issuers_with_notifications": sum(1 for r in records if r.get("events")),
            "notifications_parsed": sum(1 for e in evs if e.get("holding_ratio") is not None),
            "notifications_total": len(evs),
            "oldest_notification_date": min(dates) if dates else None}


def resolve(issuers: list[dict], client: Client, sites: dict) -> dict[str, str | None]:
    """Website je ISIN: aus Cache sites.json, sonst Wikidata (gebatcht)."""
    web = {i["isin"]: (sites.get(i["isin"]) or {}).get("website") for i in issuers if (sites.get(i["isin"]) or {}).get("website")}
    todo = [i["isin"] for i in issuers if i["isin"] not in web and not (sites.get(i["isin"]) or {}).get("no_website_checked")]
    if todo:
        found = wikidata_sites(todo, client)
        web.update(found)
        for isin in todo:
            if isin not in found:
                sites.setdefault(isin, {})["no_website_checked"] = time.time()
    return {i["isin"]: web.get(i["isin"]) for i in issuers}


def run(start: date, end: date, issuers: list[dict] | None = None, workers: int = 4, client: Client | None = None) -> tuple[list[dict], list[dict]]:
    """-> (Events, Datensaetze je Emittent)."""
    from concurrent.futures import ThreadPoolExecutor
    client = client or Client()
    issuers = issuers if issuers is not None else load_universe(client)
    sites = _load_json("sites.json")
    curated = load_curated()
    web = resolve([i for i in issuers if not (curated.get(i["isin"]) or {}).get("voting_rights_url")], client, sites)
    web = {**{i["isin"]: (curated.get(i["isin"]) or {}).get("website") for i in issuers}, **web}
    _save_json("sites.json", sites)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        recs = list(ex.map(lambda i: process_issuer(i, web.get(i["isin"]), client, start, end, sites, curated), issuers))
    _save_json("sites.json", sites)
    evs = sorted((e for r in recs for e in r["events"] if in_range(e, start, end)), key=lambda e: e["published"])
    return evs, recs


def fetch_events(start_date: date, end_date: date, issuers: list[dict] | None = None, workers: int = 4) -> list[dict]:
    evs, recs = run(start_date, end_date, issuers, workers)
    print("Abdeckung DE_IR:", json.dumps(coverage(recs), ensure_ascii=False))
    return evs
