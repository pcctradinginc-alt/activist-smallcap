"""Point-in-time-Datensatz für die Ereignisstudie (USA).

Für jede Kampagne (erstes Ereignis je Melder x Emittent) wird ausschließlich mit Information bis zur
Veröffentlichung entschieden, ob sie ein handelbares Signal gewesen wäre. Jede Stufe des Filters wird
gezählt (Trichter), damit Abdeckung und Survivorship-Lücke im Bericht sichtbar sind.
"""
from __future__ import annotations
import json
import os
from collections import Counter
from datetime import date, timedelta
import pandas as pd
from ..common import DATA, params, risk_limits
from ..features.register import Register
from ..features.universe import price_features, universe_check
from ..parse import filing13d
from ..risk.rules import candidate_reasons, next_earnings
from ..sources import delisting, hist_ticker, prices, secmeta, sector
from . import holders

INITIAL = {"SC 13D", "SCHEDULE 13D"}
AMEND = {"SC 13D/A", "SCHEDULE 13D/A"}
PASSIVE = {"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
CACHE = DATA / "cache" / "parsed13d.jsonl"


def _parsed_cache() -> dict:
    if not CACHE.exists():
        return {}
    v = filing13d.PARSER_VERSION
    return {j["url"]: j for j in map(json.loads, CACHE.read_text().splitlines()) if j and j.get("v") == v}


def _parse(url: str, cache: dict) -> dict:
    if url not in cache:
        try:
            r = filing13d.fetch_and_parse(url)
        except Exception as e:
            r = {"error": str(e)}
        r["url"] = url; r["v"] = filing13d.PARSER_VERSION; cache[url] = r
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE, "a") as f:
            f.write(json.dumps(r) + "\n")
    return cache[url]


def data_quality(row: dict, ticker_certain: bool, activist_evidence: bool) -> dict:
    """Verbindliche Datenqualität je Kampagne. Nur 'verified' fließt in den bestätigenden Alpha-Test.
    Kriterien: zwei unabhängige Kursquellen stimmen überein (Abweichung <= 2 %), Ticker eindeutig der damaligen Firma
    zugeordnet, Marktkapitalisierung aus point-in-time Aktienzahl bekannt, Aktivisten-Einstufung belegt."""
    checks = {"Kurse doppelt geprüft": row.get("price_check") is True, "Ticker eindeutig": bool(ticker_certain),
              "Marktkapitalisierung bekannt": row.get("mcap") is not None and row.get("mcap") == row.get("mcap"),
              "Aktivist belegt": bool(activist_evidence)}
    return {"verified": all(checks.values()), "dq_missing": "; ".join(k for k, v in checks.items() if not v)}


def _ticker_certain(row: dict, px, d0: date, issuer_id: str, current: str | None) -> bool:
    """Ist der Ticker der damaligen Firma sicher zugeordnet? secmeta.ticker_asof liefert nur den HEUTIGEN Ticker, daher:
    Survivorship-Pfad (historischer Ticker + Tiingo-Kurs am Einstand validiert) · Kurs am Entscheidungstag passt zum
    Einstand des Aktivisten · oder der historische Ticker aus den SEC-Meldungen gleicht dem heutigen."""
    row["ticker_check"] = None
    if str(row.get("ticker_source") or "").startswith("hist:") and row.get("price_source") == "tiingo":
        row["ticker_check"] = "hist+einstand"
    elif prices.validate_against(px, d0, row.get("activist_cost")):
        row["ticker_check"] = "einstand"
    else:
        try:
            h = (hist_ticker.ticker_hist(issuer_id, d0) or {}).get("ticker")
        except Exception:
            h = None
        norm = lambda x: str(x).upper().replace(".", "-")
        if h and current and norm(h) == norm(current):
            row["ticker_check"] = "hist"
    return row["ticker_check"] is not None


