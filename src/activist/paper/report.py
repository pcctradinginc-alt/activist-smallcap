"""Wochenbericht: offene Positionen, Entwicklung, Cash, Depotwert, Rückgang, verworfene Kandidaten der Woche."""
from __future__ import annotations
import csv, re
from datetime import date, timedelta
from ..common import JOURNAL, REPORTS, file_hash
from .portfolio import SHADOW, TRADES, load_state, portfolio_value


def _de(s: str) -> str:
    """Englische Zahlenformate -> deutsche (1,234.5 -> 1.234,5)."""
    return re.sub(r"\d[\d,]*\.?\d*", lambda m: m.group(0).replace(",", "\x00").replace(".", ",").replace("\x00", "."), s)


def weekly(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    st = load_state()
    val = portfolio_value(st, today)
    dd = 1 - val / st["peak_value"] if st["peak_value"] else 0.0
    perf = val / st["start_value"] - 1
    L = [f"WOCHENBERICHT PAPIERDEPOT – KW {today:%G-%V} (Stand {today})",
         "Schattenmodus: Regionen ohne bestandenen Nachweis sind mit [nicht nachgewiesen] markiert. Kein echtes Geld.", "",
         "DEPOT",
         f"Depotwert: {val:,.0f} € | Start {st['start_value']:,.0f} € am {st['start_date']} | Entwicklung {perf:+.2%}",
         f"Cash: {st['cash']:,.0f} € ({st['cash'] / val:.0%}) | Höchststand {st['peak_value']:,.0f} € | Rückgang {dd:.1%}",
         f"Offene Positionen: {len(st['positions'])} | offene Orders: {len(st['orders'])} | abgeschlossen: {len(st['closed'])}", ""]
    week = [h for h in st["history"] if h["date"] >= str(today - timedelta(days=7))]
    if len(week) >= 2:
        L.append(f"Woche: {week[0]['value']:,.0f} € -> {week[-1]['value']:,.0f} € ({week[-1]['value'] / week[0]['value'] - 1:+.2%})")
        L.append("")
    L.append("OFFENE POSITIONEN")
    if not st["positions"]:
        L.append("keine")
    for pid, x in sorted(st["positions"].items(), key=lambda kv: kv[1]["entry_date"]):
        r = x["last_eur"] / x["entry_eur"] - 1
        flag = "" if x.get("validated") else " [nicht nachgewiesen]"
        trig = f" | Ausstieg ausgelöst: {x['exit_trigger']}" if x.get("exit_trigger") else ""
        L.append(f"- {x['ticker']} ({x['region']}, Kl. {x.get('signal_class')}) {x.get('activist')} | seit {x['entry_date']} | "
                 f"Einstieg {x['entry_eur']:.2f} € -> {x['last_eur']:.2f} € ({r:+.1%}) | Wert {x['qty'] * x['last_eur']:,.0f} €{flag}{trig}")
    L += ["", "OFFENE ORDERS"]
    L += [f"- {o['ticker']} ({o['region']}) {o.get('activist')} | Order {o['order_date']} | {o['position_eur']:,.0f} €"
          for o in st["orders"].values()] or ["keine"]
    closed_w = [c for c in st["closed"] if c["exit_date"] >= str(today - timedelta(days=7))]
    L += ["", "AUSSTIEGE DIESE WOCHE"]
    L += [f"- {c['ticker']} {c['reason']} | brutto {c['gross']:+.1%} | Index {c['bench']:+.1%} | netto gegen Index {c['net_excess']:+.1%}"
          for c in closed_w] or ["keine"]
    if st["closed"]:
        n = [c["net_excess"] for c in st["closed"]]
        L += ["", f"Alle abgeschlossenen Trades: {len(n)} | Ø Nettoüberrendite {sum(n) / len(n):+.1%} | "
                  f"positiv {sum(1 for v in n if v > 0) / len(n):.0%}"]
    L += ["", "VERWORFENE KANDIDATEN DIESE WOCHE"]
    rows = []
    if SHADOW.exists():
        with open(SHADOW) as f:
            rows = [r for r in csv.DictReader(f) if (r.get("date") or "") >= str(today - timedelta(days=7))]
    L += [f"- {r['ticker'] or '?'} ({r['region']}): {r['reason']}" for r in rows[:25]] or ["keine"]
    if len(rows) > 25:
        L.append(f"… und {len(rows) - 25} weitere")
    L += ["", f"Journal-Prüfwert: {file_hash(TRADES) if TRADES.exists() else '-'}",
          "Papier-Trades aus einem Forschungssystem. Keine Anlageberatung."]
    body = "\n".join(_de(l) if not l.startswith("Journal-Prüfwert") else l for l in L)
    path = REPORTS / f"weekly_{today:%G-W%V}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return _de(f"[Wochenbericht] Papierdepot KW {today:%V}: {val:,.0f} € ({perf:+.1%}), {len(st['positions'])} Positionen"), body
