"""Historischer Abruf ab 2015 über den Quartals-Formularindex.

- Zeitstempel kommt immer aus dem Filing-Header (ACCEPTANCE-DATETIME), nicht aus dem Indexdatum.
- Wiederaufnehmbar: bereits gespeicherte Accession-Nummern werden vor dem Abruf übersprungen.
- 13G-Meldungen werden nur geladen, wenn ein beteiligter Name im Aktivisten-Register steht
  (für die Erkennung 13G -> 13D und Klasse B); sonst wäre der Abruf ~5x größer ohne Nutzen.
- Rate-Limit: global <= 8 Anfragen/s (SEC erlaubt 10).
"""
from __future__ import annotations
import re, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..common import EventStore, effective_decision_time, event_id, load_yaml
from . import edgar

ACTIVE = {"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"}


class RateLimiter:
    def __init__(self, per_sec: float):
        self.dt, self.lock, self.next = 1.0 / per_sec, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic(); t = max(now, self.next); self.next = t + self.dt
        time.sleep(max(0.0, t - now))


_RL = RateLimiter(8)


def _norm(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper().replace("LLC", "").replace("LP", "").replace("LTD", ""))


def activist_name_keys() -> set[str]:
    return {_norm(a["name"])[:10] for a in load_yaml("activists.yaml")["activists"]}


def quarter_rows(year: int, q: int) -> list[dict]:
    r = edgar._get(f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx")
    return edgar.parse_form_index(r.text)


def select(rows: list[dict], keys: set[str]) -> dict[str, dict]:
    """Gruppiert Indexzeilen je Filing (Pfad). Jede Meldung steht zweimal im Index (Melder + Emittent)."""
    by = defaultdict(list)   # Schlüssel = Accession: dieselbe Meldung steht unter Melder- und Emittenten-CIK
    for r in rows:
        by[r["path"].rsplit("/", 1)[-1]].append(r)
    out = {}
    for _, rs in by.items():
        path = rs[0]["path"]
        form = rs[0]["form"]
        if form in ACTIVE or any(_norm(x["company"])[:10] in keys for x in rs):
            out[path] = rs[0]
    return out


def _fetch(row: dict) -> dict:
    _RL.wait()
    txt = edgar._get("https://www.sec.gov/Archives/" + row["path"]).text[:20000]
    h = edgar.parse_header(txt)
    acc = h.get("accession") or row["path"].rsplit("/", 1)[-1].replace(".txt", "")
    pub = h.get("published")
    return {"event_id": event_id("EDGAR", acc), "source": "EDGAR", "region": "US", "form": row["form"],
            "published": effective_decision_time(pub, row["date"]).isoformat(), "has_time": bool(pub),
            "filing_date": row["date"], "issuer_name": h.get("subject_name"), "issuer_id": h.get("subject_cik"),
            "filer_name": h.get("filer_name"), "filer_id": h.get("filer_cik"),
            "url": "https://www.sec.gov/Archives/" + row["path"]}


def backfill(year: int, q: int, store: EventStore | None = None, workers: int = 8, limit: int | None = None) -> dict:
    store = store or EventStore()
    known = {eid.split(":", 1)[1] for eid in store._ids()}
    sel = select(quarter_rows(year, q), activist_name_keys())
    todo = [r for p, r in sel.items() if p.rsplit("/", 1)[-1].replace(".txt", "") not in known][:limit]
    evs, errors = [], 0
    with ThreadPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(_fetch, r) for r in todo]):
            try:
                evs.append(f.result())
            except Exception:
                errors += 1
            if len(evs) >= 500:          # zwischenspeichern -> Abbruch verliert wenig
                store.append(evs); evs = []
    store.append(evs)
    return {"quarter": f"{year}Q{q}", "selected": len(sel), "fetched": len(todo) - errors, "errors": errors}
