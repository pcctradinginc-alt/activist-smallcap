"""Read-only historical evidence and ledger audit; never promotes a record to verified."""
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from activist.common import ROOT, EventStore, atomic_write_text

def audit(root=ROOT):
    events = EventStore(root / "data/events").all()
    by_day = defaultdict(list)
    for e in events:
        by_day[(e.get("region"), str(e.get("published"))[:10])].append(e)
    results = []
    with (root / "reports/campaign_outcomes.csv").open() as f:
        for row in csv.DictReader(f):
            matches = []
            for e in by_day[(row["region"], row["published"][:10])]:
                if e.get("published") != row["published"]:
                    continue
                key = str(e.get("filer_id") or "").lstrip("0") + "-" + str(e.get("issuer_id") or "").lstrip("0")
                if key == row["campaign_key"] or (row["region"] == "JP" and
                    str(e.get("filer_id")) + "-" + str(e.get("issuer_sec_code")) == row["campaign_key"]):
                    matches.append(e)
            e = matches[0] if len(matches) == 1 else {}
            missing = [k for k in ("event_id", "url", "filer_id", "issuer_id", "issuer_name") if not e.get(k)]
            results.append({"campaign_key": row["campaign_key"], "region": row["region"],
                            "published": row["published"], "original_verified_flag": row["verified"],
                            "matches": len(matches), "event_id": e.get("event_id"), "url": e.get("url"),
                            "filer_id": e.get("filer_id"), "filer_name": e.get("filer_name"),
                            "issuer_id": e.get("issuer_id"), "issuer_name": e.get("issuer_name"),
                            "missing": missing, "review_status": "pending_original_document_review"})
    trades = []
    with (root / "journal/trades.csv").open() as f:
        for t in csv.DictReader(f):
            issues = []
            if t["region"] == "JP" and abs(float(t["qty"]) % 100) > 1e-6:
                issues.append("historical_non_board_lot")
            if t["fill_date"] < t["order_date"]:
                issues.append("fill_before_order")
            trades.append({"trade_id": t["trade_id"], "ticker": t["ticker"], "issues": issues})
    counts = Counter((e.get("region"), e.get("source")) for e in events)
    return {"raw_events": len(events), "unique_event_ids": len({e["event_id"] for e in events}),
            "source_counts": [{"region": r, "source": s, "n": n} for (r,s),n in counts.items()],
            "campaign_count": len(results), "uniquely_linked": sum(x["matches"] == 1 for x in results),
            "original_documents_reviewed": 0, "campaigns": results, "ledger": trades}

if __name__ == "__main__":
    out = audit()
    atomic_write_text(ROOT / "reports/integrity_audit.json", json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in out.items() if k not in ("campaigns","ledger")}, ensure_ascii=False))
