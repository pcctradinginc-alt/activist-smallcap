"""Point-in-Time-Ticker fuer US-Emittenten (auch delistete; submissions.tickers ist dort leer).

Vorgehen: eigene Periodenberichte/8-K/S-1/424B nahe am Stichtag aus submissions (inkl. filings.files-Seiten)
waehlen, Primaerdokument laden und den Ticker der Stammaktie aus dem Text lesen:
  (a) iXBRL dei:TradingSymbol, (b) Titelseiten-Tabelle "Trading Symbol(s)", (c) Textmuster
  "traded on ... under the symbol XYZ" / "(Nasdaq: XYZ)".
Cache: DATA/cache/hist_ticker/{cik10}.json, Schluessel = Stichtagsmonat.
"""
from __future__ import annotations
import html, json, re, time
from datetime import date
from .. import common
from . import edgar, secmeta

HEAD_BYTES = 300_000          # erstes Fenster (Titelseite)
MAX_BYTES = 1_000_000         # zweites Fenster (Item 5 / grosse iXBRL-Header liegen oft dahinter)
BEFORE_DAYS, AFTER_DAYS = 400, 200
_ANNUAL = {"10-K", "10-K405", "10-KSB", "10-KT"}
_TIERS = [  # (Formen, max. Dokumente)
    (lambda f: f in _ANNUAL, 2),
    (lambda f: f.startswith("S-1") or f.startswith("424B") or f in ("S-3", "10-12B"), 1),
    (lambda f: f in ("10-Q", "10-QSB"), 1),
    (lambda f: f == "8-K", 1),
]
_BAD = {"THE", "OF", "SYMBOL", "NONE", "N/A", "NA", "COMMON", "STOCK", "NYSE", "NASDAQ", "AMEX", "SHARES",
        "CLASS", "INC", "LTD", "CORP", "AND", "OR", "TO", "IN", "ON", "FOR", "NO", "IS", "US", "A", "I"}
_TICK = re.compile(r"^[A-Z][A-Z0-9]{0,5}(?:[.\-][A-Z]{1,2})?$")
_EXCH = [  # (Muster, normiert); Reihenfolge wichtig
    (r"nyse\s+american|nyse\s+mkt|american\s+stock\s+exchange|nyse\s+amex|\bamex\b", "NYSE American"),
    (r"new\s+york\s+stock\s+exchange|\bnyse\b", "NYSE"),
    (r"nasdaq|national\s+market\s+system", "NASDAQ"),
    (r"otc\s*(?:qb|qx|bb|markets)?|over-the-counter|pink", "OTC"),
]
_EXCH_ALT = r"(?:NASDAQ[\w ]*?(?:Market|Stock Exchange|Global Select|Capital)[\w ]*?|Nasdaq[\w ]*|New York Stock Exchange|NYSE American|NYSE MKT|NYSE Amex|NYSE|American Stock Exchange|AMEX|OTCQB|OTCQX|OTC Bulletin Board)"


def _exch_norm(s: str | None) -> str | None:
    if not s:
        return None
    for pat, name in _EXCH:
        if re.search(pat, s, re.I):
            return name
    return None


