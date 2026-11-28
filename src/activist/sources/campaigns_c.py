"""Signalklasse C: öffentliche Aktivisten-Kampagne OHNE Stimmrechtsmitteilung, gelesen von den EIGENEN Websites der Aktivisten
(config/activist_sites.yaml; Source 'ACTIVIST_SITE').

Pipeline je Site: Index (HTML-Liste mit Datum+Link, Feed oder feste Seiten) -> Items im Zeitfenster -> Item abrufen (HTML oder PDF via
eu_pdf.extract_text) -> Zielunternehmen im Universum (de_universe) erkennen -> Forderung klassifizieren -> Event.

Konformität: robots.txt je Host (urllib.robotparser) vor JEDEM Abruf, >= 1 s Abstand (zusätzlich Crawl-delay je Site), feste UA,
nie eqs-news.com / eqs.com / dgap.de / ots.at (skip_hosts, auch für Item-Links).

Zielabgleich (streng, mit Beleg): (1) ISIN im Text; (2) normalisierter Universumsname als ganze Wortfolge im normalisierten Text
(Einwort-Namen nur bei exakter Schreibweise im Originaltext und >= 5 Zeichen); (3) Unscharf (difflib, Schwelle FUZZY) nur für Firmen-
Kandidaten mit Rechtsform-Suffix (AG, SE, KGaA, ...). Je Item wird EIN Ziel gewählt: Titeltreffer vor Textreffer, dann Häufigkeit.
Der Aktivist selbst ist nie sein Ziel. Items ohne Ziel im Universum werden verworfen.

Forderungsklassen = filing13d.classify_demand (englisch) + deutsche Ergänzung mit denselben Kategorien (sale > capital_return > board > strategy).
Datum ohne Uhrzeit => Folgetag 00:00 UTC (common.effective_decision_time); Feeds mit pubDate-Uhrzeit nutzen diese.
Grenzen: nur deutsche/AT/CH/LU-Ziele im Universum (extra_targets: optionale Liste für Nicht-DE-Ziele, ISIN+Name).
"""
from __future__ import annotations
import difflib, hashlib, html, io, re, time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib import robotparser
from urllib.parse import urljoin, urlparse
import yaml
from ..common import CONFIG
from ..parse import filing13d
from . import de_universe, eu_common
from .de_universe import norm_name

SOURCE = "ACTIVIST_SITE"
FUZZY = 0.93
MAX_ITEMS_PER_SITE = 400
MONTHS = {m: i + 1 for i, m in enumerate(
    ["januar", "februar", "märz", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "dezember"])}
MONTHS_EN = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
LEGAL_SUFFIX = r"(?:AG|SE|KGaA|GmbH(?:\s*&\s*Co\.?\s*KGaA)?|Holding|N\.V\.|S\.A\.|plc|PLC)"
_host_last: dict[str, float] = {}
_robots: dict[str, robotparser.RobotFileParser] = {}


def load_sites(path=None) -> dict:
    with open(path or CONFIG / "activist_sites.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------ polite HTTP

def _blocked(url: str, cfg: dict) -> bool:
    h = (urlparse(url).hostname or "").lower()
    return any(h == b or h.endswith("." + b) for b in cfg.get("skip_hosts", []))


def _rp(url: str) -> robotparser.RobotFileParser:
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
    return _robots[base]


def fetch(url: str, cfg: dict, delay: float = 0.0):
    """GET nach robots.txt; None bei Verbot/skip_host/Fehler (kein Abbruch der übrigen Sites)."""
    if _blocked(url, cfg):
        return None
    rp = _rp(url)
    if not rp.can_fetch("activist-smallcap", url):
        return None
    host = urlparse(url).netloc
    d = max(delay, float(rp.crawl_delay("activist-smallcap") or 0), eu_common.MIN_INTERVAL)
    wait = d - (time.monotonic() - _host_last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _host_last[host] = time.monotonic()
    try:
        return eu_common.request("GET", url, retries=2, timeout=45)
    except Exception:
        return None


# ------------------------------------------------------------------ Text / Datum

def html_text(raw: str) -> tuple[str, str]:
    """(Titel, Fließtext) – <article>/<main> bevorzugt, sonst <body>."""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|noscript)\b.*?</\1>", " ", raw)
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    body = re.search(r"(?is)<article\b.*?</article>", raw) or re.search(r"(?is)<main\b.*?</main>", raw) or re.search(r"(?is)<body\b.*", raw)
    seg = body.group(0) if body else raw
    seg = re.sub(r"(?i)</(p|div|li|h\d|tr|br)\s*>|<br\s*/?>", "\n\n", seg)
    txt = html.unescape(re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"<[^>]+>", " ", seg)))
    return title, re.sub(r"\n\s*\n+", "\n\n", re.sub(r" *\n *", "\n", txt)).strip()