def campaigns(events: list[dict], reg: Register) -> list[dict]:
    """Erstes Ereignis je Kampagne + spätere Ereignisse derselben Kampagne (für Ausstiege)."""
    by: dict[tuple, list] = {}
    for e in sorted(events, key=lambda e: e["published"]):
        if e["region"] != "US" or not e.get("filer_id") or not e.get("issuer_id"):
            continue
        k = (e["filer_id"].lstrip("0"), e["issuer_id"].lstrip("0"))
        if k[0] == k[1]:
            continue
        by.setdefault(k, []).append(e)
    out = []
    for (f, i), evs in by.items():
        passive_before = False
        for idx, e in enumerate(evs):
            act = reg.is_activist(f, e["published"])
            if e["form"] in AMEND and idx == 0:
                break                          # Änderung ohne Erstmeldung im Datenfenster = laufende Kampagne, kein Einstieg
            if e["form"] in INITIAL:
                cls = "A" if act else None     # 13D eines Nicht-Aktivisten (Gründer, Insider) = kein Signal
                etype = "switch_13g_13d" if passive_before else "initial_13d"
            elif e["form"] in PASSIVE:
                if e["form"].endswith("/A") and not passive_before:
                    passive_before = True      # 13G/A ohne Erstmeldung = bestehende Position, kein Einstieg
                    continue
                passive_before = True
                # Klasse B nur für kuratierte Aktivisten: große Institutionen (Banken, Staatsfonds, Vermögensverwalter)
                # geben ebenfalls 13D ab und würden die Verhaltensregel sonst erfüllen
                cls, etype = ("B" if reg.is_listed(f, e["published"]) else None), "passive"
                if cls is None:
                    continue
            else:
                continue
            if cls:
                out.append(dict(e, campaign_key=f"{f}-{i}", signal_class=cls, event_type=etype,
                                listed_activist=reg.is_listed(f, e["published"]),
                                followups=[x for x in evs[idx + 1:]]))
            break
    # Wolfsrudel / mehrere Vehikel einer Gruppe: je Emittent zählt nur die erste Kampagne innerhalb von 30 Tagen
    out.sort(key=lambda c: c["published"])
    last: dict[str, str] = {}
    dedup = []
    for c in out:
        iss = str(c.get("issuer_id")).lstrip("0")
        prev = last.get(iss)
        if prev and (pd.Timestamp(c["published"]) - pd.Timestamp(prev)).days < 30:
            continue
        last[iss] = c["published"]; dedup.append(c)
    return dedup


