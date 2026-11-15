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
