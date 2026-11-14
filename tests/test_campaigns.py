from activist.parse import campaigns


def ev(i, form, pub, filer="777", issuer="555", source="EDGAR"):
    return {"event_id": f"{source}:{i}", "source": source, "region": "US", "form": form,
            "published": pub, "filer_id": filer, "issuer_id": issuer, "has_time": True}


def test_13g_then_13d_is_switch_class_a_first_only():
    events = [
        ev(2, "SC 13D", "2024-03-01T10:00:00+00:00"),
        ev(1, "SC 13G", "2024-01-01T10:00:00+00:00"),
        ev(3, "SC 13D/A", "2024-04-01T10:00:00+00:00"),
    ]
    out = campaigns.assign(events)
    assert [e["event_id"] for e in out] == ["EDGAR:1", "EDGAR:2", "EDGAR:3"]
    g, d, a = out
    assert len({e["campaign_id"] for e in out}) == 1
    assert g["event_type"] == "passive"
    assert d["event_type"] == "switch_13g_13d" and d["signal_class"] == "A"
    assert a["event_type"] == "amendment"
    assert [e["is_first_event"] for e in out] == [False, True, False]
    assert a["signal_class"] == "A"  # Klasse wird vererbt, aber nicht erstes Ereignis


def test_initial_13d_without_prior():
    out = campaigns.assign([ev(1, "SCHEDULE 13D", "2024-01-01T00:00:00+00:00", filer="999")])
    assert out[0]["event_type"] == "initial_13d"
    assert out[0]["signal_class"] == "A"
    assert out[0]["is_first_event"] is True


def test_different_issuers_are_separate_campaigns():
    out = campaigns.assign([
        ev(1, "SC 13D", "2024-01-01T00:00:00+00:00", issuer="1"),
        ev(2, "SC 13D", "2024-01-02T00:00:00+00:00", issuer="2"),
    ])
    assert out[0]["campaign_id"] != out[1]["campaign_id"]
    assert all(e["is_first_event"] for e in out)


def test_passive_known_activist_is_class_b_unknown_none():
    known = campaigns.assign([ev(1, "SC 13G", "2024-01-01T00:00:00+00:00", filer="0001517137")])[0]
    assert known["filer_is_activist"] is True and known["signal_class"] == "B"
    unknown = campaigns.assign([ev(2, "SC 13G", "2024-01-01T00:00:00+00:00", filer="42")])[0]
    assert unknown["signal_class"] is None and unknown["is_first_event"] is False


def test_known_activist_13g_first_fixes_class_b_first_event_rule():
    """Erstes klassifiziertes Ereignis bestimmt die Klasse: 13G eines bekannten Aktivisten (B) vor 13D (A)."""
    out = campaigns.assign([
        ev(1, "SC 13G", "2024-01-01T10:00:00+00:00", filer="1517137"),
        ev(2, "SC 13D", "2024-03-01T10:00:00+00:00", filer="1517137"),
    ])
    assert out[0]["is_first_event"] and out[0]["signal_class"] == "B"
    assert out[1]["event_type"] == "switch_13g_13d"
    assert not out[1]["is_first_event"] and out[1]["signal_class"] == "B"
