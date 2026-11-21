from datetime import date
from activist.notify import buy

O = {"ticker": "290A", "region": "JP", "signal_class": "B", "activist": "MUFG", "published": "2026-11-16T11:17:00+09:00",
     "demand": "none", "url": "https://x", "campaign_key": "E-290A", "limit_hint": 1141.2, "position_eur": 1000.0,
     "position_eur_local": 162.0, "buy_cost_est": 0.0175, "validated": False, "n_after": 2}
R = {"issuer_name": "Synspective", "form": "LargeHolding", "stake": 0.0519, "demand_evidence": float("nan"), "verified": False}


def test_compose_has_against_and_details():
    subj, text, html = buy.compose(O, R, "JP/B", date(2026, 11, 17))
    assert subj.startswith("[Papier-Kauf] 290A") and "5.2%" in subj
    assert "WAS DAGEGEN SPRICHT" in text and "NICHT bestanden" in text and "Kauflimit 1141.20" in text
    assert "Nicht validiert" in html


def test_notify_never_raises(monkeypatch):
    sent = []
    monkeypatch.setattr(buy, "send", lambda s, t, html=None: sent.append(s) or True)
    assert buy.notify(O, R, "JP/B", date(2026, 11, 17)) and sent
    monkeypatch.setattr(buy, "compose", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    assert buy.notify(O, R, "JP/B", date(2026, 11, 17)) is False
