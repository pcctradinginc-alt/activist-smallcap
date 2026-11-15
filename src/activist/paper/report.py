"""Wochenbericht: offene Positionen, Entwicklung, Cash, Depotwert, Rückgang, verworfene Kandidaten der Woche."""
from __future__ import annotations
import csv, re
from datetime import date, timedelta
from ..common import JOURNAL, REPORTS, file_hash
from .. import health
from .portfolio import SHADOW, TRADES, load_state, portfolio_value


def _de(s: str) -> str:
    """Englische Zahlenformate -> deutsche (1,234.5 -> 1.234,5)."""
    return re.sub(r"\d[\d,]*\.?\d*", lambda m: m.group(0).replace(",", "\x00").replace(".", ",").replace("\x00", "."), s)


def _status_parts() -> dict:
    st = health.status()
    dq, al, lr = st["data_quality"], st["alpha"], st["learning"]
    share = "–" if dq.get("verified_share") is None else f"{dq['verified_share']:.0%}"
    n = lambda v: "–" if v is None else str(v)
    combos = [f"{c}: {v.get('status', '?')}" + (f" (Ø {v['mean']:+.1%}, p {v['p_holm']:.3f}, n={v.get('n')})"
                                                if v.get("mean") is not None and v.get("p_holm") is not None else "")
              for c, v in sorted(al["per_combo"].items())]
    return {"st": st, "share": share, "n": n, "combos": combos,
            "yes": al["status"] == "nachgewiesen", "learn": f"{lr['closed_campaigns']} / {lr['needed']}"}


def _status_text() -> list[str]:
    p = _status_parts()
    st, dq, al, lr = p["st"], p["st"]["data_quality"], p["st"]["alpha"], p["st"]["learning"]
    return ["SYSTEMSTATUS",
            f"Datenqualität: geprüfter Anteil {p['share']}",
            f"Gültige Ereignisse/Kampagnen: {p['n'](dq.get('n_events'))} Ereignisse | {p['n'](dq.get('n_campaigns_valid'))} gültige Kampagnen"
            f" | {p['n'](dq.get('n_campaigns_unverified'))} ungeprüft",
            f"Alpha-Nachweis: {'JA' if p['yes'] else 'NEIN'} ({al['status']})" + (" | " + "; ".join(p["combos"]) if p["combos"] else ""),
            "Ungeklärte Fehler: " + ("; ".join(st["errors"]) if st["errors"] else "keine"),
            f"Lernfortschritt: {p['learn']} abgeschlossene Kampagnen | {lr['status']}", ""]


def _status_card(H) -> str:
    p = _status_parts()
    st, dq, al, lr = p["st"], p["st"]["data_quality"], p["st"]["alpha"], p["st"]["learning"]
    inner = H.kpis([("Geprüfter Anteil", p["share"]), ("Gültige Kampagnen", p["n"](dq.get("n_campaigns_valid"))),
                    ("Ereignisse", p["n"](dq.get("n_events")))])
    inner += '<div style="height:14px"></div>' + H.pill("Alpha: " + ("JA" if p["yes"] else "NEIN"), H.GREEN if p["yes"] else H.RED)
    inner += " " + H.pill(al["status"])
    if p["combos"]:
        inner += H.text(H.e("; ".join(p["combos"])), 13, H.SUB)
    inner += '<div style="height:10px"></div>' + H.text("Ungeklärte Fehler: " + (H.e("; ".join(st["errors"])) if st["errors"] else "keine"), 14)
    inner += H.text(f"Lernfortschritt: {p['learn']} abgeschlossene Kampagnen · {H.e(lr['status'])}", 14)
    return H.card("Systemstatus", inner)


