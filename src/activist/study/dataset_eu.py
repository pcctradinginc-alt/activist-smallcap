"""Point-in-time-Datensatz Europa (UK, Nordics, Benelux/Frankreich), analog zu build_jp.

Konventionen (wie build/build_jp): eine Zeile je Kampagne, Spalte `status` ('qualifiziert' | 'verworfen: ...' | 'außerhalb Universum: ...' |
'keine Uhrzeit' | 'keine ISIN' | 'kein Ticker' | 'keine Kurse'), `exit_events`, Trichter als Counter mit Präfix 'EU '.

Signalklasse: EU-Stimmrechtsmitteilungen (TR-1, AMF-Franchissement, Flagging) haben KEIN Absichtsfeld (anders als 13D Item 4 / EDINET
Haltezweck). Deshalb nur Klasse B = Melder steht im Aktivisten-Register (activists.yaml, region EU; Namensabgleich normalisiert,
Teilstring, ab `since`). Klasse A ist in Europa nicht ableitbar.
Kampagne = erstes Ereignis je (normierter Melder, Emittent-ISIN). Ohne Uhrzeit: Entscheidung frühestens am Folgetag (konservativ, kein Look-ahead).
Erste Meldung mit Anteil unter der Ausstiegsschwelle gilt als Ausstieg, nicht als Einstieg (Historienbeginn) und wird nicht gezählt.

Kurse: Yahoo (voller Ticker aus OpenFIGI-Primärlisting), ohne Zweitquelle (chk ok=None, ungeprüft). '.L'-Kurse sind in Pence (GBp) -> /100.
Umrechnung in EUR über Yahoo-FX (GBPEUR=X ...) am Entscheidungstag (asof). Marktkapitalisierung ist ohne Aktienzahl unbekannt -> nicht
ausgeschlossen, im Trichter 'EU Marktkapitalisierung ungeprüft' gezählt (Universum wird also zu weit gefasst: Large Caps rutschen durch).
Achtung Survivorship: OpenFIGI liefert HEUTIGE Listings; delistete Emittenten ergeben 'kein Ticker'.
"""
from __future__ import annotations
import re, unicodedata
from collections import Counter
from datetime import date, timedelta
import pandas as pd
from ..common import DATA, load_yaml, params, risk_limits
from ..features.universe import price_features, universe_check
from ..sources import openfigi, prices
from . import holders
from .identity import apply_identity, identity_status, load_evidence
from ..risk.rules import candidate_reasons

EU_REGIONS = {"UK", "NORDIC", "BENELUX_FR", "DACH"}
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
EXIT_BELOW = {"UK": 0.03, "NORDIC": 0.05, "BENELUX_FR": 0.05, "DACH": 0.03}
SUFFIX_FX = {".L": "GBPEUR=X", ".OL": "NOKEUR=X", ".ST": "SEKEUR=X", ".CO": "DKKEUR=X", ".SW": "CHFEUR=X"}      # EUR-Länder: kein FX
PENCE_SUFFIX = (".L",)


def _norm_name(s: str | None) -> str:
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", s or "").casefold())


def activist_names() -> list[tuple[str, str]]:
    """Namen + Aliase aller Aktivisten (EU-Meldungen nennen oft die kontrollierende Person, z. B. Paul E. Singer
    statt Elliott). Personen-Aliase gelten für alle Regionen, weil US-Aktivisten auch in Europa melden."""
    y = load_yaml("activists.yaml")
    al = y.get("aliases") or {}
    out = []
    for a in y["activists"]:
        for n in [a["name"], *al.get(a["name"], [])]:
            if _norm_name(n) and len(_norm_name(n)) >= 5:
                out.append((_norm_name(n), str(a.get("since", "1900"))))
    return out


def _is_activist(filer_norm: str, day: str, names) -> bool:
    return any(n in filer_norm and since <= day for n, since in names)


def _issuer_key(e: dict) -> str | None:
    for k in ("issuer_isin", "issuer_id"):
        if e.get(k) and ISIN_RE.match(str(e[k])):
            return e[k]
    return None


