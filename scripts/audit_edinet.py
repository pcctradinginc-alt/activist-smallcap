"""Draw reproducible strata for independent EDINET source review."""
import hashlib
import json
import os
import random
from collections import Counter, defaultdict
from datetime import date
from concurrent.futures import ThreadPoolExecutor

from activist.common import ROOT, EventStore, atomic_write_text
from activist.sources.edgar_backfill import RateLimiter
from activist.sources.edinet_docs import fetch_csv, parse_large_holding_zip
from activist.study.dataset import JP_FORMS, is_jp_financial, jp_campaigns
from activist.study.identity import norm

SAMPLE_PER_STRATUM = 16
SEED = 20261128
LIMIT = RateLimiter(3)


def stratum(row, predictions):
    if not row.get("doc_parsed") or row.get("form") not in JP_FORMS:
        return "unparsed"
    if row["event_id"] in predictions:
        return predictions[row["event_id"]]
    if is_jp_financial(row.get("filer_name")):
        return "institution"
    if row.get("important_proposal"):
        return "proposal_not_selected"
    return "other"


def sampling(rows):
    predictions = {e["event_id"]: e["signal_class"] for e in jp_campaigns(rows)}
    strata = defaultdict(list)
    for row in rows:
        strata[stratum(row, predictions)].append(row)
    rng = random.Random(SEED)
    sampled = [(name, row) for name, group in sorted(strata.items())
               for row in rng.sample(sorted(group, key=lambda x: x["event_id"]), min(len(group), SAMPLE_PER_STRATUM))]
    return predictions, {k: len(v) for k, v in sorted(strata.items())}, sampled


def read_original(item, key):
    group, event = item
    result = {k: event.get(k) for k in ("event_id", "published", "url", "filer_name", "issuer_name", "issuer_sec_code")}
    result["stratum"] = group
    if not key:
        return {**result, "status": "missing_edinet_key"}
    try:
        LIMIT.wait()
        raw = fetch_csv(event["event_id"].split(":", 1)[1], key)
        parsed = parse_large_holding_zip(raw)
    except Exception as exc:
        return {**result, "status": "unavailable", "error_type": type(exc).__name__}
    return {**result, "status": "retrieved", "sha256": hashlib.sha256(raw).hexdigest(),
            "document_filer_names": parsed["filer_names"], "document_issuer": parsed["issuer_name"],
            "document_issuer_code": parsed["issuer_sec_code"], "purpose": parsed["purpose_text"],
            "issuer_explicit": parsed["issuer_role_explicit"],
            "roles_agree": bool(norm(event.get("issuer_name")) and
                                norm(event["issuer_name"]) == norm(parsed["issuer_name"]) and
                                norm(event["filer_name"]) in {norm(n) for n in parsed["filer_names"]} and
                                event.get("issuer_sec_code") == parsed["issuer_sec_code"] and parsed["issuer_role_explicit"])}


def main():
    rows = [e for e in EventStore().all() if e["source"] == "EDINET"]
    predictions, counts, sample = sampling(rows)
    key = os.getenv("EDINET_API_KEY")
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(lambda item: read_original(item, key), sample))
    out = {"as_of": str(date.today()), "events": len(rows), "stratum_counts": counts,
           "campaign_classes": dict(Counter(predictions.values())), "seed": SEED,
           "retrieval": dict(Counter(r["status"] for r in result)), "records": result}
    atomic_write_text(ROOT / "reports/edinet_review.json", json.dumps(out, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "records"}))


if __name__ == "__main__":
    main()
