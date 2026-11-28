"""EUR conversion from the latest completed daily FX observation."""
from datetime import date
import math

import pandas as pd

from . import prices

EU_SUFFIX = {".L": "GBPEUR=X", ".OL": "NOKEUR=X", ".ST": "SEKEUR=X",
             ".CO": "DKKEUR=X", ".SW": "CHFEUR=X", ".PA": None, ".AS": None,
             ".BR": None, ".DE": None, ".F": None, ".VI": None, ".HE": None,
             ".MI": None, ".MC": None, ".LS": None, ".IR": None}
BENCHMARK_SUFFIX = {"UK": ".L", "NORDIC": ".ST", "BENELUX_FR": ".PA", "DACH": ".DE"}
RATES = {}


def available_rate(series, day):
    """Daily quotes lack intraday publication timestamps. Exclude the current date."""
    cutoff = pd.Timestamp(day).normalize()
    eligible = series.loc[(series.index < cutoff) & (series.index >= cutoff - pd.Timedelta(days=7))].dropna()
    if eligible.empty:
        raise ValueError(f"kein verfügbarer Wechselkurs am {day}")
    value = float(eligible.iloc[-1])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"ungültiger Wechselkurs am {day}")
    return value


def eur_per_unit(ticker, region, day):
    if region in ("US", "JP"):
        pair = "EURUSD=X" if region == "US" else "EURJPY=X"
        inverse = True
    else:
        suffix = "." + ticker.rsplit(".", 1)[-1] if "." in ticker else None
        if region not in BENCHMARK_SUFFIX or suffix not in EU_SUFFIX:
            raise ValueError(f"unbekannte Kurswährung für {region}/{ticker}")
        pair, inverse = EU_SUFFIX[suffix], False
    if pair is None:
        return 1.0
    if pair not in RATES:
        RATES[pair] = prices.yahoo(pair, date(2014, 1, 1), date.today())["close"].sort_index()
    rate = available_rate(RATES[pair], day)
    return 1 / rate if inverse else rate


def benchmark_eur_per_unit(region, day, fx=eur_per_unit):
    ticker = "INDEX" + BENCHMARK_SUFFIX.get(region, "")
    return fx(ticker, region, day)