def eu_campaigns(events: list[dict], funnel: Counter | None = None) -> list[dict]:
    funnel = funnel if funnel is not None else Counter()
    names = activist_names()
    by: dict[tuple, list] = {}
    for e in sorted(events, key=lambda e: e["published"]):
        if e.get("region") not in EU_REGIONS or e.get("form") not in ("MajorHolding", "PublicCampaign"):
            continue
        funnel["EU Ereignisse"] += 1
        if not e.get("filer_name"):
            funnel["EU Ereignisse ohne Melder (PDF/Text nötig)"] += 1; continue
        iss = _issuer_key(e) or e.get("issuer_id") or e.get("issuer_name")
        if not iss:
            continue
        # Klasse C (öffentliche Kampagne) bildet eigene Kampagnen – Klassen werden nie zusammengerechnet
        kind = "C" if e.get("form") == "PublicCampaign" else "B"
        by.setdefault((_norm_name(e["filer_name"]) + ("#C" if kind == "C" else ""), str(iss)), []).append(e)
    out = []
    for (f, iss), evs in by.items():
        first = evs[0]
        chain = _norm_name(first.get("chain_text") or "")   # Kontrollkette (DE/AT-Mitteilungen, TR-1 Abschnitt 9)
        is_c = first.get("form") == "PublicCampaign"     # Melder ist per Konstruktion der Aktivist (eigene Website)
        if not is_c and not (_is_activist(f, first["published"][:10], names) or (chain and _is_activist(chain, first["published"][:10], names))):
            continue
        thr = EXIT_BELOW[first["region"]]
        if first.get("previous_ratio") is not None and first["previous_ratio"] >= thr:
            funnel["EU laufende Beteiligung (Vorquote über Schwelle, kein Einstieg)"] += 1; continue
        if first.get("holding_ratio") is not None and first["holding_ratio"] < thr:
            funnel["EU erste Meldung unter Ausstiegsschwelle (kein Einstieg)"] += 1; continue
        cls = "C" if is_c else "B"
        out.append(dict(first, campaign_key=f"{f}-{iss}", signal_class=cls, event_type=f"eu_{cls}", followups=evs[1:]))
    return out


def _px_eur_factor(ticker: str) -> float:
    return 0.01 if ticker.endswith(PENCE_SUFFIX) else 1.0


def load_px(ticker: str, start: date, end: date, cache: bool = True) -> pd.DataFrame:
    """Yahoo-Kurse in Hauptwährungseinheit (GBp -> GBP). Cache DATA/prices/EU_<ticker>.csv (nur ungerechnete Rohwerte in Yahoo-Einheit)."""
    path = DATA / "prices" / f"EU_{ticker}.csv"
    df = None
    if cache and path.exists():
        c = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(c) and c.index.min().date() <= start + timedelta(days=7) and c.index.max().date() >= end - timedelta(days=7):
            df = c
    if df is None:
        df = prices.yahoo(ticker, start, end)
        if cache and len(df):
            path.parent.mkdir(parents=True, exist_ok=True); df.to_csv(path)
    df = df.loc[str(start):str(end)].copy()
    f = _px_eur_factor(ticker)
    if f != 1.0:
        for c in ("open", "high", "low", "close", "close_raw"):
            if c in df:
                df[c] = df[c] * f
    return df


def fx_series(pair: str, end: date) -> pd.Series | None:
    try:
        return prices.yahoo(pair, date(2015, 1, 1), end)["close"]
    except Exception:
        return None


