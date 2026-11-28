"""Re-read historical primary filings without modifying the event archive.
A document match is evidence of the roles, not a historical listing or intent certificate.
"""
import hashlib
import json
import os
import time
from collections import Counter
from activist.common import ROOT, atomic_write_text
from activist.sources import edgar, edinet_docs
from activist.study.identity import norm
from activist.parse.filing13d import parse_text
from activist.sources import hist_ticker
from datetime import date

def review(row):
    result = {"event_id": row["event_id"], "campaign_key": row["campaign_key"],
              "region": row["region"], "status": "unavailable"}
    if row["region"] == "US":
        raw = edgar._get(row["url"]).content
        h = edgar.parse_header(raw[:200000].decode("utf-8", errors="replace"))
        result["document_sha256"] = hashlib.sha256(raw).hexdigest()
        fields = {"filer_id": "filer_cik", "issuer_id": "subject_cik",
                  "filer_name": "filer_name", "issuer_name": "subject_name"}
        checks = {}
        for k,v in fields.items():
            a,b = norm(row.get(k)),norm(h.get(v))
            if k.endswith("_id"):
                a,b = a.lstrip("0"),b.lstrip("0")
            checks[k] = bool(a and b and a == b)
        parsed = parse_text(raw.decode("utf-8", errors="replace"))
        result.update(demand=parsed.get("demand"), demand_evidence=parsed.get("demand_evidence"),
                      intent_status="text_evidence_found" if parsed.get("demand_evidence") else "no_specific_demand_extracted")
        try:
            day = date.fromisoformat(row["published"][:10])
            hist = hist_ticker.ticker_hist(row["issuer_id"], day)
            result["historical_listing"] = hist
            result["prior_listing_evidence"] = bool(hist.get("ticker") and hist.get("filing_date") and hist["filing_date"] < str(day))
        except Exception as e:
            result["listing_error_type"] = type(e).__name__
        used = row.get("historical_tickers_used", [])
        canonical = lambda s: str(s).upper().replace(".", "-")
        result["historical_tickers_used"] = used
        result["historical_listing_status"] = ("missing_prior_evidence" if not result.get("prior_listing_evidence") else
            "missing_or_ambiguous_used_ticker" if len(used) != 1 else
            "matches_prior_issuer_filing" if canonical(used[0]) == canonical(result["historical_listing"]["ticker"]) else "ticker_mismatch")
        result["intent_class_matches"] = parsed.get("demand") == row.get("historical_demand")
        result["checks"] = checks
        result["status"] = "document_roles_match" if all(checks.values()) else "document_roles_mismatch"
    elif row["region"] == "JP":
        key = os.environ.get("EDINET_API_KEY")
        if not key:
            return {**result,"reason":"missing_edinet_key"}
        raw = edinet_docs.fetch_csv(row["event_id"].split(":",1)[1],key)
        d = edinet_docs.parse_large_holding_zip(raw)
        result["document_sha256"] = hashlib.sha256(raw).hexdigest()
        checks = {"issuer_name": bool(norm(row.get("issuer_name")) and norm(row.get("issuer_name")) == norm(d.get("issuer_name"))),
                  "filer_name": bool(norm(row.get("filer_name")) and norm(row.get("filer_name")) in {norm(x) for x in d.get("filer_names",[])}),
                  "issuer_code": row["campaign_key"].rsplit("-",1)[-1] == d.get("issuer_sec_code"),
                  "explicit_role": d.get("issuer_role_explicit") is True and not d.get("issuer_role_conflict")}
        result.update(checks=checks, proposal_evidence=d.get("important_proposal_evidence"),
                      important_proposal=d.get("important_proposal"))
        result["status"] = "document_roles_match" if all(checks.values()) else "document_roles_mismatch"
    else:
        result["reason"] = "regional_original_review_not_implemented"
    return result

def main():
    source = json.loads((ROOT/"reports/integrity_audit.json").read_text())
    results = []
    for row in source["campaigns"]:
        if row["matches"] != 1 or not row.get("url"):
            results.append({"campaign_key":row["campaign_key"],"status":"unlinked"})
            continue
        try:
            results.append(review(row))
        except Exception as e:
            results.append({"campaign_key":row["campaign_key"],"event_id":row["event_id"],
                            "status":"unavailable","error_type":type(e).__name__})
        time.sleep(.25)
    report = {"n":len(results),"counts":dict(Counter(r["status"] for r in results)),
              "historical_listing_verified":sum(r.get("historical_listing_status") == "matches_prior_issuer_filing" and r["status"] == "document_roles_match" for r in results),
              "listing_counts":dict(Counter(r.get("historical_listing_status","not_checked") for r in results)),
              "intent_class_matches":sum(r.get("intent_class_matches") is True for r in results),"prior_listing_evidence":sum(r.get("prior_listing_evidence") is True for r in results),
              "intent_counts":dict(Counter(r.get("intent_status","not_classified") for r in results)), "records":results}
    atomic_write_text(ROOT/"reports/original_review.json",json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="records"}))
if __name__ == "__main__":
    main()
