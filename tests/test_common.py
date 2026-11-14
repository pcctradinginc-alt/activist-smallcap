from datetime import datetime, timezone
from activist.common import effective_decision_time, EventStore, campaign_id, event_id


def test_effective_time_with_time():
    dt = effective_decision_time("2024-01-02T16:30:15-05:00")
    assert dt.utcoffset().total_seconds() == -5 * 3600
    assert dt.hour == 16


def test_effective_time_z_suffix_and_naive():
    assert effective_decision_time("2024-01-02T10:00:00Z") == datetime(2024, 1, 2, 10, tzinfo=timezone.utc)
    assert effective_decision_time("2024-01-02T10:00:00").tzinfo is not None


def test_effective_time_missing_time_is_next_day():
    dt = effective_decision_time(None, "2024-01-02")
    assert dt == datetime(2024, 1, 3, tzinfo=timezone.utc)
    dt = effective_decision_time("", "2024-01-31")
    assert dt == datetime(2024, 2, 1, tzinfo=timezone.utc)


def test_campaign_id_stable_and_normalised():
    assert campaign_id("a ", "b") == campaign_id("A", " B")
    assert campaign_id("a", "b") != campaign_id("b", "a")
    assert event_id("EDGAR", "x") == "EDGAR:x"


def _ev(i, pub="2024-01-02T10:00:00+00:00"):
    return {"event_id": f"EDGAR:{i}", "published": pub}


def test_eventstore_append_only_dedup(tmp_path):
    s = EventStore(tmp_path / "ev")
    assert s.append([_ev(1), _ev(2), _ev(1)]) == 2
    assert s.append([_ev(1), _ev(3)]) == 1
    # neue Instanz sieht vorhandene Ereignisse
    s2 = EventStore(tmp_path / "ev")
    assert s2.append([_ev(1), _ev(2), _ev(3)]) == 0
    assert len(s2.all()) == 3
    lines = (tmp_path / "ev" / "2024-01.jsonl").read_text().splitlines()
    assert len(lines) == 3


def test_eventstore_monthly_files_and_sorting(tmp_path):
    s = EventStore(tmp_path)
    s.append([_ev(1, "2024-02-01T00:00:00+00:00"), _ev(2, "2024-01-15T00:00:00+00:00")])
    assert sorted(p.name for p in tmp_path.glob("*.jsonl")) == ["2024-01.jsonl", "2024-02.jsonl"]
    assert [e["event_id"] for e in s.all()] == ["EDGAR:2", "EDGAR:1"]


def test_eventstore_never_rewrites_existing(tmp_path):
    s = EventStore(tmp_path)
    s.append([_ev(1)])
    before = (tmp_path / "2024-01.jsonl").read_text()
    s.append([dict(_ev(1), extra="changed")])
    assert (tmp_path / "2024-01.jsonl").read_text() == before
