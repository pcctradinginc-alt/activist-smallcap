"""Point-in-time-Datensatz für die Ereignisstudie (USA).

Für jede Kampagne (erstes Ereignis je Melder x Emittent) wird ausschließlich mit Information bis zur
Veröffentlichung entschieden, ob sie ein handelbares Signal gewesen wäre. Jede Stufe des Filters wird
gezählt (Trichter), damit Abdeckung und Survivorship-Lücke im Bericht sichtbar sind.
"""
from __future__ import annotations
import json
from collections import Counter
from datetime import date, timedelta
import pandas as pd
from ..common import DATA, params, risk_limits
from ..features.register import Register
from ..features.universe import price_features, universe_check
from ..parse import filing13d
from ..sources import prices, secmeta

INITIAL = {"SC 13D", "SCHEDULE 13D"}
AMEND = {"SC 13D/A", "SCHEDULE 13D/A"}
PASSIVE = {"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
CACHE = DATA / "cache" / "parsed13d.jsonl"


def _parsed_cache() -> dict:
    if not CACHE.exists():
        return {}
    return {j["url"]: j for j in map(json.loads, CACHE.read_text().splitlines()) if j}


def _parse(url: str, cache: dict) -> dict:
    if url not in cache:
        try:
            r = filing13d.fetch_and_parse(url)
        except Exception as e:
            r = {"error": str(e)}
        r["url"] = url; cache[url] = r
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE, "a") as f:
            f.write(json.dumps(r) + "\n")
    return cache[url]


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
            if e["form"] in INITIAL or (e["form"] in AMEND and idx == 0):
                cls = "A" if act else None     # 13D eines Nicht-Aktivisten (Gründer, Insider) = kein Signal
                etype = "switch_13g_13d" if passive_before else "initial_13d"
            elif e["form"] in PASSIVE:
                passive_before = True
                cls, etype = ("B" if act else None), "passive"
                if cls is None:
                    continue
            else:
                continue
            if cls:
                out.append(dict(e, campaign_key=f"{f}-{i}", signal_class=cls, event_type=etype,
                                followups=[x for x in evs[idx + 1:]]))
            break
    return out


def build(events: list[dict], end: date | None = None, parse_filings: bool = True) -> tuple[pd.DataFrame, Counter]:
    p, lim = params(), risk_limits()["candidate"]
    reg = Register(events)
    funnel, rows, cache = Counter(), [], _parsed_cache()
    cfg = p["universe"]["US"]
    end = end or date.today()
    for c in campaigns(events, reg):
        funnel["kampagnen"] += 1
        row = {k: c.get(k) for k in ("event_id", "campaign_key", "published", "form", "signal_class", "event_type",
                                     "filer_id", "filer_name", "issuer_id", "issuer_name", "url", "region")}
        row["activist"] = c.get("filer_name") or c.get("filer_id")
        try:
            t = secmeta.ticker_asof(c["issuer_id"], c["published"][:10])
        except Exception:
            funnel["SEC-Stammdaten nicht abrufbar"] += 1; row["status"] = "keine Stammdaten"; rows.append(row); continue
        if not t:
            funnel["ohne Ticker (Survivorship-Lücke)"] += 1; row["status"] = "kein Ticker"; rows.append(row); continue
        row["ticker"] = t["ticker"]
        co = secmeta.company(c["issuer_id"])
        d0 = date.fromisoformat(c["published"][:10])
        try:
            px, chk = prices.load(t["ticker"], "US", d0 - timedelta(days=45), min(d0 + timedelta(days=420), end))
        except Exception:
            funnel["keine Kurse"] += 1; row["status"] = "keine Kurse"; rows.append(row); continue
        if chk.get("ok") is False:
            funnel["Kursquellen weichen ab"] += 1; row["status"] = "gesperrt: Kursabweichung"; rows.append(row); continue
        pf = price_features(px, c["published"])
        try:
            so = secmeta.shares_outstanding_asof(c["issuer_id"], d0)
        except Exception:
            so = None
        uv = universe_check(cfg, pf, so[0] if so else None, secmeta.excluded_industry(co.get("sic")), t["otc"])
        row.update({k: pf.get(k) for k in ("price", "adv", "runup_20d", "half_spread")})
        row["mcap"] = so[0] * pf["price"] if so and pf.get("ok") else None
        if uv:
            for v in uv:
                funnel[f"Universum: {v}"] += 1
            row["status"] = "außerhalb Universum: " + "; ".join(uv); rows.append(row); continue
        funnel["im Universum"] += 1
        # Ausschlussregeln, soweit historisch aus öffentlichen Daten bestimmbar
        ft = _parse(c["url"], cache) if parse_filings and c["form"] in INITIAL | AMEND else {}
        row.update(demand=ft.get("demand"), demand_evidence=ft.get("demand_evidence"),
                   stake=ft.get("percent_of_class"), activist_cost=ft.get("avg_purchase_price"))
        reasons = []
        if 2 * (row["half_spread"] or 0) > lim["max_spread"]:
            reasons.append("Spread")
        if row["runup_20d"] is not None and row["runup_20d"] > lim["max_runup_20d"]:
            reasons.append("Anstieg 20T")
        if row["activist_cost"] and row["price"] > row["activist_cost"] * (1 + lim["max_premium_over_activist_cost"]):
            reasons.append("Kurs über Aktivisten-Einstand")
        if p["order"]["position_eur"] > lim["max_position_share_of_adv"] * row["adv"]:
            reasons.append("Liquidität")
        # Ausstiege: spätere Meldungen dieser Kampagne mit Anteil < 5 % oder Exit-Formulierung
        exits = []
        if parse_filings:
            for fu in c["followups"]:
                if fu["form"] in AMEND and _parse(fu["url"], cache).get("is_exit"):
                    exits.append(fu["published"]); break
        row["exit_events"] = exits
        row["status"] = ("verworfen: " + "; ".join(reasons)) if reasons else "qualifiziert"
        funnel["verworfen (Schattenportfolio)" if reasons else "qualifiziert"] += 1
        rows.append(row)
    return pd.DataFrame(rows), funnel
