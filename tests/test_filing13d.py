from pathlib import Path
import pytest
from activist.parse import filing13d as f
from activist.parse.filing13d import parse_text, classify_demand

FX = Path(__file__).parent / "fixtures" / "13d"


def load(name):
    return parse_text((FX / name).read_text(errors="replace"))


# ---------------------------------------------------------------- Fixtures (echte Einreichungen)

def test_legacy_cannell_2016_html_plain():
    r = load("legacy_cannell_2016.txt")
    assert r["percent_of_class"] == 7.32 and r["shares"] == 1389953
    assert r["item4_text"].startswith("Mr. Cannell") and len(r["item4_text"]) <= 4000
    assert "<" not in r["item4_text"] and "&#" not in r["item4_text"]
    assert r["demand"] == "strategy" and "strategic initiatives" in r["demand_evidence"]
    assert 8.0 < r["avg_purchase_price"] < 8.7      # VWAP aus Item 5(c)-Tabelle
    assert r["is_exit"] is False


def test_legacy_tybourne_2019_hedged_language_is_none_and_prose_price():
    r = load("legacy_tybourne_2019.txt")
    assert r["percent_of_class"] == 11.06
    assert r["shares"] == 1957961                    # "1,957, 961" mit Leerzeichen
    assert r["demand"] == "none" and r["demand_evidence"] is None   # nur "may from time to time"-Vorbehalt
    assert r["avg_purchase_price"] == 14.0
    assert r["item4_text"].startswith("The Reporting Persons acquired")


def test_new_xml_13d_tego_2026():
    r = load("new_13d_tego_2026.txt")
    assert r["percent_of_class"] == 8.9 and r["shares"] == 2373134
    assert r["demand"] == "board"
    assert "Reconstitute the Board" in r["demand_evidence"]
    assert 0 < len(r["item4_text"]) <= 4000 and "<" not in r["item4_text"]
    assert r["avg_purchase_price"] is None and r["is_exit"] is False


def test_new_xml_13da_allen_2026():
    r = load("new_13da_allen_2026.txt")
    assert r["percent_of_class"] == 61.8 and r["shares"] == 64498356
    assert r["item4_text"].startswith("Item 4 of the Schedule 13D is hereby amended")
    assert r["demand"] == "none" and r["demand_evidence"] is None
    assert r["is_exit"] is False


def test_new_xml_13da_without_item4():
    r = load("new_13da_aifu_noitem4_2026.txt")
    assert r["item4_text"] == "" and r["demand"] == "none" and r["demand_evidence"] is None
    assert r["percent_of_class"] == 6.67 and r["shares"] == 4826333
    assert r["avg_purchase_price"] is None


# ---------------------------------------------------------------- Demand-Klassen (synthetisch)

@pytest.mark.parametrize("sentence,cls", [
    ("The Reporting Person urges the Board to explore strategic alternatives, including a sale of the Company.", "sale"),
    ("We submitted an acquisition proposal to the Board.", "sale"),
    ("The Reporting Person will request a merger discussion.", "sale"),
    ("We want the Company to adopt a share buyback program.", "capital_return"),
    ("The Company should repurchase shares and initiate a dividend.", "capital_return"),
    ("We urge the Company to return capital to shareholders.", "capital_return"),
    ("The Reporting Person intends to nominate two director candidates.", "board"),
    ("The Reporting Person seeks board representation.", "board"),
    ("The Reporting Person requested two board seats.", "board"),
    ("We believe operational improvements and a spin-off of the segment are needed.", "strategy"),
    ("The Company must reduce its cost base.", "strategy"),
    ("The Reporting Person acquired the shares for investment purposes.", "none"),
])
def test_demand_classes(sentence, cls):
    d, ev = classify_demand(sentence)
    assert d == cls
    assert (ev == sentence) if cls != "none" else ev is None


def test_priority_sale_over_capital_return_over_board_over_strategy():
    s = "Nominate directors. Improve strategy and cost. Start a buyback. Explore a sale of the company."
    assert classify_demand(s)[0] == "sale"
    s = "Nominate directors. Improve strategy and cost. Start a buyback."
    assert classify_demand(s)[0] == "capital_return"
    s = "Nominate directors. Improve strategy and cost."
    assert classify_demand(s)[0] == "board"
    assert classify_demand("Improve strategy.")[0] == "strategy"


BOILERPLATE = (
    "Except as set forth herein, the Reporting Persons have no present plans or proposals which relate to or would "
    "result in: (a) the acquisition by any person of additional securities of the Issuer; (b) an extraordinary "
    "corporate transaction, such as a merger, reorganization or liquidation; (c) a sale or transfer of a material "
    "amount of assets; (d) any change in the present board of directors or management; (e) any material change in "
    "the present capitalization or dividend policy; (f) any other material change in the Issuer's business."
)


def test_boilerplate_negation_is_none():
    assert classify_demand(BOILERPLATE) == ("none", None)
    assert classify_demand("Item 4. Purpose of Transaction\n\n" + BOILERPLATE)[0] == "none"


def test_boilerplate_plus_real_demand_outside_list():
    d, ev = classify_demand(BOILERPLATE + "\n\nThe Reporting Person will propose a special dividend to the Board.")
    assert d == "capital_return" and "special dividend" in ev and "no present plans" not in ev