def build(events: list[dict], end: date | None = None, parse_filings: bool = True,
          since: str | None = None) -> tuple[pd.DataFrame, Counter]:
    p, lim = params(), risk_limits()["candidate"]
    reg = Register(events)
    funnel, rows, cache = Counter(), [], _parsed_cache()
    by_issuer: dict[str, list] = {}
    for e in events:
        if e.get("region") == "US" and e.get("issuer_id"):
            by_issuer.setdefault(str(e["issuer_id"]).lstrip("0"), []).append(e)
    cfg = p["universe"]["US"]
    end = end or date.today()
    for c in campaigns(events, reg):
        if since and c["published"] < since:      # Tagesbetrieb: nur neue Kampagnen, Historie bleibt für das Register
            continue
        funnel["kampagnen"] += 1
        row = {k: c.get(k) for k in ("event_id", "campaign_key", "published", "form", "signal_class", "event_type",
                                     "filer_id", "filer_name", "issuer_id", "issuer_name", "url", "region")}
        row["activist"] = c.get("filer_name") or c.get("filer_id")
        from .identity import identity_status
        row.update(identity_status(c))
        if row["identity_status"] == "conflict":
            row["status"] = "Identitätskonflikt"; funnel["Identitätskonflikt"] += 1; rows.append(row); continue
        try:
            t = secmeta.ticker_asof(c["issuer_id"], c["published"][:10])
        except Exception:
            funnel["SEC-Stammdaten nicht abrufbar"] += 1; row["status"] = "keine Stammdaten"; rows.append(row); continue
        d0 = date.fromisoformat(c["published"][:10])
        ft = _parse(c["url"], cache) if parse_filings and c["form"] in INITIAL | AMEND else {}
        sic = ft.get("subject_sic")
        row["sector"] = sector.from_sic(sic)   # point-in-time: SIC aus dem Kopf der Meldung -> Yahoo-Sektorname
        px, row["price_source"] = None, None
        if t:
            row["ticker"] = t["ticker"]
            try:
                px, chk = prices.load(t["ticker"], "US", d0 - timedelta(days=45), min(d0 + timedelta(days=420), end), second=False)
                row["price_source"] = "yahoo"
                row["price_check"] = chk.get("ok"); row["price_check_note"] = prices._scrub(chk.get("error")) if chk.get("error") else (f"Abweichung {chk.get('max_dev')}" if chk.get("ok") is False else None)
            except Exception:
                px = None
            if px is not None and chk.get("ok") is False:
                funnel["Kursquellen weichen ab"] += 1; row["status"] = "gesperrt: Kursabweichung"; rows.append(row); continue
        if px is None:
            # Survivorship: historischen Ticker aus den eigenen SEC-Meldungen rekonstruieren, Kurse über Tiingo
            ft0 = _parse(c["url"], cache) if parse_filings else {}
            row["activist_cost"] = ft0.get("avg_purchase_price")
            try:
                h = hist_ticker.ticker_hist(c["issuer_id"], d0)
            except Exception:
                h = {}
            if h.get("ticker"):
                row["ticker"], row["ticker_source"] = h["ticker"], "hist:" + str(h.get("source"))
                try:
                    tp = prices.tiingo_cached(h["ticker"], d0 - timedelta(days=45), min(d0 + timedelta(days=420), end))
                    # Ticker-Wiederverwendung ausschließen: Kurs muss zum Einstand des Aktivisten passen
                    if prices.validate_against(tp, d0, row["activist_cost"]):
                        px, row["price_source"] = tp, "tiingo"
                    else:
                        funnel["Survivorship: Tiingo-Kurs nicht plausibel"] += 1
                except Exception:
                    pass
        if px is None:
            # letzte Stufe: Endwert aus Übernahme-/Insolvenzmeldungen (für Spannweiten und Imputation)
            try:
                out = delisting.terminal_outcome(c["issuer_id"], d0)
            except Exception:
                out = {"type": "unknown"}
            row.update(terminal_type=out.get("type"), terminal_date=out.get("date"),
                       terminal_cash=out.get("cash_per_share"), terminal_evidence=out.get("evidence"))
            funnel[f"Survivorship: Endwert {out.get('type')}"] += 1
            row["status"] = "nur Endwert"; rows.append(row); continue
        pf = price_features(px, c["published"])
        try:
            so = secmeta.shares_outstanding_asof(c["issuer_id"], d0)
        except Exception:
            so = None
        uv = universe_check(cfg, pf, so[0] if so else None, secmeta.excluded_industry(sic),
                            bool(t and t["otc"]) if since else False)   # OTC-Status nur heute bekannt -> nur im Live-Betrieb
        row.update({k: pf.get(k) for k in ("price", "adv", "runup_20d", "half_spread")})
        row["mcap"] = so[0] * pf["price"] if so and pf.get("ok") else None
        if uv:
            for v in uv:
                funnel[f"Universum: {v}"] += 1
            row["status"] = "außerhalb Universum: " + "; ".join(uv); rows.append(row); continue
        funnel["im Universum"] += 1
        # Ausschlussregeln, soweit historisch aus öffentlichen Daten bestimmbar
        row.update(demand=ft.get("demand"), demand_evidence=ft.get("demand_evidence"),
                   stake=ft.get("percent_of_class"), activist_cost=ft.get("avg_purchase_price"))
        # Verhaltensregel allein reicht nicht: ohne kuratierten Eintrag braucht der 13D eine konkrete Forderung (Item 4)
        if c["signal_class"] == "A" and not c.get("listed_activist") and parse_filings \
                and (ft.get("demand") in (None, "none") or not ft.get("demand_evidence")):
            funnel["kein Aktivist (keine Forderung in Item 4)"] += 1
            row["status"] = "kein Aktivist: keine Forderung"; rows.append(row); continue
        # Großaktionär (13D/13G-Parse, Anteil in % -> Bruchteil) und Quartalszahlen-Sperre; fehlende Daten = kein Ausschluss
        mh = None
        if parse_filings:
            try:
                mh = holders.major_holder_us(by_issuer, c["issuer_id"], c["published"], lambda u: _parse(u, cache))
            except Exception:
                mh = None
        st = _major(None, None, None, row["stake"] / 100) if isinstance(row.get("stake"), (int, float)) else None
        row["major_holder"] = max((x for x in (mh, st) if x is not None), default=None)
        try:
            ed = secmeta.earnings_dates(c["issuer_id"])
            ne = next_earnings(ed, d0)
        except Exception:
            ed, ne = [], None
        row["days_to_earnings"] = (ne - d0).days if ne else None
        row["earnings_check"] = "8-K 2.02" if ed else "unbekannt"
        try:
            from ..paper.portfolio import eur_per_unit
            pos_usd = p["order"]["position_eur"] / eur_per_unit(row["ticker"], "US", d0)
        except Exception:
            pos_usd = None                        # kein stiller Fallback: ohne Wechselkurs keine Liquiditätsprüfung
        reasons = candidate_reasons(row, pos_usd, lim)      # ADV in USD
        # Ausstiege: spätere Meldungen dieser Kampagne mit Anteil < 5 % oder Exit-Formulierung
        exits = []
        if parse_filings:
            for fu in c["followups"]:
                if fu["form"] in AMEND | PASSIVE and _parse(fu["url"], cache).get("is_exit"):
                    exits.append(fu["published"]); break
        row["exit_events"] = exits
        row["status"] = ("verworfen: " + "; ".join(reasons)) if reasons else "qualifiziert"
        funnel["verworfen (Schattenportfolio)" if reasons else "qualifiziert"] += 1
        if not reasons and row.get("price_source") == "yahoo" and row.get("price_check") is None:
            try:            # Zweitquelle nur für qualifizierte Kampagnen (Tiingo-Kontingent)
                _, chk = prices.load(row["ticker"], "US", d0 - timedelta(days=45), min(d0 + timedelta(days=420), end))
                row["price_check"] = chk.get("ok")
                row["price_check_note"] = prices._scrub(chk.get("error")) if chk.get("error") else (f"Abweichung {chk.get('max_dev')}" if chk.get("ok") is False else None)
                if chk.get("ok") is False:
                    funnel["qualifiziert"] -= 1; funnel["Kursquellen weichen ab"] += 1
                    row["status"] = "gesperrt: Kursabweichung"; rows.append(row); continue
            except Exception as e:
                row["price_check_note"] = prices._scrub(e)[:200]
        row.update(data_quality(row, ticker_certain=_ticker_certain(row, px, d0, c["issuer_id"], row.get("ticker")), activist_evidence=bool(c.get("listed_activist") or ft.get("demand_evidence"))))
        rows.append(row)
    return pd.DataFrame(rows), funnel