def _strip(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = s.translate({0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'", 0x2013: "-", 0x2014: "-"})
    return re.sub(r"\s+", " ", s).strip()


def _clean_tick(t: str) -> str | None:
    t = _strip(t).strip(" .,;:\"'()[]").upper()
    t = re.sub(r"^(?:NYSE|NASDAQ|AMEX)[:\s]+", "", t)
    return t if _TICK.match(t) and t not in _BAD else None


def _is_unit_warrant(t: str, others: list[str]) -> bool:
    m = re.match(r"^(.+?)[.\-/ ]?(WS|WT|WTS|W|WSA|WSB|U|UN|RT|R)$", t)
    if not m:
        return False
    base = m.group(1)
    return any(o != t and (o == base or o.startswith(base)) and not _is_unit_warrant(o, []) for o in others) or \
        bool(re.search(r"[.\-](WS|WT|W|U|UN|RT|R|WSA|WSB)$", t))


def _pick(cands: list[tuple[str, str | None]]) -> tuple[str, str | None] | None:
    """Stammaktie bevorzugen: kein Units/Warrants-Muster, sonst erster Treffer."""
    seen, uniq = set(), []
    for t, ctx in cands:
        if t and t not in seen:
            seen.add(t); uniq.append((t, ctx))
    plain = [(t, c) for t, c in uniq if not _is_unit_warrant(t, [x for x, _ in uniq])]
    # Kontext 'warrant/unit/note/preferred' aussortieren
    ok = [(t, c) for t, c in plain if not (c and re.search(r"\bwarrants?\b|\bunits?\b|\bnotes?\b|\bdebentures?\b|\brights?\b", c, re.I))]
    for lst in (ok, plain):
        if lst:
            return lst[0]
    return None


# ---------------------------------------------------------------- Extraktion
def _ixbrl(raw: str) -> dict | None:
    syms = [_strip(m) for m in re.findall(
        r'<ix:nonNumeric[^>]*name="dei:TradingSymbol"[^>]*>(.*?)</ix:nonNumeric>', raw, re.S | re.I)]
    ex = [_strip(m) for m in re.findall(
        r'<ix:nonNumeric[^>]*name="dei:SecurityExchangeName"[^>]*>(.*?)</ix:nonNumeric>', raw, re.S | re.I)]
    titles = [_strip(m) for m in re.findall(
        r'<ix:nonNumeric[^>]*name="dei:Security12bTitle"[^>]*>(.*?)</ix:nonNumeric>', raw, re.S | re.I)]
    cands = []
    for i, s in enumerate(syms):
        t = _clean_tick(s)
        if t:
            cands.append((t, titles[i] if i < len(titles) and len(titles) == len(syms) else None))
    p = _pick(cands)
    if not p:
        return None
    idx = [t for t, _ in cands].index(p[0])
    exch = _exch_norm(ex[idx] if idx < len(ex) else (ex[0] if ex else None))
    return {"ticker": p[0], "exchange": exch, "source": "ixbrl_dei",
            "evidence": f"dei:TradingSymbol={p[0]}" + (f"; exchange={ex[idx] if idx < len(ex) else ex[0]}" if ex else "")}


def _cover_table(raw: str) -> dict | None:
    t = re.sub(r"(?i)</t[dh]\s*>", " | ", raw)
    t = re.sub(r"(?i)</tr\s*>|<br\s*/?>|</p\s*>|</div\s*>", "\n", t)
    lines = [l for l in (_strip(x) for x in t.split("\n")) if l]
    hdr = next((i for i, l in enumerate(lines[:400]) if re.search(r"trading\s+symbols?", l, re.I)), None)
    if hdr is None:
        return None
    cands, exs = [], []
    for l in lines[hdr: hdr + 14]:
        cells = [c.strip() for c in l.split("|") if c.strip()]
        if len(cells) < 2 and hdr != lines.index(l):
            continue
        e = next((c for c in cells if _exch_norm(c) and len(c) < 60), None)
        tk = next((x for x in (_clean_tick(c) for c in cells[1:]) if x), None)
        if tk and re.search(r"common|ordinary|class\s+[a-z]\b|shares|capital stock", cells[0], re.I) or tk and not cands:
            cands.append((tk, cells[0])); exs.append(e)
    p = _pick(cands)
    if not p:
        return None
    exch = exs[[t for t, _ in cands].index(p[0])]
    return {"ticker": p[0], "exchange": _exch_norm(exch), "source": "cover_table",
            "evidence": f"cover row: {p[1][:60]} | {p[0]} | {exch}"}


_P_SYMBOL = re.compile(
    r"(?P<pre>[^.]{0,200}?)(?:traded|trades|trading|listed|quoted|is quoted)\s+(?:\w+\s+){0,3}?(?:on|in|through)\s+(?:the\s+)?"
    r"(?P<ex>" + _EXCH_ALT + r")[^.]{0,80}?\s+(?:under|using)\s+(?:the\s+)?(?:(?:trading|ticker|stock)\s+)?"
    r"(?:ticker\s+)?symbols?\s*(?:of\s+)?[\"'(]*\s*(?P<t>[A-Z][A-Z0-9.\-]{0,6})\b", re.I)
_P_COLON = re.compile(r"\(\s*(?P<ex>NASDAQ|Nasdaq|NYSE|NYSE American|NYSE MKT|AMEX|NYSE Amex)\s*(?:GS|GM|CM)?\s*:\s*(?P<t>[A-Z][A-Z0-9.\-]{0,5})\s*\)")


def _text_patterns(raw: str) -> dict | None:
    txt = _strip(raw)
    cands, info = [], {}
    for m in _P_SYMBOL.finditer(txt):
        tk = m.group("t")
        # Ticker muss in der Quelle gross geschrieben sein (case-insensitive Regex sonst 'the'/'our')
        if tk != tk.upper() or not (t := _clean_tick(tk)):
            continue
        head = txt[max(0, m.start("pre") + len(m.group("pre")) - 70): m.start("ex")]
        if re.search(r"\bwarrants?\b|\bunits?\b|\bnotes?\b|\bdebentures?\b|\bpreferred\b|\brights?\b", head, re.I) and \
                not re.search(r"common|ordinary|shares|class", head, re.I):
            continue
        cands.append((t, head)); info.setdefault(t, (m.group("ex"), txt[m.start(): m.end()][-160:]))
    if not cands:
        for m in _P_COLON.finditer(txt):
            t = _clean_tick(m.group("t"))
            if t:
                cands.append((t, txt[max(0, m.start() - 60): m.start()])); info.setdefault(t, (m.group("ex"), m.group(0)))
    p = _pick(cands)
    if not p:
        return None
    ex, ev = info[p[0]]
    return {"ticker": p[0], "exchange": _exch_norm(ex), "source": "text_pattern", "evidence": ev[-200:]}


def extract_ticker(raw: str) -> dict | None:
    """Prioritaet: iXBRL -> Titelseitentabelle -> Textmuster. Liefert {ticker, exchange, source, evidence} oder None."""
    for f in (_ixbrl, _cover_table, _text_patterns):
        r = f(raw)
        if r:
            return r
    return None


# ---------------------------------------------------------------- Filings
def _all_filings(cik, lo: date, hi: date) -> list[dict]:
    """Alle Filings (recent + filings.files-Seiten, soweit sie [lo, hi] ueberlappen)."""
    c = secmeta._cik10(cik)
    s = secmeta._submissions(cik)
    rows = []

    def add(blk):
        for i in range(len(blk.get("form", []))):
            rows.append({"form": blk["form"][i], "date": blk["filingDate"][i],
                         "accession": blk["accessionNumber"][i], "doc": blk["primaryDocument"][i]})
    add(s.get("filings", {}).get("recent", {}))
    for f in s.get("filings", {}).get("files", []) or []:
        a, b = f.get("filingFrom"), f.get("filingTo")
        if (a and date.fromisoformat(a[:10]) > hi) or (b and date.fromisoformat(b[:10]) < lo):
            continue
        name = f["name"]
        add(secmeta._cached("subpage_" + re.sub(r"\W", "", name.split(".")[0]), f"https://data.sec.gov/submissions/{name}", c))
    return rows


def _candidates(rows: list[dict], asof: date) -> list[dict]:
    out = []
    for check, n in _TIERS:
        prior, after = [], []
        for r in rows:
            if not r["doc"] or not check(r["form"]):
                continue
            d = date.fromisoformat(r["date"]); delta = (asof - d).days
            if 0 <= delta <= BEFORE_DAYS:
                prior.append((delta, r))
            elif -AFTER_DAYS <= delta < 0:
                after.append((-delta, r))
        prior.sort(key=lambda x: x[0]); after.sort(key=lambda x: x[0])
        out += [r for _, r in (prior + after)[:n]]
    return out


def _doc_url(cik, r: dict) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{r['accession'].replace('-', '')}/{r['doc']}"


def _fetch_doc(url: str) -> str:
    """Dokumenttext; nur die ersten MAX_BYTES werden verwendet (SEC-Limit: >=0.12 s Abstand)."""
    r = edgar._get(url)
    time.sleep(0.12)
    return r.content[:MAX_BYTES].decode("utf-8", errors="replace")


def _cache_file(c10: str):
    p = common.DATA / "cache" / "hist_ticker"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{c10}.json"


def _empty(source="none", evidence="") -> dict:
    return {"ticker": None, "exchange": None, "source": source, "evidence": evidence, "filing": None}


def ticker_hist(cik, asof_date) -> dict:
    """Ticker (Stammaktie) zum Stichtag: {"ticker","exchange","source","evidence","filing"}; ticker=None wenn nicht ermittelbar."""
    asof = secmeta._d(asof_date)
    c10 = secmeta._cik10(cik)
    key = f"{asof:%Y-%m}"
    path = _cache_file(c10)
    cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if key in cache:
        return cache[key]
    rows = _all_filings(cik, asof.replace(year=asof.year - 2) if asof.year > 1995 else asof, asof.replace(year=asof.year + 1))
    res, errors = None, 0
    for r in _candidates(rows, asof):
        try:
            raw = _fetch_doc(_doc_url(cik, r))
        except Exception:
            errors += 1; continue
        found = extract_ticker(raw[:HEAD_BYTES]) or extract_ticker(raw)
        if found:
            res = {**found, "filing": r["accession"], "form": r["form"], "filing_date": r["date"]}
            break
    if res is None:
        res = _empty(evidence=f"no ticker found in {len(_candidates(rows, asof))} candidate filings")
    if res["ticker"] or not errors:     # Netzfehler nicht cachen
        cache[key] = res
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8"); tmp.replace(path)
    return res