def weekly(today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    st = load_state()
    val = portfolio_value(st, today)
    dd = 1 - val / st["peak_value"] if st["peak_value"] else 0.0
    perf = val / st["start_value"] - 1
    L = [f"WOCHENBERICHT PAPIERDEPOT – KW {today:%G-%V} (Stand {today})",
         "Schattenmodus: Regionen ohne bestandenen Nachweis sind mit [nicht nachgewiesen] markiert. Kein echtes Geld.", "",
         *_status_text(),
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


def weekly_html(today: date | None = None) -> str:
    from ..notify import html as H
    today = today or date.today()
    st = load_state()
    val = portfolio_value(st, today)
    dd = 1 - val / st["peak_value"] if st["peak_value"] else 0.0
    perf = val / st["start_value"] - 1
    week = [h for h in st["history"] if h["date"] >= str(today - timedelta(days=7))]
    wk = (week[-1]["value"] / week[0]["value"] - 1) if len(week) >= 2 else None
    cards = [_status_card(H), H.card(None, H.kpis([("Depotwert", H.eur(val)), ("Seit Start", H.signed(perf)), ("Diese Woche", H.signed(wk))])
                    + '<div style="height:18px"></div>'
                    + H.kpis([("Cash", f'{H.eur(st["cash"])} <span style="font-size:13px;color:#86868b">'
                                       f'{st["cash"] / val:.0%}</span>'.replace(".", ",")),
                              ("Rückgang vom Hoch", H.signed(-dd) if dd > 0 else H.signed(0.0)),
                              ("Positionen", str(len(st["positions"])))]))]
    rows = []
    for x in sorted(st["positions"].values(), key=lambda v: v["entry_date"]):
        r = x["last_eur"] / x["entry_eur"] - 1
        tag = "" if x.get("validated") else "<br>" + H.pill("nicht nachgewiesen", H.ORANGE)
        trig = "<br>" + H.pill("Ausstieg: " + x["exit_trigger"], H.RED) if x.get("exit_trigger") else ""
        rows.append([f'<b>{H.e(x["ticker"])}</b><div style="color:{H.SUB};font-size:12px">{H.e(x.get("activist"))} · '
                     f'{H.e(x["region"])} · Kl. {H.e(x.get("signal_class"))}</div>{tag}{trig}',
                     H.e(x["entry_date"][8:10] + "." + x["entry_date"][5:7] + "."),
                     H.eur(x["entry_eur"], 2) + f'<div style="color:{H.SUB};font-size:12px">→ {H.eur(x["last_eur"], 2)}</div>',
                     H.signed(r), H.eur(x["qty"] * x["last_eur"])])
    cards.append(H.card("Offene Positionen", H.table(["Titel", "Seit", "Kurs", "+/−", "Wert"], rows,
                                                      ["left", "left", "right", "right", "right"])))
    if st["orders"]:
        cards.append(H.card("Offene Orders", H.table(["Titel", "Order", "Betrag"], [
            [f'<b>{H.e(o["ticker"])}</b><div style="color:{H.SUB};font-size:12px">{H.e(o.get("activist"))} · {H.e(o["region"])}</div>',
             H.e(o["order_date"]), H.eur(o["position_eur"])] for o in st["orders"].values()], ["left", "left", "right"])))
    closed_w = [c for c in st["closed"] if c["exit_date"] >= str(today - timedelta(days=7))]
    if closed_w:
        cards.append(H.card("Ausstiege diese Woche", H.table(["Titel", "Grund", "Brutto", "Index", "Netto ggü. Index"], [
            [f'<b>{H.e(c["ticker"])}</b>', H.e(c["reason"]), H.signed(c["gross"]), H.signed(c["bench"]), H.signed(c["net_excess"])]
            for c in closed_w], ["left", "left", "right", "right", "right"])))
    if st["closed"]:
        n = [c["net_excess"] for c in st["closed"]]
        cards.append(H.card("Bilanz abgeschlossener Trades", H.kpis([
            ("Trades", str(len(n))), ("Ø netto ggü. Index", H.signed(sum(n) / len(n))),
            ("Trefferquote", f"{sum(1 for v in n if v > 0) / len(n):.0%}")])))
    sh = []
    if SHADOW.exists():
        with open(SHADOW) as f:
            sh = [r for r in csv.DictReader(f) if (r.get("date") or "") >= str(today - timedelta(days=7))]
    cards.append(H.card("Verworfene Kandidaten", H.table(["Titel", "Grund"], [
        [f'<b>{H.e(r["ticker"] or "?")}</b><div style="color:{H.SUB};font-size:12px">{H.e(r["region"])}</div>',
         f'<span style="color:{H.SUB}">{H.e(r["reason"])}</span>'] for r in sh[:25]])))
    footer = (f"Journal-Prüfwert {file_hash(TRADES) if TRADES.exists() else '–'} · Start {st['start_date']} mit "
              f"{H.eur(st['start_value'])}<br>Papier-Trades aus einem Forschungssystem. Kein echtes Geld. Keine Anlageberatung.")
    return H.page("activist-smallcap · Papierdepot", "Wochenbericht", f"KW {today:%V} · Stand {today:%d.%m.%Y}", cards, footer,
                  banner="Schattenmodus – Regionen ohne bestandene Ereignisstudie sind markiert.")
