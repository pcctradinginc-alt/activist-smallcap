import pandas as pd
import pytest
from activist.features.register import Register


def ev(form, pub, filer, issuer):
    return {"form": form, "published": pub, "filer_id": filer, "issuer_id": issuer}


def serial(filer="0000999", n=3, base="2020-01"):
    return [ev("SC 13D", f"{base}-0{i+1}T10:00:00+00:00", filer, str(100 + i)) for i in range(n)]


def test_config_activist_respects_since():
    r = Register([])
    assert r.is_activist("0001517137", "2024-01-01T00:00:00+00:00")        # Starboard since 2011
    assert r.is_activist("1517137", "2024-01-01T00:00:00+00:00")
    assert not r.is_activist("0001871408", "2020-01-01T00:00:00+00:00")     # Politan since 2021
    assert r.is_activist("0001871408", "2022-01-01T00:00:00+00:00")


def test_behavioural_activist_needs_three_distinct_issuers_strictly_before():
    r = Register(serial())
    assert r.is_activist("999", "2020-02-01T00:00:00+00:00")
    assert not r.is_activist("999", "2020-01-03T10:00:00+00:00")  # third filed exactly at asof: not strictly before
    assert r.is_activist("999", "2020-01-03T10:00:01+00:00")
    assert r.prior_campaigns("0000999", "2020-01-02T10:00:00+00:00") == 1


def test_same_issuer_repeated_and_amendments_and_self_do_not_count():
    evs = [ev("SC 13D", "2020-01-01T00:00:00+00:00", "999", "1"), ev("SC 13D", "2020-01-02T00:00:00+00:00", "999", "1"),
           ev("SC 13D/A", "2020-01-03T00:00:00+00:00", "999", "2"), ev("SC 13G", "2020-01-04T00:00:00+00:00", "999", "3"),
           ev("SC 13D", "2020-01-05T00:00:00+00:00", "999", "999")]
    r = Register(evs)
    assert r.prior_campaigns("999", "2030-01-01") == 1
    assert not r.is_activist("999", "2030-01-01")


def test_future_events_do_not_leak():
    r = Register(serial(n=2) + [ev("SC 13D", "2025-01-01T00:00:00+00:00", "999", "500")])
    assert not r.is_activist("999", "2024-01-01T00:00:00+00:00")
    assert r.is_activist("999", "2026-01-01T00:00:00+00:00")


def results():
    rows = []
    # filer 1: 2 closed before asof (+0.2,+0.2), 1 after asof (-0.9)
    rows += [dict(filer_id="0001", exit_date="2021-01-01", net_excess=0.2), dict(filer_id="1", exit_date="2021-02-01", net_excess=0.2),
             dict(filer_id="1", exit_date="2023-01-01", net_excess=-0.9)]
    rows += [dict(filer_id="2", exit_date="2021-03-01", net_excess=-0.2), dict(filer_id="2", exit_date="2021-04-01", net_excess=0.0)]
    return pd.DataFrame(rows)


def test_metrics_uses_only_exits_before_asof_and_shrinks():
    r = Register([], results())
    m = r.metrics("1", "2022-01-01T00:00:00+00:00")
    g_mean = (0.2 + 0.2 - 0.2 + 0.0) / 4
    g_hit = 2 / 4
    assert m["n_closed"] == 2
    assert m["mean_net"] == pytest.approx(g_mean + 0.2 * (0.2 - g_mean))
    assert m["hit_rate"] == pytest.approx(g_hit + 0.2 * (1.0 - g_hit))
    # exit exactly at asof is excluded
    m2 = r.metrics("1", "2021-02-01T00:00:00+00:00")
    assert m2["n_closed"] == 1
    # after the later exit the -0.9 is included
    assert r.metrics("1", "2024-01-01T00:00:00+00:00")["n_closed"] == 3


def test_metrics_unknown_filer_gets_global_mean_and_empty_cases():
    r = Register([], results())
    m = r.metrics("77", "2022-01-01T00:00:00+00:00")
    assert m["n_closed"] == 0 and m["mean_net"] == pytest.approx(0.05)
    assert Register([]).metrics("1", "2022-01-01")["mean_net"] is None
    assert r.metrics("1", "2000-01-01T00:00:00+00:00")["n_closed"] == 0
    assert r.metrics("1", "2000-01-01T00:00:00+00:00")["mean_net"] is None


def test_metrics_no_shrink_at_10():
    df = pd.DataFrame([dict(filer_id="5", exit_date="2021-01-01", net_excess=0.1)] * 10 +
                      [dict(filer_id="6", exit_date="2021-01-01", net_excess=-0.5)] * 10)
    m = Register([], df).metrics("5", "2022-01-01")  # naive asof
    assert m["n_closed"] == 10 and m["mean_net"] == pytest.approx(0.1)
    assert m["hit_rate"] == pytest.approx(1.0)


def test_missing_issuer_id_not_counted():
    r = Register([ev("SC 13D", "2020-01-06T00:00:00+00:00", "999", ""), ev("SC 13D", "2020-01-07T00:00:00+00:00", "999", None)])
    assert r.prior_campaigns("999", "2030-01-01") == 0
