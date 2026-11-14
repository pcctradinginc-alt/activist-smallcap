"""Orderausführung und Kostenmodell (verbindlich; identisch für E-Mail, Journal und Ereignisstudie)."""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd


@dataclass
class Fill:
    filled: bool
    fill_date: pd.Timestamp | None
    price: float | None
    fill_ratio: float
    limit: float | None
    reason: str = ""
    fill_days: int = 0          # Anzahl Tage mit Teilausführung -> je Tag eine Ordergebühr


def cost_one_side(half_spread: float | None, cfg: dict, region: str, side: str,
                  position_eur: float | None = None) -> float:
    """Kosten je Seite als Anteil: halbe Spanne (min. 0,5 %) + 0,25 % + Abgaben + feste Ordergebühr / Positionswert."""
    c = max(half_spread if half_spread is not None else cfg["default_half_spread"], cfg["min_half_spread"])
    c += cfg["fx_and_fees"]
    if position_eur:
        c += cfg.get("fixed_fee_eur_per_order", 0.0) / position_eur
    if side == "buy":
        c += cfg.get("levies_buy", {}).get(region, 0.0)
    return c


def simulate_entry(px: pd.DataFrame, decision_time: pd.Timestamp, order_cfg: dict, position_value: float) -> Fill:
    """px: Tageskurse (open, high, low, close, volume) mit DatetimeIndex.
    Erster Handelstag nach Veröffentlichung; Limit = letzter Schluss vor Order * (1+3 %)."""
    from ..features.universe import local_day
    d0 = local_day(decision_time)                       # identisch zu den Merkmalen (kein Look-ahead)
    before = px[px.index <= d0]
    after = px[px.index > d0]
    if before.empty or after.empty:
        return Fill(False, None, None, 0.0, None, "keine Kurse")
    limit = float(before.close.iloc[-1]) * (1 + order_cfg["limit_above_last_close"])
    remaining, notional, qty_val, first, days = 1.0, 0.0, 0.0, None, 0
    for ts, row in after.head(order_cfg["max_fill_days"]).iterrows():
        if row.open <= limit:
            p = float(row.open)
        elif row.low <= limit:
            p = limit
        else:
            continue
        vol = float(row.volume) if pd.notna(row.volume) else 0.0     # fehlender Umsatz = keine Ausführung
        cap = order_cfg["max_share_of_daily_volume"] * vol * p / max(position_value, 1e-9)
        take = min(remaining, cap)
        if take <= 0:
            continue
        notional += take * p; qty_val += take; remaining -= take; days += 1
        first = first or ts
        if remaining <= 1e-9:
            break
    if qty_val == 0:
        return Fill(False, None, None, 0.0, limit, "Limit nicht erreicht / kein Umsatz")
    return Fill(True, first, notional / qty_val, qty_val, limit, fill_days=days)


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
