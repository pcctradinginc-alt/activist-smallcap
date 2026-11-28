"""Conservative role checks; structural evidence is not independent document verification."""
import re
import unicodedata

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
    missing = [k for k in ("event_id","url","filer_name","issuer_name") if not norm(event.get(k))]
    return {"identity_status": "conflict" if issues else "pending_original_review",
            "identity_issues": "; ".join(issues + ["missing_"+k for k in missing]),
            "identity_verified": False}