# ------------------------------------------------------------------ Japan
JP_FORMS = {"LargeHolding", "LargeHoldingChange"}


def _norm_name(s: str) -> str:
    import unicodedata, re as _re
    return _re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", s or "").lower())


# Banken, Versicherer, Broker, Treuhänder: melden >5 % aus Geschäft/Treuhand, nicht als Aktivist (seit 2026-11-21)
JP_FINANCIAL = ("銀行", "信託", "証券", "證券", "生命", "保険", "損保", "フィナンシャル", "ファイナンシャル", "ホールディングス・バンク",
                "bank", "securities", "insurance", "trust", "financial group", "life ")


def is_jp_financial(name) -> bool:
    import unicodedata
    n = unicodedata.normalize("NFKC", str(name or "")).lower()
    return any(k in n for k in JP_FINANCIAL)


def jp_campaigns(events: list[dict]) -> list[dict]:
    """Kampagne = (Melder, Emittent). Klasse A = Haltezweck 'wichtige Vorschläge' (重要提案行為等);
    Klasse B = bekannter Aktivist (Liste oder >= 3 frühere A-Meldungen bei verschiedenen Emittenten) ohne diesen Zweck."""
    from ..common import load_yaml
    register = load_yaml("activists.yaml")
    aliases = register.get("aliases") or {}
    names = [(_norm_name(n), str(a.get("since", "1900"))) for a in register["activists"]
             if a.get("region") == "JP" for n in [a["name"], *aliases.get(a["name"], [])]]
    jp = sorted([e for e in events if e.get("source") == "EDINET" and e.get("form") in JP_FORMS and e.get("doc_parsed")],
                key=lambda e: e["published"])
    prior_a: dict[str, list[tuple[str, str]]] = {}     # Melder -> [(Zeit, Emittent)] aller A-Meldungen, zeitlich sortiert
    for e in jp:
        if e.get("important_proposal") and e.get("filer_id"):
            prior_a.setdefault(e["filer_id"], []).append((e["published"], e.get("issuer_sec_code")))
    by: dict[tuple, list] = {}
    for e in jp:
        by.setdefault((e.get("filer_id"), e.get("issuer_sec_code")), []).append(e)
    out = []
    for (f, sec), evs in by.items():
        if not f or not sec:
            continue
        for idx, e in enumerate(evs):
            nm = _norm_name(e.get("filer_name"))
            listed = any(k and nm == k and since <= e["published"][:10] for k, since in names)
            behav = len({iss for t, iss in prior_a.get(f, []) if t < e["published"] and iss != sec}) >= 3
            if not listed and (is_jp_financial(e.get("filer_name")) or "アセットマネジメント" in str(e.get("filer_name") or "")):
                break                                   # Finanzinstitut: keine Aktivisten-Kampagne
            cls = "A" if e.get("important_proposal") else ("B" if (listed or behav) else None)
            if cls:
                out.append(dict(e, campaign_key=f"{f}-{sec}", signal_class=cls, event_type="jp_" + cls,
                                followups=evs[idx + 1:], ticker=sec,
                                activist_evidence_kind="proposal" if cls == "A" else ("register_name" if listed else "prior_proposals"),
                                prior_proposal_count=len({iss for t, iss in prior_a.get(f, []) if t < e["published"] and iss != sec})))
                break
    return out


