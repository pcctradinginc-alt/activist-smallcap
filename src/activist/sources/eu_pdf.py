"""PDF-Anhänge der EU-Meldungen (Oslo NewsWeb, AMF BDIF): Melder, Anteil, ISIN per pypdf-Textextraktion + Regex.

Best effort: Formulare sind teils gescannt (kein Text), teils nicht-standardisiert -> dann bleibt das Event unverändert.
Jede Anreicherung schreibt `pdf_evidence` (Textauszug, auf dem der Wert beruht) und `pdf_enriched` (True/False).
Muster (mehrsprachig): ESMA-TR-1-Formular ("Details of person subject to the notification obligation", "Name:", "Total of both in %",
"Resulting situation"), norwegisch/dänisch/schwedisch grob ("Meldepliktig", "Anmelder"), französische AMF-Erklärung
("Déclaration de franchissement de seuils", "la société X ... a déclaré avoir franchi ... soit 4,96% du capital").
"""
from __future__ import annotations
import io, re
from .eu_common import request, pct_to_fraction

OSLO_MSG = "https://api3.oslo.oslobors.no/v1/newsreader/message"
OSLO_ATT = "https://api3.oslo.oslobors.no/v1/newsreader/attachment"
ISIN = r"[A-Z]{2}[A-Z0-9]{9}\d"
PCT = r"(\d{1,3}(?:[.,]\d+)?)\s*%"


def extract_text(pdf: bytes) -> str:
    from pypdf import PdfReader                      # Import lokal: Modul bleibt ohne pypdf importierbar
    r = PdfReader(io.BytesIO(pdf))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def _norm(t: str) -> str:
    t = t.replace("\xa0", " ").replace("’", "'")
    t = re.sub(r"-\n(?=[a-zà-ÿ])", "", t)            # Silbentrennung am Zeilenende ("at-\ntached")
    return t


def _clean_name(s: str) -> str | None:
    s = re.sub(r"\s+", " ", s).strip(" :;,.-")
    s = re.split(r"\s+(?:City and country|Registered office|Sted|By og land|Stad|Ville)\b", s, flags=re.I)[0].strip(" :;,.-")
    return s if 2 <= len(s) <= 120 else None


def _snippet(t: str, m: re.Match | None, w: int = 160) -> str | None:
    return re.sub(r"\s+", " ", t[max(0, m.start() - 20): m.end() + w]) if m else None


def parse_tr1_text(text: str) -> dict:
    """ESMA-Standardformular (englisch; Oslo-PDFs, teils NSM). Ziff. 3 = Melder, Ziff. 6 'Resulting situation' = Gesamtanteil."""
    t = _norm(text)
    out: dict = {"filer_name": None, "holding_ratio": None, "isin": None, "evidence": []}
    m = re.search(r"3\.\s*Details of person subject.{0,200}?Name\s*:?\s*(.+?)(?:\n\s*\n|City and country|\n\s*(?:4\.|Full name))", t, re.I | re.S)
    if m:
        out["filer_name"] = _clean_name(m.group(1))
        out["evidence"].append(_snippet(t, m, 0))
    m = re.search(r"Issuer[^\n]{0,80}\n?.{0,40}?\(ISIN:?\s*(" + ISIN + r")\)", t, re.I | re.S) or re.search(r"ISIN[:\s]+(" + ISIN + ")", t)
    if m:
        out["isin"] = m.group(1)
    m = re.search(r"Resulting situation(.*?)(?:Position of previous|Date:|\n7\.)", t, re.I | re.S)
    if m:
        nums = re.findall(PCT, m.group(1))
        if len(nums) >= 3:
            out["holding_ratio"] = pct_to_fraction(nums[2])        # 'Total of both in %'
        elif nums:
            out["holding_ratio"] = pct_to_fraction(nums[-1])
        out["evidence"].append(re.sub(r"\s+", " ", m.group(0))[:200])
    if out["holding_ratio"] is None:                                # Fallback: 'Total of both in %' beliebig, oder SUBTOTAL A
        m = re.search(r"(?:SUBTOTAL A|Total of both)[^%]{0,80}?" + PCT, t, re.I)
        if m:
            out["holding_ratio"] = pct_to_fraction(m.group(1)); out["evidence"].append(_snippet(t, m, 0))
    return out


