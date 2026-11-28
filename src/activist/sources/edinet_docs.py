"""EDINET API v2 Dokumentabruf (type=5 CSV) + Parser fuer Grossbeteiligungsmeldungen (jplvh_cor).

Die Element-IDs sind aus der oeffentlichen Taxonomie-Dokumentation abgeleitet und per Teilstring
plus japanischem Label (項目名) als Fallback gematcht; Live-Validierung: scripts/jp_probe.py.
"""
from __future__ import annotations
import csv
import io
import json
import re
import time
import unicodedata
import zipfile
from pathlib import Path
import requests
from ..common import DATA

DOC_API = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"
CODE_LIST_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"

C_ELEM, C_LABEL, C_CTX, C_VAL = "要素ID", "項目名", "コンテキストID", "値"


def fetch_csv(doc_id: str, key: str, timeout: int = 60) -> bytes:
    r = requests.get(DOC_API.format(doc_id=doc_id), params={"type": 5, "Subscription-Key": key}, timeout=timeout)
    ctype = (r.headers.get("content-type") or "").lower()
    if "json" in ctype:
        try:
            meta = r.json()
        except ValueError:
            meta = r.text[:200]
        raise RuntimeError(f"EDINET {doc_id}: JSON-Fehlerantwort (HTTP {r.status_code}): {_scrub(str(meta), key)}")
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise RuntimeError(f"EDINET {doc_id}: keine ZIP-Antwort (content-type={ctype!r})")
    return r.content


def _scrub(s: str, key: str) -> str:
    return s.replace(key, "***") if key else s


# ---------------------------------------------------------------- Normalisierung
def _nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s if s is not None else "")).strip()


def normalize_sec_code(v) -> str | None:
    """'72030' -> '7203'; '130A0' -> '130A'; '7203' -> '7203'; sonst None."""
    s = _nfkc(v).upper().replace(" ", "")
    if not s or s in ("-", "－"):
        return None
    if len(s) == 5 and s.endswith("0"):
        s = s[:4]
    if len(s) == 4 and re.fullmatch(r"[0-9]{3}[0-9A-Z]|[0-9]{4}", s):
        return s
    return None


