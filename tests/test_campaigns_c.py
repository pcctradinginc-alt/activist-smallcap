from datetime import date
from pathlib import Path
import pytest
from activist.sources import campaigns_c as c

FX = Path(__file__).parent / "fixtures" / "campaigns_c"
CFG = c.load_sites()
SITE = {s["activist"]: s for s in CFG["sites"]}
UNI = [
    {"isin": "DE000ZAL1111", "name": "ZALANDO SE", "segment": "Prime Standard", "regulated": True, "yahoo_ticker": "ZAL.DE"},
    {"isin": "DE0007664039", "name": "VOLKSWAGEN AG VZO O.N.", "segment": "Prime Standard", "regulated": True, "yahoo_ticker": "VOW3.DE"},
    {"isin": "DE000A2NB650", "name": "MUTARES SE & CO. KGAA", "segment": "Prime Standard", "regulated": True},
    {"isin": "DE0005408116", "name": "Allerthal-Werke AG", "segment": "General Standard", "regulated": True},
    {"isin": "DE0005190003", "name": "BAYERISCHE MOTOREN WERKE AG", "segment": "Prime Standard", "regulated": True},
    {"isin": "DE000A1A6V48", "name": "Bauer AG", "segment": "Prime Standard", "regulated": True},
    {"isin": "DE0006305006", "name": "Dialog Semiconductor", "segment": "Prime Standard", "regulated": True},
]
M = c.Matcher(UNI)


def test_sites_yaml_valid_and_blocked_hosts_not_used():
    assert CFG["sites"] and set(CFG["skip_hosts"]) >= {"eqs-news.com", "eqs.com", "dgap.de", "ots.at"}
    for s in CFG["sites"]:
        urls = [s.get("url"), *s.get("pages", [])]
        assert all(u and not c._blocked(u, CFG) for u in urls if u)
        assert s["kind"] in ("index", "feed", "pages") and s["type"] in ("activist", "association", "holding")
    assert SITE["SdK"]["type"] == "association"
    assert c._blocked("https://www.eqs-news.com/x", CFG) and c._blocked("https://ots.at/a", CFG) and not c._blocked("https://sdk.org", CFG)


def test_sdk_index_real_fixture():
    items = c.parse_index_html((FX / "sdk_index.html").read_text(encoding="utf8"), SITE["SdK"]["url"], SITE["SdK"])
    assert len(items) == 15 and items[0]["date"] == "2026-10-23"          # Datum aus dem stats-Block, nicht aus dem Fließtext (14. Oktober)
    assert items[0]["title"].startswith("SdK erzielt Erfolg beim Volkswagen") and items[0]["url"].startswith("https://sdk.org/veroeffentlichungen/")
    assert all(i["date"] for i in items)


def test_scherzer_index_real_fixture_date_from_url():
    items = c.parse_index_html((FX / "scherzer_index.html").read_text(encoding="utf8"), SITE["Scherzer & Co."]["url"], SITE["Scherzer & Co."])
    assert len(items) == 10 and items[0]["date"] == "2026-11-02"
    assert any("allerthal" in i["url"] and i["date"] == "2026-10-15" for i in items)


def test_balaton_feed_real_fixture_has_time():
    items = c.parse_feed((FX / "balaton_feed.xml").read_text(encoding="utf8"))
    assert len(items) == 15 and items[0]["date"] == "2026-07-01" and items[0]["time"].startswith("2026-07-01T15:15")


def test_elliottletters_only_own_host_links():
    items = c.parse_index_html((FX / "elliottletters_home.html").read_text(encoding="utf8"), "https://elliottletters.com/", SITE["Elliott Investment Management"])
    assert {i["url"] for i in items} == {"https://elliottletters.com/nst", "https://elliottletters.com/daikin-industries/"}


