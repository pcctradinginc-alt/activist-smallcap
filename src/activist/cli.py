"""Kommandozeile: python -m activist.cli <befehl>."""
from __future__ import annotations
import argparse, json
from collections import Counter
from datetime import date, timedelta
from .common import EventStore, JOURNAL, file_hash
from .notify.mail import send


def cmd_fetch(a):
    from .sources import edgar, edinet
    days = [date.fromisoformat(a.date) - timedelta(days=i) for i in range(a.days)] if a.date else \
           [date.today() - timedelta(days=i) for i in range(1, a.days + 1)]
    store, total = EventStore(), 0
    for d in days:
        evs = []
        if a.source in ("edgar", "all"):
            try:
                evs += edgar.fetch_events(d, with_headers=not a.no_headers, limit=a.limit)
            except Exception as e:     # ein Quellenfehler darf den Tageslauf (und das Papierdepot) nicht stoppen
                print(f"::warning::EDGAR {d}: {e}")
        n = store.append(evs)
        if a.source in ("edinet", "all"):
            import os
            from .sources.edinet_backfill import fetch_day
            if os.environ.get("EDINET_API_KEY"):
                try:
                    r = fetch_day(d, os.environ["EDINET_API_KEY"], store); n += r["new"]; evs += [None] * r["docs"]
                except Exception as e:
                    print(f"::warning::EDINET {d}: {e}")
            else:
                print("EDINET übersprungen: EDINET_API_KEY fehlt")
        total += n
        print(f"{d}: {len(evs)} Meldungen, {n} neu")
    print(f"gesamt neu: {total}")


def cmd_status(a):
    from .parse.campaigns import assign
    evs = assign(EventStore().all())
    by = Counter((e["region"], e.get("signal_class") or "-", e["event_type"]) for e in evs)
    lines = [f"Ereignisse gesamt: {len(evs)}",
             f"Kampagnen (erstes Ereignis mit Klasse): {sum(1 for e in evs if e.get('is_first_event'))}",
             f"Ohne Uhrzeit (Folgetag-Regel): {sum(1 for e in evs if not e.get('has_time'))}", ""]
    lines += [f"{r} | Klasse {c} | {t}: {n}" for (r, c, t), n in sorted(by.items())]
    lines += ["", f"Journal-Prüfwert: {file_hash(JOURNAL / 'trades.csv')}",
              "Forschungssystem, Phase 0/1. Keine Anlageberatung."]
    body = "\n".join(lines)
    if a.mail:
        send(f"[Status] Ereignisstudie – Woche {date.today():%G-W%V}", body)
    else:
        print(body)


def cmd_study(a):
    from .common import params
    from .sources import prices
    from .study.dataset import build
    from .study.event_study import run, write_report
    evs = [e for e in EventStore().all() if not a.since or e["published"] >= a.since]
    import pandas as pd
    from .study.dataset import build_jp
    ds, funnel = build(evs, parse_filings=not a.no_parse)
    if any(e.get("source") == "EDINET" for e in evs):
        dj, fj = build_jp(evs)
        ds = pd.concat([ds, dj], ignore_index=True); funnel.update(fj)
    if any(e.get("region") in ("UK", "NORDIC", "BENELUX_FR", "DACH") for e in evs):
        from .study.dataset_eu import build_eu, load_px
        de, fe = build_eu(evs)
        ds = pd.concat([ds, de], ignore_index=True); funnel.update(fe)
    U = params()["universe"]

    def pl(t, r, s, e):
        if r in ("UK", "NORDIC", "BENELUX_FR", "DACH"):
            from .study.dataset_eu import load_px
            return load_px(t, s, e)          # EU: vollständiger Yahoo-Ticker, Pence -> Pfund
        return prices.load(t, r, s, e)[0]

    def bl(r, s, e):
        b = U[r]["benchmark"]
        if b.startswith("^"):                 # Indizes ohne Börsensuffix direkt laden
            return prices.yahoo(b, s, e)
        return prices.load(b, r, s, e)[0]
    from .paper.portfolio import eur_per_unit
    fx_cache = {}

    def fxl(t, r, d):            # Landeswährung je EUR (gecacht je Region/Tag)
        k = (r, t.rsplit(".", 1)[-1] if r in ("UK", "NORDIC", "DACH", "BENELUX_FR") else "", d)
        if k not in fx_cache:
            fx_cache[k] = 1 / eur_per_unit(t, r, d)
        return fx_cache[k]
    res, trades = run(ds, pl, bl, variants=not a.main_only, fx_loader=fxl)
    print(write_report(res, funnel, ds, trades, bench_loader=bl))