def find_date(s: str) -> str | None:
    """Erstes Datum im Text: ISO, 23.10.2026, '23. Oktober 2026', 'October 23, 2026', '23 October 2026'."""
    for m in re.finditer(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)|(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)"
                         r"|(?<!\d)(\d{1,2})\.?\s+([A-Za-zäöüÄÖÜ]{3,9})\s+(\d{4})|([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", s):
        g = m.groups()
        try:
            if g[0]:
                return date(int(g[0]), int(g[1]), int(g[2])).isoformat()
            if g[3]:
                return date(int(g[5]), int(g[4]), int(g[3])).isoformat()
            if g[6]:
                mo = MONTHS.get(g[7].lower()) or MONTHS_EN.get(g[7].lower())
                if mo:
                    return date(int(g[8]), mo, int(g[6])).isoformat()
            if g[9]:
                mo = MONTHS_EN.get(g[9].lower())
                if mo:
                    return date(int(g[11]), mo, int(g[10])).isoformat()
        except ValueError:
            continue
    return None


def page_date(raw: str) -> str | None:
    m = re.search(r'(?i)(?:article:published_time|datePublished|og:updated_time)["\']?[^>]*?(?:content=["\']|:\s*["\'])(\d{4}-\d{2}-\d{2})', raw) \
        or re.search(r'(?i)<time[^>]*datetime=["\'](\d{4}-\d{2}-\d{2})', raw)
    return m.group(1) if m else None


# ------------------------------------------------------------------ Index-Parser

def parse_index_html(raw: str, base: str, site: dict) -> list[dict]:
    """Items {url,title,date,time=None}. date_from: url (YYYY/MM/DD im Pfad), after/before (Datum im HTML nach/vor dem Link), page (später)."""
    href_re = re.compile(site["href_re"])
    ex = set(site.get("exclude_hrefs", []))
    out, seen = [], set()
    anchors = list(re.finditer(r'(?is)<a\b[^>]*\bhref=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', raw))
    for i, m in enumerate(anchors):
        href = html.unescape(m.group(1))
        path = urlparse(urljoin(base, href)).path or "/"
        if not (href_re.search(href) or href_re.search(path)) or path in ex or href in ex:
            continue
        url = urljoin(base, href)
        if (urlparse(url).hostname or "").removeprefix("www.") != (urlparse(base).hostname or "").removeprefix("www."):
            continue                                   # nur Links auf den eigenen Host
        title = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if not title or title.lower() in {"weiterlesen", "read more", "mehr"}:
            continue                                   # Bild-/Weiterlesen-Anker: Titel kommt vom Textanker desselben Items
        if url in seen:
            continue
        seen.add(url)
        src = site.get("date_from", "after")
        d = None
        if src == "url":
            um = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", url)
            d = f"{um.group(1)}-{um.group(2)}-{um.group(3)}" if um else None
        elif src == "after":
            win = raw[m.end(): m.end() + 3000]
            cal = re.search(r"(?is)calendar[^>]*></i>\s*([^<]{6,40})<", win)    # SdK: Datum steht im 'stats'-Block, Fließtext davor kann Fremddaten nennen
            d = find_date(cal.group(1)) if cal else None
            if d is None:
                d = find_date(re.sub(r"<[^>]+>", " ", win[:1500]))
        elif src == "before":
            d = find_date(re.sub(r"<[^>]+>", " ", raw[max(0, m.start() - 600): m.start()][::1])[-120:])
        out.append({"url": url, "title": title, "date": d, "time": None})
    return out


