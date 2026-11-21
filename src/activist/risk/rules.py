"""Risikoprüfung als Tor: Ja/Nein-Regeln. Ein Verstoß verwirft – kein Konfidenzwert gleicht ihn aus.
Je eine Stelle: candidate_reasons (Kandidatenregeln, Studie und Papierdepot) und check_portfolio (Portfoliogrenzen + Cash)."""
from __future__ import annotations
from datetime import date, timedelta
from ..common import risk_limits

def next_earnings(dates: list[date], d0: date) -> date | None:
    """Erwartete nächste Quartalszahlen ab d0 (nur Termine <= d0 zählen, kein Look-ahead): gleicher Termin des Vorjahres
    (+364 Tage, falls ein Termin 300-400 Tage zurückliegt) und letzter Termin + 91 Tage; frühester Termin >= d0. Keine Termine = None."""
    known = sorted(d for d in dates if d <= d0)
    if not known:
        return None
    proj = [e + timedelta(days=364) for e in known if 300 <= (d0 - e).days <= 400] + [known[-1] + timedelta(days=91)]
    ahead = [p for p in proj if p >= d0]
    return min(ahead) if ahead else None


def candidate_reasons(row: dict, position_local: float | None, lim: dict | None = None) -> list[str]:
    """Die historisch bestimmbaren Ausschlussregeln – EINE Stelle für Ereignisstudie und Papierdepot (alle Regionen).
    row: half_spread, runup_20d, price, activist_cost, adv (Landeswährung); position_local: Positionsgröße in
    Landeswährung (None = Wechselkurs fehlt -> kein stiller Fallback, sondern Ausschluss)."""
    L = lim or risk_limits()["candidate"]
    ok = lambda v: v is not None and v == v            # nicht None und nicht NaN
    r = []
    if 2 * (row.get("half_spread") or 0) > L["max_spread"]:
        r.append("Spread")
    if ok(row.get("runup_20d")) and row["runup_20d"] > L["max_runup_20d"]:
        r.append("Anstieg 20T")
    if ok(row.get("activist_cost")) and row["activist_cost"] and ok(row.get("price")) \
            and row["price"] > row["activist_cost"] * (1 + L["max_premium_over_activist_cost"]):
        r.append("Kurs über Aktivisten-Einstand")
    if ok(row.get("major_holder")) and row["major_holder"] > L["max_major_holder"]:
        r.append("Großaktionär über Grenze")          # kontrollierte Gesellschaft; fehlende Daten schließen nicht aus
    if ok(row.get("days_to_earnings")) and L.get("earnings_blackout_days") is not None and 0 <= row["days_to_earnings"] <= L["earnings_blackout_days"]:
        r.append("Quartalszahlen-Sperre")             # unbekannter Termin (JP/EU) schließt nicht aus
    if position_local is None:
        r.append("Wechselkurs fehlt")
    elif not ok(row.get("adv")) or position_local > L["max_position_share_of_adv"] * row["adv"]:
        r.append("Liquidität")
    return r


def _sector(x: dict) -> str:
    s = x.get("sector")
    return s if isinstance(s, str) and s else f"unbekannt:{x.get('region')}"   # None/NaN/"" = eigener Topf je Region


def check_portfolio(held: list[dict], cand: dict, value: float, cash_free: float, lim: dict | None = None,
                    regions_passed: int = 0) -> str | None:
    """Das EINE Portfoliotor. held: offene Positionen + offene Orders (ticker, activist, region, sector, position_eur,
    optional fill_ratio); cand: ticker, activist, region, sector, position_eur, buy_cost (Kosten einer Seite inkl. Gebühren);
    value: Depotwert EUR; cash_free: Cash abzüglich für offene Orders reservierten Cashs. Unbekannte Branche = eigener Topf
    je Region (fail-closed). Regionsgrenze gilt erst, wenn mindestens 3 Regionen den Nachweis bestanden haben.
    Gibt den ersten verletzten Grund zurück, sonst None."""
    P = (lim or risk_limits())["portfolio"]
    expo = lambda xs: sum(x.get("position_eur", 0) * x.get("fill_ratio", 1) for x in xs)
    if len(held) >= P["max_positions"]:
        return "max. Positionen erreicht"
    if sum(1 for x in held if x.get("activist") == cand.get("activist")) >= P["max_per_activist"]:
        return "max. Positionen je Aktivist"
    if any(x.get("ticker") == cand.get("ticker") for x in held):
        return "Titel bereits im Depot/Orderbuch"
    pe = cand["position_eur"]
    if pe > P["max_position"] * value:
        return "Positionsgröße über Grenze"
    if expo([x for x in held if _sector(x) == _sector(cand)]) + pe > P["max_sector"] * value:
        return "Branchengrenze"
    if regions_passed >= 3 and expo([x for x in held if x.get("region") == cand.get("region")]) + pe \
            > P["max_region_once_3_regions_pass"] * value:
        return "Regionsgrenze"
    if pe * (1 + cand.get("buy_cost", 0)) > cash_free:
        return "Cash reicht nicht"
    return None


def portfolio_size_factor(drawdown: float, lim: dict | None = None) -> float:
    P = (lim or risk_limits())["portfolio"]
    if drawdown >= P["drawdown_stop"]:
        return 0.0
    return 0.5 if drawdown >= P["drawdown_half_size"] else 1.0
