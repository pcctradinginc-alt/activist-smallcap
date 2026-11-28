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
    store, total, health_src = EventStore(), 0, {}
    for d in days:
        evs = []
        if a.source in ("edgar", "all"):
            try:
                edgar_rows = edgar.fetch_events(d, with_headers=not a.no_headers, limit=a.limit)
                evs += edgar_rows
                health_src.setdefault("EDGAR", {"ok": True, "n_new": 0})
            except Exception as e:     # ein Quellenfehler darf den Tageslauf (und das Papierdepot) nicht stoppen
                print(f"::warning::EDGAR {d}: {e}")
                health_src["EDGAR"] = {"ok": False, "n_new": 0, "error": str(e)[:200]}
        n = store.append(evs)
        if "EDGAR" in health_src:
            health_src["EDGAR"]["n_new"] += n
        if a.source in ("edinet", "all"):
            import os
            from .sources.edinet_backfill import fetch_day
            if os.environ.get("EDINET_API_KEY"):
                try:
                    r = fetch_day(d, os.environ["EDINET_API_KEY"], store)
                    n += r["new"]
                    evs += [None] * r["docs"]
                    health_src.setdefault("EDINET", {"ok": True, "n_new": 0})["n_new"] += r["new"]
                    if r["parse_errors"]:
                        health_src["EDINET"].update(ok=False, error=f"{r['parse_errors']} Dokumente nicht geparst")
                except Exception as e:
                    print(f"::warning::EDINET {d}: {e}")
                    health_src["EDINET"] = {"ok": False, "n_new": 0, "error": str(e)[:200]}
            else:
                health_src["EDINET"] = {"ok": False, "n_new": 0, "error": "EDINET_API_KEY fehlt"}
        total += n
        health_src.setdefault("tage", {"ok": True, "n_new": 0, "error": None})["n_new"] += n
        print(f"{d}: {len(evs)} Meldungen, {n} neu")
    print(f"gesamt neu: {total}")
    from .health import record_run
    record_run("fetch", health_src)


def cmd_status(a):
    from .parse.campaigns import summary
    sm = summary(EventStore().all())
    lines = [f"Ereignisse gesamt: {sm['n']}", f"Ohne Uhrzeit (Folgetag-Regel): {sm['no_time']}", "",
             "Nach Region: " + ", ".join(f"{k} {v}" for k, v in sorted(sm["by_region"].items())),
             "Nach Quelle: " + ", ".join(f"{k} {v}" for k, v in sorted(sm["by_source"].items())), "",
             f"Journal-Prüfwert: {file_hash(JOURNAL / 'trades.csv')}", "Forschungssystem. Keine Anlageberatung."]
    body = "\n".join(lines)
    if a.mail:
        send(f"[Status] Ereignisse – Woche {date.today():%G-W%V}", body)
    else:
        print(body)


def cmd_freeze(a):
    from .study.alpha import freeze
    rec = freeze(a.note)
    print(f"Protokoll eingefroren am {rec['frozen_at']} (Commit {rec['git_commit']}); {len(rec['hashes'])} Dateien gesichert")