def _num(v) -> float | None:
    s = _nfkc(v).replace(",", "").replace("%", "").replace("％", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _ratio(v) -> float | None:
    """Anteil als Bruch. Werte > 1 oder mit %-Zeichen gelten als Prozent (Bruchform 'x<=1' bleibt Bruch)."""
    x = _num(v)
    if x is None:
        return None
    pct = "%" in str(v) or "％" in str(v)
    return x / 100.0 if (x > 1.0 or pct) else x


# ---------------------------------------------------------------- Zeilen lesen
def _decode(b: bytes) -> str:
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16")
    for enc in ("utf-16-le", "utf-8-sig", "cp932"):
        try:
            t = b.decode(enc)
        except UnicodeDecodeError:
            continue
        if C_ELEM in t[:400]:
            return t
    return b.decode("utf-16-le", errors="replace")


def _rows_from_text(text: str) -> list[dict]:
    rd = csv.DictReader(io.StringIO(text), delimiter="\t", quotechar='"')
    return [{(k or "").strip(): (v or "") for k, v in r.items()} for r in rd]


def read_zip_rows(zip_bytes: bytes) -> list[dict]:
    rows: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for n in sorted(z.namelist()):
            if n.lower().endswith(".csv"):
                rows += _rows_from_text(_decode(z.read(n)))
    return rows


# ---------------------------------------------------------------- Extraktion
def _match(row, elems=(), labels=(), not_elems=(), not_labels=()) -> bool:
    e, l = row.get(C_ELEM, ""), _nfkc(row.get(C_LABEL, ""))
    el = e.lower()
    if any(x.lower() in el for x in not_elems) or any(x in l for x in not_labels):
        return False
    return any(x.lower() in el for x in elems) or any(x in l for x in labels)


def _pick(rows, **kw):
    return [r for r in rows if _match(r, **kw) and _nfkc(r.get(C_VAL)) not in ("", "－", "-")]


_PREV = ("Last", "Previous", "Prior", "Preceding")
_NEG = re.compile(r"(目的としていない|目的としない|行わない|行いません|行わず|予定はない|予定はありません|予定がない|"
                  r"考えていない|考えておりません|ありません|いたしません|しない|していない|ことはない|目的ではない|目的としておりません)")


def important_proposal(text: str) -> tuple[bool, str | None]:
    """True, wenn ein Satz 重要提案行為 enthaelt und nicht verneint ist. Evidenz = der (erste positive, sonst verneinte) Satz."""
    t = _nfkc(text)
    sents = [s.strip() for s in re.split(r"[。\n]", t) if s.strip()]
    hits = [s for s in sents if "重要提案行為" in s]
    if not hits:
        return False, None
    for s in hits:
        if not _NEG.search(s):
            return True, s
    return False, hits[0]


def proposal_category(text: str | None) -> str:
    """Separate substantiated issuer action from a legal reservation and control deals."""
    purpose = _nfkc(text)
    if not purpose:
        return "unknown"
    if re.search(r"資本業務提携契約|業務提携契約に基づ", purpose):
        return "strategic_alliance"
    clauses = re.split(r"[。\n]", purpose)
    for clause in clauses:
        if _NEG.search(clause):
            continue
        if re.search(r"状況に応じ|場合があ|可能性があ|ことがある|あり[得う]る|こともある", clause):
            continue
        if re.search(r"増配|特別配当|自己株式(?:の)?取得|取締役(?:候補|退任|.{0,10}選任|.{0,10}解[任職]|.{0,8}指名)|"
                     r"事業.{0,14}譲渡|資産(?:の)?売却|有利子負債(?:の)?活用|株主資本(?:の)?抑制", clause):
            if re.search(r"提案|求め|要求|要請|すべき|発行者に対し", clause):
                return "specific_intervention"
    if re.search(r"公開買付|完全子会社|非公開化|株式併合", purpose):
        return "control_transaction"
    if "重要提案行為" in purpose:
        return "generic_or_negated_proposal"
    return "no_specific_intervention"


def parse_rows(rows: list[dict]) -> dict:
    out: dict = {"issuer_sec_code": None, "issuer_name": None, "purpose_text": None, "important_proposal": False,
                 "important_proposal_evidence": None, "holding_ratio": None, "previous_holding_ratio": None,
                 "total_shares_outstanding": None, "filer_name": None, "filer_names": [], "n_rows": len(rows)}
    # Only explicitly issuer-labelled fields establish the security identity.
    codes = {normalize_sec_code(r[C_VAL]) for r in _pick(
        rows, elems=("SecurityCodeOfIssuer", "IssuerSecurityCode"), labels=("発行者の証券コード",),
        not_elems=("Filer", "Holder"), not_labels=("提出者", "共同保有者"))}
    codes.discard(None)
    names = {_nfkc(r[C_VAL]) for r in _pick(
        rows, elems=("NameOfIssuer", "IssuerName", "NameOfIssuerOfStock"), labels=("発行者の名称", "発行者名"),
        not_elems=("Filer", "Holder"), not_labels=("提出者", "共同保有者"))}
    out["issuer_sec_code"] = next(iter(codes)) if len(codes) == 1 else None
    out["issuer_name"] = next(iter(names)) if len(names) == 1 else None
    out["issuer_role_conflict"] = len(codes) > 1 or len(names) > 1
    out["issuer_role_explicit"] = len(codes) == 1 and len(names) == 1
    purp = _pick(rows, elems=("PurposeOfHolding",), labels=("保有目的",))
    if purp:
        out["purpose_text"] = max((r[C_VAL].strip() for r in purp), key=len)
        out["important_proposal"], out["important_proposal_evidence"] = important_proposal(out["purpose_text"])
    # Quote
    prev = _pick(rows, elems=("RatioOfShareCertificatesEtc",), labels=("株券等保有割合",)) 
    prev = [r for r in prev if any(p.lower() in r[C_ELEM].lower() for p in _PREV) or re.search("直前|前回", r.get(C_LABEL, ""))]
    cur = _pick(rows, elems=("HoldingRatioOfShareCertificatesEtc",), labels=("株券等保有割合",),
                not_elems=_PREV + ("Increase", "Decrease"), not_labels=("直前", "前回", "増減"))
    out["holding_ratio"] = _choose_ratio(cur)
    out["previous_holding_ratio"] = _choose_ratio(prev)
    tot = _pick(rows, elems=("TotalNumberOfStocksEtcIssued", "TotalNumberOfIssuedShares"), labels=("発行済株式総数", "発行済株式等総数"))
    for r in tot:
        n = _num(r[C_VAL])
        if n is not None:
            out["total_shares_outstanding"] = n
            break
    names = []
    for r in _pick(rows, elems=("FilerNameInJapanese", "NameOfFiler", "FilerName"), labels=("提出者氏名又は名称", "提出者 氏名又は名称", "氏名又は名称", "提出者名")):
        nm = _nfkc(r[C_VAL])
        if nm and nm not in names:
            names.append(nm)
    out["filer_names"] = names
    out["filer_name"] = names[0] if names else None
    return out


def _choose_ratio(cands: list[dict]) -> float | None:
    vals = [(r, _ratio(r[C_VAL])) for r in cands]
    vals = [(r, v) for r, v in vals if v is not None]
    if not vals:
        return None
    tag = re.compile(r"Total|Joint|Aggregate|Combined|Sum|合計|共同|合算")
    totals = [v for r, v in vals if tag.search(r.get(C_CTX, "") + r.get(C_ELEM, "") + r.get(C_LABEL, ""))]
    return max(totals) if totals else max(v for _, v in vals)


def parse_large_holding_zip(zip_bytes: bytes) -> dict:
    return parse_rows(read_zip_rows(zip_bytes))


# ---------------------------------------------------------------- EDINET-Code-Liste
def edinet_code_map(path_or_bytes) -> dict[str, dict]:
    """EdinetcodeDlInfo.csv (cp932; Zeile 1 Metadaten, Zeile 2 Header) -> {edinet_code: {name, sec_code}}."""
    b = path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else Path(path_or_bytes).read_bytes()
    if b[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            b = z.read(next(n for n in z.namelist() if n.lower().endswith(".csv")))
    text = b.decode("cp932", errors="replace")
    lines = text.splitlines()
    rd = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    out = {}
    for r in rd:
        code = _nfkc(r.get("ＥＤＩＮＥＴコード") or r.get("EDINETコード"))
        if not code:
            continue
        out[code] = {"name": (r.get("提出者名") or "").strip(), "sec_code": normalize_sec_code(r.get("証券コード"))}
    return out


def download_code_map(timeout: int = 120) -> dict[str, dict]:
    r = requests.get(CODE_LIST_URL, timeout=timeout)
    r.raise_for_status()
    return edinet_code_map(r.content)


# ---------------------------------------------------------------- Eigene Kurse des Meldenden (Preis-Referenz)
_DATE = re.compile(r"(令和|平成)(元|\d+)年(\d+)月(\d+)日|(\d{4})年(\d+)月(\d+)日")
_TRADE = re.compile(r"市場外\s*(取得|処分)\s*([\d,]+(?:\.\d+)?)")
_ERA = {"令和": 2018, "平成": 1988}


def _iso(m) -> str:
    if m.group(1):
        y, mo, d = _ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2))), m.group(3), m.group(4)
    else:
        y, mo, d = m.group(5), m.group(6), m.group(7)
    return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"


