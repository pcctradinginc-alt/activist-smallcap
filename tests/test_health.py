import json
from datetime import datetime, timedelta, timezone
import pytest
from activist import cli, health


def test_record_run_rotation(tmp_path):
    p = tmp_path / "h.json"
    for i in range(65):
        health.record_run(f"r{i}", {"edgar": {"ok": True, "n_new": i, "error": None}}, p)
    runs = json.loads(p.read_text())
    assert len(runs) == 60 and runs[-1]["name"] == "r64" and runs[0]["name"] == "r5"
    assert runs[-1]["sources"]["edgar"] == {"ok": True, "n_new": 64, "error": None}


def test_status_without_files(tmp_path):
    s = health.status(tmp_path / "h.json", tmp_path / "e_*.json", tmp_path / "freeze.json")
    assert s["alpha"]["status"] == "kein Bericht"
    assert s["errors"] == ["Protokoll nicht eingefroren"]
    assert s["learning"] == {"closed_campaigns": 0, "needed": 200, "status": "zu wenig Daten"}
    assert s["data_quality"]["verified_share"] is None


def test_status_with_study_and_failing_source(tmp_path):
    (tmp_path / "freeze.json").write_text("{}")
    (tmp_path / "event_study_1.json").write_text(json.dumps({
        "data_quality": {"verified_share": 0.9, "n_events": 10, "n_campaigns_valid": 5, "n_campaigns_unverified": 1},
        "alpha": {"frozen": True, "status": "nachgewiesen", "passed": ["US/A"],
                  "holdout": {"US/A": {"status": "bestanden", "mean": 0.05, "p_holm": 0.01, "n": 80}}}}))
    h = tmp_path / "h.json"
    health.record_run("a", {"edinet": {"ok": False, "n_new": 0, "error": "timeout"}, "edgar": {"ok": True, "n_new": 1}}, h)
    s = health.status(h, tmp_path / "event_study_*.json", tmp_path / "freeze.json")
    assert s["alpha"]["status"] == "nachgewiesen" and s["data_quality"]["verified_share"] == 0.9
    assert s["errors"] == ["edinet: timeout"]
    health.record_run("b", {"edinet": {"ok": True, "n_new": 2}}, h)   # zuletzt erfolgreich -> geklärt
    assert health.status(h, tmp_path / "event_study_*.json", tmp_path / "freeze.json")["errors"] == []


@pytest.mark.parametrize("alpha,exp", [
    ({"frozen": True, "status": "nicht nachgewiesen", "holdout": {"US/A": {"status": "nicht bestanden"}}}, "nicht nachgewiesen"),
    ({"frozen": False, "status": "nicht beurteilbar", "holdout": {}}, "nicht beurteilbar"),
    ({}, "nicht beurteilbar")])
def test_alpha_status_from_study(tmp_path, alpha, exp):
    (tmp_path / "event_study_1.json").write_text(json.dumps({"alpha": alpha}))
    s = health.status(tmp_path / "h.json", tmp_path / "event_study_*.json", tmp_path / "f.json")
    assert s["alpha"]["status"] == exp


def _rec(p, name, srcs, ts):
    p.write_text(json.dumps([{"ts": ts.isoformat(timespec="seconds"), "name": name,
                              "sources": {k: {"ok": ok, "n_new": 0, "error": None if ok else "x"} for k, ok in srcs.items()}}]))


@pytest.mark.parametrize("src,exp", [("EDGAR", "FAILED"), ("nsm", "DEGRADED")])
def test_stale_source(tmp_path, src, exp):
    now = datetime(2026, 1, 20, tzinfo=timezone.utc)
    h = tmp_path / "h.json"
    _rec(h, "a", {src: True, "tage": False}, now - timedelta(days=6))
    st = health.status(h, tmp_path / "e_*.json", tmp_path / "f.json", now=now)
    assert st["overall"] == exp and st["sources"][src]["stale"] and not st["sources"][src]["ok"]
    assert "tage" not in st["sources"] and f"{src}: veraltet (letzter Lauf 2026-01-14)" in st["errors"]
    _rec(h, "a", {src: True}, now - timedelta(days=1))
    st = health.status(h, tmp_path / "e_*.json", tmp_path / "f.json", now=now)
    assert st["overall"] == "OK" and not st["sources"][src]["stale"]


def test_gate():
    ok, bad = {"ok": True, "ts": "", "error": None, "stale": False}, {"ok": False, "ts": "", "error": "boom", "stale": False}
    assert health.gate({"sources": {"EDGAR": ok}}, ["EDGAR", "EDINET"], []) == ["EDINET: nie gelaufen"]
    assert health.gate({"sources": {"EDGAR": bad}}, ["EDGAR"], []) == ["EDGAR: boom"]
    assert health.gate({"sources": {"a": ok, "b": bad}}, [], ["a", "b", "c"]) == ["nur 1 von 3 Quellen erfolgreich"]
    assert health.gate({"sources": {"a": ok, "b": ok, "c": bad}}, [], ["a", "b", "c"]) == []
    assert health.gate({"sources": {}}, [], []) == []


def test_per_name_files_and_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(health, "REPORTS", tmp_path)
    health.record_run("fetch", {"EDGAR": {"ok": True}})
    health.record_run("eu", {"six": {"ok": False, "error": "x"}})
    assert (tmp_path / "health_fetch.json").exists() and (tmp_path / "health_eu.json").exists()
    st = health.status(study_json_glob=tmp_path / "e_*.json", freeze_path=tmp_path / "f.json")
    assert set(st["sources"]) == {"EDGAR", "six"} and st["overall"] == "DEGRADED"


def test_cmd_health_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(health, "REPORTS", tmp_path)
    monkeypatch.setattr(health, "ROOT", tmp_path)
    with pytest.raises(SystemExit) as e:
        cli.main(["health", "--core", "EDGAR"])
    assert e.value.code == 1 and "::error::EDGAR" in capsys.readouterr().out
    health.record_run("fetch", {"EDGAR": {"ok": True}})
    cli.main(["health", "--core", "EDGAR"])
    assert "Gesamtstatus: OK" in capsys.readouterr().out
