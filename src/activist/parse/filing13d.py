"""Volltext einer Schedule 13D / 13D/A (EDGAR .txt) -> strukturierte Felder.

Unterstützt: (a) Legacy HTML/Plain-Text (bis Dez 2024) und (b) neue XML-Einreichungen
(<edgarSubmission xmlns=".../schedule13D">). Reine Regeln, keine Heuristik ohne Textbeleg:
Die Forderungsklasse wird nur vergeben, wenn ein Satz im Item-4-Text sie belegt (demand_evidence).
"""
from __future__ import annotations
import html
import re
from datetime import datetime, timedelta

ITEM4_MAX = 4000
EVIDENCE_MAX = 400
DEMAND_PRIORITY = ("oppose_merger", "sale", "capital_return", "board", "strategy")

# --------------------------------------------------------------------------- Textaufbereitung

_BLOCK = re.compile(r"(?i)</?(p|div|br|tr|li|h\d|table|center|pre)\b[^>]*>")
_CELL = re.compile(r"(?i)</t[dh]\s*>")


def _clean(s: str) -> str:
    """HTML-Tags/Entities entfernen; Absätze (Leerzeile) bleiben, harte Zeilenumbrüche werden Leerzeichen."""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", s)
    s = _BLOCK.sub("\n\n", s)
    s = _CELL.sub(" ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s).replace("\xa0", " ").replace("\r", "")
    s = re.sub(r"[ \t\f\v]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{2,}", "\x00", s)         # Absatzgrenze merken
    s = s.replace("\n", " ")
    s = re.sub(r" {2,}", " ", s)
    return s.replace("\x00", "\n\n").strip()


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _num(s: str) -> float | None:
    """'1,957, 961' / '2373134.00' -> float."""
    s = re.sub(r"[,\s]", "", s or "")
    try:
        return float(s)
    except ValueError:
        return None


_NUM = r"\d{1,3}(?:,\s?\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"


# --------------------------------------------------------------------------- Format-Erkennung / Extraktion

def _is_xml(text: str) -> bool:
    return "<edgarSubmission" in text


def _xml_tags(xml: str, tag: str) -> list[str]:
    return [m for m in re.findall(rf"<{tag}>(.*?)</{tag}>", xml, flags=re.S)]


def _xml_part(text: str) -> str:
    i = text.find("<edgarSubmission")
    j = text.find("</edgarSubmission>")
    return text[i:j] if j > i else text[i:]


def _legacy_section(txt: str, start_pat: str, end_pat: str) -> str:
    """Längster Abschnitt zwischen Start- und Endmuster (umgeht Inhaltsverzeichnis/Querverweise)."""
    best = ""
    for m in re.finditer(start_pat, txt):
        e = re.search(end_pat, txt[m.end():])
        sec = txt[m.end(): m.end() + e.start()] if e else txt[m.end(): m.end() + ITEM4_MAX * 3]
        if len(sec) > len(best):
            best = sec
    return best.strip()


_ITEM4_START = r"(?i)item\s*4\s*[.:\-–—]*\s*(?:purpose\s+of\s+(?:the\s+)?transactions?)?[.:\-–—]*"
_ITEM5_START = r"(?i)item\s*5\s*[.:\-–—]*\s*interest\s+in\s+securities[^\n]*"
_ITEM5_END = r"(?i)item\s*[67]\s*[.:\-–—]"


def _legacy_item4(txt: str) -> str:
    end = r"(?i)item\s*5\s*[.:\-–—]*\s*interest\s+in\s+(?:the\s+)?securities"
    named = r"(?i)item\s*4\s*[.:\-–—]*\s*purpose\s+of\s+(?:the\s+)?transactions?[.:\-–—]*"
    abbreviated = r"(?im)^item\s*4\s*[.:\-–—]"
    return _legacy_section(txt, named, end) or _legacy_section(txt, abbreviated, end)


# --------------------------------------------------------------------------- Prozent / Stückzahl

def _percent_legacy(txt: str) -> float | None:
    vals = []
    for m in re.finditer(r"(?i)percent\s+of\s+class\s+represented\s+by\s+amount\s+in\s+row\s*\(?\s*11\s*\)?[^0-9]{0,80}?(\d+(?:\.\d+)?)\s*%", txt):
        vals.append(float(m.group(1)))
    if not vals:   # Fallback Item 5(a)
        for m in re.finditer(r"(?i)(?:approximately|representing|or)\s+(\d+(?:\.\d+)?)\s*%\s+of\s+the", txt):
            vals.append(float(m.group(1)))
    return max(vals) if vals else None


def _shares_legacy(txt: str) -> float | None:
    vals = []
    for m in re.finditer(rf"(?i)aggregate\s+amount\s+beneficially\s+owned\s+by\s+each\s+reporting\s+person[^0-9]{{0,40}}?({_NUM})", txt):
        v = _num(m.group(1))
        if v is not None:
            vals.append(v)
    if not vals:
        m = re.search(rf"(?i)beneficially\s+own\w*\s+({_NUM})\s+(?:shares|ordinary|common)", txt)
        if m:
            vals.append(_num(m.group(1)))
    return max(vals) if vals else None


# --------------------------------------------------------------------------- Forderungsklassifikation

# Standard-Verneinung (Item 4 (a)-(j)): ganze Sätze, die nur Boilerplate sind
_NEGATION = re.compile(
    r"(?i)(?:has|have|had|does|do)\s+(?:not\s+)?(?:currently\s+)?(?:no|any)\s+(?:present\s+|current\s+)?(?:plans?|proposals?|intentions?)"
    r"|(?:no|not\s+(?:have|has|any))\s+(?:present\s+|current\s+)?plans?\s+or\s+proposals?"
    r"|(?:no|any)\s+plans?\s+or\s+proposals?\s+which\s+relate\s+to"
    r"|except\s+as\s+(?:set\s+forth|described|disclosed)\s+(?:in|above|herein)"
)
_LIST_PHRASES = [
    r"extraordinary\s+corporate\s+transaction", r"sale\s+or\s+transfer\s+of\s+a\s+material\s+amount\s+of\s+assets",
    r"change\s+in\s+the\s+present\s+board", r"material\s+change\s+in\s+the\s+present\s+capitalization",
    r"any\s+other\s+material\s+change\s+in\s+the\s+issuer", r"changes\s+in\s+the\s+issuer.s\s+charter",
    r"causing\s+a\s+class\s+of\s+securities", r"acquisition\s+by\s+any\s+person\s+of\s+additional\s+securities",
]
# Vorbehalts-Boilerplate ("may from time to time ...", "reserves the right ...") = keine Forderung
_RESERVATION = re.compile(r"(?i)\bfrom\s+time\s+to\s+time\b.*\b(?:may|might|could)\b|\b(?:may|might)\b.*\bfrom\s+time\s+to\s+time\b|reserves?\s+(?:the|their|its)\s+right")

_RULES: dict[str, re.Pattern] = {
    "oppose_merger": re.compile(
        r"(?i)\b(?:oppos(?:e[sd]?|ing|ition to)|defeat(?:ing)?|vot(?:e|ing) [^.]{0,65}against)"
        r"\s+(?:[^.]{0,40}\s+)?(?:merger|business combination)\b"),
    "sale": re.compile(
        r"(?i)sale\s+of\s+(?:the\s+)?(?:company|issuer|business)|sell\s+(?:the\s+)?(?:company|itself)|strategic\s+alternatives|\bmergers?\b"
        r"|acquisition\s+proposal|business\s+combination|take[\s-]private|going[\s-]private|(?:proposal|offer)\s+to\s+(?:acquire|purchase)\s+(?:all|the\s+company)"
        r"|(?:explore|pursue|evaluate)\s+(?:a\s+)?(?:sale|transaction\s+to\s+sell)"),
    "capital_return": re.compile(
        r"(?i)buy[\s-]?backs?|repurchas\w+|\bdividends?\b|return\s+(?:of\s+)?(?:excess\s+)?(?:capital|cash)|capital\s+return|capital\s+allocation|special\s+distribution"),
    "board": re.compile(
        r"(?i)board\s+representation|\bnominate\b|\bnominating\b|\bnominations?\b|director\s+candidates?|board\s+seats?|seats?\s+on\s+(?:the\s+)?(?:company.s\s+|issuer.s\s+)?board"
        r"|reconstitut\w+\s+(?:the\s+)?board|(?:add|appoint|elect)\w*\s+(?:new\s+|independent\s+)?directors?|proxy\s+(?:contest|solicitation)|replace\s+(?:the\s+)?(?:board|directors)"),
    "strategy": re.compile(
        r"(?i)\boperational\b|\boperations\s+(?:improvement|review)|\bstrategy\b|strategic\s+(?:plan|direction|review|initiatives?)"
        r"|spin[\s-]?offs?|\bcosts?\b|operating\s+expenses|margins?\b|divest\w*"),
}


def _sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n{2,}", text):
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(\"“\-•])", para):
            s = _flat(s)
            if s:
                out.append(s)
    return out


PARSER_VERSION = 5   # erhöhen bei jeder Änderung der Klassifikation -> Cache wird neu aufgebaut

_ITEM4_LIST = [
    r"acquisition by any person of additional securities", r"extraordinary corporate transaction",
    r"sale or transfer of a material amount of assets", r"change in the present board of directors",
    r"material change in the present capitalization", r"any other material change in the issuer",
    r"changes in the issuer.s charter", r"class of securities of the issuer to be delisted",
    r"class of equity securities of the issuer becoming eligible for termination", r"any action similar to any of those",
]


def _is_boilerplate(s: str) -> bool:
    if re.search(r"(?i)\b(?:depending (?:on|upon)|in pursuing (?:such )?investment purposes|"
                 r"may (?:in the future |from time to time )?(?:engage in|discuss|consider|wish to)|"
                 r"may (?:take positions|make proposals) with respect to|"
                 r"suggestions or positions may relate to|may be made at any time|any such actions could involve|"
                 r"do(?:es)? not have any present|no present (?:plan|proposal|intention))\b", s):
        return True
    if _NEGATION.search(s):
        return True
    if sum(bool(re.search(p, s, re.I)) for p in _LIST_PHRASES) >= 2:
        return True
    if len(re.findall(r"\(\s*[a-j]\s*\)", s)) >= 3:      # eingebettete (a)-(j)-Liste
        return True
    # einzelner Punkt der SEC-Standardliste (Item 4 (a)-(j)), oft ohne Negationssatz kopiert
    if re.match(r"^\s*\(?\s*[a-j]\s*\)\s", s) and any(re.search(p, s, re.I) for p in _ITEM4_LIST):
        return True
    return bool(_RESERVATION.search(s))


def classify_demand(item4: str) -> tuple[str, str | None]:
    """Return a filing statement and its demand category in priority order."""
    sents = [s for s in _sentences(item4) if not _is_boilerplate(s)]
    for cls in DEMAND_PRIORITY:
        for s in sents:
            if _RULES[cls].search(s):
                return cls, s[:EVIDENCE_MAX]
    return "none", None


# --------------------------------------------------------------------------- Kaufpreis (Item 5(c))

_ROW = re.compile(
    rf"(\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|\d{{4}}-\d{{2}}-\d{{2}})\s+({_NUM})\s+\$?\s*(\d+\.\d+|\d+)\b\s*(?:\(\d+\)\s*)?([A-Za-z][A-Za-z ]{{0,30}})?")
_PROSE = re.compile(
    rf"(?i)(?:purchase[sd]?|bought|acquired)\s+(?:of\s+)?({_NUM})\s+(?:[\w.]+\s+){{0,4}}?(?:shares|stock)[^.;]{{0,100}}?\$\s?(\d+(?:\.\d+)?)")


def _pdate(s: str) -> datetime | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def avg_purchase_price(item5: str, ref_date: datetime | None = None) -> float | None:
    """Volumengewichteter Kaufpreis aus der Transaktionstabelle der letzten 60 Tage (nur Käufe)."""
    rows = []
    for m in _ROW.finditer(item5):
        d, q, p, kind = _pdate(m.group(1)), _num(m.group(2)), _num(m.group(3)), (m.group(4) or "").lower()
        if d is None or not q or not p:
            continue
        if re.match(r"\s*(sell|sold|sale|dispos)", kind):
            continue
        rows.append((d, q, p))
    if rows:
        ref = ref_date or max(r[0] for r in rows)
        rows = [r for r in rows if ref - timedelta(days=60) <= r[0] <= ref + timedelta(days=1)]
        vol = sum(q for _, q, _ in rows)
        return round(sum(q * p for _, q, p in rows) / vol, 4) if vol else None
    pr = [(_num(m.group(1)), _num(m.group(2))) for m in _PROSE.finditer(item5)]
    pr = [(q, p) for q, p in pr if q and p]
    vol = sum(q for q, _ in pr)
    return round(sum(q * p for q, p in pr) / vol, 4) if vol else None


# --------------------------------------------------------------------------- Hauptfunktion

_EXIT_TEXT = re.compile(r"(?i)ceased\s+to\s+be\s+(?:the\s+)?beneficial\s+owner\s+of\s+more\s+than\s+(?:five|5)\s*(?:percent|per\s*cent|%)")


def subject_sic(text: str) -> str | None:
    """SIC des Emittenten laut SEC-Kopf DIESER Meldung (Stand zum Meldezeitpunkt, nicht heute)."""
    subj = text.split("SUBJECT COMPANY:")[1].split("FILED BY:")[0] if "SUBJECT COMPANY:" in text else ""
    m = re.search(r"STANDARD INDUSTRIAL CLASSIFICATION:[^\n]*?\[(\d{4})\]", subj)
    return m.group(1) if m else None


def parse_text(text: str, *, full_context=False) -> dict:
    m = re.search(r"FILED AS OF DATE:\s*(\d{8})", text)
    ref = datetime.strptime(m.group(1), "%Y%m%d") if m else None

    if _is_xml(text):
        xml = _xml_part(text)
        pcts = [_num(x) for x in _xml_tags(xml, "percentOfClass")]
        pcts = [p for p in pcts if p is not None]
        shs = [_num(x) for x in _xml_tags(xml, "aggregateAmountOwned")]
        shs = [s for s in shs if s is not None]
        item4 = _clean("\n\n".join(_xml_tags(xml, "transactionPurpose")))
        item5 = _clean("\n\n".join(
            "\n\n".join(_xml_tags(xml, t)) for t in ("percentageOfClassSecurities", "transactionDesc")))
        percent = max(pcts) if pcts else None
        shares = max(shs) if shs else None
        if shares is None:
            ns = [_num(x) for x in _xml_tags(xml, "numberOfShares")]
            ns = [n for n in ns if n is not None]
            shares = max(ns) if ns else None
        if percent is None:
            percent = _percent_legacy(item5)
        full = _clean(xml)
    else:
        # Dokumentteil des Hauptformulars (Anlagen/Exhibits können Fremdtext enthalten)
        main = text
        docs = re.split(r"(?i)<DOCUMENT>", text)
        if len(docs) > 1:
            main = next((d for d in docs[1:] if re.search(r"(?i)<TYPE>\s*(?:SC|SCHEDULE)\s*13D", d)), docs[1])
        full = _clean(main)
        item4 = _legacy_item4(full)
        item5 = _legacy_section(full, _ITEM5_START, _ITEM5_END)
        percent = _percent_legacy(full)
        shares = _shares_legacy(full)

    item4_text = item4 if full_context else item4[:ITEM4_MAX]
    demand, evidence = classify_demand(item4)
    is_exit = bool((percent is not None and percent < 5) or _EXIT_TEXT.search(full))
    return {
        "percent_of_class": percent,
        "shares": shares,
        "item4_text": item4_text,
        "demand": demand,
        "demand_evidence": evidence,
        "avg_purchase_price": avg_purchase_price(item5, ref),
        "is_exit": is_exit,
        "subject_sic": subject_sic(text),
    }


def fetch_and_parse(url: str) -> dict:
    """Lädt die .txt-Einreichung (SEC-User-Agent via sources.edgar._get) und parst sie."""
    from ..sources import edgar
    return parse_text(edgar._get(url).text)