def _parse_trades(text: str) -> list[tuple]:
    """Textblock 'Erwerb/Veraeusserung der letzten 60 Tage' -> [(iso, Preis, '取得'|'処分')]; nur Aktien, ausserboerslich (nur dort Preis)."""
    t, out = _nfkc(text), []
    ms = list(_DATE.finditer(t))
    for i, m in enumerate(ms):
        seg = t[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(t)]
        if "市場外" not in seg or "新株予約権" in seg or not ("株券" in seg or "普通株式" in seg):
            continue
        k = _TRADE.search(seg)
        if k and _num(k.group(2)):
            out.append((_iso(m), float(k.group(2).replace(",", "")), k.group(1)))
    return out


def parse_price_refs(rows: list[dict]) -> dict:
    """Preis-Referenzen des Meldenden: Stueckpreise (60-Tage-Tabelle), Ø-Einstand (Summe ueber Melder-Kontexte) und dessen Basisdatum."""
    trades, texts, fund, held, base = [], set(), 0.0, 0.0, None
    for r in rows:
        e, ctx, v = r.get(C_ELEM, ""), r.get(C_CTX, ""), r.get(C_VAL, "")
        if "DetailsOfAcquisitionsAndDisposalsOfStocksEtcIssuedByIssuerOfSaidStocks" in e and _nfkc(v) not in ("", "－", "-"):
            if v not in texts:
                texts.add(v)
                trades += [x for x in _parse_trades(v) if x not in trades]
        elif ctx.endswith("Member") and e.endswith(":TotalAmountOfFundingForAcquisition"):
            fund += _num(v) or 0.0
        elif ctx.endswith("Member") and e.endswith(":TotalNumberOfStocksEtcHeld"):
            held += _num(v) or 0.0
        elif e.endswith(":BaseDate") and base is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", _nfkc(v)):
            base = _nfkc(v)
    return {"trades": trades, "avg_cost": fund / held if fund > 0 and held > 0 else None, "base_date": base}


_REF_CACHE: dict | None = None
_REF_FILE = DATA / "cache" / "edinet_price_refs.jsonl"
_last_req = [0.0]


def price_refs(doc_id: str, key: str) -> dict:
    """parse_price_refs fuer ein EDINET-Dokument; jsonl-Cache je doc_id (inkl. Fehler), Live-Abrufe >= 1 s Abstand. Key nie gespeichert."""
    global _REF_CACHE
    if _REF_CACHE is None:
        _REF_CACHE = {}
        if _REF_FILE.exists():
            for ln in _REF_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(ln)
                    _REF_CACHE[d["doc_id"]] = {k: v for k, v in d.items() if k != "doc_id"}
                except (ValueError, KeyError):
                    pass
    if doc_id in _REF_CACHE:
        return _REF_CACHE[doc_id]
    time.sleep(max(0.0, 1.0 - (time.monotonic() - _last_req[0])))
    try:
        res = parse_price_refs(read_zip_rows(fetch_csv(doc_id, key)))
    except Exception as e:   # noqa: BLE001 - Fehler werden zwischengespeichert
        res = {"error": _scrub(f"{type(e).__name__}: {e}", key)}
    _last_req[0] = time.monotonic()
    _REF_CACHE[doc_id] = res
    _REF_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _REF_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"doc_id": doc_id, **res}, ensure_ascii=False) + "\n")
    return res
