import pandas as pd
from activist.study import dataset


def ev(i, pub, filer, sec, ip, ratio=0.06, form="LargeHolding", name="X Fund"):
    return {"event_id": f"EDINET:{i}", "source": "EDINET", "region": "JP", "form": form, "published": pub,
            "filer_id": filer, "filer_name": name, "issuer_sec_code": sec, "important_proposal": ip,
            "holding_ratio": ratio, "doc_parsed": True, "total_shares_outstanding": 1e7, "url": "u"}


def test_jp_class_a_from_important_proposal_and_exit_followup():
    evs = [ev(1, "2023-01-05T10:00:00+09:00", "E1", "1234", True),
           ev(2, "2023-03-05T10:00:00+09:00", "E1", "1234", False, 0.04, "LargeHoldingChange")]
    out = dataset.jp_campaigns(evs)
    assert len(out) == 1 and out[0]["signal_class"] == "A" and out[0]["ticker"] == "1234"
    assert out[0]["followups"][0]["holding_ratio"] == 0.04


def test_jp_pure_investment_non_activist_ignored_and_behavioural_b():
    evs = [ev(10 + k, f"2023-0{k+1}-01T10:00:00+09:00", "E9", f"10{k}0", True) for k in range(3)]
    evs += [ev(20, "2023-06-01T10:00:00+09:00", "E9", "2000", False),     # B: 3 frühere A-Meldungen
            ev(21, "2023-06-01T10:00:00+09:00", "E7", "3000", False)]     # reiner Investor -> nichts
    out = {c["campaign_key"]: c["signal_class"] for c in dataset.jp_campaigns(evs)}
    assert out["E9-2000"] == "B" and "E7-3000" not in out


def test_jp_listed_activist_by_name_is_b():
    evs = [ev(30, "2023-06-01T10:00:00+09:00", "E5", "4000", False, name="Oasis Management Company Ltd.")]
    assert dataset.jp_campaigns(evs)[0]["signal_class"] == "B"


def test_build_jp_lot_size_rule(monkeypatch):
    idx = pd.bdate_range("2022-11-01", "2024-03-01")
    px = pd.DataFrame({"open": 5000.0, "high": 5050.0, "low": 4950.0, "close": 5000.0, "volume": 1e6}, index=idx)
    monkeypatch.setattr(dataset.prices, "load", lambda *a, **k: (px, {"ok": None}))
    monkeypatch.setattr(dataset.prices, "yahoo", lambda *a, **k: pd.DataFrame({"close": 160.0}, index=idx))
    ds, f = dataset.build_jp([ev(1, "2023-01-05T10:00:00+09:00", "E1", "1234", True)], end=pd.Timestamp("2024-03-01").date())
    # 100 Stück x 5.000 JPY = 500.000 JPY > 1.000 EUR x 160 = 160.000 JPY
    assert ds.iloc[0]["status"].startswith("verworfen") and "Mindestorder" in ds.iloc[0]["status"]
