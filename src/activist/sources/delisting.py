"""Terminal outcome of a US issuer that vanished: merger (cash/stock/mixed), bankruptcy, going private,
plain deregistration - derived only from free SEC filings (submissions JSON + filing documents).

terminal_outcome(cik, after_date) -> dict with keys
  type, date, cash_per_share, stock_ratio, acquirer, evidence, filing (accession), form.
Network: edgar._get with >= 0.12 s between requests. Submissions are fetched directly (ALL pages of
filings.files, own in-process cache), so nothing is written to data/.
"""
from __future__ import annotations
import html, re, time
from datetime import date, datetime
from . import edgar

SLEEP = 0.12          # SEC limit 10 req/s
MAX_DOC_CHARS = 400_000
MAX_DOC_FETCHES = 12
_SUB_CACHE: dict = {}


# ------------------------------------------------------------------ network (monkeypatch points)
def _get_json(url: str) -> dict:
    r = edgar._get(url)
    time.sleep(SLEEP)
    return r.json()


def _get_text(url: str) -> str:
    r = edgar._get(url)
    time.sleep(SLEEP)
    return r.text


def _cik_int(cik) -> int:
    return int(str(cik).strip())


def _d(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


def list_filings(cik, use_cache: bool = True) -> tuple:
    """(issuer name, all filings ascending by date): 'recent' plus every older page in filings.files."""
    c10 = str(_cik_int(cik)).zfill(10)
    if use_cache and c10 in _SUB_CACHE:
        return _SUB_CACHE[c10]
    j = _get_json(f"https://data.sec.gov/submissions/CIK{c10}.json")
    blocks = [j.get("filings", {}).get("recent", {})]
    for f in j.get("filings", {}).get("files", []) or []:
        blocks.append(_get_json("https://data.sec.gov/submissions/" + f["name"]))
    rows = []
    for b in blocks:
        for i, form in enumerate(b.get("form", [])):
            rows.append({"form": form, "date": b["filingDate"][i], "accession": b["accessionNumber"][i],
                         "items": b["items"][i] if b.get("items") else "",
                         "doc": b["primaryDocument"][i] if b.get("primaryDocument") else ""})
    rows.sort(key=lambda r: (r["date"], r["accession"]))
    _SUB_CACHE[c10] = (j.get("name") or "", rows)
    return _SUB_CACHE[c10]


def _doc_url(cik, f: dict) -> str:
    return (f"https://www.sec.gov/Archives/edgar/data/{_cik_int(cik)}/"
            f"{f['accession'].replace('-', '')}/{f['doc']}")


def _html_text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = html.unescape(t).replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()


def _doc_text(cik, f: dict) -> str:
    if not f.get("doc"):
        return ""
    try:
        return _html_text(_get_text(_doc_url(cik, f)))[:MAX_DOC_CHARS]
    except Exception:
        return ""


# ------------------------------------------------------------------ form classification
def _norm(form: str) -> str:
    return re.sub(r"[^A-Z0-9/]", "", form.upper())


def _kind(f: dict):
    n = _norm(f["form"])
    items = {i.strip() for i in (f.get("items") or "").split(",")}
    if n in ("25", "25NSE"):
        return "f25"
    if n.startswith("15") and n != "15" and ("12" in n or "15D" in n or n.startswith("15F")):
        return "f15"
    if n in ("8K", "8K/A") and "1.03" in items:
        return "bk"
    if n in ("8K", "8K/A") and "2.01" in items:
        return "c201"
    if n.startswith("SCTOT") or n.startswith("SCHEDULETOT"):
        return "tot"
    if n.startswith("SC13E3") or n.startswith("SCHEDULE13E3"):
        return "e3"
    if n in ("DEFM14A", "PREM14A", "DEFM14C", "PREM14C"):
        return "proxy"
    if n in ("S4", "S4/A", "S4EF", "424B3", "424B1"):
        return "s4"
    return None


# ------------------------------------------------------------------ text parsing
_ABBR = r"\b(Inc|Corp|Ltd|Co|No|Nos|Mr|Ms|St|U\.S|L\.P|N\.V|S\.A|L\.L\.C|Bros)\."
_SUF = (r"Inc\.|Incorporated|Corporation|Corp\.|Ltd\.|Limited|LLC|L\.L\.C\.|plc|PLC|Company|Holdings|"
        r"N\.V\.|S\.A\.|L\.P\.|AG|SE")
ENT = r"[A-Z][A-Za-z0-9&.'\-]*(?: [A-Za-z0-9&.'\-]+){0,6}?,? (?:" + _SUF + r")"
_CUE = re.compile(r"(?i)right to receive|converted into|in exchange for|exchange ratio|offer price|per share|"
                  r"purchase price|merger consideration|price of")
_AMT = r"\$\s?(\d[\d,]*(?:\.\d+)?)"
_CASH_PATS = [
    re.compile(r"(?i)(?:offer price|purchase price|price|consideration|cash payment)\s+of\s+(?:\w+\s+){0,2}?" + _AMT),
    re.compile(r"(?i)(?:right to receive|converted into|entitled to receive)\D{0,70}?" + _AMT),
    re.compile(r"(?i)" + _AMT + r"\s+per\s+share[^.]{0,70}?\bcash"),
    re.compile(r"(?i)" + _AMT + r"\s+(?:\w+\s+){0,3}in\s+cash"),
    re.compile(r"(?i)for\s+" + _AMT + r"\s+per\s+share"),
]
_INCREASE = re.compile(r"(?i)(?:increas|rais|amend|revis)\w*[^.$]{0,160}?" + _AMT + r"[^.$]{0,60}?\bto\s+" + _AMT)
_RATIO = re.compile(
    r"(?<![\d,.])(\d{1,2}(?:\.\d{1,6})?|\.\d+)\s+(?:validly issued,?\s+)?(?:fully paid(?:\s+and\s+non-?assessable)?,?\s+)?"
    r"(?:(?:common\s+)?(?:shares?|stock|ADSs?|American depositary shares)\b|shares?\s+of\s+(?:[A-Z][\w.&'-]*\s+){0,4}common)")
_RATIO_CUE = re.compile(r"(?i)right to receive|exchange ratio|converted into|in exchange for|for each share|exchange each")
_MERGERISH = re.compile(r"(?i)right to receive|merger agreement|agreement and plan of merger|merger sub|tender offer|offer price")
_MON = "January|February|March|April|May|June|July|August|September|October|November|December"


def _sentences(text: str) -> list:
    t = re.sub(_ABBR, lambda m: m.group(1) + "§", text)
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z“\"(•])", t)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _bad_amount(sent: str, m) -> bool:
    pre = sent[max(0, m.start(1) - 25):m.start(1)].lower()
    return _num(m.group(1)) < 0.01 or "par value" in pre


