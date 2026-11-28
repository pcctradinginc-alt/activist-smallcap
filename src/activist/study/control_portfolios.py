"""Exploratory capital-constrained replay of precomputed fills.

Each strategy has independent cash and inventory. No future outcome is
used to select entries. Optional daily EUR marks retain explicit gaps.
"""
from __future__ import annotations
import math
import pandas as pd

def replay(rows: pd.DataFrame, start_cash=20000.0, max_positions=15, *, marks=None, calendar=None):
    cash = float(start_cash)
    held, ledger, positions = {}, [], []
    rejected = 0
    if rows.empty:
        result = {"cash": cash, "open_cost_basis": 0.0, "open_positions": 0, "ledger": [], "rejected": 0,
                "status": "exploratory_cash_flow_replay_not_mtm"}
        return _marked(result, [], marks, calendar, cash) if calendar is not None else result
    rows = rows.copy()
    rows["entry_date"] = pd.to_datetime(rows.entry_date, errors="coerce").dt.normalize()
    rows["exit_date"] = pd.to_datetime(rows.exit_date, errors="coerce").dt.normalize()
    rows = rows.dropna(subset=["entry_date"]).sort_values(["entry_date", "campaign_key"])
    def settle(day):
        nonlocal cash
        for key, pos in sorted(list(held.items()), key=lambda item: (pd.isna(item[1]["exit_date"]), item[1]["exit_date"], item[0])):
            if pos["complete"] and pd.notna(pos["exit_date"]) and pos["exit_date"] < day:
                proceeds = pos["exit_value"] * (1 - pos["sell"])
                cash += proceeds
                ledger.append({"day": str(pos["exit_date"].date()), "side": "SELL",
                               "key": key, "cash_flow": proceeds, "cash": cash})
                del held[key]
    for row in rows.to_dict("records"):
        if row.get("executed") != True:
            continue
        day = row["entry_date"]
        settle(day)
        key = row["campaign_key"]
        ticker = (row.get("region"), row.get("ticker"))
        try:
            value, buy = float(row["invested_eur"]), float(row["buy_cost"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        if not math.isfinite(value) or value <= 0 or not math.isfinite(buy) or buy < 0:
            rejected += 1
            continue
        debit = value * (1 + buy)
        if (not math.isfinite(debit) or debit <= 0 or debit > cash or len(held) >= max_positions
                or key in held or any(p["ticker"] == ticker for p in held.values())):
            rejected += 1
            continue
        gross, sell = row.get("gross"), row.get("sell_cost")
        exit_value = row.get("exit_value_eur")
        if "exit_value_eur" not in rows.columns and pd.notna(gross):
            exit_value = value * (1 + gross)  # synthetic constant-currency callers
        closes = row.get("position_closes", row.get("complete")) == True
        complete = bool(closes and pd.notna(sell) and 0 <= sell < 1 and
                        pd.notna(exit_value) and math.isfinite(exit_value) and exit_value > 0 and
                        pd.notna(row["exit_date"]) and row["exit_date"] >= day)
        held[key] = {"ticker": ticker, "debit": debit, "complete": complete,
                     "sell": sell, "exit_value": exit_value, "exit_date": row["exit_date"]}
        positions.append((row, held[key]))
        cash -= debit
        ledger.append({"day": str(day.date()), "side": "BUY", "key": key,
                       "cash_flow": -debit, "cash": cash})
    # Only settle exits observed in the simulated sample; no cash from open outcomes.
    horizon = rows.exit_date.max()
    if pd.notna(horizon):
        settle(horizon + pd.Timedelta(days=1))
    result = {"cash": cash, "open_cost_basis": sum(p["debit"] for p in held.values()),
            "open_positions": len(held), "ledger": ledger, "rejected": rejected,
            "status": "exploratory_cash_flow_replay_not_mtm"}
    return _marked(result, positions, marks, calendar, start_cash) if calendar is not None else result


def _marked(result, positions, marks, calendar, start_cash):
    """Close-of-day marks, with no imputation of missing or stale quotes."""
    days = pd.DatetimeIndex(calendar)
    cash_changes = pd.Series(0., index=days)
    market = pd.Series(0., index=days)
    missing = pd.Series(0, index=days)
    invested = pd.Series(0., index=days)
    for transaction in result["ledger"]:
        d = pd.Timestamp(transaction["day"])
        if d in days:
            cash_changes.at[d] += transaction["cash_flow"]
    for row, pos in positions:
        active = days[days >= row["entry_date"]]
        if pos["complete"]:
            active = active[active < pos["exit_date"]]
        if active.empty:
            continue
        quotes = marks(row, active) if marks else pd.Series(dtype=float)
        quotes = quotes.reindex(active)
        market.loc[active] += quotes.fillna(0)
        missing.loc[active] += quotes.isna().astype(int)
        invested.loc[active] += pos["debit"]
    nav = (start_cash + cash_changes.cumsum() + market).where(missing == 0)
    complete = bool(len(nav) and nav.notna().all())
    drawdown = 1 - nav / nav.cummax().clip(lower=start_cash)
    result.update(status="daily_mark_to_market" if complete else "incomplete_daily_mark_to_market",
                  daily_nav={str(d.date()): float(v) if pd.notna(v) else None for d, v in nav.items()},
                  missing_mark_days=int((missing > 0).sum()), capital_days_eur=float(invested.sum()),
                  max_drawdown=float(drawdown.max()) if complete else None,
                  net_return=float(nav.iloc[-1] / start_cash - 1) if complete else None,
                  worst_realized_eur=min((pos["exit_value"] * (1 - pos["sell"]) - pos["debit"]
                                         for _, pos in positions if pos["complete"]), default=None))
    return result


def buy_and_hold(quotes, calendar, start_cash, region, costs):
    """EUR regional-index reference with the same per-side cost model."""
    from ..paper.execution import cost_one_side

    days = pd.DatetimeIndex(calendar)
    valid = quotes.reindex(days).where(lambda s: s.gt(0) & s.lt(float("inf")))
    observed = valid.dropna()
    if observed.empty:
        return {"status": "benchmark_prices_missing", "net_return": None, "max_drawdown": None}
    entry_day, exit_day = observed.index[[0, -1]]
    first = float(observed.iloc[0])
    last = float(observed.iloc[-1])
    fixed = costs.get("fixed_fee_eur_per_order", 0.0)
    rate = cost_one_side(None, costs, region, "buy")
    invested = (start_cash - fixed) / (1 + rate)
    if invested <= 0 or entry_day == exit_day:
        return {"status": "benchmark_window_insufficient", "net_return": None, "max_drawdown": None}
    filled = valid.ffill(limit=7)
    mark = pd.Series(start_cash, index=days, dtype=float)
    mark.loc[entry_day:] = invested * filled.loc[entry_day:] / first
    proceeds = invested * last / first
    net = proceeds * (1 - cost_one_side(None, costs, region, "sell", proceeds))
    mark.loc[exit_day] = net
    if mark.loc[exit_day:].isna().any() or (days[-1] - exit_day).days > 7:
        return {"status": "benchmark_prices_missing_at_horizon", "net_return": None, "max_drawdown": None}
    mark.loc[exit_day:] = net
    complete = mark.notna().all()
    dd = (1 - mark / mark.cummax().clip(lower=start_cash)).max()
    return {"status": "cost_adjusted_index_reference" if complete else "benchmark_marks_incomplete",
            "net_return": float(net / start_cash - 1) if complete else None,
            "max_drawdown": float(dd) if complete else None,
            "daily_nav": {str(d.date()): float(v) if pd.notna(v) else None for d, v in mark.items()}}


def compare(trades: pd.DataFrame, start_cash=20000.0, max_positions=15, *, marks=None, benchmark_marks=None):
    from .alpha import dev_only, split_of
    required = {"entry_date", "exit_date", "campaign_key", "invested_eur", "buy_cost",
                "published", "variant", "qualified", "status", "executed", "complete"}
    if trades.empty or not required.issubset(trades.columns):
        return {}
    d = dev_only(trades).copy()
    d["split"] = d.published.map(split_of)
    out = {}
    if "identity_verified" in d:
        d = d[(d.identity_verified == True) & (d.verified == True)]
    for split, g in d.groupby("split"):
        from .alpha import SPLITS
        edge = pd.Timestamp(SPLITS[split][1])
        entry = pd.to_datetime(g.entry_date, errors="coerce")
        end = pd.to_datetime(g.exit_date, errors="coerce")
        g = g.loc[(entry <= edge) & (end.isna() | (end >= entry))].copy()
        if g.empty:
            continue
        if marks:
            g.loc[end > edge, ["complete", "position_closes"]] = False
            calendar = pd.date_range(pd.to_datetime(g.entry_date).min(), edge, freq="D")
        else:
            calendar = None
        policies = {v: g[(g.variant == v) & (g.qualified == True)] for v in g.variant.unique()}
        from .event_study import ABLATION_FILTERS
        main = g[g.variant == "main"]
        for f in ABLATION_FILTERS:
            policies["without_" + f] = main[(main.qualified == True) | (main.status == "verworfen: " + f)]
        policies["first_signal_per_region"] = (main[main.qualified == True]
                                               .sort_values(["published", "campaign_key"])
                                               .drop_duplicates(["region"]))
        policies["cash"] = g.iloc[0:0]
        for name, rows in policies.items():
            out[split + "/" + name] = replay(rows, start_cash, max_positions, marks=marks, calendar=calendar)
        if benchmark_marks is not None and calendar is not None:
            from ..common import params
            for region in sorted(set(g.region)):
                prices = benchmark_marks(region, calendar)
                out[f"{split}/buy_and_hold/{region}"] = buy_and_hold(
                    prices, calendar, start_cash, region, params()["costs"])
    return out
