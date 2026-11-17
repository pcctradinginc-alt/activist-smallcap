import json
from datetime import date
import numpy as np
import pandas as pd
import pytest
from activist.features.register import Register
from activist.study import dataset

STAR = "0001517137"   # Starboard, in activists.yaml since 2011


def ev(i, form, pub, filer, issuer, url=None):
    return {"event_id": f"EDGAR:{i}", "source": "EDGAR", "region": "US", "form": form, "published": pub,
            "filer_id": filer, "issuer_id": issuer, "filer_name": f"F{filer}", "url": url or f"http://x/{i}"}


def by_key(evs):
    reg = Register(evs)
    return {c["campaign_key"]: c for c in dataset.campaigns(evs, reg)}


def test_campaigns_non_activist_13d_yields_nothing():
    evs = [ev(1, "SC 13D", "2023-03-01T10:00:00+00:00", "888", "100")]
    assert dataset.campaigns(evs, Register(evs)) == []


def test_campaigns_activist_initial_and_filters():
    evs = [ev(1, "SC 13D", "2023-03-01T10:00:00+00:00", STAR, "100"),
           ev(2, "SC 13D", "2023-03-01T10:00:00+00:00", "100", "100"),       # filer == issuer
           ev(3, "SC 13D", "2023-03-01T10:00:00+00:00", None, "100"),         # no filer
           {**ev(4, "SC 13D", "2023-03-01T10:00:00+00:00", STAR, "200"), "region": "JP"}]
    out = dataset.campaigns(evs, Register(evs))
    assert [c["campaign_key"] for c in out] == ["1517137-100"]
    assert out[0]["signal_class"] == "A" and out[0]["event_type"] == "initial_13d"


POLITAN = "0001871408"   # since 2021-01-01


def test_campaigns_switch_and_followups():
    # 13G filed while the filer was not yet a (PIT) activist, then 13D after -> switch, class A
    evs = [ev(3, "SC 13D/A", "2022-05-01T10:00:00+00:00", POLITAN, "100"),
           ev(1, "SC 13G", "2020-01-01T10:00:00+00:00", POLITAN, "100"),
           ev(2, "SC 13D", "2022-03-01T10:00:00+00:00", POLITAN, "100"),
           ev(4, "SC 13D/A", "2022-06-01T10:00:00+00:00", POLITAN, "100")]
    out = by_key(evs)["1871408-100"]
    assert out["event_id"] == "EDGAR:2" and out["event_type"] == "switch_13g_13d" and out["signal_class"] == "A"
    assert [f["event_id"] for f in out["followups"]] == ["EDGAR:3", "EDGAR:4"]


def test_campaigns_activist_13g_then_13d_stays_class_b():
    """Protokoll: das erste Ereignis bestimmt die Klasse; der spätere 13D ist Folgeereignis."""
    evs = [ev(1, "SC 13G", "2023-01-01T10:00:00+00:00", STAR, "100"),
           ev(2, "SC 13D", "2023-03-01T10:00:00+00:00", STAR, "100")]
    out = by_key(evs)["1517137-100"]
    assert out["event_type"] == "passive" and out["signal_class"] == "B"
    assert [f["form"] for f in out["followups"]] == ["SC 13D"]


def test_campaigns_13g_by_activist_is_class_b_and_nonactivist_13g_skipped():
    evs = [ev(1, "SC 13G", "2023-01-01T10:00:00+00:00", STAR, "100"),
           ev(2, "SC 13G", "2023-01-01T10:00:00+00:00", "888", "100")]
    out = dataset.campaigns(evs, Register(evs))
    assert len(out) == 1 and out[0]["signal_class"] == "B" and out[0]["event_type"] == "passive"


def test_campaigns_pit_activist_status_at_event_time():
    # Politan (since 2021): 13D in 2020 is not a signal
    evs = [ev(1, "SC 13D", "2020-06-01T10:00:00+00:00", "0001871408", "100")]
    assert dataset.campaigns(evs, Register(evs)) == []


