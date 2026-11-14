"""ISIN -> Börsenlistings über die freie OpenFIGI-v3-API (POST /v3/mapping).

Ohne Key: 25 Jobs/min (je Request bis 10 Jobs -> 1 Request alle ~2,5 s reicht nicht; wir drosseln auf 25 Requests/min
konservativ, bei Key 250/min). Optional Header X-OPENFIGI-APIKEY aus OPENFIGI_API_KEY.
Ergebnis wird in DATA/cache/openfigi.json gecacht (auch Fehlschläge: leere Liste), damit Backfills reproduzierbar sind.
Achtung: das Mapping ist ein HEUTIGER Snapshot (Ticker/Listings heute), nicht point-in-time -> Survivorship-Lücke.
"""
from __future__ import annotations
import json, os, re, time
import requests
from ..common import DATA

URL = "https://api.openfigi.com/v3/mapping"
CACHE = DATA / "cache" / "openfigi.json"
BATCH = 10
# FIGI-exchCode -> Yahoo-Suffix (verifiziert per Live-Aufruf: BP LN, Ericsson SS/FH, Equinor NO)
EXCH_SUFFIX = {"LN": ".L", "NO": ".OL", "SS": ".ST", "DC": ".CO", "FH": ".HE", "FP": ".PA", "NA": ".AS", "BB": ".BR",
               "SW": ".SW", "SE": ".SW", "AV": ".VI", "GY": ".DE", "GR": ".DE"}
# ISIN-Länderpräfix -> bevorzugter exchCode (Heimatmarkt)
HOME = {"GB": "LN", "JE": "LN", "GG": "LN", "IE": "LN", "NO": "NO", "SE": "SS", "DK": "DC", "FI": "FH",
        "FR": "FP", "NL": "NA", "BE": "BB", "CH": "SW", "AT": "AV", "DE": "GY"}
_last = [0.0]


def _load() -> dict:
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def _save(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def parse_response(isins: list[str], resp: list[dict]) -> dict[str, list[dict]]:
    """OpenFIGI-Antwort (je Job {data:[...]} oder {warning|error}) -> {isin: [Listings]} (nur relevante Felder, nur Aktien)."""
    out = {}
    for isin, r in zip(isins, resp):
        out[isin] = [{"exch": d.get("exchCode"), "ticker": d.get("ticker"), "name": d.get("name"),
                      "figi": d.get("figi"), "type": d.get("securityType"), "sector": d.get("marketSector")}
                     for d in (r.get("data") or []) if d.get("marketSector") == "Equity"]
    return out


def _post(isins: list[str]) -> dict[str, list[dict]]:
    key = os.environ.get("OPENFIGI_API_KEY")
    hdr = {"Content-Type": "application/json", **({"X-OPENFIGI-APIKEY": key} if key else {})}
    gap = 0.3 if key else 2.6
    for i in range(5):
        wait = gap - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
        r = requests.post(URL, headers=hdr, timeout=60,
                          data=json.dumps([{"idType": "ID_ISIN", "idValue": x} for x in isins]))
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 10))); continue
        r.raise_for_status()
        return parse_response(isins, r.json())
    raise RuntimeError("OpenFIGI: Rate-Limit")


def map_isins(isins: list[str], use_network: bool = True) -> dict[str, list[dict]]:
    """Mit Cache; nur unbekannte ISINs gehen ins Netz (je 10 pro Request)."""
    cache = _load()
    todo = [i for i in dict.fromkeys(isins) if i and i not in cache]
    if todo and use_network:
        for k in range(0, len(todo), BATCH):
            cache.update(_post(todo[k:k + BATCH]))
            _save(cache)
    return {i: cache.get(i, []) for i in isins}


def primary_listing(isin: str, listings: list[dict]) -> dict | None:
    """Primärlisting: Heimatbörse des ISIN-Länderpräfixes (Stammaktie), sonst erster unterstützter exchCode in Reihenfolge der Antwort."""
    ok = [l for l in listings if l.get("exch") in EXCH_SUFFIX and (l.get("type") or "").lower().startswith("common")]
    if not ok:
        ok = [l for l in listings if l.get("exch") in EXCH_SUFFIX]
    home = HOME.get(isin[:2])
    for l in ok:
        if l["exch"] == home:
            return l
    return ok[0] if ok else None


def yahoo_ticker(listing: dict) -> str | None:
    """FIGI-Ticker -> Yahoo-Symbol. 'BP/' -> 'BP.L'; 'BT/A' -> 'BT-A.L'; Nordic-Gattungen: 'ERICB' + Name '...-B SHS' -> 'ERIC-B.ST'."""
    if not listing or listing.get("exch") not in EXCH_SUFFIX or not listing.get("ticker"):
        return None
    t = listing["ticker"].strip().rstrip("/").replace("/", "-").replace(" ", "-")
    if listing["exch"] in ("SS", "DC", "FH") and "-" not in t:
        m = re.search(r"[-\s]([A-D])(?:\s+(?:SHS|SER|ORD))*$", (listing.get("name") or "").upper())
        if m and t.endswith(m.group(1)) and len(t) > 2:
            t = t[:-1] + "-" + m.group(1)
    return t + EXCH_SUFFIX[listing["exch"]]


def isin_to_yahoo(isin: str, use_network: bool = True) -> str | None:
    ls = map_isins([isin], use_network)[isin]
    return yahoo_ticker(primary_listing(isin, ls)) if ls else None
