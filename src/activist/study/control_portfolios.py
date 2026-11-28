"""Exploratory capital-constrained replay of precomputed fills.

Each strategy has independent cash and inventory. No future outcome is
used to select entries. Open positions retain their cost basis: this is
a cash-flow replay, not a marked-to-market performance backtest.
"""
from __future__ import annotations
import math
import pandas as pd

def replay(rows: pd.DataFrame, start_cash=20000.0, max_positions=15):
    cash = float(start_cash)
    held, ledger = {}, []
    rejected = 0
    if rows.empty:
        return {"cash": cash, "open_cost_basis": 0.0, "ledger": [], "rejected": 0}
    rows = rows.copy()
    rows["entry_date"] = pd.to_datetime(rows.entry_date, errors="coerce").dt.normalize()
    rows["exit_date"] = pd.to_datetime(rows.exit_date, errors="coerce").dt.normalize()
    rows = rows.dropna(subset=["entry_date"]).sort_values(["entry_date", "campaign_key"])
    def settle(day):
        nonlocal cash
        for key, pos in sorted(list(held.items())):
            if pos["complete"] and pd.notna(pos["exit_date"]) and pos["exit_date"] < day:
                proceeds = pos["value"] * (1 + pos["gross"]) * (1 - pos["sell"])
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
        debit = value * (1 + buy)
        if (not math.isfinite(debit) or debit <= 0 or debit > cash or len(held) >= max_positions
                or key in held or any(p["ticker"] == ticker for p in held.values())):
            rejected += 1
            continue
        gross, sell = row.get("gross"), row.get("sell_cost")
        complete = row.get("complete") == True and pd.notna(gross) and pd.notna(sell)
        held[key] = {"ticker": ticker, "value": value, "debit": debit,
                     "gross": gross, "sell": sell, "complete": complete,
                     "exit_date": row["exit_date"]}
        cash -= debit
        ledger.append({"day": str(day.date()), "side": "BUY", "key": key,
                       "cash_flow": -debit, "cash": cash})
    # Only settle exits observed in the simulated sample; no cash from open outcomes.
    horizon = rows.exit_date.max()
    if pd.notna(horizon):
        settle(horizon + pd.Timedelta(days=1))
    return {"cash": cash, "open_cost_basis": sum(p["debit"] for p in held.values()),
            "open_positions": len(held), "ledger": ledger, "rejected": rejected,
            "status": "exploratory_cash_flow_replay_not_mtm"}

def compare(trades: pd.DataFrame, start_cash=20000.0, max_positions=15):
    from .alpha import dev_only, split_of
    required = {"entry_date", "exit_date", "campaign_key", "invested_eur", "buy_cost",
                "published", "variant", "qualified", "status", "executed", "complete"}
    if trades.empty or not required.issubset(trades.columns):
        return {}
    d = dev_only(trades).copy()
    d["split"] = d.published.map(split_of)
    out = {}
    for split, g in d.groupby("split"):
        policies = {v: g[(g.variant == v) & (g.qualified == True)] for v in g.variant.unique()}
        policies["cash"] = g.iloc[0:0]
        for name, rows in policies.items():
            out[split + "/" + name] = replay(rows, start_cash, max_positions)
    return out
