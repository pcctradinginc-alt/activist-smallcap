"""Tests: Preis-Referenzen aus EDINET-Grossbeteiligungsmeldungen und reference_check."""
import json
import pandas as pd
import pytest
from activist.sources import edinet_docs as ed
from activist.sources.prices import reference_check

EL = "jplvh_cor:DetailsOfAcquisitionsAndDisposalsOfStocksEtcIssuedByIssuerOfSaidStocks"
EX1 = ("  （５）【…最近60日間の取得又は処分の状況】            年月日      株券等の種類      数量      割合      市場内外取引の別      取得又は処分の別      単価"
       "              令和４年12月21日      株券（普通株式）      6,279,185      92.22      市場外      取得      5,660円      ")
EX2 = "（５）【…】年月日株券等の種類数量割合市場内外取引の別取得又は処分の別単価2025年11月4日普通株式1,0000.05市場内取得2025年11月6日普通株式1,5000.08市場内取得"
EX3 = ("…単価2026年9月8日株券（普通株式）556,5000.32市場内処分2026年9月8日株券（普通株式）13,5000.01市場外処分60.292026年9月8日新株予約権証券（第70回）"
       "500,0000.28市場外処分新株予約権の行使2026年9月8日株券（普通株式）500,0000.28市場外取得60（新株予約権の行使による取得）2026年9月9日株券（普通株式）1000.01市場内取得")


def row(e, v, ctx="X_FilerLargeVolumeHolder1Member", label=""):
    return {ed.C_ELEM: e, ed.C_LABEL: label, ed.C_CTX: ctx, ed.C_VAL: v}


def test_examples_and_exclusions():
    assert ed._parse_trades(EX1) == [("2022-12-21", 5660.0, "取得")]
    assert ed._parse_trades(EX2) == []
    assert ed._parse_trades(EX3) == [("2026-09-08", 60.29, "処分")]


def test_eras():
    t = "単価{}株券市場外取得100円"
    assert ed._parse_trades(t.format("平成30年3月1日"))[0][0] == "2018-03-01"
    assert ed._parse_trades(t.format("令和元年5月1日"))[0][0] == "2019-05-01"
    assert ed._parse_trades(t.format("２０２５年１１月４日"))[0][0] == "2025-11-04"


def test_parse_price_refs_avg_dedupe_ignore():
    rows = [row(EL, EX1), row(EL, EX1, "X_FilerLargeVolumeHolder2Member"), row(EL, "－"),
            row("jplvh_cor:TotalAmountOfFundingForAcquisition", "1,000,000"),
            row("jplvh_cor:TotalAmountOfFundingForAcquisition", "3,000,000", "X_FilerLargeVolumeHolder2Member"),
            row("jplvh_cor:TotalAmountOfFundingForAcquisition", "9,000,000", "FilingDateInstant"),
            row("jplvh_cor:TotalNumberOfStocksEtcHeld", "400"),
            row("jplvh_cor:TotalNumberOfStocksEtcHeld", "600", "X_FilerLargeVolumeHolder2Member"),
            row("jplvh_cor:TotalNumberOfStocksEtcHeld", "99999", "FilingDateInstant"),
            row("jplvh_cor:BaseDate", "2024-01-31", "FilingDateInstant")]
    r = ed.parse_price_refs(rows)
    assert r == {"trades": [("2022-12-21", 5660.0, "取得")], "avg_cost": 4000.0, "base_date": "2024-01-31"}
    assert ed.parse_price_refs([])["avg_cost"] is None


def px(vals, start="2024-01-01"):
    return pd.DataFrame({"close": vals}, index=pd.bdate_range(start, periods=len(vals)))


def test_reference_check():
    p = px([100.0] * 10)
    d = "2024-01-05"
    assert reference_check(p, {"trades": [(d, 105.0, "取得")]})[0] is True
    ok, msg = reference_check(p, {"trades": [(d, 200.0, "取得")]})
    assert ok is None and "nicht bestätigt" in msg
    assert reference_check(p, {"trades": [(d, 200.0, "取得")], "avg_cost": 110.0, "base_date": d}) == (True, "EDINET-Einstand: Ø-Kaufkurs passt")
    assert reference_check(p, {"trades": [], "avg_cost": None, "base_date": None})[0] is None
    ok, msg = reference_check(px([100, 100, 400, 400]), {"trades": []})
    assert ok is False and "Faktor 3" in msg and "2024-01-03" in msg
    assert reference_check(p, {"error": "x" * 300}) == (None, "EDINET: " + "x" * 150)


def test_price_refs_cache(tmp_path, monkeypatch):
    f = tmp_path / "c.jsonl"
    monkeypatch.setattr(ed, "_REF_FILE", f)
    monkeypatch.setattr(ed, "_REF_CACHE", None)
    monkeypatch.setattr(ed, "_last_req", [float("-inf")])
    calls = []

    def fake(doc, key):
        calls.append(doc)
        if doc == "bad":
            raise RuntimeError(f"HTTP 500 ?Subscription-Key={key}")
        return b"zip"
    monkeypatch.setattr(ed, "fetch_csv", fake)
    monkeypatch.setattr(ed, "read_zip_rows", lambda b: [row(EL, EX1)])
    assert ed.price_refs("d1", "SECRETKEY")["trades"] == [("2022-12-21", 5660.0, "取得")]
    e = ed.price_refs("bad", "SECRETKEY")
    assert "error" in e and "SECRETKEY" not in e["error"]
    ed.price_refs("d1", "SECRETKEY")
    assert calls == ["d1", "bad"]
    txt = f.read_text(encoding="utf-8")
    assert "SECRETKEY" not in txt and len(txt.strip().splitlines()) == 2
    monkeypatch.setattr(ed, "_REF_CACHE", None)   # Neuladen aus Datei
    assert ed.price_refs("d1", "SECRETKEY")["avg_cost"] is None and calls == ["d1", "bad"]


def test_reference_check_split_is_no_jump():
    import pandas as pd
    from activist.sources.prices import reference_check
    idx = pd.date_range("2024-01-01", periods=4)
    px = pd.DataFrame({"close": [100.0, 101, 102, 103], "close_raw": [100.0, 101, 102, 103],
                       "close_unadj": [500.0, 505, 102, 103]}, index=idx)    # Split 1:5 am 3. Tag
    ok, note = reference_check(px, {"trades": [("2024-01-01", 500.0, "取得")]})
    assert ok is True, note