def parse_consideration(text: str) -> dict:
    """-> {cash, ratio, cash_evidence, ratio_evidence, increased}. An explicit 'increased from $X to $Y' wins."""
    out = {"cash": None, "ratio": None, "cash_evidence": None, "ratio_evidence": None, "increased": False}
    sents = _sentences(text)
    for s in sents:
        m = _INCREASE.search(s)
        if m and _num(m.group(2)) > _num(m.group(1)) > 0:
            out.update(cash=_num(m.group(2)), cash_evidence=s[:500], increased=True)
    if out["cash"] is None:
        for s in sents:
            if not _CUE.search(s):
                continue
            for p in _CASH_PATS:
                m = p.search(s)
                if m and not _bad_amount(s, m):
                    out.update(cash=_num(m.group(1)), cash_evidence=s[:500])
                    break
            if out["cash"] is not None:
                break
    for s in sents:
        if _RATIO_CUE.search(s) and not re.search(r"(?i)preferred", s):
            for m in _RATIO.finditer(s):
                v = _num(m.group(1))
                if 0 < v < 50 and "par value" not in s[max(0, m.start() - 20):m.start()].lower():
                    out.update(ratio=v, ratio_evidence=s[:500])
                    break
        if out["ratio"] is not None:
            break
    return out


def parse_acquirer(text: str, issuer: str = ""):
    head = re.sub(r"\W+", " ", issuer).strip().lower().split(" ")[0] if issuer else None

    def ok(name: str) -> bool:
        n = name.strip().lower()
        if re.search(r"merger sub|acquisition|purchaser|\bsub\b|merger corp", n):
            return False
        return not (head and n.startswith(head))
    q = "[“\"]"
    qe = "[”\"]"
    pats = [rf"({ENT})\s*\(\s*{q}[^”\"]+{qe}\s*\)\s+(?:has\s+)?(?:completed|consummated|acquired|closed)",
            rf"({ENT})(?:,\s*an? [^()]{{0,80}})?\s*\(\s*{q}\s*(?:Parent|Acquirer|Acquiror|Buyer)\s*{qe}",
            rf"filed by\s+({ENT})",
            r"by and (?:among|between)\s+(.{0,400})",
            rf"(?:acquisition of|merger with|merged with and into)[^.]{{0,100}}?\bby\s+({ENT})"]
    for p in pats:
        for m in re.finditer(p, text):
            g = m.group(1)
            if p.startswith("by and"):
                for e in re.finditer(ENT, g):
                    if ok(e.group(0)):
                        return re.sub(r"\s+", " ", e.group(0)).strip()
            elif ok(g):
                return re.sub(r"\s+", " ", g).strip()
    return None


