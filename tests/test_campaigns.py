from activist.parse import campaigns


def test_known_activists_point_in_time():
    now = campaigns.known_activists()
    assert now and all(isinstance(k, str) for k in now)
    assert len(campaigns.known_activists("1900-01-01")) <= len(now)


def test_summary_counts():
    evs = [{"region": "US", "source": "EDGAR", "form": "SC 13D", "has_time": True},
           {"region": "US", "source": "EDGAR", "form": "SC 13G"},
           {"region": "JP", "source": "EDINET", "form": "120", "has_time": False}]
    s = campaigns.summary(evs)
    assert s["n"] == 3 and s["by_region"] == {"US": 2, "JP": 1}
    assert s["by_source"]["EDGAR"] == 2 and s["by_form"]["SC 13D"] == 1 and s["no_time"] == 2
    assert campaigns.summary([])["n"] == 0
