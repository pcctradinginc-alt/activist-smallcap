import pytest


@pytest.fixture(autouse=True)
def _tiingo_no_wait(monkeypatch):
    from activist.sources import prices
    monkeypatch.setattr(prices, "TIINGO_MIN_GAP_S", 0.0)
    monkeypatch.setattr(prices, "TIINGO_429_WAIT_S", 0.0)
    monkeypatch.setitem(prices._TIINGO_STATE, "calls", 0)
    monkeypatch.setitem(prices._TIINGO_STATE, "blocked", False)
