"""Conservative role checks; structural evidence is not independent document verification."""
import re
import unicodedata
from datetime import date
import json
from ..common import ROOT

def norm(value):
    if value is None or not isinstance(value, (str, int)):
        return ""
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", str(value)).casefold())

def valid_isin(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", value):
        return False
    digits = "".join(str(ord(c)-55) if c.isalpha() else c for c in value)
    return sum((int(c)*2//10 + int(c)*2%10) if i%2 else int(c)
               for i,c in enumerate(reversed(digits))) % 10 == 0

def role_issues(event):
    issues = []
    region = event.get("region")
    f, i = norm(event.get("filer_id")), norm(event.get("issuer_id"))
    fn, inn = norm(event.get("filer_name")), norm(event.get("issuer_name"))
    if f and i and (f.lstrip("0") == i.lstrip("0") if region == "US" else f == i):
        issues.append("same_entity_id")
    if fn and inn and fn == inn:
        issues.append("same_entity_name")
    if region == "JP" and event.get("issuer_role_conflict") is True:
        issues.append("conflicting_document_issuer")
    if region == "JP" and event.get("issuer_role_explicit") is False:
        issues.append("missing_explicit_document_issuer")
    if region in ("UK","DACH","BENELUX_FR","NORDIC"):
        a, b = event.get("issuer_isin"), event.get("issuer_id")
        if isinstance(a,str) and len(a)==12 and not valid_isin(a):
            issues.append("invalid_issuer_isin")
        if valid_isin(a) and valid_isin(b) and a != b:
            issues.append("conflicting_issuer_isin")
    return issues

def identity_status(event):
    issues = role_issues(event)
    missing = [k for k in ("event_id","url","published","filer_name","issuer_name") if not norm(event.get(k))]
    return {"identity_status": "conflict" if issues else "pending_original_review",
            "identity_issues": "; ".join(issues + ["missing_"+k for k in missing]),
            "identity_verified": False}


# This register contains reviewed evidence only. Raw event fields cannot grant approval.
EVIDENCE = ROOT / "config/identity_evidence.json"
BINDING_FIELDS = ("event_id", "source", "region", "published", "url",
                  "filer_id", "filer_name", "issuer_id", "issuer_name")


def load_evidence():
    if not EVIDENCE.exists():
        return {}
    records = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    by_event = {}
    for record in records:
        by_event.setdefault(record["event_id"], []).append(record)
    return by_event


def binding_matches(event, record):
    required = {"event_id", "source", "region", "published", "url", "filer_name", "issuer_name"}
    return all(event.get(k) == record.get(k) and (k not in required or bool(event.get(k)))
               for k in BINDING_FIELDS)


def _document(record, key):
    doc = record.get(key) or {}
    return (isinstance(doc.get("url"), str) and doc["url"].startswith("https://")
            and re.fullmatch(r"[0-9a-f]{64}", str(doc.get("sha256") or ""))
            and isinstance(doc.get("excerpt"), str) and bool(doc["excerpt"].strip()))


def approved_identity(event, ticker, evidence):
    candidates = evidence.get(event.get("event_id"), [])
    if not ticker or len(candidates) != 1 or role_issues(event):
        return None
    record = candidates[0]
    if not binding_matches(event, record) or ticker != record.get("ticker"):
        return None
    if not all(_document(record, k) for k in ("original", "listing", "price_series")):
        return None
    digests = {record[k]["sha256"] for k in ("original", "listing", "price_series")}
    if record["original"]["url"] != event["url"] or len(digests) != 3:
        return None
    if event.get("document_sha256") and event["document_sha256"] != record["original"]["sha256"]:
        return None
    if event["region"] == "JP" and event.get("issuer_role_explicit") is not True:
        return None
    try:
        day = date.fromisoformat(event["published"][:10])
        listing_day = date.fromisoformat(record["listing"]["published"])
        start = date.fromisoformat(record["price_series"]["valid_from"])
        end = date.fromisoformat(record["price_series"]["valid_through"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (listing_day < day and start <= day <= end):
        return None
    isin = record.get("isin")
    if not valid_isin(isin) or not record.get("exchange"):
        return None
    if event["region"] == "JP":
        if (not isin.startswith("JP") or not event.get("issuer_id") or not event.get("filer_id")
                or event.get("issuer_sec_code") != ticker):
            return None
    elif event["region"] == "US":
        if not isin.startswith("US") and not record.get("foreign_sec_listing"):
            return None
        if not event.get("issuer_id") or not event.get("filer_id"):
            return None
    elif isin != (event.get("issuer_isin") or event.get("issuer_id")):
        return None
    return record


def apply_identity(row, event, evidence):
    approved = approved_identity(event, row.get("ticker"), evidence)
    if approved and approved["price_series"].get("provider", "yahoo") == row.get("price_source", "yahoo"):
        row.update(identity_status="verified", identity_issues="", identity_verified=True,
                   security_isin=approved["isin"],
                   security_evidence_through=approved["price_series"]["valid_through"])
        return approved
    return None
