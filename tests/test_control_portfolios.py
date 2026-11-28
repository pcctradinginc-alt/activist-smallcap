import pandas as pd
import pytest
from activist.study.control_portfolios import replay, compare

def row(key, entry, exit, complete=True, **kw):
    r = dict(campaign_key=key, ticker=key, region="US", published="2022-01-01",
             variant="main", qualified=True, status="qualifiziert", executed=True,
             complete=complete, entry_date=entry, exit_date=exit,
             invested_eur=1000., buy_cost=.01, sell_cost=.02, gross=.1)
    r.update(kw)
    return r

def test_open_position_reserves_cash():
    rows = pd.DataFrame([row("a","2022-01-03","2022-01-20",False),
                         row("b","2022-01-04","2022-01-10")])
    res = replay(rows, start_cash=1500)
    assert res["rejected"] == 1
    assert res["cash"] == pytest.approx(490)
    assert res["open_cost_basis"] == pytest.approx(1010)

def test_no_same_day_cash_reuse():
    rows = pd.DataFrame([row("a","2022-01-03","2022-01-04"),
                         row("b","2022-01-04","2022-01-05"),
                         row("c","2022-01-05","2022-01-06")])
    res = replay(rows, start_cash=1100)
    assert res["rejected"] == 1
    assert [x["key"] for x in res["ledger"] if x["side"]=="BUY"] == ["a","c"]
    assert res["cash"] == pytest.approx(1100 + 2*(1078-1010))
    assert all(x["cash"] >= 0 for x in res["ledger"])

def test_independent_filter_control_and_holdout_exclusion():
    rows = pd.DataFrame([row("a","2022-01-03","2022-01-04"),
                         row("b","2022-01-05","2022-01-06",qualified=False,status="verworfen: Spread"),
                         row("c","2023-01-05","2023-01-06",published="2023-01-01")])
    r = compare(rows)
    assert set(k.split("/")[0] for k in r) == {"valid"}
    assert len(r["valid/main"]["ledger"]) == 2
    assert len(r["valid/without_Spread"]["ledger"]) == 4
    assert r["valid/cash"]["cash"] == 20000


def test_daily_marks_use_eur_values_and_missing_marks_invalidate_drawdown():
    rows = pd.DataFrame([row("a", "2022-01-03", "2022-01-05", exit_value_eur=1050.)])
    days = pd.date_range("2022-01-03", "2022-01-05")
    result = replay(rows, start_cash=2000, calendar=days,
                    marks=lambda row, active: pd.Series([990., 950.], index=active))
    assert result["daily_nav"] == {"2022-01-03": 1980., "2022-01-04": 1940., "2022-01-05": 2019.}
    assert result["max_drawdown"] == pytest.approx(.03)
    assert result["capital_days_eur"] == 2020.
    unavailable = replay(rows, start_cash=2000, calendar=days)
    assert unavailable["missing_mark_days"] == 2 and unavailable["net_return"] is None