def cmd_backfill_jp(a):
    from .sources.edinet_backfill import backfill
    backfill(date.fromisoformat(a.start), date.fromisoformat(a.end))


def cmd_backfill_eu(a):
    """EU-Ereignisse einer Quelle abrufen und append-only in den EventStore schreiben (--enrich: TR-1/PDF/Text je Meldung lesen,
    1 Request je Meldung, >= 1 s Abstand -> langsam)."""
    start, end = date.fromisoformat(a.start), date.fromisoformat(a.end)
    if a.source == "nsm":
        from .sources import eu_nsm
        evs = eu_nsm.fetch_events(start, end, enrich_docs=a.enrich)
    elif a.source == "nasdaq_nordic":
        from .sources import eu_nasdaq_nordic
        evs = eu_nasdaq_nordic.fetch_events(start, end, enrich_docs=a.enrich)
    elif a.source == "oslo":
        from .sources import eu_oslo, eu_pdf
        evs = eu_oslo.fetch_events(start, end)
        evs = [eu_pdf.enrich_oslo(e) for e in evs] if a.enrich else evs
    elif a.source == "activist_sites":
        from .sources import campaigns_c
        evs = campaigns_c.fetch_events(start, end)
    elif a.source == "six":
        from .sources import eu_ch
        evs = eu_ch.fetch_events(start, end)
    elif a.source == "de_ir":
        from .sources import eu_de_ir
        evs = eu_de_ir.fetch_events(start, end)
    else:
        from .sources import eu_amf, eu_pdf
        evs = eu_amf.fetch_events(start, end)
        evs = [eu_pdf.enrich_amf(e) for e in evs] if a.enrich else evs
    n = EventStore().append(evs)
    print(f"{a.source} {start}..{end}: {len(evs)} Meldungen, {n} neu")


def cmd_backfill(a):
    from .sources.edgar_backfill import backfill
    for y in range(a.start, a.end + 1):
        for q in range(1, 5):
            if a.quarter and q != a.quarter:
                continue
            print(backfill(y, q, limit=a.limit), flush=True)


def cmd_paper(a):
    from .paper.portfolio import run_day
    print(run_day())


def cmd_weekly(a):
    from .paper.report import weekly, weekly_html
    subj, body = weekly()
    if a.mail:
        send(subj, body, html=weekly_html())
    else:
        print(subj); print(body)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="activist")
    sp = ap.add_subparsers(required=True)
    f = sp.add_parser("fetch"); f.add_argument("--source", default="all", choices=["edgar", "edinet", "all"])
    f.add_argument("--date"); f.add_argument("--days", type=int, default=1); f.add_argument("--limit", type=int)
    f.add_argument("--no-headers", action="store_true"); f.set_defaults(fn=cmd_fetch)
    s = sp.add_parser("status"); s.add_argument("--mail", action="store_true"); s.set_defaults(fn=cmd_status)
    st = sp.add_parser("study"); st.add_argument("--main-only", action="store_true")
    st.add_argument("--since"); st.add_argument("--no-parse", action="store_true"); st.set_defaults(fn=cmd_study)
    b = sp.add_parser("backfill"); b.add_argument("--start", type=int, default=2015); b.add_argument("--end", type=int, default=2015)
    b.add_argument("--quarter", type=int); b.add_argument("--limit", type=int); b.set_defaults(fn=cmd_backfill)
    bj = sp.add_parser("backfill-jp"); bj.add_argument("--start", required=True); bj.add_argument("--end", required=True)
    bj.set_defaults(fn=cmd_backfill_jp)
    be = sp.add_parser("backfill-eu"); be.add_argument("--source", required=True, choices=["nsm", "nasdaq_nordic", "oslo", "amf", "six", "activist_sites", "de_ir"])
    be.add_argument("--start", required=True); be.add_argument("--end", required=True)
    be.add_argument("--enrich", action="store_true"); be.set_defaults(fn=cmd_backfill_eu)
    pp = sp.add_parser("paper"); pp.set_defaults(fn=cmd_paper)
    wk = sp.add_parser("weekly"); wk.add_argument("--mail", action="store_true"); wk.set_defaults(fn=cmd_weekly)
    a = ap.parse_args(argv); a.fn(a)


if __name__ == "__main__":
    main()
