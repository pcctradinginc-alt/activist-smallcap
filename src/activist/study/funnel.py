"""Count historical observations by region at each available processing stage."""
from collections import Counter, defaultdict


def archive_funnel(events):
    regions = defaultdict(Counter)
    seen = set()
    for e in events:
        counts = regions[e["region"]]
        counts["raw"] += 1
        if e["event_id"] in seen:
            counts["duplicates"] += 1
            continue
        seen.add(e["event_id"])
        counts["unique"] += 1
        if e.get("filer_name") and e.get("issuer_name"):
            counts["both_roles_named"] += 1
        if e.get("filer_id") and e.get("issuer_id"):
            counts["both_roles_identified"] += 1
        if e.get("doc_parsed") is True:
            counts["original_parsed_at_import"] += 1
        elif e.get("doc_parsed") is False:
            counts["original_parse_failed"] += 1
        else:
            counts["original_parse_not_recorded"] += 1
    return {region: dict(n) for region, n in sorted(regions.items())}


def append_study_stages(funnel, dataset, trades):
    for region, counts in funnel.items():
        rows = dataset[dataset.region == region] if "region" in dataset else dataset.iloc[0:0]
        main = trades[(trades.region == region) & (trades.variant == "main")] if len(trades) else trades
        counts.update(campaigns=int(len(rows)), disposition=dict(Counter(rows.get("status", []))),
                      verified=int((rows.get("verified", False) == True).sum()) if "verified" in rows else 0,
                      qualified=int((rows.status == "qualifiziert").sum()) if len(rows) else 0,
                      no_order=int((main.executed != True).sum()) if len(main) else 0,
                      executed=int((main.executed == True).sum()) if len(main) else 0,
                      closed=int((main.get("position_closes", main.get("complete")) == True).sum())
                      if "complete" in main else 0,
                      execution_reasons=dict(Counter(main["reason"].dropna())) if "reason" in main else {})
    return funnel
