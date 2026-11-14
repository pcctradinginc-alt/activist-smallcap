"""Offline-Tests fuer sources/eu_de_ir.py (echte, gekuerzte IR-Seiten von Elmos und United Internet + synthetische Seiten)."""
import io, zipfile
from datetime import date
from pathlib import Path
import pytest
import requests
from activist.sources import eu_de_ir as m

FX = Path(__file__).parent / "fixtures" / "eu_de_ir"
ELMOS_LIST = "https://www.elmos.com/ueber-elmos/investor/regulatorische-bekanntmachungen.html"
ELMOS_DWS = "https://www.elmos.com/fileadmin/user_upload/20260604_stimmrechte_dws_d.pdf"
ELMOS_WEYER = "https://www.elmos.com/fileadmin/user_upload/20260608_stimmrechte_weyer_d.pdf"


class Fake:
    """Ersetzt Client._raw: URL -> (status, bytes, content-type). Protokolliert alle Abrufe."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, client, url, cap, **kw):
        self.calls.append(url)
        status, body, ct = self.routes.get(url, (404, b"", "text/html"))
        r = requests.Response()
        r.status_code, r._content, r.url = status, body, url
        r.headers["Content-Type"] = ct
        if status in (301, 302):
            r.headers["Location"] = body.decode()
        return r


def make_client(monkeypatch, routes):
    f = Fake(routes)
    monkeypatch.setattr(m.Client, "_raw", lambda self, url, cap, **kw: f(self, url, cap, **kw))
    c = m.Client(interval=0)
    return c, f


def html(body, title="x"):
    return (f"<html><head><title>{title}</title></head><body>{body}</body></html>").encode()


# ---------------------------------------------------------------- Universum
def _xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    strings = []
    def sid(s):
        if s not in strings:
            strings.append(s)
        return strings.index(s)
    names = list(sheets)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
                   "".join(f'<sheet name="{n}" sheetId="{i+1}" r:id="rId{i+1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                   "".join(f'<Relationship Id="rId{i+1}" Target="worksheets/sheet{i+1}.xml"/>' for i in range(len(names))) + "</Relationships>")
        for i, n in enumerate(names):
            rows = ""
            for ri, row in enumerate(sheets[n], 1):
                cells = "".join(f'<c r="{chr(65+ci)}{ri}" t="s"><v>{sid(v)}</v></c>' for ci, v in enumerate(row) if v)
                rows += f'<row r="{ri}">{cells}</row>'
            z.writestr(f"xl/worksheets/sheet{i+1}.xml", f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>{rows}</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
                   "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
    return buf.getvalue()


def test_parse_universe():
    hdr = ["ISIN", "Trading Symbol", "Company", "Sector", "Subsector", "Country", "Instrument Exchange", "Index", "SDAX"]
    blob = _xlsx({"Cover": [["Report"]],
                  "Prime Standard": [["Companies in Prime Standard"], [], hdr,
                                     ["DE0005677108", "ELG", "ELMOS SEMICONDUCTOR", "Technology", "Semis", "Germany", "XETRA", "", "SDAX"],
                                     ["DE0005677109", "BAD", "Bad checksum", "", "", "Germany"]],
                  "Scale": [hdr, ["DE0005089031", "UTDI", "UNITED INTERNET", "Telecom", "x", "Germany"]],
                  "Basic Board": [hdr, ["NL0010872388", "3SQ1", "AHT", "", "", "Netherlands"]]})
    u = m.parse_universe(blob)
    assert [(x["isin"], x["segment"]) for x in u] == [("DE0005677108", "Prime Standard"), ("DE0005089031", "Scale")]
    assert u[0]["index"] == ["SDAX"] and u[0]["name"] == "ELMOS SEMICONDUCTOR"


def test_parse_wikidata_prefers_short_and_skips_blocked():
    r = {"results": {"bindings": [
        {"isin": {"value": "DE1"}, "site": {"value": "https://www.a.de/de/investor/long"}},
        {"isin": {"value": "DE1"}, "site": {"value": "https://www.a.de/"}},
        {"isin": {"value": "DE2"}, "site": {"value": "https://www.eqs-news.com/x"}},
        {"isin": {"value": "DE3"}}]}}
    assert m.parse_wikidata(r) == {"DE1": "https://www.a.de/"}


# ---------------------------------------------------------------- Blockliste / robots
def test_host_blocked():
    for u in ("https://www.eqs-news.com/x", "https://eqs.com", "https://x.dgap.de/a", "https://www.ots.at/a", "https://www.bafin.de/",
              "https://www.unternehmensregister.de/x"):
        assert m.host_blocked(u)
    assert not m.host_blocked("https://www.elmos.com/") and not m.host_blocked("https://noteqs.com/")


def test_robots_disallow_honored(monkeypatch):
    routes = {"https://a.de/robots.txt": (200, b"User-agent: *\nDisallow: /investor\n", "text/plain"),
              "https://a.de/": (200, html('<a href="/investor/stimmrechte">Stimmrechtsmitteilungen</a>'), "text/html"),
              "https://a.de/investor/stimmrechte": (200, html("<p>secret</p>"), "text/html")}
    c, f = make_client(monkeypatch, routes)
    assert c.allowed("https://a.de/") and not c.allowed("https://a.de/investor/stimmrechte")
    with pytest.raises(m.Skip):
        c.get("https://a.de/investor/stimmrechte")
    assert "https://a.de/investor/stimmrechte" not in f.calls


def test_robots_unreadable_blocks_everything_and_404_allows(monkeypatch):
    c, f = make_client(monkeypatch, {"https://b.de/robots.txt": (403, b"", "text/html"), "https://b.de/": (200, html("x"), "text/html")})
    assert not c.allowed("https://b.de/")
    assert "https://b.de/" not in f.calls
    c2, _ = make_client(monkeypatch, {"https://c.de/": (200, html("x"), "text/html")})      # robots 404 -> erlaubt
    assert c2.allowed("https://c.de/")


def test_blocked_host_never_fetched_even_via_redirect(monkeypatch):
    routes = {"https://a.de/robots.txt": (404, b"", "text/html"), "https://a.de/go": (302, b"https://www.eqs-news.com/x", "text/html")}
    c, f = make_client(monkeypatch, routes)
    with pytest.raises(m.Skip):
        c.get("https://a.de/go")
    assert not any("eqs-news.com" in u for u in f.calls)


# ---------------------------------------------------------------- Listen/Detail (echte Fixtures)
def test_extract_items_elmos_list():
    items = m.extract_items((FX / "elmos_list.html").read_text(), ELMOS_LIST)
    v = [i for i in items if m.VOTING_RE.search(i["title"] + i["url"] + i["context"])]
    assert len(v) >= 20
    by = {i["url"]: i for i in v}
    assert by[ELMOS_DWS]["date"] == "2026-06-04" and by[ELMOS_WEYER]["date"] == "2026-06-08"


def test_extract_items_united_internet_html_list():
    items = m.extract_items((FX / "ui_list.html").read_text(), "https://www.united-internet.de/investor-relations/corporate-governance/stimmrechtsmitteilungen/")
    assert len(items) >= 5 and all(i["url"].endswith("/") and i["date"].startswith("2025-") for i in items)


def test_parse_detail_pdf_de_form():
    from activist.sources.eu_pdf import extract_text
    d = m.parse_detail(extract_text((FX / "elmos_dws.pdf").read_bytes()))
    assert d["filer_name"] == "DWS Investment GmbH" and d["holding_ratio"] == pytest.approx(0.0338) and d["previous_ratio"] == pytest.approx(0.0263)
    assert d["isin"] == "DE0005677108" and d["threshold_date"] == "2026-06-04" and d["total_voting_rights"] == 17_700_000
    assert d["stamp"] == "2026-06-09T11:47:00+02:00" and "Angaben zum Mitteilungsp" in d["chain_text"]


def test_parse_detail_html_en_form():
    d = m.parse_detail(m.html_text((FX / "ui_detail.html").read_text()))
    assert d["filer_name"] == "UBS Group AG" and d["holding_ratio"] == pytest.approx(0.0324) and d["total_voting_rights"] == 192_000_000
    assert d["stamp"] == "2025-06-05T15:48:00+02:00"


def test_find_date_variants():
    assert m.find_date("Stimmrechtsmitteilung vom 03.11.2026") == "2026-11-03"
    assert m.find_date("November 4, 2025") == "2025-11-04" and m.find_date("4. März 2025") == "2025-03-04"
    assert m.find_date_url("/x/voting-rights-notification-24-10-2025.pdf?download=true") == "2025-10-24"
    assert m.find_date_url("/user_upload/20260604_dws.pdf") == "2026-06-04"


def test_voting_re_excludes_proxy_forms():
    assert m.VOTING_RE.search("Stimmrechtsmitteilung") and m.VOTING_RE.search("Voting Rights Announcements")
    assert not m.VOTING_RE.search("Stimmrechtsvertreterformular HV 2024")


# ---------------------------------------------------------------- Event-Bau (Zeitregel)
ISSUER = {"isin": "DE0005677108", "name": "ELMOS SEMICONDUCTOR", "segment": "Prime Standard"}


def test_build_event_stamp_vs_next_day_rule():
    item = {"date": "2026-06-04", "time": "08:00", "title": "Stimmrechtsmitteilung", "url": ELMOS_DWS, "context": ""}
    e = m.build_event(ISSUER, item, {"stamp": "2026-06-09T11:47:00+02:00", "holding_ratio": 0.0338})
    assert e["has_time"] and e["published"].startswith("2026-06-09T11:47") and e["filing_date"] == "2026-06-09"
    e2 = m.build_event(ISSUER, item, {"holding_ratio": 0.03})             # Listen-Uhrzeit wird ignoriert -> Folgetag 00:00 UTC
    assert not e2["has_time"] and e2["published"].startswith("2026-06-05T00:00") and e2["source"] == "DE_IR" and e2["form"] == "MajorHolding"
    assert e2["issuer_id"] == "DE0005677108" and e2["region"] == "DACH"
    e3 = m.build_event(ISSUER, item, {"stamp": "2026-05-01T10:00:00+02:00"})   # Stempel weit vor Listendatum: verworfen
    assert not e3["has_time"]


# ---------------------------------------------------------------- Discovery / EQS-Widget
def site_routes(host, pages, robots=b""):
    r = {f"https://{host}/robots.txt": (200, robots, "text/plain")}
    r.update({f"https://{host}{p}": (200, b, "text/html") for p, b in pages.items()})
    return r


LIST = ('<ul><li><span>05.11.2026</span> <a href="/ir/vr/1.html">Stimmrechtsmitteilung A</a></li>'
        '<li><span>01.11.2026</span> <a href="/ir/vr/2.html">Stimmrechtsmitteilung B</a></li></ul>')


def test_discover_via_ir_navigation(monkeypatch):
    pages = {"/": html('<a href="/investor-relations">Investor Relations</a>'),
             "/investor-relations": html('<a href="/ir/aktie">Aktie</a>'),
             "/ir/aktie": html('<a href="/ir/aktie/stimmrechtsmitteilungen">Stimmrechtsmitteilungen</a>'),
             "/ir/aktie/stimmrechtsmitteilungen": html(LIST)}
    c, _ = make_client(monkeypatch, site_routes("x.de", pages))
    r = m.discover("https://x.de", c)
    assert r["status"] == "ok" and r["ir_urls"] == ["https://x.de/ir/aktie/stimmrechtsmitteilungen"] and not r["eqs_widget"]


def test_discover_eqs_widget_recorded_and_not_fetched(monkeypatch):
    pages = {"/": html('<a href="/investor-relations/voting-rights">Voting Rights</a>'),
             "/investor-relations/voting-rights": html('<iframe src="https://www.eqs-news.com/widget/abc"></iframe>')}
    c, f = make_client(monkeypatch, site_routes("y.de", pages))
    r = m.discover("https://y.de", c)
    assert r["status"] == "eqs_widget" and r["eqs_widget"] and r["ir_urls"]
    assert not any("eqs" in u for u in f.calls)
    assert m.detect_eqs_widget('<script src="https://eqs.com/w.js"></script>') and not m.detect_eqs_widget('<a href="https://www.eqs-news.com/x">link</a>')


def test_discover_robots_blocked_homepage(monkeypatch):
    c, f = make_client(monkeypatch, {"https://z.de/robots.txt": (200, b"User-agent: *\nDisallow: /\n", "text/plain"), "https://z.de/": (200, html("x"), "text/html")})
    r = m.discover("https://z.de", c)
    assert r["status"] == "robots_blocked" and not r["robots_ok"] and "https://z.de/" not in f.calls


def test_discover_sitemap_fallback(monkeypatch):
    sm = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://s.de/de/stimmrechtsmitteilungen</loc></url><url><loc>https://s.de/de/kontakt</loc></url></urlset>'
    routes = site_routes("s.de", {"/": html("<p>nothing</p>"), "/de/stimmrechtsmitteilungen": html(LIST)})
    routes["https://s.de/sitemap.xml"] = (200, sm, "application/xml")
    c, _ = make_client(monkeypatch, routes)
    r = m.discover("https://s.de", c)
    assert r["status"] == "ok" and r["ir_urls"] == ["https://s.de/de/stimmrechtsmitteilungen"]


# ---------------------------------------------------------------- Ende-zu-Ende + Abdeckung
def test_extract_events_end_to_end(monkeypatch):
    routes = {"https://www.elmos.com/robots.txt": (404, b"", "text/html"),
              ELMOS_LIST: (200, (FX / "elmos_list.html").read_bytes(), "text/html"),
              ELMOS_DWS: (200, (FX / "elmos_dws.pdf").read_bytes(), "application/pdf"),
              ELMOS_WEYER: (200, (FX / "elmos_weyer.pdf").read_bytes(), "application/pdf")}
    c, f = make_client(monkeypatch, routes)
    evs = m.extract_events(ISSUER, [ELMOS_LIST], c, date(2026, 6, 1), date(2026, 6, 10))
    assert {e["url"] for e in evs} == {ELMOS_DWS, ELMOS_WEYER}
    e = next(e for e in evs if e["url"] == ELMOS_WEYER)
    assert e["filer_name"].startswith("Dr. Weyer") and e["holding_ratio"] == pytest.approx(0.1502) and e["previous_ratio"] == pytest.approx(0.1456)
    assert e["total_voting_rights"] == 17_160_000 and e["issuer_id"] == "DE0005677108" and "Weyer Beteiligungsgesellschaft" in e["chain_text"]
    assert e["published"].startswith("2026-06-09T11:38")


def test_coverage():
    recs = [{"website": "https://a", "robots_ok": True, "ir_urls": ["u"], "eqs_widget": False, "events": [{"filing_date": "2026-01-02", "holding_ratio": 0.05}]},
            {"website": "https://b", "robots_ok": False, "ir_urls": [], "eqs_widget": False, "events": []},
            {"website": "https://c", "robots_ok": True, "ir_urls": ["u"], "eqs_widget": True, "events": []},
            {"website": None, "robots_ok": None, "ir_urls": [], "eqs_widget": False, "events": []}]
    assert m.coverage(recs) == {"issuers": 4, "with_website": 3, "robots_ok": 2, "ir_page_found": 2, "eqs_widget": 1, "issuers_with_notifications": 1,
                                "notifications_parsed": 1, "notifications_total": 1, "oldest_notification_date": "2026-01-02"}


def test_process_issuer_no_website(monkeypatch):
    c, _ = make_client(monkeypatch, {})
    assert m.process_issuer(ISSUER, None, c, date(2026, 1, 1), date(2026, 2, 1))["status"] == "no_website"


# ---------------------------------------------------------------- Handelskalender + konservative Veroeffentlichungsregel
def test_easter_and_holidays():
    assert m.easter(2026) == date(2026, 4, 5) and m.easter(2025) == date(2025, 4, 20) and m.easter(2024) == date(2024, 3, 31)
    h = m.de_holidays(2026)
    assert h == {date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6), date(2026, 5, 1), date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 26), date(2026, 12, 31)}
    assert not m.is_trading_day(date(2026, 4, 3)) and not m.is_trading_day(date(2026, 6, 6)) and m.is_trading_day(date(2026, 6, 5))
    assert m.is_trading_day(date(2026, 6, 4))                                   # Fronleichnam ist kein Boersenfeiertag in der Regel


def test_add_trading_days_skips_weekend_and_holidays():
    assert m.add_trading_days(date(2026, 6, 4), 7) == date(2026, 6, 15)          # Do + 7 HT = Mo
    assert m.add_trading_days(date(2026, 4, 1), 3) == date(2026, 4, 8)           # Mi: Do, (Karfreitag/Sa/So/Ostermontag frei), Di, Mi
    assert m.add_trading_days(date(2026, 12, 23), 2) == date(2026, 12, 29)       # 24./25./26. frei, 27./28. WE -> Mo 28 ... Di 29
    assert m.add_trading_days(date(2026, 6, 4), 0) == date(2026, 6, 4)


def test_conservative_publication_rule():
    # Schwelle Do 04.06. + 7 HT = Mo 15.06. liegt nach Listendatum + 1 -> Schwellenregel
    assert m.conservative_publication("2026-06-05", "2026-06-04") == ("2026-06-15", "threshold_date+7td")
    # Listendatum spaeter als Schwelle + 7 HT -> Listendatum + 1 Tag
    assert m.conservative_publication("2026-07-01", "2026-06-04") == ("2026-07-02", "list_date+1d")
    # ohne Schwellendatum -> Listendatum + 1
    assert m.conservative_publication("2026-06-05", None) == ("2026-06-06", "list_date+1d")


def test_build_event_uses_conservative_rule_without_stamp():
    item = {"date": "2026-06-05", "time": None, "title": "Stimmrechtsmitteilung", "url": ELMOS_DWS, "context": ""}
    e = m.build_event(ISSUER, item, {"threshold_date": "2026-06-04", "holding_ratio": 0.05})
    assert not e["has_time"] and e["published"] == "2026-06-15T00:00:00+00:00" and e["publication_rule"] == "threshold_date+7td" and e["filing_date"] == "2026-06-05"
    e2 = m.build_event(ISSUER, item, {"holding_ratio": 0.05})
    assert e2["published"].startswith("2026-06-06T00:00") and e2["publication_rule"] == "list_date+1d"
    e3 = m.build_event(ISSUER, item, {"stamp": "2026-06-09T11:47:00+02:00", "threshold_date": "2026-06-04"})
    assert e3["has_time"] and e3["publication_rule"] == "eqs_stamp" and e3["published"].startswith("2026-06-09T11:47")


# ---------------------------------------------------------------- Regression: Singer/Elliott, Prime Standard
SINGER_TEXT = """Release according to Article 40, Section 1 of the WpHG
Example Prime AG
Example Prime AG: Release according to Article 40, Section 1 of the WpHG [the German Securities Trading Act] with the objective of Europe-wide distribution
05.06.2026 / 15:48 CET/CEST
Dissemination of a Voting Rights Announcement transmitted by EQS News - a service of EQS Group.
Notification of Major Holdings
1. Details of issuer
Name:
Example Prime AG
Legal Entity Identifier (LEI):
3VEKWPJHTD4NKMBVG947
2. Reason for notification
X
Acquisition/disposal of instruments
3. Details of person subject to the notification obligation
Natural person
Surname:
Singer
First name:
Paul E.
Date of birth:
4. Names of shareholder(s)
holding directly 3% or more voting rights, if different from 3.
Elliott Associates, L.P.
Elliott International, L.P.
5. Date on which threshold was crossed or reached:
03 Jun 2026
6. Total positions
% of voting rights attached to shares
% of voting rights through instruments
Total of both in %
Total number of voting rights pursuant to Sec. 41 WpHG
New
2.1 %
3.4 %
5.5 %
10000000
Previous notification
1.0 %
0.5 %
1.5 %
/
7. Details on total positions
a. Voting rights attached to shares (Sec. 33, 34 WpHG)
ISIN
DE0005677108
"""


def test_singer_elliott_regression_end_to_end():
    from activist.study import dataset_eu
    from activist.sources import eu_de
    d = eu_de.parse_notification(SINGER_TEXT)
    assert d["filer_name"] == "Paul E. Singer" and d["holding_ratio"] == pytest.approx(0.055) and d["isin"] == "DE0005677108"
    det = m.parse_detail(SINGER_TEXT)
    assert "Elliott Associates, L.P." in det["chain_text"] and det["stamp"] == "2026-06-05T15:48:00+02:00"
    item = {"date": "2026-06-05", "time": None, "title": "Stimmrechtsmitteilung", "url": "https://example.de/vr/1", "context": ""}
    ev = m.build_event({"isin": "DE0005677108", "name": "Example Prime AG", "segment": "Prime Standard"}, item, det)
    assert ev["region"] == "DACH" and ev["has_time"] and ev["filer_name"] == "Paul E. Singer" and ev["holding_ratio"] == pytest.approx(0.055)
    camps = dataset_eu.eu_campaigns([ev])
    assert len(camps) == 1 and camps[0]["issuer_id"] == "DE0005677108"


# ---------------------------------------------------------------- Kuratierung, SDAX-Abdeckung, js_only
def test_curated_url_used_first_and_sdax_coverage(tmp_path):
    cur = {"DE0005545503": {"name": "1&1 AG", "index": ["SDAX"], "voting_rights_url": "https://x.de/vr", "status": "ok", "verified": "2026-11-14", "note": "n"},
           "DE0005677108": {"name": "ELMOS", "index": ["SDAX"], "voting_rights_url": None, "status": "eqs_widget", "verified": "2026-11-14", "note": "w"},
           "DE000A1": {"name": "Other", "index": ["MDAX"], "voting_rights_url": None, "status": "none_found"}}
    p = tmp_path / "c.yaml"
    m.save_curated(cur, p)
    assert m.load_curated(p) == cur
    issuers = [{"isin": "DE0005545503", "name": "1&1 AG", "index": ["SDAX"]}, {"isin": "DE0005677108", "name": "ELMOS", "index": ["SDAX"]},
               {"isin": "DE000A3H2200", "name": "Neu AG", "index": ["SDAX"]}, {"isin": "DE000A1", "name": "Other", "index": ["MDAX"]}]
    rows = m.sdax_coverage(issuers, cur)
    assert {r["isin"]: r["status"] for r in rows} == {"DE0005545503": "ok", "DE0005677108": "eqs_widget", "DE000A3H2200": "uncurated"}
    assert sorted(r["isin"] for r in m.sdax_coverage(None, cur)) == ["DE0005545503", "DE0005677108"]


def test_process_issuer_prefers_curated_url(monkeypatch):
    routes = {"https://a.de/robots.txt": (404, b"", "text/html"), "https://a.de/vr": (200, html(LIST.replace("/ir/vr/", "https://a.de/vr/")), "text/html")}
    c, f = make_client(monkeypatch, routes)
    cur = {ISSUER["isin"]: {"voting_rights_url": "https://a.de/vr", "status": "ok"}}
    rec = m.process_issuer(ISSUER, "https://a.de", c, date(2026, 11, 1), date(2026, 11, 30), None, cur)
    assert rec["curated"] and rec["ir_urls"] == ["https://a.de/vr"] and f.calls.count("https://a.de/") == 0


def test_js_only_page_rendered_when_static_empty(monkeypatch):
    from activist.sources import js_render
    shell = html("<div id='app'></div>")
    routes = {"https://j.de/robots.txt": (404, b"", "text/html"), "https://j.de/vr": (200, shell, "text/html"),
              "https://j.de/ir/vr/1.html": (200, html("<p>Stimmrechtsmitteilung</p>"), "text/html")}
    c, _ = make_client(monkeypatch, routes)
    rendered = LIST.replace("/ir/vr/", "https://j.de/ir/vr/")
    monkeypatch.setattr(js_render, "render_html", lambda url, client, **kw: "<html><body>" + rendered + "</body></html>")
    evs = m.extract_events(ISSUER, ["https://j.de/vr"], c, date(2026, 11, 1), date(2026, 11, 30), render=True)
    assert len(evs) == 2
    assert m.extract_events(ISSUER, ["https://j.de/vr"], c, date(2026, 11, 1), date(2026, 11, 30), render=False) == []


def test_js_render_skips_blocked_and_robots(monkeypatch):
    from activist.sources import js_render
    c, f = make_client(monkeypatch, {"https://k.de/robots.txt": (200, b"User-agent: *\nDisallow: /vr\n", "text/plain")})
    for u in ("https://www.eqs-news.com/w", "https://k.de/vr"):
        with pytest.raises(m.Skip):
            js_render.render_html(u, c)


def test_js_render_real_chromium(tmp_path):
    from activist.sources import js_render
    if not js_render.available():
        pytest.skip("playwright nicht installiert")
    import http.server, threading, functools
    (tmp_path / "p.html").write_text("<html><body><ul id=l></ul><script>document.getElementById('l').innerHTML='<li>05.11.2026 <a href=\"/a\">Stimmrechtsmitteilung</a></li>'</script></body></html>")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        c = m.Client(interval=0)
        try:
            out = js_render.render_html(f"http://127.0.0.1:{srv.server_address[1]}/p.html", c)
        except m.Skip as e:
            if "Rendering-Fehler" in str(e):
                pytest.skip(str(e))
            raise
        assert "Stimmrechtsmitteilung" in out and m.extract_items(out, "http://127.0.0.1/p.html")
    finally:
        srv.shutdown()
