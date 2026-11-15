from datetime import date
import json
import pandas as pd
from activist.sources import prices


def test_tiingo_functions_exist():
    assert callable(prices.tiingo) and callable(prices.validate_against)


def test_second_us_without_key_raises_valueerror(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    monkeypatch.setattr(prices, "DATA", __import__("pathlib").Path("/nonexistent-xyz"))
    try:
        prices._second_us("ZZZZ", date(2018, 1, 1), date(2018, 2, 1))
    except ValueError as e:
        assert "TIINGO_API_KEY" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_cached_unchecked_result_is_rechecked(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    idx = pd.date_range("2018-01-01", "2018-03-01", freq="B")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "close_unadj": 1.0, "volume": 1}, index=idx)
    (tmp_path / "prices").mkdir()
    df.to_csv(tmp_path / "prices" / "US_ABC.csv")
    (tmp_path / "prices" / "US_ABC.check.json").write_text(json.dumps({"ok": None}))
    monkeypatch.setattr(prices, "yahoo", lambda *a, **k: df)
    monkeypatch.setitem(prices.SECOND_SOURCE, "US", lambda t, s, e: df[["close"]])
    _, chk = prices.load("ABC", "US", date(2018, 1, 2), date(2018, 2, 1))
    assert chk["ok"] is True and not chk.get("cached")


def test_tiingo_retries_on_429(monkeypatch):
    calls = []

    class R:
        def __init__(self, code): self.status_code = code
        def raise_for_status(self): pass
        def json(self): return [{"date": "2018-01-02T00:00:00+00:00", "adjOpen": 1, "adjHigh": 1, "adjLow": 1,
                                 "adjClose": 1, "adjVolume": 1, "close": 1}]

    def get(*a, **k):
        calls.append(1)
        return R(429 if len(calls) == 1 else 200)
    monkeypatch.setattr(prices.requests, "get", get)
    monkeypatch.setattr(prices, "TIINGO_MIN_GAP_S", 0.0)
    monkeypatch.setattr(prices, "TIINGO_429_WAIT_S", 0.0)
    df = prices.tiingo("ABC", date(2018, 1, 1), date(2018, 1, 5), key="k")
    assert len(calls) == 2 and len(df) == 1


def _frame(n=30, v=1.0, start="2018-01-01"):
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v, "close_unadj": v, "volume": 1}, index=idx)


def test_cross_check_overlap_rules():
    a = _frame(30)
    assert prices.cross_check(a, _frame(30)[["close"]])["ok"] is True
    r = prices.cross_check(a, _frame(30, 1.1)[["close"]])
    assert r["ok"] is False and r["max_dev"] > 0.02
    r = prices.cross_check(a, _frame(5)[["close"]])                       # 5 Tage Überlappung: ungeprüft
    assert r["ok"] is None and r["overlap"] == 5 and "zu wenig Überlappung" in r["error"]
    assert prices.cross_check(a, _frame(5, start="2020-01-01")[["close"]])["ok"] is None   # leer = nicht prüfbar


def test_tiingo_http_errors_never_leak_key(monkeypatch):
    monkeypatch.setattr(prices, "TIINGO_MIN_GAP_S", 0.0); monkeypatch.setattr(prices, "TIINGO_429_WAIT_S", 0.0)

    class R:
        def __init__(self, code): self.status_code = code
        def raise_for_status(self): raise RuntimeError("https://api.tiingo.com/x?token=SECRET")
    for code in (429, 500):
        monkeypatch.setattr(prices.requests, "get", lambda *a, code=code, **k: R(code))
        try:
            prices.tiingo("ABC", date(2018, 1, 1), date(2018, 1, 5), key="SECRET")
        except ValueError as e:
            assert "SECRET" not in str(e) and str(code) in str(e)
        else:
            raise AssertionError("expected ValueError")

    def boom(*a, **k): raise prices.requests.ConnectionError("GET https://api.tiingo.com/x?startDate=1&token=SECRET failed")
    monkeypatch.setattr(prices.requests, "get", boom)
    try:
        prices.tiingo("ABC", date(2018, 1, 1), date(2018, 1, 5), key="SECRET")
    except ValueError as e:
        assert "SECRET" not in str(e)