def completion_date_from_text(text: str, filed: str):
    """Completion date stated in the 8-K ('On April 3, 2019 ... completed a merger') if 0-45 days before filing."""
    for s in _sentences(text):
        if re.search(r"(?i)completed|consummated|effective time|closed", s):
            m = re.search(rf"\b({_MON})\s+(\d{{1,2}}),\s+(\d{{4}})", s)
            if m:
                try:
                    d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
                except ValueError:
                    continue
                if 0 <= (_d(filed) - d).days <= 45:
                    return d.isoformat()
    return None


# ------------------------------------------------------------------ main
def _res(type_, date_=None, cash=None, ratio=None, acq=None, ev="", filing=None, form=None) -> dict:
    return {"type": type_, "date": date_, "cash_per_share": cash, "stock_ratio": ratio, "acquirer": acq,
            "evidence": ev, "filing": filing, "form": form}


def terminal_outcome(cik, after_date) -> dict:
    after = _d(after_date)
    name, allf = list_filings(cik)
    fl = [f for f in allf if _d(f["date"]) > after]
    if not fl:
        return _res("unknown", ev="no filings after after_date")
    by: dict = {}
    for f in fl:
        k = _kind(f)
        if k:
            by.setdefault(k, []).append(f)
    g = lambda k: by.get(k, [])
    anchors = sorted(g("f25") + g("f15"), key=lambda f: f["date"])
    anchor = anchors[0] if anchors else None
    n_fetch = [0]
    texts: dict = {}

    def text(f):
        if f["accession"] in texts:
            return texts[f["accession"]]
        if n_fetch[0] >= MAX_DOC_FETCHES:
            return ""
        n_fetch[0] += 1
        texts[f["accession"]] = _doc_text(cik, f)
        return texts[f["accession"]]

    c201 = g("c201")
    if anchor:   # only 2.01 filings near the delisting/deregistration, closest first
        c201 = sorted([f for f in c201 if abs((_d(f["date"]) - _d(anchor["date"])).days) <= 45],
                      key=lambda f: abs((_d(f["date"]) - _d(anchor["date"])).days))
    merger_201 = None
    for f in c201[:3]:
        if _MERGERISH.search(text(f)):
            merger_201 = f
            break
    defm = sorted([f for f in g("proxy") if f["form"].upper().startswith("DEFM")], key=lambda f: f["date"], reverse=True)
    has_merger_sig = bool(merger_201 or g("tot") or defm)

    if g("bk") and not has_merger_sig:
        f = g("bk")[0]
        sent = next((s for s in _sentences(text(f)) if re.search(r"(?i)voluntary petition|chapter 11|chapter 7|receivership", s)),
                    "Item 1.03 Bankruptcy or Receivership")
        return _res("bankruptcy", f["date"], ev=sent[:500], filing=f["accession"], form=f["form"])

    if has_merger_sig and (anchor or merger_201):
        cand = ([merger_201] if merger_201 else []) \
            + sorted(g("tot"), key=lambda f: f["date"], reverse=True) + defm \
            + sorted(g("s4"), key=lambda f: f["date"], reverse=True)[:2]
        best = best_f = None
        for f in cand:
            c = parse_consideration(text(f))
            if c["cash"] is not None or c["ratio"] is not None:
                best, best_f = c, f
                break
        if merger_201:
            date_ = completion_date_from_text(text(merger_201), merger_201["date"]) or merger_201["date"]
        else:
            date_ = anchor["date"]
        if best is None:
            ref = merger_201 or anchor
            return _res("unknown", date_, ev="merger/tender signals found but consideration not parseable",
                        filing=ref["accession"], form=ref["form"])
        acq = None
        for f in [best_f] + [x for x in cand if x is not best_f][:3]:
            acq = parse_acquirer(text(f), name)
            if acq:
                break
        cash, ratio = best["cash"], best["ratio"]
        typ = "mixed" if (cash is not None and ratio is not None) else ("cash_merger" if cash is not None else "stock_merger")
        if g("e3") and typ == "cash_merger":
            typ = "going_private"
        ev = best["cash_evidence"] if cash is not None else best["ratio_evidence"]
        if typ == "mixed":
            ev = f"{best['cash_evidence']} | {best['ratio_evidence']}"
        return _res(typ, date_, cash, ratio, acq, ev, best_f["accession"], best_f["form"])

    if g("e3") and anchor:
        f = g("e3")[-1]
        return _res("going_private", anchor["date"], ev="Schedule 13E-3 filed and registration terminated",
                    filing=f["accession"], form=f["form"])
    if anchor:
        return _res("deregistered", anchor["date"], ev=f"Form {anchor['form']} filed without merger/bankruptcy signal",
                    filing=anchor["accession"], form=anchor["form"])
    last = fl[-1]
    return _res("unknown", last["date"], ev="no terminal signal found", filing=last["accession"], form=last["form"])