def build_eu(events: list[dict], end: date | None = None, use_network_figi: bool = True,
             since: str | None = None, *, confirmed_only=False, allow_universe_ablations=False) -> tuple[pd.DataFrame, Counter]:
    from .dataset import ECONOMIC_UNIVERSE_REASONS, _major, data_quality
    p, lim = params(), risk_limits()["candidate"]
    funnel, rows = Counter(), []
    evidence = load_evidence()
    idx = holders.build_index(events)
    end = end or date.today()
    for c in eu_campaigns(events, funnel):
        if since and c["published"] < since:
            continue
        funnel["EU kampagnen"] += 1
        reg = c["region"]
        isin = _issuer_key(c)
        row = {k: c.get(k) for k in ("event_id", "campaign_key", "published", "form", "signal_class", "event_type",
                                     "filer_id", "filer_name", "issuer_id", "issuer_name", "url", "region")}
        row.update(activist=c.get("filer_name"), isin=isin, demand="none", demand_evidence=None, stake=c.get("holding_ratio"))
        row.update(identity_status(c))
        if confirmed_only and c["event_id"] not in evidence:
            row["status"] = "gesperrt: kein geprüfter Identitätsnachweis"
            funnel["EU " + row["status"]] += 1
            rows.append(row)
            continue
        if row["identity_status"] == "conflict":
            row["status"] = "Identitätskonflikt"; funnel["EU Identitätskonflikt"] += 1; rows.append(row); continue
        if not c.get("published"):
            funnel["EU ohne Datum (kein Signal)"] += 1; row["status"] = "kein Datum"; rows.append(row); continue
        # Ohne Uhrzeit (z. B. SIX): published ist bereits konservativ der Folgetag 00:00 UTC (effective_decision_time)
        if not isin:
            funnel["EU ohne ISIN"] += 1; row["status"] = "keine ISIN"; rows.append(row); continue
        try:
            ticker = openfigi.isin_to_yahoo(isin, use_network_figi)
        except Exception:
            ticker = None
        if not ticker:
            funnel["EU ohne Ticker (Survivorship-Lücke)"] += 1; row["status"] = "kein Ticker"; rows.append(row); continue
        row["ticker"] = ticker
        approved = apply_identity(row, c, evidence)
        d0 = date.fromisoformat(c["published"][:10])
        try:
            px = load_px(ticker, d0 - timedelta(days=45), min(d0 + timedelta(days=420), end))
            if px.empty:
                raise ValueError("leer")
        except Exception:
            funnel["EU keine Kurse (delistet/Survivorship)"] += 1; row["status"] = "keine Kurse"; rows.append(row); continue
        pf = price_features(px, c["published"])
        # lokale Währung -> EUR am Entscheidungstag
        from ..sources.fx import eur_per_unit
        try:
            fx = eur_per_unit(ticker, reg, d0)
        except (ValueError, OSError, TypeError, ArithmeticError):
            funnel["EU Wechselkurs fehlt"] += 1; row["status"] = "gesperrt: Wechselkurs fehlt"; rows.append(row); continue
        pf_eur = dict(pf, price=pf["price"] * fx, adv=pf["adv"] * fx) if pf.get("ok") else pf
        shares = c.get("total_voting_rights")
        if not shares and c.get("voting_rights_held") and c.get("holding_ratio"):
            shares = c["voting_rights_held"] / c["holding_ratio"]   # Gesamtzahl aus der Meldung selbst
        uv = universe_check(p["universe"][reg], pf_eur, shares, None, False)
        row.update(price=pf_eur.get("price"), price_local=pf.get("price"), adv=pf_eur.get("adv"),
                   adv_eur=pf_eur.get("adv"), runup_20d=pf.get("runup_20d"),
                   half_spread=pf.get("half_spread"),
                   mcap=(shares * pf_eur["price"]) if shares and pf_eur.get("price") else None, fx=fx)
        if uv:
            for v in uv:
                funnel[f"EU Universum: {v}"] += 1
            if not (allow_universe_ablations and approved and set(uv) <= ECONOMIC_UNIVERSE_REASONS):
                row["status"] = "außerhalb Universum: " + "; ".join(uv)
                rows.append(row)
                continue
        else:
            funnel["EU im Universum"] += 1
        row["sector"] = None  # no archived sector classifications
        row["major_holder"] = _major(idx, (reg, isin), c["published"], row.get("stake"))
        row["days_to_earnings"], row["earnings_check"] = None, "unbekannt (keine Datenquelle)"
        reasons = [*uv, *candidate_reasons(row, p["order"]["position_eur"], lim)]      # row["adv"] ist bereits in EUR
        thr = EXIT_BELOW[reg]
        row["exit_events"] = [fu["published"] for fu in c["followups"]
                              if fu.get("holding_ratio") is not None and fu["holding_ratio"] < thr][:1]
        row["status"] = ("verworfen: " + "; ".join(reasons)) if reasons else "qualifiziert"
        funnel["EU verworfen" if reasons else "EU qualifiziert"] += 1
        row["price_check"] = None                 # keine zweite kostenlose Kursquelle für Europa -> ungeprüft
        row.update(data_quality(row, ticker_certain=bool(approved), activist_evidence=bool(approved and approved.get("activist_review"))))
        rows.append(row)
    return pd.DataFrame(rows), funnel
