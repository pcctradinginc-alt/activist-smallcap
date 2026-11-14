from activist.study import dataset_eu


def ev(filer, chain="", pub="2024-03-01T08:00:00+00:00"):
    return {"event_id": "NSM:1", "source": "NSM", "region": "UK", "form": "MajorHolding", "published": pub,
            "has_time": True, "filer_name": filer, "issuer_id": "GB00BD7XPJ64", "issuer_isin": "GB00BD7XPJ64",
            "holding_ratio": 0.051, "chain_text": chain}


def test_elliott_found_via_paul_singer_as_notifier():
    out = dataset_eu.eu_campaigns([ev("Paul E. Singer")])
    assert len(out) == 1 and out[0]["signal_class"] == "B"


def test_elliott_found_via_control_chain():
    chain = "Paul E. Singer | Elliott Capital Advisors, L.P. | Elliott Associates, L.P. | 4.2%"
    assert len(dataset_eu.eu_campaigns([ev("Some Holding GP LLC", chain)])) == 1


def test_unrelated_filer_not_matched():
    assert dataset_eu.eu_campaigns([ev("Morgan Stanley", "Morgan Stanley | Morgan Stanley & Co. International plc")]) == []