def parse_feed(raw: str) -> list[dict]:
    root = ET.fromstring(raw.lstrip())
    out = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        pd = it.findtext("pubDate")
        t = None
        d = None
        if pd:
            try:
                dt = parsedate_to_datetime(pd).astimezone(timezone.utc)
                d, t = dt.date().isoformat(), dt.isoformat()
            except Exception:
                d = find_date(pd)
        desc = html.unescape(re.sub(r"<[^>]+>", " ", it.findtext("description") or ""))
        out.append({"url": link, "title": (it.findtext("title") or "").strip(), "date": d, "time": t, "summary": desc})
    return out


def list_items(site: dict, cfg: dict, start: date, end: date) -> list[dict]:
    kind = site["kind"]
    items: list[dict] = []
    if kind == "pages":
        return [{"url": u, "title": "", "date": None, "time": None} for u in site["pages"]]
    if kind == "feed":
        r = fetch(site["url"], cfg, site.get("crawl_delay", 0))
        return parse_feed(r.text) if r is not None else []
    # index mit Blättern
    for n in range(int(site.get("max_pages", 1))):
        if site.get("page_param"):
            sep = "&" if "?" in site["url"] else "?"
            url = site["url"] if n == 0 else f"{site['url']}{sep}{site['page_param']}={n * int(site.get('page_step', 1))}"
        elif site.get("page_path"):
            url = site["url"] if n == 0 else urljoin(site["url"], site["page_path"].format(n=n + 1))
        else:
            url = site["url"]
            if n:
                break
        r = fetch(url, cfg, site.get("crawl_delay", 0))
        if r is None:
            break
        page = parse_index_html(r.text, url, site)
        new = [p for p in page if p["url"] not in {i["url"] for i in items}]
        if not new:
            break
        items += new
        dated = [p["date"] for p in page if p["date"]]
        if dated and max(dated) < start.isoformat() and min(dated) < start.isoformat():
            break                                       # Seite komplett älter als Fenster
        if len(items) >= MAX_ITEMS_PER_SITE:
            break
    return items


# ------------------------------------------------------------------ Zielabgleich

