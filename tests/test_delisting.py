import json
from pathlib import Path
import pytest
from activist.sources import delisting as dl

FIX = Path(__file__).parent / "fixtures" / "delisting"


class Net:
    def __init__(self):
        self.subs, self.pages, self.docs, self.calls = {}, {}, {}, []

    def add(self, cik, name, rows, docs=None):
        """rows: (date, form, accession, items); docs: accession -> html text."""
        rows = sorted(rows, reverse=True)
        self.subs[str(cik).zfill(10)] = {"name": name, "filings": {"recent": {
            "form": [r[1] for r in rows], "filingDate": [r[0] for r in rows],
            "accessionNumber": [r[2] for r in rows], "items": [r[3] if len(r) > 3 else "" for r in rows],
            "primaryDocument": [r[2] + ".htm" for r in rows]}, "files": []}}
        self.docs.update(docs or {})

    def get_json(self, url):
        self.calls.append(url)
        if "-submissions-" in url:
            return self.pages[url.rsplit("/", 1)[1]]
        return self.subs[url.rsplit("CIK", 1)[1][:10]]

    def get_text(self, url):
        self.calls.append(url)
        acc = url.rsplit("/", 2)[1]
        for k, v in self.docs.items():
            if k.replace("-", "") == acc:
                return v
        raise AssertionError("unexpected doc " + url)


@pytest.fixture
def net(monkeypatch):
    n = Net()
    dl._SUB_CACHE.clear()
    monkeypatch.setattr(dl, "_get_json", n.get_json)
    monkeypatch.setattr(dl, "_get_text", n.get_text)
    yield n
    dl._SUB_CACHE.clear()


@pytest.fixture
def fixnet(monkeypatch):
    """Real SEC fixtures (trimmed submissions + original filing documents)."""
    dl._SUB_CACHE.clear()

    def gj(url):
        name = url.rsplit("/", 1)[1]
        if "-submissions-001" in name:
            return json.loads((FIX / "sub_1087423_p1.json").read_text())
        return json.loads((FIX / f"sub_{int(name[3:13])}.json").read_text())

    def gt(url):
        acc = url.rsplit("/", 2)[1]
        acc = f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"
        p = FIX / f"{acc}.htm"
        if not p.exists():
            raise FileNotFoundError(p)
        return p.read_text(errors="ignore")
    monkeypatch.setattr(dl, "_get_json", gj)
    monkeypatch.setattr(dl, "_get_text", gt)
    yield
    dl._SUB_CACHE.clear()


# ------------------------------------------------------------ real fixtures
def test_red_hat_cash_merger_with_paged_submissions(fixnet):
    r = dl.terminal_outcome(1087423, "2019-05-01")
    assert r["type"] == "cash_merger" and r["cash_per_share"] == 190.0 and r["stock_ratio"] is None
    assert r["date"] == "2019-07-09" and r["form"] == "8-K" and r["filing"] == "0000950142-19-001516"
    assert "International Business Machines" in r["acquirer"]
    assert "$190.00" in r["evidence"]


def test_rightside_tender_offer(fixnet):
    r = dl.terminal_outcome("0001589094", "2017-05-01")
    assert r["type"] == "cash_merger" and r["cash_per_share"] == 10.60
    assert r["date"] == "2017-07-28" and r["acquirer"] == "Donuts Inc."


def test_achaogen_bankruptcy_not_confused_by_asset_sale_2_01(fixnet):
    r = dl.terminal_outcome(1301501, "2019-03-01")
    assert r["type"] == "bankruptcy" and r["cash_per_share"] is None
    assert r["date"] == "2019-04-15" and r["filing"] == "0001564590-19-011576"
    assert "Chapter 11" in r["evidence"]


def test_pershing_stock_merger_uses_stated_completion_date(fixnet):
    r = dl.terminal_outcome(1432196, "2018-09-01")
    assert r["type"] == "stock_merger" and r["stock_ratio"] == 0.715 and r["cash_per_share"] is None
    assert r["date"] == "2019-04-03"          # 8-K filed 04-09, text says 04-03
    assert r["acquirer"] == "Americas Silver Corporation"


def test_tender_docs_alone_from_fixtures(fixnet):
    t = dl._html_text((FIX / "0001193125-17-214767.htm").read_text())
    assert dl.parse_consideration(t)["cash"] == 10.60       # par value $0.0001 per share must be ignored


# ------------------------------------------------------------ synthetic types
def doc(body):
    return f"<html><body><p>{body}</p></body></html>"


def test_mixed(net):
    net.add(5, "TARGET CORP", [("2020-03-01", "25-NSE", "a-1", ""), ("2020-03-01", "8-K", "a-2", "2.01,3.01")],
            {"a-2": doc("Item 2.01. On March 1, 2020, Big Buyer Inc. (“Parent”) completed its acquisition of Target Corp. "
                        "Each share was converted into the right to receive $12.50 in cash, without interest, and 0.25 shares of Parent common stock.")})
    r = dl.terminal_outcome(5, "2020-01-01")
    assert (r["type"], r["cash_per_share"], r["stock_ratio"]) == ("mixed", 12.5, 0.25)
    assert r["acquirer"] == "Big Buyer Inc." and r["date"] == "2020-03-01"


