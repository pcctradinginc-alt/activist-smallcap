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


def test_approved_identity_binds_listing_and_price_series_to_original():
    from activist.study.identity import apply_identity

    event = dict(source="EDGAR", region="US", event_id="EDGAR:example", filer_id="200",
                 issuer_id="100", filer_name="An Investor", issuer_name="An Issuer",
                 published="2020-01-03T10:00:00-05:00", url="https://example.org/event")
    record = {**event, "ticker": "ABC", "isin": "US0378331005", "exchange": "NASDAQ",
              "original": {"url": event["url"], "sha256": "1" * 64, "excerpt": "original"},
              "listing": {"url": "https://example.org/listing", "sha256": "2" * 64,
                          "excerpt": "listing", "published": "2020-01-02"},
              "price_series": {"url": "https://example.org/series", "sha256": "3" * 64,
                               "excerpt": "series", "valid_from": "2019-01-01", "valid_through": "2021-01-03"}}

    def approved(e=event, ticker="ABC", records=None):
        rows = {e["event_id"]: records if records is not None else [record]}
        return apply_identity({"ticker": ticker}, e, rows)

    assert approved() == record
    assert approved(ticker="REUSED") is None
    assert approved({**event, "issuer_id": "777"}) is None
    assert approved({**event, "published": "2020-01-02T10:00:00-05:00"}) is None
    assert approved(records=[record, record]) is None
    assert approved(records=[{**record, "price_series": {**record["price_series"], "valid_through": "2019-12-31"}}]) is None