def test_load_scrubs_token_in_check_error(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    monkeypatch.setattr(prices, "yahoo", lambda *a, **k: _frame())
    def second(t, s, e): raise RuntimeError("400 for url: https://x/?a=1&token=SECRET&b=2")
    monkeypatch.setitem(prices.SECOND_SOURCE, "US", second)
    _, chk = prices.load("ABC", "US", date(2018, 1, 1), date(2018, 2, 1))
    assert chk["ok"] is None and "SECRET" not in json.dumps(chk) and "token=***" in chk["error"]
    assert "SECRET" not in (tmp_path / "prices" / "US_ABC.check.json").read_text()


def test_load_cache_uses_requested_range(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    calls = []
    def yahoo(*a, **k): calls.append(1); return _frame()          # Reihe endet lange vor end (delistet)
    monkeypatch.setattr(prices, "yahoo", yahoo)
    monkeypatch.setitem(prices.SECOND_SOURCE, "US", lambda t, s, e: _frame()[["close"]])
    args = ("ABC", "US", date(2018, 1, 1), date(2019, 6, 1))
    _, c1 = prices.load(*args); _, c2 = prices.load(*args)
    assert len(calls) == 1 and c1["ok"] is True and c2["cached"]


def test_load_old_check_with_small_overlap_is_rechecked(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    df = _frame(); (tmp_path / "prices").mkdir()
    df.to_csv(tmp_path / "prices" / "US_ABC.csv")
    (tmp_path / "prices" / "US_ABC.check.json").write_text(json.dumps({"ok": True, "overlap": 3}))
    monkeypatch.setattr(prices, "yahoo", lambda *a, **k: df)
    monkeypatch.setitem(prices.SECOND_SOURCE, "US", lambda t, s, e: df[["close"]])
    _, chk = prices.load("ABC", "US", date(2018, 1, 2), date(2018, 2, 1))
    assert chk["ok"] is True and chk["overlap"] == 30 and not chk.get("cached")


def test_tiingo_cached_second_call_without_request(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    calls = []
    def fake(t, s, e, key=None): calls.append(1); return _frame(30).assign(close_raw=1.0)
    monkeypatch.setattr(prices, "tiingo", fake)
    for _ in range(2):
        prices.tiingo_cached("ABC", date(2018, 1, 1), date(2018, 2, 1))
    assert len(calls) == 1 and (tmp_path / "cache" / "tiingo" / "ABC.meta.json").exists()


def test_tiingo_cached_negative_cache_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    calls = []
    def fake(t, s, e, key=None): calls.append(1); raise ValueError(f"Tiingo: {t} unbekannt")
    monkeypatch.setattr(prices, "tiingo", fake)
    for _ in range(2):
        try:
            prices.tiingo_cached("ZZZ", date(2018, 1, 1), date(2018, 2, 1))
        except ValueError as e:
            assert "unbekannt" in str(e)
    assert len(calls) == 1
    mp = tmp_path / "cache" / "tiingo" / "ZZZ.meta.json"
    old = (date.today() - __import__("datetime").timedelta(days=31)).isoformat()
    mp.write_text(json.dumps({"fetched": old, "error": "Tiingo: ZZZ unbekannt"}))
    try:
        prices.tiingo_cached("ZZZ", date(2018, 1, 1), date(2018, 2, 1))
    except ValueError:
        pass
    assert len(calls) == 2


def test_tiingo_cached_missing_key_not_negative_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(prices, "DATA", tmp_path)
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    for _ in range(2):
        try:
            prices.tiingo_cached("ABC", date(2018, 1, 1), date(2018, 2, 1))
        except ValueError as e:
            assert "TIINGO_API_KEY" in str(e)
    assert not (tmp_path / "cache" / "tiingo" / "ABC.meta.json").exists()