def test_real_sdk_item_zalando_event():
    title, text = c.html_text((FX / "sdk_item_zalando.html").read_text(encoding="utf8"))
    assert "Zalando" in text
    it = {"url": "https://sdk.org/x/zalando/", "title": "SdK begrüßt Bafin-Prüfung bei Zalando SE", "date": "2026-06-26", "time": None}
    site = dict(SITE["SdK"], require_demand=False)
    ev = c.build_event(site, it, it["title"], text, M)
    assert ev["issuer_id"] == "DE000ZAL1111" and ev["source"] == "ACTIVIST_SITE" and ev["form"] == "PublicCampaign"
    assert ev["signal_class"] == "C" and ev["region"] == "DACH" and ev["filer_name"] == "SdK" and ev["match_where"] == "title"
    assert ev["published"] == "2026-06-27T00:00:00+00:00" and ev["has_time"] is False         # Folgetag-Regel
    assert ev["match_evidence"] and ev["event_id"].startswith("ACTIVIST_SITE:")


def test_feed_time_used_for_published():
    it = {"url": "http://x/a", "title": "Aufruf an Mutares SE & Co. KGaA: Aktienrückkauf jetzt", "date": "2026-07-01", "time": "2026-07-01T15:15:00+00:00"}
    ev = c.build_event({"activist": "Deutsche Balaton", "name": "Deutsche Balaton AG", "type": "holding"}, it, it["title"], "", M)
    assert ev["published"] == "2026-07-01T15:15:00+00:00" and ev["has_time"] is True and ev["demand"] == "capital_return"


# ---------------------------------------------------------------- Matching synthetisch
def test_isin_and_exact_name_and_fuzzy():
    assert M.find("", "Wir fordern bei DE0005190003 Änderungen", set())[0]["how"] == "isin"
    h = M.find("Brief an den Aufsichtsrat der Volkswagen AG", "", set())
    assert h and h[0]["row"]["isin"] == "DE0007664039" and h[0]["where"] == "title" and h[0]["how"] == "exact_name"
    f = M.find("Brief an Bayerische Motoren Werke Aktiengesellschaft AG", "", set())      # kein exakter Treffer -> fuzzy
    h = M.find("Aufruf an die Bayrische Motoren Werke AG", "", set())
    assert h and h[0]["how"] == "fuzzy_name" and h[0]["score"] >= c.FUZZY and h[0]["row"]["isin"] == "DE0005190003"


def test_negatives_no_target_generic_words_and_self():
    assert M.find("Anlegerforen im März", "Wir veranstalten Anlegerforen. Das Bauer-Wetter war schön, der Dialog geht weiter.", set()) == []
    assert M.find("Neue bauer entwicklung", "", set()) == []                                 # Einwortname nur großgeschrieben
    assert M.find("Bauer informiert", "", set()) != []
    assert M.find("Das Bauer Wetter", "", set()) != [] and M.find("das bauer wetter", "", set()) == []
    assert M.find("Zalando SE Jahreszahlen", "", {c.norm_name("Zalando SE")}) == []         # Aktivist selbst ausgeschlossen
    assert M.find("Mutaresque Sonderfall AG", "", set()) == []                               # kein Teilstring-Match


def test_build_event_drops_weak_text_only_single_mention_and_no_demand_when_required():
    site = {"activist": "SdK", "name": "SdK", "type": "association", "require_demand": True}
    it = {"url": "http://x/1", "title": "Allgemeine Meldung", "date": "2026-05-01", "time": None}
    assert c.build_event(site, it, it["title"], "Am Rande wurde Zalando SE erwähnt.", M) is None          # 1 Textnennung
    assert c.build_event(site, it, "Zalando SE", "Nur ein Forum.", M) is None                            # keine Forderung
    ev = c.build_event(site, it, "Zalando SE", "Die SdK stellt ein Gegenantrag zur Wahl des Aufsichtsrats.", M)
    assert ev["demand"] == "board" and "Gegenantrag" in ev["demand_evidence"]