def cmd_study(a):
    from .common import params
    from .sources import prices
    from .study.event_study import evaluate, load_trades, simulate, write_report
    import pandas as pd
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
    from .sources.fx import benchmark_eur_per_unit, eur_per_unit
    fx_cache = {}

    def fxl(t, r, d):            # Landeswährung je EUR (gecacht je Region/Tag)
        k = (r, t.rsplit(".", 1)[-1] if r in ("UK", "NORDIC", "DACH", "BENELUX_FR") else "", d)
        if k not in fx_cache:
            fx_cache[k] = 1 / eur_per_unit(t, r, d)
        return fx_cache[k]
    def benchmark_fxl(region, day):
        return 1 / benchmark_eur_per_unit(region, day)
    if a.reuse and getattr(a, "confirmed_only", False):
        raise SystemExit("--reuse und --confirmed-only schließen sich aus")
    if a.reuse:      # nur Statistik aus gespeicherten Trades: keine Datensatz-Erstellung, keine Simulation, kein Kursabruf
        from .common import REPORTS
        files = sorted(REPORTS.glob("event_study_*_trades.csv"))
        if not files:
            print("Keine gespeicherten Trades (reports/event_study_*_trades.csv) gefunden: zuerst die volle Studie ohne --reuse laufen lassen.")
            raise SystemExit(1)
        tf = files[-1]
        base = tf.with_name(tf.name[:-len("_trades.csv")])
        from .study.alpha import _hashes
        metadata = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
        if metadata.get("protocol_hashes") != _hashes() or (metadata.get("result") or {}).get("confirmed_only"):

            print("Studienartefakt ohne passenden Protokoll-Prüfwert: vollständige Studie erforderlich.")
            raise SystemExit(1)
        trades, ds = load_trades(tf), pd.read_csv(base.with_name(base.name + "_dataset.csv"), low_memory=False)
        funnel = Counter(metadata["funnel"])
        print(f"Wiederverwendung: {tf.name}")
        print(write_report(evaluate(trades), funnel, ds, trades, bench_loader=bl))
        return
    from .study.dataset import build, build_jp
    from .study.funnel import archive_funnel, append_study_stages
    evs = EventStore().all()
    regional_funnel = archive_funnel(evs)
    confirmed_only = getattr(a, "confirmed_only", False)
    ds, funnel = build(evs, parse_filings=not a.no_parse, since=a.since, confirmed_only=confirmed_only)
    if any(e.get("source") == "EDINET" for e in evs):
        dj, fj = build_jp(evs, since=a.since, confirmed_only=confirmed_only)
        ds = pd.concat([ds, dj], ignore_index=True); funnel.update(fj)
    if any(e.get("region") in ("UK", "NORDIC", "BENELUX_FR", "DACH") for e in evs):
        from .study.dataset_eu import build_eu, load_px
        de, fe = build_eu(evs, since=a.since, confirmed_only=confirmed_only)
        ds = pd.concat([ds, de], ignore_index=True); funnel.update(fe)
    trades = simulate(ds, pl, bl, variants=not a.main_only, fx_loader=fxl, bench_fx_loader=benchmark_fxl)
    def marks(row, days):
        try:
            start = days[0].date()
            end = days[-1].date()
            if row.get("price_source") == "tiingo":
                quotes = prices.tiingo_cached(row["ticker"], start - timedelta(days=7), end)
            else:
                quotes = pl(row["ticker"], row["region"], start - timedelta(days=7), end)
            close = quotes.close.reindex(days, method="ffill", tolerance=pd.Timedelta(days=7))
            values = [float(v) * row["qty"] / fxl(row["ticker"], row["region"], d.date())
                      for d, v in close.items()]
            return pd.Series(values, index=days)
        except (OSError, ValueError, TypeError, KeyError):
            return pd.Series(index=days, dtype=float)

    result = evaluate(trades, marks=marks)
    result["confirmed_only"] = confirmed_only
    result["regional_funnel"] = append_study_stages(regional_funnel, ds, trades)
    print(write_report(result, funnel, ds, trades, bench_loader=bl))


def cmd_backfill_jp(a):
    from .sources.edinet_backfill import backfill
    backfill(date.fromisoformat(a.start), date.fromisoformat(a.end))


def cmd_backfill_eu(a):
    try:
        _backfill_eu(a)
    except Exception as e:
        from .health import record_run
        record_run("backfill-eu", {a.source: {"ok": False, "n_new": 0, "error": str(e)[:200]}})
        raise


def _backfill_eu(a):
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
    from .health import record_run
    record_run("backfill-eu", {a.source: {"ok": True, "n_new": n, "error": None}})


def cmd_backfill(a):
    from .sources.edgar_backfill import backfill
    for y in range(a.start, a.end + 1):
        for q in range(1, 5):
            if a.quarter and q != a.quarter:
                continue
            print(backfill(y, q, limit=a.limit), flush=True)


def cmd_fill_prices(a):
    """Füllt den Kurs-Cache der Zweitquelle für qualifizierte US-Kampagnen ohne Prüfung (Tiingo-Kontingent je Lauf)."""
    import glob
    from datetime import date, timedelta
    import pandas as pd
    from .common import REPORTS
    from .sources import prices
    files = sorted(glob.glob(str(REPORTS / "event_study_*_dataset.csv")))
    if not files:
        print("kein Datensatz"); return
    d = pd.read_csv(files[-1], low_memory=False)
    q = d[(d.region == "US") & (d.status == "qualifiziert") & (d.price_check.astype(str) != "True")
          & (d.price_check.astype(str) != "False") & d.ticker.notna()]
    done = 0
    for _, r in q.iterrows():
        d0 = date.fromisoformat(str(r.published)[:10])
        try:
            _, chk = prices.load(r.ticker, "US", d0 - timedelta(days=45), min(d0 + timedelta(days=420), date.today()))
            done += chk.get("ok") is not None
        except Exception as e:
            print(r.ticker, prices._scrub(e)[:80])
        if prices._TIINGO_STATE["blocked"] or prices._TIINGO_STATE["calls"] >= prices.TIINGO_MAX_CALLS:
            break
    print(f"offen {len(q)}, jetzt geprüft {done}, Tiingo-Abrufe {prices._TIINGO_STATE['calls']}")


