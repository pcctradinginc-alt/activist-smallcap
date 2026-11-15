import pandas as pd


def test_mixed_iso_timestamps_parse():
    s = pd.Series(["2025-04-03T11:44:05.368307+02:00", "2024-01-02T09:00:00+00:00"])
    d = pd.to_datetime(s, utc=True, format="ISO8601")
    assert d.notna().all()
