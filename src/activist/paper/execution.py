"""Orderausführung und Kostenmodell (verbindlich; identisch für E-Mail, Journal und Ereignisstudie)."""
from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd


@dataclass(frozen=True)
class Execution:
    day: pd.Timestamp
    price: float
    qty: float


@dataclass
class Fill:
    filled: bool
    fill_date: pd.Timestamp | None
    price: float | None
    fill_ratio: float
    limit: float | None
    reason: str = ""
    first_fill_date: pd.Timestamp | None = None
    fill_days: int = 0          # Anzahl Tage mit Teilausführung -> je Tag eine Ordergebühr
    executions: tuple[Execution, ...] = ()


def cost_one_side(half_spread: float | None, cfg: dict, region: str, side: str,
                  position_eur: float | None = None) -> float:
    """Kosten je Seite als Anteil: halbe Spanne (min. 0,5 %) + 0,25 % + Abgaben + feste Ordergebühr / Positionswert."""
    spread = half_spread if half_spread is not None and math.isfinite(half_spread) else cfg["default_half_spread"]
    c = max(spread, cfg["min_half_spread"])
    c += cfg["fx_and_fees"]
    if position_eur:
        c += cfg.get("fixed_fee_eur_per_order", 0.0) / position_eur
    if side == "buy":
        c += cfg.get("levies_buy", {}).get(region, 0.0)
    return c


def simulate_entry(px: pd.DataFrame, decision_time: pd.Timestamp, order_cfg: dict, position_value: float,
                   lot_size: int | None = None) -> Fill:
    """px: Tageskurse (open, high, low, close, volume) mit DatetimeIndex.
    Erster Handelstag nach Veröffentlichung; Limit = letzter Schluss vor Order * (1+3 %)."""
    from ..features.universe import local_day
    d0 = local_day(decision_time)                       # identisch zu den Merkmalen (kein Look-ahead)
    before = px[px.index <= d0]
    after = px[px.index > d0]
    if before.empty or after.empty:
        return Fill(False, None, None, 0.0, None, "keine Kurse")
    limit = float(before.close.iloc[-1]) * (1 + order_cfg["limit_above_last_close"])
    if not math.isfinite(position_value) or position_value <= 0:
        raise ValueError("ungültiges Ordervolumen")
    remaining = position_value
    executions = []
    for ts, row in after.head(order_cfg["max_fill_days"]).iterrows():
        if row.open <= limit:
            p = float(row.open)
        elif row.low < limit:          # konservativ: Limit muss durchhandelt werden, Berührung reicht nicht
            p = limit
        else:
            continue
        vol = float(row.volume) if pd.notna(row.volume) else 0.0     # fehlender Umsatz = keine Ausführung
        if not math.isfinite(p) or p <= 0 or not math.isfinite(vol):
            continue
        qty = min(remaining / p, order_cfg["max_share_of_daily_volume"] * vol)
        if lot_size:
            qty = math.floor(qty / lot_size) * lot_size
        if qty <= 0:
            continue
        executions.append(Execution(ts, p, qty))
        remaining -= qty * p
        if remaining <= 1e-9:
            break
    if not executions:
        reason = "Mindestorder am Fill nicht erreicht" if lot_size else "Limit nicht erreicht / kein Umsatz"
        return Fill(False, None, None, 0.0, limit, reason)
    notional = sum(x.price * x.qty for x in executions)
    return Fill(True, executions[-1].day, notional / sum(x.qty for x in executions),
                notional / position_value, limit, first_fill_date=executions[0].day,
                fill_days=len(executions), executions=tuple(executions))


def entry_cashflows(fill, eur_per_local, half_spread, cfg, region):
    """Translate each partial execution at its available rate, charging its own order fee."""
    rows = []
    for x in fill.executions:
        rate = float(eur_per_local(x.day.date()))
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Einstiegswechselkurs fehlt")
        eur = x.price * x.qty * rate
        cost = cost_one_side(half_spread, cfg, region, "buy", eur)
        rows.append({"date": str(x.day.date()), "qty": x.qty, "price": x.price,
                     "eur_per_local": rate, "notional_eur": eur, "cost_eur": eur * cost})
    return rows


def net_returns(debit_eur, proceeds_eur, benchmark_eur):
    net = proceeds_eur / debit_eur - 1
    return net, net - benchmark_eur if benchmark_eur is not None else None


def benchmark_return(cashflows, close_eur):
    """Match benchmark subscriptions to the timing and EUR weights of actual partial fills."""
    if close_eur is None or not math.isfinite(close_eur) or close_eur <= 0:
        return None
    if not cashflows or any(not x or not math.isfinite(x) or x <= 0 for _, x in cashflows):
        return None
    capital = sum(eur for eur, _ in cashflows)
    return close_eur * sum(eur / quote for eur, quote in cashflows) / capital - 1


def simulate_exit(px: pd.DataFrame, entry_date: pd.Timestamp, entry_price: float, exit_cfg: dict,
                  exit_events: list[pd.Timestamp] = (), stop: bool = True,
                  max_days: int | None = None) -> tuple[pd.Timestamp, float, str]:
    """Ausstieg: Reduzierungsmeldung (nächste Eröffnung) | Stopp -30 % | 12 Monate. Gibt Datum, Kurs, Grund."""
    path = px[px.index > entry_date]
    max_days = max_days or exit_cfg["max_holding_days"]
    ev = sorted(pd.Timestamp(e).tz_localize(None) if pd.Timestamp(e).tzinfo else pd.Timestamp(e) for e in exit_events)
    for i, (ts, row) in enumerate(path.iterrows()):
        if ev and ts > ev[0]:
            return ts, float(row.open), "Aktivist reduziert"
        if stop and row.low <= entry_price * (1 + exit_cfg["stop_loss"]):
            return ts, min(float(row.open), entry_price * (1 + exit_cfg["stop_loss"])), "Stopp"
        if i + 1 >= max_days:
            return ts, float(row.close), "Haltedauer"
    if path.empty:
        return entry_date, entry_price, "keine Folgekurse"
    return path.index[-1], float(path.close.iloc[-1]), "offen (Datenende)"