def parse_amf_text(text: str) -> dict:
    """AMF-Avis 'Déclaration de franchissement de seuils': Melder aus 'la société X (...)', Anteil 'soit 4,96% du capital', ISIN im Kopf."""
    t = _norm(text)
    out: dict = {"filer_name": None, "holding_ratio": None, "isin": None, "direction": None, "evidence": []}
    m = re.search(r"\b(" + ISIN + r")\b", t[:400])
    if m:
        out["isin"] = m.group(1)
    m = re.search(r"(?:courrier|lettre|d[ée]claration)[^,\n]{0,80}?,\s*(?:la\s+soci[ée]t[ée](?:\s+\w+){0,3}?|le\s+fonds|l[ea]s?\s+soci[ée]t[ée]s?|Monsieur|Madame|M\.|Mme|la\s+banque|l['e]\s*)\s*"
                  r"(.{2,160}?)\s*(?:\(|,\s*(?:agissant|a\s+d[ée]clar[ée]|d[ée]clare))", t, re.I | re.S)
    if m:
        out["filer_name"] = _clean_name(m.group(1))
        out["evidence"].append(_snippet(t, m, 0))
    m = re.search(r"franchi\s+en\s+(hausse|baisse)", t, re.I)
    if m:
        out["direction"] = "up" if m.group(1).lower() == "hausse" else "down"
    m = re.search(r"soit\s+" + PCT + r"\s+(?:du\s+capital|des\s+droits\s+de\s+vote)", t, re.I) or \
        re.search(r"repr[ée]sentant[^%]{0,120}?" + PCT, t, re.I)
    if m:
        out["holding_ratio"] = pct_to_fraction(m.group(1)); out["evidence"].append(_snippet(t, m, 60))
    return out


def parse_any(text: str) -> dict:
    """Wählt Muster nach Sprachsignal; fällt auf das jeweils andere zurück."""
    fr = bool(re.search(r"franchissement|d[ée]clarant|droits de vote", text, re.I))
    a, b = (parse_amf_text, parse_tr1_text) if fr else (parse_tr1_text, parse_amf_text)
    r = a(text)
    if r["filer_name"] is None or r["holding_ratio"] is None:
        r2 = b(text)
        for k in ("filer_name", "holding_ratio", "isin"):
            r[k] = r.get(k) or r2.get(k)
    return r


def download(url: str) -> bytes:
    return request("GET", url).content


def _apply(ev: dict, d: dict) -> dict:
    ev["pdf_enriched"] = bool(d.get("filer_name") or d.get("holding_ratio") is not None)
    ev["pdf_evidence"] = " || ".join(e for e in d.get("evidence", []) if e)[:600] or None
    if d.get("filer_name"):
        ev["filer_name"] = d["filer_name"]
    if d.get("holding_ratio") is not None:
        ev["holding_ratio"] = d["holding_ratio"]
    if d.get("isin"):
        ev["issuer_isin"] = d["isin"]
    return ev


def enrich_amf(ev: dict) -> dict:
    """ev['url'] ist direkt das PDF. Fehler -> Event unverändert (pdf_enriched False)."""
    if not ev.get("url"):
        return ev
    try:
        return _apply(ev, parse_amf_text(extract_text(download(ev["url"]))))
    except Exception as e:
        ev["pdf_enriched"] = False; ev["pdf_error"] = str(e)[:120]
        return ev


def enrich_oslo(ev: dict) -> dict:
    """Nachricht abrufen -> Anhangsliste -> PDFs (bis Melder gefunden) per parse_any auswerten."""
    mid = str(ev["event_id"]).split(":", 1)[-1]
    try:
        msg = request("GET", OSLO_MSG, params={"messageId": mid}).json()["data"]["message"]
        best = None
        for att in msg.get("attachments") or []:
            if not str(att.get("name", "")).lower().endswith(".pdf"):
                continue
            d = parse_any(extract_text(download(f"{OSLO_ATT}?messageId={mid}&attachmentId={att['id']}")))
            best = best or d
            if d["filer_name"] and d["holding_ratio"] is not None:
                best = d; break
        if best:
            return _apply(ev, best)
        ev["pdf_enriched"] = False
    except Exception as e:
        ev["pdf_enriched"] = False; ev["pdf_error"] = str(e)[:120]
    return ev
