"""Risikoprüfung als Tor: Ja/Nein-Regeln. Ein Verstoß verwirft – kein Konfidenzwert gleicht ihn aus."""
from __future__ import annotations
from ..common import risk_limits

VERWERFEN, VERKLEINERN, SPERREN, VERSCHIEBEN = "verwerfen", "verkleinern", "sperren", "verschieben"


def check_candidate(c: dict, lim: dict | None = None) -> list[tuple[str, str]]:
    """c: dict mit position_eur, adv_eur, spread, price, activist_cost, runup_20d, capital_raise, going_concern,
    major_holder, new_poison_pill, mapping_ok, prices_ok, days_to_earnings. Fehlende Felder => sperren."""
    L = (lim or risk_limits())["candidate"]; v = []
    def need(*keys):
        return all(c.get(k) is not None for k in keys)
    if not need("position_eur", "adv_eur") or c["position_eur"] > L["max_position_share_of_adv"] * c["adv_eur"]:
        v.append(("Liquidität", VERKLEINERN))
    if c.get("spread") is None or c["spread"] > L["max_spread"]:
        v.append(("Spread", VERWERFEN))
    if c.get("activist_cost") and c.get("price") and c["price"] > c["activist_cost"] * (1 + L["max_premium_over_activist_cost"]):
        v.append(("Vorweggenommener Kurs", VERWERFEN))
    if c.get("runup_20d") is not None and c["runup_20d"] > L["max_runup_20d"]:
        v.append(("Vorweggenommener Kurs (20T)", VERWERFEN))
    if c.get("capital_raise") or c.get("going_concern"):
        v.append(("Verwässerung", VERWERFEN))
    if (c.get("major_holder") or 0) >= L["max_major_holder"] or c.get("new_poison_pill"):
        v.append(("Kontrolle", VERWERFEN))
    if not (c.get("mapping_ok") and c.get("prices_ok")):
        v.append(("Datenqualität", SPERREN))
    if c.get("days_to_earnings") is not None and c["days_to_earnings"] <= L["earnings_blackout_days"]:
        v.append(("Termin", VERSCHIEBEN))
    return v


def passes(c: dict, lim: dict | None = None) -> bool:
    return not check_candidate(c, lim)


def portfolio_size_factor(drawdown: float, lim: dict | None = None) -> float:
    P = (lim or risk_limits())["portfolio"]
    if drawdown >= P["drawdown_stop"]:
        return 0.0
    return 0.5 if drawdown >= P["drawdown_half_size"] else 1.0