class Matcher:
    def __init__(self, universe: list[dict], extra_targets: list[dict] | None = None, stop_names: set[str] | None = None):
        self.rows = [dict(r) for r in universe] + [dict(r, segment=r.get("segment", "extra")) for r in (extra_targets or [])]
        self.by_isin = {r["isin"]: r for r in self.rows}
        self.names: dict[str, list[dict]] = {}
        for r in self.rows:
            n = norm_name(r["name"])
            if len(n) >= 4:
                self.names.setdefault(n, []).append(r)
        self.stop = {norm_name(s) for s in (stop_names or set())}
        self.fuzzy_keys = list(self.names)

    @staticmethod
    def _orig_form(row: dict) -> str:
        return re.sub(r"\s+", " ", re.sub(r"\b" + LEGAL_SUFFIX + r"\b\.?", "", row["name"]).strip(" .,-"))

    def find(self, title: str, text: str, exclude: set[str]) -> list[dict]:
        """Treffer [{row, score, where('title'|'text'), count, evidence, how}] absteigend nach Güte."""
        hits: dict[str, dict] = {}

        def add(row, score, where, ev, how):
            rn = norm_name(row["name"])
            if rn in exclude or any(difflib.SequenceMatcher(None, rn, x).ratio() >= 0.85 for x in exclude if x):
                return                                  # der Aktivist selbst (auch 'SCHERZER U. CO. AG O.N.') ist nie Ziel
            h = hits.setdefault(row["isin"], {"row": row, "score": score, "where": where, "count": 0, "evidence": ev, "how": how})
            h["count"] += 1
            if where == "title" and h["where"] != "title":
                h.update(where="title", evidence=ev)
            h["score"] = max(h["score"], score)

        for where, s in (("title", title), ("text", text)):
            for m in re.finditer(r"\b[A-Z]{2}[A-Z0-9]{9}\d\b", s):
                r = self.by_isin.get(m.group(0))
                if r:
                    add(r, 1.0, where, s[max(0, m.start() - 60): m.end() + 60], "isin")
            ns = " " + norm_name(s) + " "
            for n, rows in self.names.items():
                if n in self.stop or f" {n} " not in ns:
                    continue
                for r in rows:
                    ofm = self._orig_form(r)
                    single = " " not in n
                    if single and len(n) < 5:
                        continue
                    if not single and len(n) < 6:
                        continue
                    first = re.escape(ofm.split()[0]) if ofm else re.escape(n.split()[0])
                    ms = [x for x in re.finditer(r"(?<![\w-])" + first + r"(?![\w-])", s, re.I) if x.group(0)[:1].isupper()]
                    if single and not ms:                               # Einwortnamen: nur großgeschrieben im Originaltext
                        continue
                    om = ms[0] if ms else None
                    pos = om.start() if om else 0
                    ev = re.sub(r"\s+", " ", s[max(0, pos - 60): pos + 140])
                    for _ in range(max(1, ns.count(f" {n} "))):
                        add(r, 1.0, where, ev, "exact_name")
            for m in re.finditer(r"((?:[A-ZÄÖÜ0-9][\w&.\-]*\s+){0,4}[A-ZÄÖÜ0-9][\w&.\-]*)\s+" + LEGAL_SUFFIX + r"\b", s):
                cand = norm_name(m.group(1))
                if len(cand) < 6 or cand in self.names or cand in self.stop:
                    continue
                best = difflib.get_close_matches(cand, self.fuzzy_keys, n=1, cutoff=FUZZY)
                if best:
                    sc = difflib.SequenceMatcher(None, cand, best[0]).ratio()
                    for r in self.names[best[0]]:
                        add(r, sc, where, re.sub(r"\s+", " ", s[max(0, m.start() - 40): m.end() + 80]), "fuzzy_name")
        pref = lambda h: bool(re.search(r"(?i)\b(?:vzo|vz|vorz\w*|pref\w*)\b", h["row"]["name"]))   # Stammaktie vor Vorzugsaktie
        return sorted(hits.values(), key=lambda h: (h["where"] != "title", -h["count"], -h["score"], pref(h)))


_DE = {
    "sale": re.compile(r"(?i)verkauf\s+(?:des\s+)?(?:unternehmens|der\s+gesellschaft)|übernahme(?:angebot)?|\bfusion\b|zusammenschluss|strategische\s+alternativen|squeeze[\s-]?out|gesamtverkauf|aufspaltung\s+und\s+verkauf"),
    "capital_return": re.compile(r"(?i)aktienrückkauf|rückkaufprogramm|\bdividende|sonderdividende|kapitalrückzahlung|ausschüttung|kapitalherabsetzung"),
    "board": re.compile(r"(?i)aufsichtsrat|abwahl|abberufung|gegenantrag|gegenanträge|neuwahl|außerordentliche[nr]?\s+hauptversammlung|einberufung\s+(?:einer\s+)?(?:außerordentlichen\s+)?hauptversammlung|sonderprüfung|nominier\w+|(?:vorstand|aufsichtsrat)\w*\s+(?:auszutauschen|ersetzen)|rücktritt\s+(?:des|der)"),
    "strategy": re.compile(r"(?i)\bstrategie|restrukturierung|abspaltung|\bspin[\s-]?off|kostensenkung|margenverbesserung|portfolio\w*\s+(?:bereinigung|überprüfung)|operative\w*\s+(?:verbesserung|performance)|governance"),
}


def classify(text: str) -> tuple[str, str | None]:
    """Gleiche Kategorien/Priorität wie filing13d.classify_demand; englisch zuerst, dann deutsche Ergänzung."""
    cls, ev = filing13d.classify_demand(text)
    if cls != "none":
        return cls, ev
    sents = filing13d._sentences(text)
    for c, pattern in _DE.items():
        for s in sents:
            if pattern.search(s):
                return c, s[:filing13d.EVIDENCE_MAX]
    return "none", None


# ------------------------------------------------------------------ Item -> Event