def _major(idx: dict, key, before: str, stake) -> float | None:
    """Größter bekannter Aktionär (Ereignisindex) und eigener Anteil des Aktivisten (Bruchteil)."""
    st = stake if isinstance(stake, (int, float)) and 0 < stake <= 0.99 else None      # 1.0 = Lesefehler
    mh = holders.major_holder(idx, key, before) if idx is not None else None
    return max((x for x in (mh, st) if x is not None), default=None)


def build_jp(events: list[dict], end: date | None = None, since: str | None = None) -> tuple[pd.DataFrame, Counter]:
    p, lim = params(), risk_limits()["candidate"]
    cfg, funnel, rows = p["universe"]["JP"], Counter(), []
    jp_idx = holders.build_index(events)
    end = end or date.today()
    try:
        fx = prices.yahoo("EURJPY=X", date(2015, 1, 1), end)["close"]
    except Exception:
        fx = None
    for c in jp_campaigns(events):
        if since and c["published"] < since:
            continue
        funnel["JP kampagnen"] += 1
        row = {k: c.get(k) for k in ("event_id", "campaign_key", "published", "form", "signal_class", "event_type",
                                     "filer_id", "filer_name", "issuer_id", "issuer_name", "url", "region", "ticker",
                                     "activist_evidence_kind", "prior_proposal_count")}
        row.update(activist=c.get("filer_name"), demand="capital_or_proposal" if c.get("important_proposal") else "none",
                   demand_evidence=c.get("important_proposal_evidence"), stake=c.get("holding_ratio"))
        from .identity import identity_status
        row.update(identity_status(c))
        if row["identity_status"] == "conflict":
            row["status"] = "Identitätskonflikt"; funnel["JP Identitätskonflikt"] += 1; rows.append(row); continue
        d0 = date.fromisoformat(c["published"][:10])
        try:
            px, chk = prices.load(c["ticker"], "JP", d0 - timedelta(days=45), min(d0 + timedelta(days=420), end))
        except Exception:
            funnel["JP keine Kurse (delistet/Survivorship)"] += 1; row["status"] = "keine Kurse"; rows.append(row); continue
        pf = price_features(px, c["published"])
        uv = universe_check(cfg, pf, c.get("total_shares_outstanding"), None, False)
        row.update({k: pf.get(k) for k in ("price", "adv", "runup_20d", "half_spread")})
        if uv:
            for v in uv:
                funnel[f"JP Universum: {v}"] += 1
            row["status"] = "außerhalb Universum: " + "; ".join(uv); rows.append(row); continue
        funnel["JP im Universum"] += 1
        eurjpy = float(fx.asof(pd.Timestamp(d0))) if fx is not None else float("nan")
        if pd.isna(eurjpy):                       # kein stiller Fallback auf einen Näherungskurs
            funnel["JP Wechselkurs fehlt"] += 1; row["status"] = "gesperrt: Wechselkurs fehlt"; rows.append(row); continue
        pos_jpy = p["order"]["position_eur"] * eurjpy
        row["sector"] = sector.yahoo(c["ticker"] + prices.YAHOO_SUFFIX["JP"])
        row["major_holder"] = _major(jp_idx, ("JP", c.get("issuer_sec_code") or c["ticker"]), c["published"], row.get("stake"))
        row["days_to_earnings"], row["earnings_check"] = None, "unbekannt (keine Datenquelle)"
        reasons = candidate_reasons(row, pos_jpy, lim)
        if cfg.get("lot_size", 100) * pf["price"] > pos_jpy:
            reasons.insert(0, "Mindestorder (100 Stück) > Position")
        row["exit_events"] = [fu["published"] for fu in c["followups"]
                              if fu.get("holding_ratio") is not None and fu["holding_ratio"] < 0.05][:1]
        # Konzept: Handelbarkeit über deutschen Broker ungeprüft -> Japan vorerst nur Schattenportfolio
        row["status"] = ("verworfen: " + "; ".join(reasons)) if reasons else "qualifiziert"
        funnel["JP verworfen" if reasons else "JP qualifiziert"] += 1
        row["price_check"] = chk.get("ok")
        if not reasons and os.environ.get("EDINET_API_KEY"):
            # keine zweite freie Kursreihe für Japan: Kursniveau gegen die eigenen Preisangaben des Meldenden (EDINET) prüfen
            from ..sources import edinet_docs
            refs = edinet_docs.price_refs(str(c["event_id"]).split(":", 1)[-1], os.environ["EDINET_API_KEY"])
            row["price_check"], row["price_check_note"] = prices.reference_check(px, refs)
            row["price_check_kind"] = "EDINET-Einstand"
            if row["price_check"] is False:
                funnel["JP qualifiziert"] -= 1; funnel["JP Kursreihe unplausibel"] += 1
                row["status"] = "gesperrt: Kursabweichung"; rows.append(row); continue
        row["mcap"] = c.get("total_shares_outstanding") * pf["price"] if c.get("total_shares_outstanding") else None
        row.update(data_quality(row, ticker_certain=False, activist_evidence=False))  # original-document identity and independent activist evidence pending
        rows.append(row)
    return pd.DataFrame(rows), funnel
