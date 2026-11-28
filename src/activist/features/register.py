"""Aktivisten-Register, point-in-time (Regel 2).

Wer als Aktivist gilt, wird je Stichtag bestimmt:
  1. Eintrag in config/activists.yaml mit since <= Stichtag, oder
  2. Verhalten: der Melder hat vor dem Stichtag 13D-Erstmeldungen bei >= MIN_ISSUERS verschiedenen
     Emittenten abgegeben (Serien-13D-Melder = Fonds mit Einflussabsicht, keine Gründer/Insider).
Kennzahlen werden nur aus Kampagnen berechnet, deren Ausstieg vor dem Stichtag lag, und bei
< 10 abgeschlossenen Kampagnen zum Gesamtdurchschnitt hin geschrumpft.
"""
from __future__ import annotations
import bisect
from collections import defaultdict
import pandas as pd
from ..confidence.basic import shrink_to_mean
from ..common import load_yaml

MIN_ISSUERS = 3
INITIAL = {"SC 13D", "SCHEDULE 13D"}


class Register:
    def __init__(self, events: list[dict], results: pd.DataFrame | None = None):
        self.first13d = defaultdict(list)          # filer -> sortierte (zeit, emittent)
        seen = set()
        for e in sorted(events, key=lambda e: e["published"]):
            f, i = str(e.get("filer_id") or "").lstrip("0"), str(e.get("issuer_id") or "").lstrip("0")
            if e["form"] in INITIAL and f and i and f != i and (f, i) not in seen:
                seen.add((f, i)); self.first13d[f].append(e["published"])
        self.listed_since = {}
        for entry in load_yaml("activists.yaml")["activists"]:
            for field in ("cik", "edinet"):
                if entry.get(field):
                    key = str(entry[field]).lstrip("0")
                    since = str(entry.get("since", "1900-01-01"))
                    self.listed_since[key] = min(since, self.listed_since.get(key, since))
        self.results = results if results is not None else pd.DataFrame()

    def prior_campaigns(self, filer: str, asof: str) -> int:
        return bisect.bisect_left(self.first13d.get(str(filer).lstrip("0"), []), asof)

    def is_listed(self, filer: str, asof: str) -> bool:
        """Im kuratierten Register (config/activists.yaml) zum Stichtag."""
        start = self.listed_since.get(str(filer or "").lstrip("0"))
        return start is not None and start <= asof[:10]

    def is_activist(self, filer: str, asof: str) -> bool:
        f = str(filer or "").lstrip("0")
        return self.is_listed(f, asof) or self.prior_campaigns(f, asof) >= MIN_ISSUERS

    def metrics(self, filer: str, asof: str) -> dict:
        """Nur abgeschlossene Kampagnen mit exit_date < asof; Schrumpfung bei < 10."""
        r = self.results
        if r.empty:
            return {"n_closed": 0, "mean_net": None, "hit_rate": None, "prior_campaigns": self.prior_campaigns(filer, asof)}
        ts = pd.Timestamp(asof)
        ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        closed = r[pd.to_datetime(r.exit_date, utc=True, format="ISO8601") < ts]
        if closed.empty:
            return {"n_closed": 0, "mean_net": None, "hit_rate": None, "prior_campaigns": self.prior_campaigns(filer, asof)}
        g_mean, g_hit = closed.net_excess.mean(), (closed.net_excess > 0).mean()
        own = closed[closed.filer_id.astype(str).str.lstrip("0") == str(filer).lstrip("0")]
        n = len(own)
        return {"n_closed": n,
                "mean_net": shrink_to_mean(own.net_excess.mean() if n else g_mean, n, g_mean),
                "hit_rate": shrink_to_mean((own.net_excess > 0).mean() if n else g_hit, n, g_hit),
                "prior_campaigns": self.prior_campaigns(filer, asof)}