def test_reservation_of_rights_is_not_a_demand():
    assert classify_demand("The Reporting Persons may from time to time discuss a merger or nominate directors.")[0] == "none"
    assert classify_demand("The Reporting Persons reserve the right to propose a sale of the company.")[0] == "none"


# ---------------------------------------------------------------- Prozent, Exit, Preis, Format (synthetisch)

LEGACY = """<SEC-DOCUMENT><DOCUMENT><TYPE>SC 13D/A<TEXT><html><body>
<p>Aggregate Amount Beneficially Owned by Each Reporting Person</p><p>1,000,000</p>
<p>Percent of Class Represented by Amount in Row (11)</p><p>%PCT%%</p>
<p><b>Item 4.&nbsp;Purpose of Transaction</b></p>
<p>The Reporting Person&#146;s ask: the Board should pursue a buyback &amp; cut costs.</p>
<p>Item 5. Interest in Securities of the Issuer</p>
<p>(c) Transactions in the last 60 days:</p>
<table><tr><td>Date</td><td>Qty</td><td>Price</td><td>Type</td></tr>
<tr><td>05/01/2023</td><td>1,000</td><td>10.00</td><td>Buy</td></tr>
<tr><td>05/10/2023</td><td>3,000</td><td>$12.00</td><td>Buy</td></tr>
<tr><td>05/11/2023</td><td>9,000</td><td>99.00</td><td>Sell</td></tr>
<tr><td>01/10/2023</td><td>5,000</td><td>1.00</td><td>Buy</td></tr></table>
<p>Item 6. Contracts</p></body></html></TEXT></DOCUMENT></SEC-DOCUMENT>"""


def test_legacy_html_synthetic_vwap_window_and_entities():
    r = parse_text(LEGACY.replace("%PCT%", "6.1"))
    assert r["percent_of_class"] == 6.1 and r["shares"] == 1_000_000
    assert r["demand"] == "capital_return" and "buyback & cut costs" in r["demand_evidence"]
    assert "<" not in r["item4_text"] and "&amp;" not in r["item4_text"]
    # (1000*10 + 3000*12)/4000 = 11.5 ; Verkauf und >60 Tage alte Zeile ausgeschlossen
    assert r["avg_purchase_price"] == 11.5


@pytest.mark.parametrize("pct,exit_", [("4.9", True), ("5.0", False), ("0.0", True)])
def test_exit_by_percent(pct, exit_):
    assert parse_text(LEGACY.replace("%PCT%", pct))["is_exit"] is exit_


def test_exit_by_text_without_percent():
    t = "Item 4. Purpose of Transaction\nNone.\nItem 5. Interest in Securities of the Issuer\nThe Reporting Person ceased to be the beneficial owner of more than five percent of the class.\nItem 6. x"
    r = parse_text(t)
    assert r["percent_of_class"] is None and r["is_exit"] is True


def test_empty_and_garbage_input():
    r = parse_text("")
    assert r["percent_of_class"] is None and r["shares"] is None and r["demand"] == "none"
    assert r["item4_text"] == "" and r["is_exit"] is False and r["avg_purchase_price"] is None


def test_item4_truncated_to_4000():
    t = "Item 4. Purpose of Transaction\n" + ("Words about nothing. " * 500) + "\nItem 5. Interest in Securities of the Issuer\nx"
    assert len(parse_text(t)["item4_text"]) == 4000


def test_xml_synthetic_multiple_persons_takes_max():
    t = ("<edgarSubmission><reportingPersonInfo><aggregateAmountOwned>100.00</aggregateAmountOwned><percentOfClass>3.0</percentOfClass></reportingPersonInfo>"
         "<reportingPersonInfo><aggregateAmountOwned>900.00</aggregateAmountOwned><percentOfClass>9.0</percentOfClass></reportingPersonInfo>"
         "<item4><transactionPurpose>We will nominate directors &amp; seek a sale of the company.</transactionPurpose></item4></edgarSubmission>")
    r = parse_text(t)
    assert (r["percent_of_class"], r["shares"], r["demand"]) == (9.0, 900.0, "sale")
    assert "&amp;" not in r["item4_text"] and r["is_exit"] is False


def test_fetch_and_parse_uses_edgar_get(monkeypatch):
    from activist.sources import edgar
    calls = []

    class R:
        text = LEGACY.replace("%PCT%", "7")

    monkeypatch.setattr(edgar, "_get", lambda url: calls.append(url) or R())
    r = f.fetch_and_parse("https://www.sec.gov/Archives/edgar/data/1/x.txt")
    assert calls == ["https://www.sec.gov/Archives/edgar/data/1/x.txt"]
    assert r["percent_of_class"] == 7.0


def test_item4_standard_list_item_alone_is_not_a_demand():
    from activist.parse.filing13d import classify_demand
    txt = ("The Reporting Persons acquired the shares for investment purposes. "
           "(b) An extraordinary corporate transaction, such as a merger, reorganization or liquidation, "
           "involving the Issuer or any of its subsidiaries; "
           "(d) Any change in the present board of directors or management of the Issuer.")
    assert classify_demand(txt)[0] == "none"


def test_real_demand_still_detected_next_to_list():
    from activist.parse.filing13d import classify_demand
    txt = ("(b) An extraordinary corporate transaction, such as a merger. "
           "The Reporting Persons believe the Board should immediately explore a sale of the Company.")
    assert classify_demand(txt)[0] == "sale"
