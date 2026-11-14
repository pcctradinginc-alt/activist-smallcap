"""Papier-Kauf-Mail nach Konzeptvorlage (Text + HTML). 'Was dagegen spricht' ist Pflicht."""
from __future__ import annotations
from . import html as H


def buy_mail(d: dict) -> tuple[str, str, str]:
    if not (d.get("against") or "").strip():
        raise ValueError("Pflichtabschnitt 'Was dagegen spricht' fehlt")
    subj = f"[Papier-Kauf] {d['ticker']} | {d['region']}, Klasse {d['cls']} | {d['activist']} meldet {d['stake']}"
    conf = d["conf"]
    text = "\n".join([
        "EREIGNIS", f"{d['activist']} hat am {d['when']} eine Beteiligung von {d['stake']} an {d['company']} veröffentlicht "
        f"({d['form']}). Forderung: {d['demand']}.", f"Textbeleg: {d['evidence']}", f"Link: {d['url']}",
        f"Kampagne: {d['campaign']}", "", "ORDER (identisch mit dem Journal)",
        f"Kauflimit {d['limit']} = letzter Schluss + 3 %, gültig {d['valid']}", f"Positionsgröße: {d['size']}", "",
        f"ERWARTUNG AUS VERGLEICHSFÄLLEN ({d['region']}, Klasse {d['cls']})",
        f"Erwartete Nettoüberrendite: {conf['mean']} (90-%-Bereich {conf['ci']})", f"Unabhängige Vergleichskampagnen: {conf['n']}",
        f"Schlechtestes Viertel: {conf['q25']} | schlechtester Fall: {conf['worst']}", f"Anteil positiv: {conf['pos']}", "",
        "RISIKOPRÜFUNG", d["risk"], f"Portfolio danach: {d['after']}", "", "WAS DAGEGEN SPRICHT", d["against"], "",
        "AUSSTIEG", "Aktivist reduziert | Ergebnis erreicht | 12 Monate | −30 %", "",
        f"Journal-Prüfwert: {d['hash']}", "Papier-Trade aus einem Forschungssystem. Keine Anlageberatung."])
    lbl = lambda k, v: (f'<tr><td style="font-size:14px;color:{H.SUB};padding:6px 12px 6px 0;white-space:nowrap;vertical-align:top">{H.e(k)}</td>'
                        f'<td style="font-size:14px;color:{H.INK};padding:6px 0">{v}</td></tr>')
    grid = lambda rows: f'<table cellpadding="0" cellspacing="0" role="presentation" width="100%">{"".join(lbl(k, v) for k, v in rows)}</table>'
    cards = [
        H.card("Ereignis", H.text(f'<b>{H.e(d["activist"])}</b> meldet <b>{H.e(d["stake"])}</b> an {H.e(d["company"])}.')
               + '<div style="height:12px"></div>'
               + grid([("Veröffentlicht", H.e(d["when"])), ("Meldung", H.e(d["form"])), ("Forderung", H.pill(d["demand"], H.BLUE)),
                       ("Kampagne", H.e(d["campaign"]))])
               + f'<div style="margin-top:14px;padding:12px 14px;background:{H.BG};border-radius:12px;font-size:13px;'
                 f'color:{H.SUB};font-style:italic">„{H.e(d["evidence"])}“</div>'
               + f'<div style="margin-top:14px"><a href="{H.e(d["url"])}" style="color:{H.BLUE};text-decoration:none;'
                 f'font-size:14px;font-weight:500">Zur Meldung ›</a></div>'),
        H.card("Order", H.kpis([("Kauflimit", H.e(d["limit"])), ("Position", H.e(d["size_short"]))])
               + '<div style="height:10px"></div>' + grid([("Gültig", H.e(d["valid"])), ("Menge", "höchstens 5 % des Tagesumsatzes je Tag"),
                                                            ("Kosten", H.e(d["costs"]))])),
        H.card(f"Erwartung · {d['region']}, Klasse {d['cls']}",
               H.kpis([("Erwartet netto", f'<span style="color:{H.GREEN}">{H.e(conf["mean"])}</span>'),
                       ("90-%-Bereich", f'<span style="font-size:17px">{H.e(conf["ci"])}</span>'),
                       ("Vergleichsfälle", H.e(conf["n"]))]) + '<div style="height:14px"></div>'
               + H.kpis([("Schlechtestes Viertel", f'<span style="color:{H.RED};font-size:17px">{H.e(conf["q25"])}</span>'),
                         ("Schlechtester Fall", f'<span style="color:{H.RED};font-size:17px">{H.e(conf["worst"])}</span>'),
                         ("Anteil positiv", f'<span style="font-size:17px">{H.e(conf["pos"])}</span>')])),
        H.card("Risikoprüfung", H.text(H.e(d["risk"]), 14) + H.text("Portfolio danach: " + H.e(d["after"]), 13, H.SUB)),
        H.card("Was dagegen spricht", H.text(H.e(d["against"]), 15)),
        H.card("Ausstieg", " ".join(H.pill(x, H.INK) for x in ("Aktivist reduziert", "Ergebnis erreicht", "12 Monate", "−30 %"))),
    ]
    footer = f"Journal-Prüfwert {H.e(d['hash'])}<br>Papier-Trade aus einem Forschungssystem. Kein echtes Geld. Keine Anlageberatung."
    html = H.page("activist-smallcap · Papier-Kauf", d["ticker"], f"{d['company']} · {d['region']} · Klasse {d['cls']}", cards, footer,
                  banner=d.get("banner"))
    return subj, text, html
