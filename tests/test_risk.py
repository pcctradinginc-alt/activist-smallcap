from activist.risk import rules

CL = {"max_position_share_of_adv": 0.05, "max_spread": 0.02, "max_premium_over_activist_cost": 0.15,
      "max_runup_20d": 0.20, "max_major_holder": 0.50, "earnings_blackout_days": 3}
LIM = {"candidate": CL, "portfolio": {"drawdown_half_size": 0.15, "drawdown_stop": 0.25}}
ROW = {"half_spread": 0.005, "runup_20d": 0.05, "price": 10.0, "activist_cost": 9.5, "adv": 100000}


def reasons(pos=500, **kw):
    return rules.candidate_reasons({**ROW, **kw}, pos, CL)


def test_clean_row():
    assert reasons() == []


def test_spread():
    assert reasons(half_spread=0.015) == ["Spread"]       # 2 * 0,015 > 0,02


def test_runup():
    assert reasons(runup_20d=0.25) == ["Anstieg 20T"]


def test_price_above_activist_cost():
    assert reasons(price=12.0) == ["Kurs über Aktivisten-Einstand"]       # > 9,5 * 1,15


def test_liquidity():
    assert reasons(pos=6000) == ["Liquidität"]            # > 5 % von 100k
    assert reasons(adv=None) == ["Liquidität"]


def test_fx_missing():
    assert reasons(pos=None) == ["Wechselkurs fehlt"]


def test_nan_activist_cost_ignored():
    assert reasons(activist_cost=float("nan")) == []


def test_portfolio_size_factor():
    assert rules.portfolio_size_factor(0.0, LIM) == 1.0
    assert rules.portfolio_size_factor(0.15, LIM) == 0.5
    assert rules.portfolio_size_factor(0.25, LIM) == 0.0
