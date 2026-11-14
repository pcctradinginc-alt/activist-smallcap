import pandas as pd
import pytest
from activist.paper.execution import simulate_entry, simulate_exit, cost_one_side

ORDER = {"limit_above_last_close": 0.03, "max_fill_days": 3,
         "max_share_of_daily_volume": 0.05, "position_eur": 500}
EXIT = {"max_holding_days": 252, "stop_loss": -0.30}
COSTS = {"min_half_spread": 0.005, "fx_and_fees": 0.0025, "default_half_spread": 0.01,
         "levies_buy": {"UK": 0.005}}


def frame(rows, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close", "volume"])


DECISION = pd.Timestamp("2024-01-02 12:00")  # Tag 0 = Entscheidungstag, Schluss 100 -> Limit 103


def test_entry_open_below_limit_fills_at_open():
    px = frame([(100, 101, 99, 100, 1e6), (101, 104, 100, 102, 1e6)])
    f = simulate_entry(px, DECISION, ORDER, 500)
    assert f.filled and f.price == 101
    assert f.fill_date == pd.Timestamp("2024-01-03")
    assert f.fill_ratio == pytest.approx(1.0)
    assert f.limit == pytest.approx(103)


def test_entry_open_above_limit_low_touches_fills_at_limit():
    px = frame([(100, 101, 99, 100, 1e6), (106, 107, 102, 105, 1e6)])
    f = simulate_entry(px, DECISION, ORDER, 500)
    assert f.filled and f.price == pytest.approx(103)


def test_entry_never_touches_not_filled():
    px = frame([(100, 101, 99, 100, 1e6)] + [(110, 112, 108, 111, 1e6)] * 5)
    f = simulate_entry(px, DECISION, ORDER, 500)
    assert not f.filled and f.fill_ratio == 0.0 and f.price is None


def test_entry_no_prices_after():
    px = frame([(100, 101, 99, 100, 1e6)])
    assert not simulate_entry(px, DECISION, ORDER, 500).filled


def test_entry_volume_cap_partial_over_three_days():
    # Preis 10, Position 1000, Volumen 400/Tag -> 5 % * 400 * 10 / 1000 = 0.2 je Tag
    rows = [(10, 10, 10, 10, 400)] + [(10, 10, 10, 10, 400)] * 5
    f = simulate_entry(frame(rows), DECISION, ORDER, 1000)
    assert f.filled
    assert f.fill_ratio == pytest.approx(0.6)  # nur 3 Tage erlaubt
    assert f.fill_date == pd.Timestamp("2024-01-03")


def test_entry_volume_cap_full_fill_in_three_days():
    rows = [(10, 10, 10, 10, 800)] * 5  # 0.4 je Tag -> 0.4 + 0.4 + 0.2
    f = simulate_entry(frame(rows), DECISION, ORDER, 1000)
    assert f.fill_ratio == pytest.approx(1.0)


def test_exit_stop_loss():
    px = frame([(100, 100, 100, 100, 1)] + [(95, 96, 60, 70, 1)] + [(70, 70, 70, 70, 1)] * 3)
    d, p, why = simulate_exit(px, px.index[0], 100.0, EXIT)
    assert why == "Stopp" and d == px.index[1]
    assert p == pytest.approx(70.0)


def test_exit_stop_gap_down_uses_open():
    px = frame([(100, 100, 100, 100, 1), (50, 55, 48, 52, 1)])
    _, p, why = simulate_exit(px, px.index[0], 100.0, EXIT)
    assert why == "Stopp" and p == 50


def test_exit_stop_disabled():
    px = frame([(100, 100, 100, 100, 1), (50, 55, 48, 52, 1), (52, 52, 52, 52, 1)])
    _, _, why = simulate_exit(px, px.index[0], 100.0, EXIT, stop=False, max_days=2)
    assert why == "Haltedauer"


def test_exit_holding_period():
    px = frame([(100, 100, 100, 100, 1)] + [(100, 101, 99, 100 + i, 1) for i in range(1, 8)])
    d, p, why = simulate_exit(px, px.index[0], 100.0, EXIT, max_days=5)
    assert why == "Haltedauer"
    assert d == px.index[5] and p == 105


def test_exit_end_of_data_open():
    px = frame([(100, 100, 100, 100, 1), (100, 101, 99, 100, 1)])
    _, _, why = simulate_exit(px, px.index[0], 100.0, EXIT)
    assert why == "offen (Datenende)"


def test_exit_no_following_prices():
    px = frame([(100, 100, 100, 100, 1)])
    d, p, why = simulate_exit(px, px.index[0], 100.0, EXIT)
    assert why == "keine Folgekurse" and p == 100.0


def test_exit_activist_reduction_next_open():
    px = frame([(100, 100, 100, 100, 1)] + [(100 + i, 101 + i, 99 + i, 100 + i, 1) for i in range(1, 6)])
    ev_day = px.index[2]
    d, p, why = simulate_exit(px, px.index[0], 100.0, EXIT, exit_events=[ev_day])
    assert why == "Aktivist reduziert"
    assert d == px.index[3] and p == 103  # Eröffnung des Folgetags


def test_cost_minimums():
    assert cost_one_side(0.0, COSTS, "US", "buy") == pytest.approx(0.005 + 0.0025)
    assert cost_one_side(0.001, COSTS, "US", "sell") == pytest.approx(0.0075)
    assert cost_one_side(None, COSTS, "US", "buy") == pytest.approx(0.01 + 0.0025)
    assert cost_one_side(0.02, COSTS, "US", "buy") == pytest.approx(0.0225)


def test_cost_round_trip_at_least_1_5_percent():
    for region in ("US", "JP", "UK"):
        total = cost_one_side(0.0, COSTS, region, "buy") + cost_one_side(0.0, COSTS, region, "sell")
        assert total >= 0.015 - 1e-12


def test_cost_uk_levy_buy_only():
    uk_b = cost_one_side(0.0, COSTS, "UK", "buy")
    us_b = cost_one_side(0.0, COSTS, "US", "buy")
    assert uk_b - us_b == pytest.approx(0.005)
    assert cost_one_side(0.0, COSTS, "UK", "sell") == cost_one_side(0.0, COSTS, "US", "sell")
    assert uk_b + cost_one_side(0.0, COSTS, "UK", "sell") >= 0.015 + 0.005 - 1e-12


def test_fixed_fee_per_order_scales_with_position():
    from activist.paper.execution import cost_one_side
    cfg = {"min_half_spread": 0.005, "fx_and_fees": 0.0025, "default_half_spread": 0.01, "fixed_fee_eur_per_order": 15.0}
    assert cost_one_side(0.0, cfg, "US", "buy", 500) == pytest.approx(0.0375)
    assert cost_one_side(0.0, cfg, "US", "buy", 3000) == pytest.approx(0.0125)
    assert cost_one_side(0.0, cfg, "US", "buy") == pytest.approx(0.0075)
