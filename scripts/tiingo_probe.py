"""Prüft den Tiingo-Schlüssel mit delisteten US-Titeln (Schlüssel wird nie ausgegeben)."""
from datetime import date
from activist.sources import prices
for t, ref in [("RHT", 125.0), ("TIF", 90.0), ("NAME", 8.5)]:
    try:
        df = prices.tiingo(t, date(2017, 6, 1), date(2017, 7, 1))
        print(t, len(df), "Zeilen, Schluss", round(float(df.close_raw.iloc[-1]), 2),
              "plausibel:", prices.validate_against(df, date(2017, 6, 30), ref))
    except Exception as e:
        print(t, "Fehler:", str(e)[:120])