# ---------- build ----------
PUB = "2023-03-01T10:00:00+00:00"


def mkpx(start, end, kind):
    idx = pd.bdate_range(start, end)
    n = len(idx)
    c = 10.0 * (1.01 ** np.arange(n)) if kind == "runup" else np.full(n, 10.0)
    return pd.DataFrame({"open": c, "high": c * 1.003, "low": c * 0.997, "close": c, "volume": 100000.0}, index=idx)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(dataset, "CACHE", tmp_path / "cache" / "parsed.jsonl")
    real_params = dataset.params

    def fixed_params():     # works around params.yaml 50e6 being loaded as str (see test_params_yaml_numbers_are_numeric)
        p = real_params()
        for r in p["universe"].values():
            for k in ("mcap_min", "mcap_max"):
                r[k] = float(r[k])
        return p
    monkeypatch.setattr(dataset, "params", fixed_params)
    tick = {"100": "OKAY", "300": "BIG", "400": "RUN", "500": "SWITCH"}
    shares = {"100": 10e6, "300": 1.0, "400": 10e6, "500": 10e6}
    monkeypatch.setattr(dataset.secmeta, "ticker_asof",
                        lambda cik, asof: ({"ticker": tick[str(cik)], "otc": False} if str(cik) in tick else None))
    monkeypatch.setattr(dataset.secmeta, "company", lambda cik: {"sic": "3570"})
    monkeypatch.setattr(dataset.secmeta, "shares_outstanding_asof",
                        lambda cik, asof: (shares[str(cik)], "x", "y"))
    monkeypatch.setattr(dataset.secmeta, "excluded_industry", lambda sic: None)
    calls = []

    def load(ticker, region, start, end, second=True):
        calls.append((ticker, start, end))
        return mkpx(start, end, "runup" if ticker == "RUN" else "flat"), {"ok": True}
    monkeypatch.setattr(dataset.prices, "load", load)
    parsed = []

    def fetch(url):
        parsed.append(url)
        return {"demand": "board seats", "demand_evidence": "e", "percent_of_class": 6.0, "avg_purchase_price": None,
                "is_exit": url.endswith("exit")}
    monkeypatch.setattr(dataset.filing13d, "fetch_and_parse", fetch)
    return calls, parsed


def events():
    return [ev(1, "SC 13D", PUB, STAR, "100", "http://x/init"),
            ev(2, "SC 13D/A", "2023-04-01T10:00:00+00:00", STAR, "100", "http://x/amend1"),
            ev(3, "SC 13G", "2023-05-01T10:00:00+00:00", STAR, "100", "http://x/g"),      # not parsed
            ev(4, "SC 13D/A", "2023-06-01T10:00:00+00:00", STAR, "100", "http://x/exit"),
            ev(5, "SC 13D/A", "2023-07-01T10:00:00+00:00", STAR, "100", "http://x/exit2"),
            ev(6, "SC 13D", PUB, STAR, "200"),                       # no ticker
            ev(7, "SC 13D", PUB, STAR, "300"),                       # mcap out
            ev(8, "SC 13D", PUB, STAR, "400"),                       # run-up
            ev(9, "SC 13G", "2020-12-01T10:00:00+00:00", POLITAN, "500"),
            ev(10, "SC 13D", PUB, POLITAN, "500"),                      # switch, qualified
            ev(11, "SC 13D", PUB, "888", "100")]                     # non-activist: ignored


