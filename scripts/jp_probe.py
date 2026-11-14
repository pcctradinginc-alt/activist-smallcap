#!/usr/bin/env python3
"""Live-Probe fuer EDINET (benoetigt EDINET_API_KEY): Retention je Datum + Parser-Validierung. Gibt den Key nie aus."""
import os
import sys
from datetime import date, timedelta
from pathlib import Path
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from activist.sources import edinet_docs as ed  # noqa: E402

LIST_API = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
KEYWORDS = ("Purpose", "Ratio", "SecurityCode", "Total")


def list_docs(d: date, key: str) -> list[dict]:
    r = requests.get(LIST_API, params={"date": d.isoformat(), "type": 2, "Subscription-Key": key}, timeout=60)
    r.raise_for_status()
    return r.json().get("results") or []


def main() -> int:
    key = os.environ.get("EDINET_API_KEY")
    if not key:
        print("EDINET_API_KEY nicht gesetzt"); return 1
    dates = [date.today() - timedelta(days=3), date(2021, 6, 1), date(2019, 6, 3), date(2016, 6, 1)]
    recent: list[dict] = []
    for d in dates:
        try:
            docs = list_docs(d, key)
        except Exception as e:  # Fehlertext ohne Key
            print(d, "FEHLER", str(e).replace(key, "***")[:200]); continue
        n = {c: sum(1 for x in docs if x.get("docTypeCode") == c) for c in ("350", "360")}
        print(d, "docs gesamt", len(docs), "350:", n["350"], "360:", n["360"])
        if d == dates[0]:
            recent = [x for x in docs if x.get("docTypeCode") == "350"]
    for doc in recent[:3]:
        did = doc["docID"]
        print("\n==", did, doc.get("docDescription"))
        try:
            z = ed.fetch_csv(did, key)
            rows = ed.read_zip_rows(z)
            print(ed.parse_rows(rows))
            ids = sorted({r.get(ed.C_ELEM, "") for r in rows if any(k in r.get(ed.C_ELEM, "") for k in KEYWORDS)})
            print("Element-IDs:", *ids, sep="\n  ")
        except Exception as e:
            print("FEHLER", str(e).replace(key, "***")[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
