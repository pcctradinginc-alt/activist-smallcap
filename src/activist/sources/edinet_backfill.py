"""Japan: EDINET-Abruf mit Dokumentauswertung (Haltezweck, Anteil, Börsencode, Aktienzahl).

EDINET hält Großaktionärsmeldungen nur ca. 5 Jahre vor (Probe 2026-11: 2021-06 leer) -> Historie ab ~2021-11.
Jede Meldung (350/360) wird als CSV (type=5) geladen; das Ergebnis wird in das Ereignis geschrieben.
Wiederaufnehmbar: bekannte docIDs werden übersprungen.
"""
from __future__ import annotations
import os, time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from ..common import EventStore
from . import edinet, edinet_docs
from .edgar_backfill import RateLimiter

_RL = RateLimiter(3)
FIELDS = ("issuer_sec_code", "issuer_name", "purpose_text", "important_proposal", "important_proposal_evidence",
          "holding_ratio", "previous_holding_ratio", "total_shares_outstanding")


def enrich(ev: dict, key: str) -> dict:
    doc_id = ev["event_id"].split(":", 1)[1]
    try:
        _RL.wait()
        info = edinet_docs.parse_large_holding_zip(edinet_docs.fetch_csv(doc_id, key))
        ev.update({k: info.get(k) for k in FIELDS})
        ev["issuer_name"] = info.get("issuer_name") or ev.get("issuer_name")
        ev["doc_parsed"] = True
    except Exception as e:
        ev["doc_parsed"], ev["doc_error"] = False, str(e)[:200]
    return ev


def fetch_day(d: date, key: str, store: EventStore, workers: int = 3) -> dict:
    evs = edinet.fetch_events(d, key)
    known = store._ids()
    todo = [e for e in evs if e["event_id"] not in known]
    with ThreadPoolExecutor(workers) as ex:
        done = list(ex.map(lambda e: enrich(e, key), todo))
    n = store.append(done)
    return {"date": str(d), "docs": len(evs), "new": n, "parse_errors": sum(1 for e in done if not e["doc_parsed"])}


def backfill(start: date, end: date, key: str | None = None, store: EventStore | None = None):
    key = key or os.environ.get("EDINET_API_KEY")
    if not key:
        raise RuntimeError("EDINET_API_KEY fehlt")
    store = store or EventStore()
    d = start
    while d <= end:
        if d.weekday() < 5:
            for i in range(3):
                try:
                    print(fetch_day(d, key, store), flush=True); break
                except Exception as e:
                    print(f"{d}: Fehler {str(e)[:120]}", flush=True); time.sleep(5 * (i + 1))
        d += timedelta(days=1)
