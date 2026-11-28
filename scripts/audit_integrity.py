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
    # Historical trade rows, not today's security master. Join on the original campaign and exact publication.
    historical = defaultdict(set)
    for f in sorted((root / 'reports').glob('event_study_*_trades.csv')):
        with f.open() as fh:
            for t in csv.DictReader(fh):
                if t.get('variant') == 'main' and t.get('ticker'):
                    historical[(t.get('campaign_key'), t.get('published'))].add(t['ticker'])
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
                            "historical_tickers_used": sorted(historical[(row["campaign_key"], row["published"])]),
                            "historical_demand": row.get("demand"),
                            "matches": len(matches), "event_id": e.get("event_id"), "url": e.get("url"),
                            "filer_id": e.get("filer_id"), "filer_name": e.get("filer_name"),
                            "issuer_id": e.get("issuer_id"), "issuer_name": e.get("issuer_name"),
                            "missing": missing, "review_status": "pending_original_document_review"})
    from activist.study.dataset import jp_campaigns
    from activist.study.identity import role_issues
    jp_current = {c["campaign_key"]: c for c in jp_campaigns(events)}
    ev_by_id = {e["event_id"]: e for e in events}
    journal = root / "journal_archive/2026-11-28-pre-p0-reset"
    if not journal.exists():
        journal = root / "journal"
    state = json.loads((journal / "portfolio.json").read_text())
    trades = []
    with (journal / "trades.csv").open() as f:
        for t in csv.DictReader(f):
            issues = []
            if t["region"] == "JP" and abs(float(t["qty"]) % 100) > 1e-6:
                issues.append("historical_non_board_lot")
            if t["fill_date"] < t["order_date"]:
                issues.append("fill_before_order")
            event = ev_by_id.get(t["event_id"], {})
            current = jp_current.get(t["campaign_id"], {})
            pos = state["positions"].get(t["trade_id"], {})
            entry_rate = pos.get("entry_rate") if t["side"] == "BUY" else None
            notional = float(t["price"]) * float(t["qty"])
            cash_flow = -notional * entry_rate * (1 + float(t["cost_pct"])) if entry_rate else None
            trades.append({"trade_id": t["trade_id"], "ticker": t["ticker"], "side": t["side"], "region": t["region"],
                           "campaign_id": t["campaign_id"], "issues": issues + role_issues(event),
                           "filer_name": event.get("filer_name"), "issuer_name": event.get("issuer_name"),
                           "current_signal_class": current.get("signal_class"),
                           "current_activist_evidence": current.get("activist_evidence_kind"),
                           "local_notional": notional, "entry_fx_from_state": entry_rate,
                           "cash_flow_eur_from_state": cash_flow,
                           "fx_independently_verified": False})
    jp_buys = [t for t in trades if t["side"] == "BUY" and t["region"] == "JP"]
    counts = Counter((e.get("region"), e.get("source")) for e in events)
    return {"raw_events": len(events), "unique_event_ids": len({e["event_id"] for e in events}),
            "source_counts": [{"region": r, "source": s, "n": n} for (r,s),n in counts.items()],
            "campaign_count": len(results), "uniquely_linked": sum(x["matches"] == 1 for x in results),
            "original_documents_reviewed": 0,
            "jp_historical_buys": len(jp_buys), "jp_buys_not_currently_classified": sum(not t["current_signal_class"] for t in jp_buys),
            "jp_buys_with_board_lot_violation": sum("historical_non_board_lot" in t["issues"] for t in jp_buys), "campaigns": results, "ledger": trades}

if __name__ == "__main__":
    out = audit()
    atomic_write_text(ROOT / "reports/integrity_audit.json", json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps({k:v for k,v in out.items() if k not in ("campaigns","ledger")}, ensure_ascii=False))
