from activist.study.identity import valid_isin, role_issues, identity_status

def test_isin_checksum():
    assert valid_isin("DE0007164600")
    assert valid_isin("US0378331005")
    assert not valid_isin("DE0007164601")
    assert not valid_isin("US037833100X")

def test_same_cik_and_name_conflict():
    e = {"region":"US","filer_id":"000123","issuer_id":"123",
         "filer_name":"Example Inc.","issuer_name":"EXAMPLE INC"}
    assert set(role_issues(e)) == {"same_entity_id","same_entity_name"}
    assert identity_status(e)["identity_status"] == "conflict"

def test_europe_conflicting_isins():
    e = {"region":"DACH","issuer_isin":"DE0007164600","issuer_id":"US0378331005"}
    assert "conflicting_issuer_isin" in role_issues(e)

def test_structural_pass_is_not_document_verification():
    e = {"region":"JP","filer_id":"E1","issuer_id":"E2","filer_name":"Investor",
         "issuer_name":"Issuer","issuer_role_explicit":True,"url":"https://example.org","event_id":"EDINET:1"}
    assert identity_status(e)["identity_status"] == "pending_original_review"
    assert identity_status(e)["identity_verified"] is False
    e["issuer_role_conflict"] = True
    assert identity_status(e)["identity_status"] == "conflict"
