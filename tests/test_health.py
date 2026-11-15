import json
from activist import health


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
        "alpha": {"frozen": True, "holdout": {"US/A": {"status": "nachgewiesen", "mean": 0.05, "p_holm": 0.01, "n": 80}}}}))
    h = tmp_path / "h.json"
    health.record_run("a", {"edinet": {"ok": False, "n_new": 0, "error": "timeout"}, "edgar": {"ok": True, "n_new": 1}}, h)
    s = health.status(h, tmp_path / "event_study_*.json", tmp_path / "freeze.json")
    assert s["alpha"]["status"] == "nachgewiesen" and s["data_quality"]["verified_share"] == 0.9
    assert s["errors"] == ["edinet: timeout"]
    health.record_run("b", {"edinet": {"ok": True, "n_new": 2}}, h)   # zuletzt erfolgreich -> geklärt
    assert health.status(h, tmp_path / "event_study_*.json", tmp_path / "freeze.json")["errors"] == []
