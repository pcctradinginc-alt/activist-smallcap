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
            evs += edgar.fetch_events(d, with_headers=not a.no_headers, limit=a.limit)
        if a.source in ("edinet", "all"):
            try:
                evs += edinet.fetch_events(d)
            except RuntimeError as e:
                print(f"EDINET übersprungen: {e}")
        n = store.append(evs); total += n
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
    ds, funnel = build(evs, parse_filings=not a.no_parse)
    U = params()["universe"]

    def pl(t, r, s, e):
        return prices.load(t, r, s, e)[0]

    def bl(r, s, e):
        return prices.load(U[r]["benchmark"], r, s, e)[0]
    res, trades = run(ds, pl, bl, variants=not a.main_only)
    print(write_report(res, funnel, ds, trades))


def cmd_backfill(a):
    from .sources.edgar_backfill import backfill
    for y in range(a.start, a.end + 1):
        for q in range(1, 5):
            if a.quarter and q != a.quarter:
                continue
            print(backfill(y, q, limit=a.limit), flush=True)


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
    a = ap.parse_args(argv); a.fn(a)


if __name__ == "__main__":
    main()