def test_classify_german_and_english_priority():
    assert c.classify("We urge the board to explore strategic alternatives including a sale of the company.")[0] == "sale"
    assert c.classify("Wir fordern einen Aktienrückkauf und eine höhere Dividende.")[0] == "capital_return"
    assert c.classify("Wir beantragen die Abwahl des Aufsichtsrats.")[0] == "board"
    assert c.classify("Wetterbericht und Sonnenschein.") == ("none", None)


def test_find_date_formats():
    assert c.find_date("am 23. Oktober 2026 um") == "2026-10-23" and c.find_date("(26.06.2026)") == "2026-06-26"
    assert c.find_date("October 5, 2023") == "2023-10-05" and c.find_date("x 2024-03-09 y") == "2024-03-09" and c.find_date("kein Datum") is None


def test_fetch_events_offline_with_monkeypatched_http(monkeypatch):
    pages = {
        "http://www.deutsche-balaton.de/feed/": (FX / "balaton_feed.xml").read_text(encoding="utf8"),
        "http://www.deutsche-balaton.de/news/mutares/": "<html><title>t</title><body><article>Deutsche Balaton fordert von der Mutares SE & Co. KGaA einen Aktienrückkauf. Mutares muss Kapital zurückgeben.</article></body></html>",
    }
    class R:
        def __init__(s, t): s.text, s.content, s.headers = t, t.encode(), {"Content-Type": "text/html"}
    def fake_fetch(url, cfg, delay=0.0):
        return R(pages[url]) if url in pages else None
    monkeypatch.setattr(c, "fetch", fake_fetch)
    feed = pages["http://www.deutsche-balaton.de/feed/"].replace(
        "http://www.deutsche-balaton.de/news/deutsche-balaton-ag-ungepruefte-steigerung-des-auf-die-anteilseigner-entfallenden-konzerneigenkapitals-ifrs-2025-um-rd-1309-mio-euro/",
        "http://www.deutsche-balaton.de/news/mutares/")
    pages["http://www.deutsche-balaton.de/feed/"] = feed
    cfg = {"skip_hosts": CFG["skip_hosts"], "sites": [{k: v for k, v in SITE["Deutsche Balaton"].items() if k != "exclude_title_re"}]}
    stats = {}
    evs = c.fetch_events(date(2026, 1, 1), date(2026, 11, 13), cfg, UNI, stats=stats)
    assert len(evs) == 1 and evs[0]["issuer_id"] == "DE000A2NB650" and evs[0]["demand"] == "capital_return"
    assert stats["Deutsche Balaton"]["events"] == 1


def test_self_exclusion_fuzzy_title_filter_and_pref_share():
    uni = UNI + [{"isin": "DE0006942808", "name": "SCHERZER U. CO. AG O.N.", "segment": "General Standard", "regulated": True},
                 {"isin": "DE0007664005", "name": "VOLKSWAGEN AG ST O.N.", "segment": "Prime Standard", "regulated": True}]
    m = c.Matcher(uni)
    site = dict(SITE["Scherzer & Co."])
    it = {"url": "http://x/s", "title": "Scherzer & Co. AG: Net Asset Value zum 30.09.2026", "date": "2026-10-01", "time": None}
    assert c.build_event(site, it, it["title"], "Scherzer & Co. AG DE0006942808 Mutares SE & Co. KGaA Mutares", m) is None      # Titel-Filter
    it["title"] = "Scherzer & Co. fordert Verkauf der Mutares SE & Co. KGaA"
    ev = c.build_event(site, it, it["title"], "Mutares sollte verkauft werden. Strategische Alternativen prüfen.", m)
    assert ev["issuer_id"] == "DE000A2NB650" and ev["demand"] == "sale"                    # nicht Scherzer selbst
    assert m.find("Volkswagen AG Hauptversammlung", "", set())[0]["row"]["isin"] == "DE0007664005"