def test_build_funnel_and_statuses(env):
    calls, parsed = env
    df, f = dataset.build(events(), end=date(2025, 1, 1))
    st = dict(zip(df.campaign_key, df.status))
    assert st["1517137-100"] == "qualifiziert"
    assert st["1517137-200"] == "nur Endwert"
    assert st["1517137-300"].startswith("außerhalb Universum") and "Marktkapitalisierung außerhalb" in st["1517137-300"]
    assert st["1517137-400"] == "verworfen: Anstieg 20T"
    assert st["1871408-500"] == "qualifiziert"
    assert len(df) == 5 and "888-100" not in st
    assert f["kampagnen"] == 5
    assert f["Survivorship: Endwert unknown"] == 1
    assert f["Universum: Marktkapitalisierung außerhalb"] == 1
    assert f["im Universum"] == 3
    assert f["verworfen (Schattenportfolio)"] == 1 and f["qualifiziert"] == 2
    row = df.set_index("campaign_key")
    assert row.loc["1871408-500", "event_type"] == "switch_13g_13d"
    assert row.loc["1517137-100", "signal_class"] == "A" and row.loc["1517137-100", "ticker"] == "OKAY"
    assert row.loc["1517137-400", "runup_20d"] > 0.2
    assert row.loc["1517137-100", "mcap"] == pytest.approx(10e6 * 10)
    assert row.loc["1517137-100", "stake"] == 6.0 and row.loc["1517137-100", "demand"] == "board seats"
    # price window: 45 days before to 420 after, capped by end
    t, s, e = next(c for c in calls if c[0] == "OKAY")
    assert s == date(2023, 1, 15) and e == date(2024, 4, 24)


def test_build_exit_events_from_first_exit_amendment(env):
    calls, parsed = env
    df, _ = dataset.build(events(), end=date(2025, 1, 1))
    row = df.set_index("campaign_key").loc["1517137-100"]
    assert row.exit_events == ["2023-06-01T10:00:00+00:00"]
    assert "http://x/exit2" not in parsed      # break after first exit
    assert "http://x/g" in parsed              # 13G-Folgemeldungen werden ebenfalls auf Ausstieg geprüft
    assert df.set_index("campaign_key").loc["1871408-500", "exit_events"] == []
    # cache written, and reused on second run
    n = len(parsed)
    lines = dataset.CACHE.read_text().splitlines()
    assert {json.loads(l)["url"] for l in lines} >= {"http://x/init", "http://x/exit"}
    dataset.build(events(), end=date(2025, 1, 1))
    assert len(parsed) == n


def test_build_parse_filings_false(env):
    _, parsed = env
    df, f = dataset.build(events(), end=date(2025, 1, 1), parse_filings=False)
    assert parsed == [] and f["qualifiziert"] == 2
    assert all(x == [] for x in df[df.status == "qualifiziert"].exit_events)


def test_build_price_failures(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("net")
    monkeypatch.setattr(dataset.prices, "load", boom)
    df, f = dataset.build(events()[:1], end=date(2025, 1, 1))
    assert df.status.tolist() == ["nur Endwert"] and f["Survivorship: Endwert unknown"] == 1
    monkeypatch.setattr(dataset.prices, "load", lambda *a, **k: (pd.DataFrame(), {"ok": False}))
    df, f = dataset.build(events()[:1], end=date(2025, 1, 1))
    assert df.status.tolist() == ["gesperrt: Kursabweichung"]


def test_build_parse_error_is_cached_not_raised(env, monkeypatch):
    def bad(url):
        raise ValueError("bad")
    monkeypatch.setattr(dataset.filing13d, "fetch_and_parse", bad)
    df, _ = dataset.build(events()[:1], end=date(2025, 1, 1))
    assert df.status.tolist() == ["qualifiziert"]


def test_params_yaml_numbers_are_numeric():
    cfg = dataset.params()["universe"]["US"]
    assert isinstance(cfg["mcap_min"], (int, float)) and isinstance(cfg["mcap_max"], (int, float))


@pytest.fixture(autouse=True)
def _no_survivorship_network(monkeypatch):
    monkeypatch.setattr(dataset.hist_ticker, "ticker_hist", lambda *a, **k: {})
    monkeypatch.setattr(dataset.delisting, "terminal_outcome", lambda *a, **k: {"type": "unknown"})
