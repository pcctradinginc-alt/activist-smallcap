"""Frankreich: AMF BDIF (bdif.amf-france.org/back/api/v1/informations), Typ SPDE 'Seuils, pactes, dérogations' / Dokumenttyp 'Declarations'
(Schwellenüberschreitungen und Absichtserklärungen). Melder, ISIN und Prozentsatz stehen nur im PDF -> nicht enthalten.

Zeitstempel: datePublication (sekundengenau, Europe/Paris). Der Altbestand vor 2022 wurde im Dez. 2021/Jan. 2022 migriert
(datePublication = Migrationsdatum!). Dann gilt dateMiseEnLigne (falls mit Uhrzeit) sonst nur dateInformation (has_time False).
"""
from __future__ import annotations
from datetime import date, timedelta
from .eu_common import make_event, request, in_range

API = "https://bdif.amf-france.org/back/api/v1/informations"
DOC = "https://bdif.amf-france.org/back/api/v1/documents/"
SOURCE, REGION = "AMF_BDIF", "BENELUX_FR"
MIGRATION_END = "2022-02-01"        # datePublication < dieses Datum UND Info-Datum früher => Migrationsstempel
PAGE = 400


def _is_migrated(r: dict) -> bool:
    dp, di = r.get("datePublication") or "", (r.get("dateInformation") or "")[:10]
    return bool(dp[:10] >= "2021-12-01" and dp[:10] < MIGRATION_END and di and di < "2021-12-01")


def _publication(r: dict) -> tuple[str | None, str, bool]:
    """-> (iso mit tz | None, Datum, has_time)"""
    dp, dm, di = r.get("datePublication") or "", r.get("dateMiseEnLigne") or "", (r.get("dateInformation") or "")[:10]
    if not _is_migrated(r) and dp:
        return dp, dp[:10], True
    if dm and not dm.endswith("T00:00:00") and "T" in dm:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        dt = datetime.fromisoformat(dm).replace(tzinfo=ZoneInfo("Europe/Paris"))
        return dt.isoformat(), dm[:10], True
    return None, di or dm[:10], False


def parse_informations(resp: dict) -> list[dict]:
    out = []
    for r in resp.get("result", []):
        if "SPDE" not in (r.get("typesInformation") or []) or "Declarations" not in (r.get("typesDocument") or []):
            continue
        iso, d, has_time = _publication(r)
        if not d:
            continue
        soc = next((s for s in r.get("societes", []) if s.get("role") == "SocieteConcernee"), (r.get("societes") or [{}])[0] if r.get("societes") else {})
        docs = r.get("documents") or []
        out.append(make_event(
            source=SOURCE, native_id=r["numero"], region=REGION, published=iso, date_only=d, has_time=has_time,
            issuer_name=soc.get("raisonSociale"), issuer_id=soc.get("jeton"), filer_name=None, filer_id=None,
            url=(DOC + docs[0]["path"]) if docs and docs[0].get("path") else None,
            amf_numero=r["numero"], ts_migrated=True if _is_migrated(r) else None))
    return out


def fetch_events(start_date: date, end_date: date) -> list[dict]:
    out, frm = [], 0
    while True:
        r = request("GET", API, params={
            "From": frm, "Size": PAGE, "TypesInformation": "SPDE", "TypesDocument": "Declarations",
            "DateDebut": f"{(start_date - timedelta(days=10)).isoformat()}T00:00:00.000Z", "DateFin": f"{end_date.isoformat()}T23:59:59.000Z"}).json()
        res = r.get("result", [])
        out += parse_informations(r)
        frm += len(res)
        if not res or frm >= r.get("total", 0):
            break
    seen = set(); uniq = []
    for e in out:
        if e["event_id"] not in seen and in_range(e, start_date, end_date):
            seen.add(e["event_id"]); uniq.append(e)
    return sorted(uniq, key=lambda e: e["published"])
