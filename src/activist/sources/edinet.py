"""EDINET API v2: Großaktionärsmeldungen (docTypeCode 350 = Meldung, 360 = Änderung)."""
from __future__ import annotations
import os
from datetime import date
import requests
from ..common import effective_decision_time, event_id

API = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
DOC_TYPES = {"350": "LargeHolding", "360": "LargeHoldingAmendment"}


def fetch_events(d: date, key: str | None = None) -> list[dict]:
    key = key or os.environ.get("EDINET_API_KEY")
    if not key:
        raise RuntimeError("EDINET_API_KEY fehlt (GitHub Secret)")
    r = requests.get(API, params={"date": d.isoformat(), "type": 2, "Subscription-Key": key}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if str(j.get("metadata", {}).get("status")) not in ("200", "None"):
        raise RuntimeError(f"EDINET: {j.get('metadata')}")
    return parse_documents(j.get("results") or [])


def parse_documents(results: list[dict]) -> list[dict]:
    out = []
    for doc in results:
        if doc.get("docTypeCode") not in DOC_TYPES or doc.get("withdrawalStatus") not in (None, "0"):
            continue
        sub = doc.get("submitDateTime")  # "YYYY-MM-DD HH:MM" JST
        if not sub:   # ohne Veröffentlichungszeitpunkt nicht datierbar -> nicht verwendbar (Regel 1)
            continue
        pub = f"{sub.replace(' ', 'T')}:00+09:00" if sub else None
        out.append({
            "event_id": event_id("EDINET", doc["docID"]), "source": "EDINET", "region": "JP",
            "form": DOC_TYPES[doc["docTypeCode"]],
            "published": effective_decision_time(pub, (sub or "")[:10] or None).isoformat(),
            "has_time": bool(sub), "filing_date": (sub or "")[:10],
            "issuer_name": None, "issuer_id": doc.get("issuerEdinetCode") or doc.get("subjectEdinetCode"),
            "filer_name": doc.get("filerName"), "filer_id": doc.get("edinetCode"),
            "description": doc.get("docDescription"),
            "url": f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{doc['docID']}",
        })
    return out
