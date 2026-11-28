import json
from datetime import date
import pytest
from activist import health
from activist.paper import portfolio as pf, report
from activist import common
from activist.study.alpha import _hashes


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    j, r = tmp_path / "journal", tmp_path / "reports"
    j.mkdir(); r.mkdir()
    for mod in (pf, report):
        monkeypatch.setattr(mod, "SHADOW", j / "shadow.csv")
        monkeypatch.setattr(mod, "TRADES", j / "trades.csv")
    monkeypatch.setattr(pf, "STATE", j / "portfolio.json")
    monkeypatch.setattr(pf, "JOURNAL", j)
    monkeypatch.setattr(report, "JOURNAL", j)
    monkeypatch.setattr(common, "JOURNAL", j)
    monkeypatch.setattr(common, "REPORTS", r)
    monkeypatch.setattr(report, "REPORTS", r)
    monkeypatch.setattr(health, "REPORTS", r)
    monkeypatch.setattr(health, "HEALTH", r / "health.json")
    monkeypatch.setattr(health, "ROOT", tmp_path)
    (r / "event_study_x.json").write_text(json.dumps({
        "protocol_hashes": _hashes(),
        "data_quality": {"verified_share": 0.5, "n_events": 12, "n_campaigns_valid": 7, "n_campaigns_unverified": 2}}))
    health.record_run("t", {"edgar": {"ok": False, "n_new": 0, "error": "HTTP 500"}}, r / "health.json")
    return tmp_path


def test_status_in_text_and_html():
    subj, body = report.weekly(date(2024, 3, 1))
    assert body.index("SYSTEMSTATUS") < body.index("DEPOT\n")
    assert "Alpha-Nachweis: NEIN" in body and "edgar: HTTP 500" in body and "7 / 200" in body and "50%" in body
    html = report.weekly_html(date(2024, 3, 1))
    assert "Systemstatus" in html and html.index("Systemstatus") < html.index("Depotwert")
    assert "HTTP 500" in html and "7 / 200" in html


def test_weekly_has_three_required_items():
    _, body = report.weekly(date(2026, 1, 3))
    for label in ("Alpha-Nachweis:", "Champion vs. Challenger:", "Lernstatus:"):
        assert label in body
    html = report.weekly_html(date(2026, 1, 3))
    assert "Champion vs. Challenger" in html and "Lernstatus" in html
