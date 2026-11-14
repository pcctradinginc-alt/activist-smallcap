from activist.sources import edgar, edinet

FORM_IDX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------
SC 13D                Alpha Widgets Inc                                             1111111     20240102    edgar/data/1111111/0001111111-24-000001.txt
SCHEDULE 13D/A        Beta Corp                                                     2222222     20240102    edgar/data/2222222/0002222222-24-000002.txt
10-K                  Gamma Holdings                                                3333333     20240102    edgar/data/3333333/0003333333-24-000003.txt
SC 13G                Delta Co  Ltd                                                 4444444     20240102    edgar/data/4444444/0004444444-24-000004.txt
"""

HEADER = """<SEC-HEADER>0001111111-24-000001.hdr.sgml : 20240102
<ACCEPTANCE-DATETIME>20240102163015
ACCESSION NUMBER:\t\t0001111111-24-000001
CONFORMED SUBMISSION TYPE:\tSC 13D
SUBJECT COMPANY:

\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\tAlpha Widgets Inc
\t\tCENTRAL INDEX KEY:\t\t\t0001111111

FILED BY:

\tCOMPANY DATA:
\t\tCOMPANY CONFORMED NAME:\t\t\tStarboard Value LP
\t\tCENTRAL INDEX KEY:\t\t\t0001517137
</SEC-HEADER>
"""


def test_parse_form_index_filters_and_formats():
    rows = edgar.parse_form_index(FORM_IDX)
    forms = [r["form"] for r in rows]
    assert forms == ["SC 13D", "SCHEDULE 13D/A", "SC 13G"]
    assert "10-K" not in forms
    r = rows[0]
    assert r["date"] == "2024-01-02"
    assert r["cik"] == "1111111"
    assert r["company"] == "Alpha Widgets Inc"
    assert r["path"].endswith("0001111111-24-000001.txt")
    assert rows[2]["company"] == "Delta Co  Ltd"


def test_parse_form_index_ignores_rows_before_dashes_and_empty():
    assert edgar.parse_form_index("") == []
    before = "SC 13D   X Corp   1   20240102   a.txt\n"
    assert edgar.parse_form_index(before) == []


def test_parse_header():
    h = edgar.parse_header(HEADER)
    assert h["published"] == "2024-01-02T16:30:15-05:00"
    assert h["subject_name"] == "Alpha Widgets Inc"
    assert h["subject_cik"] == "0001111111"
    assert h["filer_name"] == "Starboard Value LP"
    assert h["filer_cik"] == "0001517137"
    assert h["accession"] == "0001111111-24-000001"


def test_parse_header_summer_time_offset():
    h = edgar.parse_header(HEADER.replace("20240102163015", "20240702163015"))
    assert h["published"].endswith("-04:00")


def test_parse_header_missing_time():
    h = edgar.parse_header("ACCESSION NUMBER: 0001-24-000001\n")
    assert h["published"] is None
    assert h["subject_name"] is None and h["filer_name"] is None


def _doc(**kw):
    d = {"docID": "S100AAAA", "docTypeCode": "350", "withdrawalStatus": "0",
         "submitDateTime": "2024-03-05 15:30", "filerName": "Oasis", "edinetCode": "E1",
         "issuerEdinetCode": "E999", "docDescription": "大量保有報告書"}
    d.update(kw)
    return d


def test_edinet_parse_documents_mix():
    res = [
        _doc(docID="D1", docTypeCode="350"),
        _doc(docID="D2", docTypeCode="360"),
        _doc(docID="D3", docTypeCode="120"),                  # Wertpapierbericht: ignoriert
        _doc(docID="D4", docTypeCode="350", withdrawalStatus="1"),  # zurückgezogen
        _doc(docID="D5", docTypeCode="350", withdrawalStatus=None),
    ]
    out = edinet.parse_documents(res)
    assert [e["event_id"] for e in out] == ["EDINET:D1", "EDINET:D2", "EDINET:D5"]
    assert out[0]["form"] == "LargeHolding"
    assert out[1]["form"] == "LargeHoldingAmendment"
    assert out[0]["published"] == "2024-03-05T15:30:00+09:00"
    assert out[0]["has_time"] is True
    assert out[0]["region"] == "JP"
    assert out[0]["issuer_id"] == "E999"
    assert out[0]["filer_id"] == "E1"


def test_edinet_date_only_submit():
    out = edinet.parse_documents([_doc(submitDateTime="2024-03-05")])
    assert out[0]["filing_date"] == "2024-03-05"
