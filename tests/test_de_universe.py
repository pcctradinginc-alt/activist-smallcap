import io, zipfile
from pathlib import Path
from activist.sources import de_universe as u

FX = Path(__file__).parent / "fixtures" / "campaigns_c"


def _xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    """Minimaler XLSX mit gleicher Struktur wie 'Listed-companies.xlsx' (sharedStrings, Zellreferenzen)."""
    sst, names = [], []
    def si(s):
        if s not in sst:
            sst.append(s)
        return sst.index(s)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        wb, rels = [], []
        for n, (name, rows) in enumerate(sheets.items(), 1):
            xml = []
            for ri, row in enumerate(rows, 3):
                cs = "".join(f'<c r="{chr(66 + ci)}{ri}" t="s"><v>{si(v)}</v></c>' for ci, v in enumerate(row) if v != "")
                xml.append(f'<row r="{ri}">{cs}</row>')
            z.writestr(f"xl/worksheets/sheet{n}.xml", f'<worksheet {ns}><sheetData>{"".join(xml)}</sheetData></worksheet>')
            wb.append(f'<sheet name="{name}" sheetId="{n}" r:id="rId{n}"/>')
            rels.append(f'<Relationship Id="rId{n}" Target="worksheets/sheet{n}.xml"/>')
        z.writestr("xl/workbook.xml", f'<workbook {ns} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{"".join(wb)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{"".join(rels)}</Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in sst) + "</sst>")
    return b.getvalue()


HEAD = ["ISIN", "Trading Symbol", "Company", "Sector", "Subsector", "Country"]


def test_xlsx_listed_companies_segments_and_regulated():
    data = _xlsx({
        "Cover": [["Report: Listed Companies"]],
        "Prime Standard": [["Companies in Prime Standard"], HEAD,
                           ["DE0005545503", "1U1", "1&amp;1 AG ", "T", "W", "Germany"],
                           ["LU1250154413", "ADJ", "ADLER GROUP S.A.", "R", "R", "Luxembourg"], ["XX", "", "bad"]],
        "General Standard": [HEAD, ["DE0005167902", "UUU", "3U HOLDING AG", "I", "I", "Germany"]],
        "Scale": [HEAD, ["DE000A3E5A59", "ABC", "SCALE CO AG", "I", "I", "Germany"]],
        "Basic Board": [HEAD, ["DE0005214506", "ADC", "ADCAPITAL AG", "I", "I", "Germany"]],
    })
    rows = u.parse_listed_companies(u.read_xlsx(data))
    seg = {r["isin"]: (r["segment"], r["regulated"]) for r in rows}
    assert seg["DE0005545503"] == ("Prime Standard", True) and seg["DE0005167902"] == ("General Standard", True)
    assert seg["DE000A3E5A59"] == ("Scale", False) and seg["DE0005214506"] == ("Basic Board", False)
    assert [r["name"] for r in rows][0] == "1&1 AG" and seg["LU1250154413"][0] == "Prime Standard"
    assert len(rows) == 5 and rows[1]["country"] == "LU"


def test_maccess_real_excerpt():
    rows = u.parse_maccess((FX / "maccess_excerpt.html").read_text(encoding="utf8"))
    assert len(rows) >= 5 and rows[0]["isin"] == "DE0005760029" and rows[0]["name"].startswith("ABO Energy")
    assert all(r["segment"] == "m:access" and r["regulated"] is False for r in rows)


def test_t7_real_excerpt_only_cs_de_and_merge_priority():
    t7 = u.parse_t7_csv((FX / "t7_xfra_excerpt.csv").read_text(encoding="utf8"))
    assert {r["isin"] for r in t7} == {"DE000A2NBX80", "DE000A2NB601", "DE000A2NB650", "DE000A2N4H07"}   # Anleihe fehlt
    assert all(r["segment"] == "Open Market" and not r["regulated"] for r in t7)
    listed = [{"isin": "DE000A2NBX80", "name": "INSTONE REAL ESTATE GROUP SE", "segment": "Prime Standard", "regulated": True, "country": "DE"},
              {"isin": "NL0000238145", "name": "AD PEPPER", "segment": "Prime Standard", "regulated": True, "country": "NL"},
              {"isin": "AT0000A18XM4", "name": "AMS", "segment": "Prime Standard", "regulated": True, "country": "AT"}]
    m = u.merge(listed, [], t7)
    by = {r["isin"]: r for r in m}
    assert by["DE000A2NBX80"]["segment"] == "Prime Standard" and by["DE000A2NBX80"]["regulated"] is True   # Börsensegment gewinnt
    assert by["DE000A2NB650"]["segment"] == "Open Market" and "NL0000238145" not in by and "AT0000A18XM4" in by
    assert u.counts(m) == {"Prime Standard": 2, "Open Market": 3}


def test_yahoo_from_listings_gy_gr_then_gf():
    L = lambda e, t: {"exch": e, "ticker": t}
    assert u.yahoo_from_listings([L("GF", "SAP"), L("GY", "SAP")]) == "SAP.DE"
    assert u.yahoo_from_listings([L("GR", "BMW"), L("GF", "BMW")]) == "BMW.DE"
    assert u.yahoo_from_listings([L("GF", "ABC")]) == "ABC.F"
    assert u.yahoo_from_listings([L("LN", "ABC")]) is None and u.yahoo_from_listings([]) is None


def test_norm_name_strips_legal_forms():
    assert u.norm_name("INSTONE REAL EST.GRP O.N.") == "instone real est grp"
    assert u.norm_name("Mutares SE & Co. KGaA") == "mutares"
    assert u.norm_name("Müller – Die lila Logistik AG") == "muller die lila logistik"
