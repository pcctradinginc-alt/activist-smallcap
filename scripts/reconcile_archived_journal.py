"""Reconcile the pre-reset journal against official ECB daily reference rates."""
import argparse
import csv
import json
from decimal import Decimal as D

from activist.common import ROOT, atomic_write_text

ARCHIVE = ROOT / "journal_archive/2026-11-28-pre-p0-reset"
ECB = "https://data-api.ecb.europa.eu/service/data/EXR/D.JPY.EUR.SP00.A"


def money(value):
    return float(value) if value is not None else None


def reconcile(ecb_rates):
    state = json.loads((ARCHIVE / "portfolio.json").read_text())
    final_cash = D(str(state["cash"]))
    initial_cash = D(str(state["start_value"]))
    rows = []
    for trade in csv.DictReader((ARCHIVE / "trades.csv").open()):
        side = trade["side"]
        day = trade["fill_date"]
        position = state["positions"].get(trade["trade_id"], {})
        notional = D(trade["price"]) * D(trade["qty"])
        cost_rate = D(trade["cost_pct"])
        fx = D(str(position["entry_rate"])) if position.get("entry_rate") else None
        ecb = D(ecb_rates[day]) if day in ecb_rates else None
        ecb_notional = notional / ecb if ecb else None
        ecb_cost = ecb_notional * cost_rate if ecb_notional is not None else None
        cfg = json.loads((ARCHIVE / "decisions" / (trade["trade_id"].removesuffix("-S") + ".json")).read_text())
        half_spread = D(str(cfg["half_spread"]))
        # Historical configuration on RESET.source_commit: 0.50% minimum spread,
        # 0.25% undivided fees/FX and EUR 10 per fill day. No separate slippage field.
        spread_rate = max(half_spread, D("0.005"))
        modeled_fees = D("0.0025")
        history_eur = (D(str(position["qty"])) * D(str(position["entry_eur"]))) if fx else None
        ecb_cash = ((-ecb_notional - ecb_cost) if side == "BUY" else (ecb_notional - ecb_cost)) if ecb else None
        rows.append({**trade, "notional_jpy_rounded_csv": money(notional),
                     "ecb_jpy_per_eur": money(ecb), "archived_jpy_per_eur": money(1 / fx) if fx else None,
                     "fx_difference_pct": money(((notional * fx / ecb_notional) - 1) * 100) if fx and ecb else None,
                     "ecb_reference_notional_eur": money(ecb_notional), "ecb_reference_total_cost_eur": money(ecb_cost),
                     "ecb_reference_spread_eur": money(ecb_notional * spread_rate) if ecb else None,
                     "ecb_reference_combined_fx_and_fees_eur": money(ecb_notional * modeled_fees) if ecb else None,
                     "ecb_reference_residual_order_fee_eur": money(ecb_cost - ecb_notional * (spread_rate + modeled_fees)) if ecb else None,
                     "separate_slippage": None,
                     "archived_cash_flow_eur": money(-history_eur * (1 + D(str(position["buy_cost"])))) if fx else None,
                     "csv_rounding_notional_eur": money(notional * fx - history_eur) if fx else None,
                     "ecb_reference_cash_flow_eur": money(ecb_cash),
                     "status": "reconstructed_from_open_position" if fx else "only_independent_reference_available",
                     "board_lot_violation": D(trade["qty"]) % 100 != 0})
    ecb_cash = initial_cash + sum((D(str(r["ecb_reference_cash_flow_eur"])) for r in rows
                                 if r["ecb_reference_cash_flow_eur"] is not None), D(0))
    known = sum((D(str(r["archived_cash_flow_eur"])) for r in rows if r["archived_cash_flow_eur"] is not None), D(0))
    return {"ecb_series": ECB, "starting_cash_eur": money(initial_cash), "archived_cash_eur": money(final_cash),
            "independent_reference_cash_eur": money(ecb_cash),
            "reference_minus_archive_eur": money(ecb_cash - final_cash),
            "unallocated_archived_flows_eur": money(final_cash - initial_cash - known),
            "entries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ecb_csv", type=argparse.FileType("r"))
    args = parser.parse_args()
    data = list(csv.DictReader(args.ecb_csv))
    rates = {r["TIME_PERIOD"]: r["OBS_VALUE"] for r in data if r["KEY"] == "EXR.D.JPY.EUR.SP00.A"}
    report = reconcile(rates)
    atomic_write_text(ROOT / "reports/archived_journal_reconciliation.json", json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "entries"}))


if __name__ == "__main__":
    main()
