import types
import pytest


@pytest.fixture(autouse=True)
def _tiingo_no_wait(monkeypatch):
    from activist.sources import prices
    monkeypatch.setattr(prices, "TIINGO_MIN_GAP_S", 0.0)
    monkeypatch.setattr(prices, "TIINGO_429_WAIT_S", 0.0)
    monkeypatch.setitem(prices._TIINGO_STATE, "calls", 0)
    monkeypatch.setitem(prices._TIINGO_STATE, "blocked", False)


@pytest.fixture(autouse=True)
def _no_network_sector_earnings(monkeypatch, tmp_path):
    """Branchen-Suche und SEC-submissions nie live: Standard = Netzfehler (Tests überschreiben gezielt)."""
    from activist.sources import sector, secmeta
    def boom(*a, **k):
        raise OSError("kein Netz im Test")
    monkeypatch.setattr(sector, "requests", types.SimpleNamespace(get=boom))   # nur im Modul sector, nicht global
    monkeypatch.setattr(sector, "DATA", tmp_path)
    monkeypatch.setattr(sector, "MIN_GAP_S", 0.0)
    monkeypatch.setattr(secmeta, "earnings_dates", boom)
