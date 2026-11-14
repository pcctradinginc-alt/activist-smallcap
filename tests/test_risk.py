import copy
from activist.risk import rules

LIM = {
    "candidate": {"max_position_share_of_adv": 0.05, "max_spread": 0.02, "max_premium_over_activist_cost": 0.15,
                  "max_runup_20d": 0.20, "max_major_holder": 0.50, "earnings_blackout_days": 3},
    "portfolio": {"drawdown_half_size": 0.15, "drawdown_stop": 0.25},
}

CLEAN = {"position_eur": 500, "adv_eur": 100000, "spread": 0.01, "price": 10.0, "activist_cost": 9.5,
         "runup_20d": 0.05, "capital_raise": False, "going_concern": False, "major_holder": 0.2,
         "new_poison_pill": False, "mapping_ok": True, "prices_ok": True, "days_to_earnings": 30}


def viol(**kw):
    c = copy.deepcopy(CLEAN); c.update(kw)
    return rules.check_candidate(c, LIM)


def test_clean_passes():
    assert rules.check_candidate(CLEAN, LIM) == []
    assert rules.passes(CLEAN, LIM)


def test_liquidity():
    assert ("Liquidität", rules.VERKLEINERN) in viol(position_eur=6000)  # > 5 % von 100k


def test_spread():
    assert ("Spread", rules.VERWERFEN) in viol(spread=0.03)


def test_premium_over_activist_cost():
    assert ("Vorweggenommener Kurs", rules.VERWERFEN) in viol(price=12.0)  # > 9.5*1.15 = 10.925


def test_runup():
    assert ("Vorweggenommener Kurs (20T)", rules.VERWERFEN) in viol(runup_20d=0.25)


def test_dilution():
    assert ("Verwässerung", rules.VERWERFEN) in viol(capital_raise=True)
    assert ("Verwässerung", rules.VERWERFEN) in viol(going_concern=True)


def test_control():
    assert ("Kontrolle", rules.VERWERFEN) in viol(major_holder=0.5)
    assert ("Kontrolle", rules.VERWERFEN) in viol(new_poison_pill=True)


def test_data_quality():
    assert ("Datenqualität", rules.SPERREN) in viol(mapping_ok=False)
    assert ("Datenqualität", rules.SPERREN) in viol(prices_ok=False)


def test_earnings_blackout():
    assert ("Termin", rules.VERSCHIEBEN) in viol(days_to_earnings=3)
    assert ("Termin", rules.VERSCHIEBEN) not in viol(days_to_earnings=4)


def test_missing_data_blocks():
    v = rules.check_candidate({}, LIM)
    assert ("Datenqualität", rules.SPERREN) in v
    assert not rules.passes({}, LIM)


def test_missing_liquidity_and_spread_fields():
    v = viol(adv_eur=None, spread=None)
    assert ("Liquidität", rules.VERKLEINERN) in v and ("Spread", rules.VERWERFEN) in v


def test_portfolio_size_factor():
    assert rules.portfolio_size_factor(0.0, LIM) == 1.0
    assert rules.portfolio_size_factor(0.15, LIM) == 0.5
    assert rules.portfolio_size_factor(0.25, LIM) == 0.0


def test_default_limits_from_config_clean_passes():
    assert rules.passes(CLEAN)  # liest config/risk_limits.yaml