def item_text(url: str, cfg: dict, delay: float = 0.0) -> tuple[str, str, str | None] | None:
    """(Titel, Text, Seitendatum) – PDF per eu_pdf.extract_text, sonst HTML."""
    r = fetch(url, cfg, delay)
    if r is None:
        return None
    if url.lower().split("?")[0].endswith(".pdf") or "pdf" in r.headers.get("Content-Type", "").lower():
        from . import eu_pdf
        try:
            t = eu_pdf.extract_text(r.content)
        except Exception:
            return None
        return "", t, None
    ti, tx = html_text(r.text)
    return ti, tx, page_date(r.text)


def build_event(site: dict, item: dict, title: str, text: str, matcher: Matcher) -> dict | None:
    act = site["activist"]
    if site.get("exclude_title_re") and re.search(site["exclude_title_re"], item.get("title") or title or ""):
        return None                                  # periodische Berichte/Portfolio-Listen sind keine Kampagnen
    exclude = {norm_name(act), norm_name(site["name"]), norm_name(site["name"].split("(")[0])}
    hits = matcher.find(title, text, exclude)
    if not hits:
        return None
    h = hits[0]
    if h["where"] != "title" and h["count"] < 2 and h["how"] != "isin":
        return None                                  # Einzelnennung im Fließtext = zu schwach
    key = (matcher._orig_form(h["row"]).split() or [""])[0]
    paras = [p for p in re.split(r"\n{2,}", f"{title}.\n\n{text}") if key and re.search(re.escape(key), p, re.I)]
    demand, ev = classify("\n\n".join(paras)) if paras else ("none", None)       # Forderung im Absatz des Ziels ...
    if demand == "none" and h["where"] == "title":                                 # ... oder (Ziel im Titel) irgendwo im Text
        demand, ev = classify(f"{title}.\n{text}")
    if site.get("require_demand", True) and demand == "none":
        return None
    row = h["row"]
    iso = item["date"]
    return eu_common.make_event(
        source=SOURCE, native_id=hashlib.sha1(item["url"].encode()).hexdigest()[:16],
        region="DACH" if row["isin"][:2] in ("DE", "AT", "CH", "LU") else "EU",
        published=item.get("time"), date_only=iso, has_time=bool(item.get("time")),
        issuer_name=row["name"], issuer_id=row["isin"], filer_name=act, filer_id=None, url=item["url"],
        form="PublicCampaign", signal_class="C", demand=demand, demand_evidence=ev, issuer_isin=row["isin"],
        activist_type=site.get("type"), item_title=item["title"] or title, match_how=h["how"], match_score=round(h["score"], 3),
        match_where=h["where"], match_evidence=h["evidence"], issuer_segment=row.get("segment"), yahoo_ticker=row.get("yahoo_ticker"))


def fetch_events(start: date, end: date, sites_cfg: dict | None = None, universe: list[dict] | None = None,
                 extra_targets: list[dict] | None = None, stats: dict | None = None) -> list[dict]:
    cfg = sites_cfg or load_sites()
    uni = universe if universe is not None else de_universe.load_universe()
    stop = set(cfg.get("stop_names", []))
    matcher = Matcher(uni, extra_targets, stop)
    stats = stats if stats is not None else {}
    events = []
    for site in cfg["sites"]:
        k = site["activist"]
        st = stats.setdefault(k, {"items": 0, "in_window": 0, "fetched": 0, "events": 0})
        for it in list_items(site, cfg, start, end):
            st["items"] += 1
            if it["date"] and not (start.isoformat() <= it["date"] <= end.isoformat()):
                continue
            if _blocked(it["url"], cfg):
                continue
            got = item_text(it["url"], cfg, site.get("crawl_delay", 0))
            if got is None:
                continue
            title, text, pdate = got
            if not it["date"]:
                it = dict(it, date=pdate or find_date(text[:600]))
            if not it["date"] or not (start.isoformat() <= it["date"] <= end.isoformat()):
                continue
            st["in_window"] += 1; st["fetched"] += 1
            ev = build_event(site, it, it["title"] or title, text, matcher)
            if ev:
                events.append(ev); st["events"] += 1
    return events