def cmd_paper(a):
    from .paper.portfolio import run_day
    print(run_day(date.fromisoformat(a.date) if getattr(a, "date", None) else None))


def cmd_weekly(a):
    from .paper.report import weekly, weekly_html
    subj, body = weekly()
    if a.mail:
        send(subj, body, html=weekly_html())
    else:
        print(subj); print(body)


def cmd_health(a):
    from . import health
    st = health.status()
    print(f"Gesamtstatus: {st['overall']}")
    for k, v in sorted(st["sources"].items()):
        print(f"  {k}: {'ok' if v['ok'] else 'FEHLER'} (letzter Lauf {v['ts'][:16]}){'' if v['ok'] else ' - ' + str(v['error'])}")
        if not v["ok"]:
            print(f"::warning::{k}: {v['error']}")
    print(f"Alpha-Nachweis: {st['alpha']['status']}")
    for e in st["errors"]:
        print(f"  Fehler: {e}")
    problems = health.gate(st, [x for x in a.core.split(",") if x], [x for x in a.pool.split(",") if x])
    for p in problems:
        print(f"::error::{p}")
    if problems:
        raise SystemExit(1)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="activist")
    sp = ap.add_subparsers(required=True)
    f = sp.add_parser("fetch"); f.add_argument("--source", default="all", choices=["edgar", "edinet", "all"])
    f.add_argument("--date"); f.add_argument("--days", type=int, default=1); f.add_argument("--limit", type=int)
    f.add_argument("--no-headers", action="store_true"); f.set_defaults(fn=cmd_fetch)
    s = sp.add_parser("status"); s.add_argument("--mail", action="store_true"); s.set_defaults(fn=cmd_status)
    st = sp.add_parser("study"); st.add_argument("--main-only", action="store_true")
    st.add_argument("--confirmed-only", action="store_true", help="Fälle ohne geprüften Identitätsnachweis vor Kursabruf ausschließen")
    st.add_argument("--since"); st.add_argument("--no-parse", action="store_true"); st.add_argument("--reuse", action="store_true")
    st.set_defaults(fn=cmd_study)
    b = sp.add_parser("backfill"); b.add_argument("--start", type=int, default=2015); b.add_argument("--end", type=int, default=2015)
    b.add_argument("--quarter", type=int); b.add_argument("--limit", type=int); b.set_defaults(fn=cmd_backfill)
    bj = sp.add_parser("backfill-jp"); bj.add_argument("--start", required=True); bj.add_argument("--end", required=True)
    bj.set_defaults(fn=cmd_backfill_jp)
    be = sp.add_parser("backfill-eu"); be.add_argument("--source", required=True, choices=["nsm", "nasdaq_nordic", "oslo", "amf", "six", "activist_sites", "de_ir"])
    be.add_argument("--start", required=True); be.add_argument("--end", required=True)
    be.add_argument("--enrich", action="store_true"); be.set_defaults(fn=cmd_backfill_eu)
    fp = sp.add_parser("fill-prices"); fp.set_defaults(fn=cmd_fill_prices)
    pp = sp.add_parser("paper"); pp.add_argument("--date"); pp.set_defaults(fn=cmd_paper)
    fz = sp.add_parser("freeze"); fz.add_argument("--note", default=""); fz.set_defaults(fn=cmd_freeze)
    wk = sp.add_parser("weekly"); wk.add_argument("--mail", action="store_true"); wk.set_defaults(fn=cmd_weekly)
    hp = sp.add_parser("health"); hp.add_argument("--core", default=""); hp.add_argument("--pool", default=""); hp.set_defaults(fn=cmd_health)
    a = ap.parse_args(argv); a.fn(a)


if __name__ == "__main__":
    main()