def test_offer_increase_uses_latest_amount(net):
    net.add(6, "TARGET CORP", [
        ("2021-05-01", "SC TO-T", "t-1", ""), ("2021-05-20", "SC TO-T/A", "t-2", ""),
        ("2021-06-10", "SC TO-T/A", "t-3", ""), ("2021-06-15", "8-K", "t-4", "2.01"), ("2021-06-15", "25", "t-5", "")],
        {"t-1": doc("Tender offer by Acme Holdings, Inc. to purchase all shares at a price of $10.00 per Share, net to the seller in cash."),
         "t-2": doc("The Offer Price has been increased from $10.00 to $11.50 per Share in cash."),
         "t-3": doc("The Offer expired. Purchaser accepted all Shares validly tendered."),     # no price -> falls back to t-2
         "t-4": doc("Item 2.01 Completion of Acquisition. See the Introductory Note. Merger Agreement was completed.")})
    r = dl.terminal_outcome(6, "2021-01-01")
    assert r["type"] == "cash_merger" and r["cash_per_share"] == 11.5
    assert r["filing"] == "t-2" and r["form"] == "SC TO-T/A"


def test_going_private_with_13e3(net):
    net.add(7, "SMALL INC", [("2019-02-01", "SC 13E3", "g-1", ""), ("2019-04-01", "8-K", "g-2", "2.01"), ("2019-04-02", "15-12G", "g-3", "")],
            {"g-2": doc("Item 2.01. Pursuant to the Agreement and Plan of Merger each share was converted into the right to receive $3.25 in cash.")})
    r = dl.terminal_outcome(7, "2019-01-01")
    assert r["type"] == "going_private" and r["cash_per_share"] == 3.25


def test_bankruptcy_without_price(net):
    net.add(8, "BROKE INC", [("2022-01-10", "8-K", "b-1", "1.03,2.04")],
            {"b-1": doc("Item 1.03. On January 9, 2022 the Company filed a voluntary petition under Chapter 11.")})
    r = dl.terminal_outcome(8, "2021-12-01")
    assert r["type"] == "bankruptcy" and r["cash_per_share"] is None and r["date"] == "2022-01-10"


def test_deregistered(net):
    net.add(9, "QUIET INC", [("2018-08-01", "15-12G", "d-1", "")])
    r = dl.terminal_outcome(9, "2018-01-01")
    assert r["type"] == "deregistered" and r["date"] == "2018-08-01" and r["form"] == "15-12G"


def test_unknown_cases(net):
    net.add(10, "NOTHING INC", [("2018-08-01", "10-K", "n-1", "")])
    assert dl.terminal_outcome(10, "2018-01-01")["type"] == "unknown"
    assert dl.terminal_outcome(10, "2019-01-01")["type"] == "unknown"       # nothing after cutoff
    # merger signals without any readable price
    net.add(11, "ODD INC", [("2018-08-01", "SC TO-T", "o-1", ""), ("2018-09-01", "15-12B", "o-2", "")],
            {"o-1": doc("Tender offer for all shares.")})
    r = dl.terminal_outcome(11, "2018-01-01")
    assert r["type"] == "unknown" and r["cash_per_share"] is None
    # proxy filed but deal never closed: no completion signal
    net.add(12, "LIVE INC", [("2018-08-01", "DEFM14A", "l-1", "")], {"l-1": doc("right to receive $5.00 in cash")})
    assert dl.terminal_outcome(12, "2018-01-01")["type"] == "unknown"


def test_filings_before_cutoff_ignored(net):
    net.add(13, "OLD INC", [("2015-01-01", "8-K", "x-1", "1.03"), ("2019-01-01", "15-12G", "x-2", "")],
            {"x-1": doc("voluntary petition Chapter 11")})
    assert dl.terminal_outcome(13, "2016-01-01")["type"] == "deregistered"


# ------------------------------------------------------------ parsing / plumbing
def test_parse_consideration_variants():
    p = dl.parse_consideration
    assert p("Each share will be converted into the right to receive $41.00 per share in cash, without interest.")["cash"] == 41.0
    assert p("at an offer price of $1,234.50 per Share, net to the seller")["cash"] == 1234.5
    assert p("holders will receive $7.00 per share, in cash")["cash"] == 7.0
    c = p("each share will be converted into the right to receive 1.2345 shares of Acme common stock")
    assert c["ratio"] == 1.2345 and c["cash"] is None
    assert p("The par value is $0.0001 per share.")["cash"] is None


def test_sleep_between_requests(monkeypatch):
    sleeps = []

    class R:
        text = "x"
        def json(self): return {}
    monkeypatch.setattr(dl.edgar, "_get", lambda url: R())
    monkeypatch.setattr(dl.time, "sleep", lambda s: sleeps.append(s))
    dl._get_json("u"); dl._get_text("u")
    assert sleeps and min(sleeps) >= 0.12
