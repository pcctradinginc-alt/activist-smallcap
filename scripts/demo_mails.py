"""Schickt zwei Beispiel-Mails (Wochenbericht, Papier-Kauf) mit ERFUNDENEN Daten, deutlich als TEST markiert."""
import os, tempfile
from datetime import date, timedelta
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["ACTIVIST_ROOT"] = str(tmp)
import shutil
shutil.copytree(Path(__file__).resolve().parents[1] / "config", tmp / "config")
(tmp / "journal").mkdir(); (tmp / "reports").mkdir()

from activist.paper import portfolio, report   # noqa: E402  (nach ACTIVIST_ROOT)
from activist.notify.mail import send          # noqa: E402

t = date.today()
st = portfolio.load_state()
st.update(start_date=str(t - timedelta(days=90)), cash=16385.0, peak_value=20710.0)
demo = [("BEISPIEL-A", "US", "A", "Starboard Value", 60, 12.40, 13.95, True),
        ("BEISPIEL-B", "UK", "B", "Saba Capital", 41, 3.10, 2.88, False),
        ("BEISPIEL-C", "CH", "B", "Cevian Capital", 23, 48.20, 51.10, False),
        ("BEISPIEL-D", "JP", "A", "Oasis Management", 9, 14.75, 15.20, False)]
for i, (tk, reg, cl, act, ago, e, l, val) in enumerate(demo):
    st["positions"][f"demo{i}"] = {"ticker": tk, "region": "DACH" if reg == "CH" else reg, "signal_class": cl,
                                  "activist": act, "entry_date": str(t - timedelta(days=ago)), "entry_eur": e,
                                  "last_eur": l, "qty": 1000 / e, "validated": val}
st["orders"]["demo-o"] = {"ticker": "BEISPIEL-E", "region": "NORDIC", "activist": "Teleios Capital",
                          "order_date": str(t), "position_eur": 1000}
st["closed"].append({"ticker": "BEISPIEL-F", "region": "US", "activist": "Engaged Capital", "entry_date": str(t - timedelta(days=200)),
                     "exit_date": str(t - timedelta(days=2)), "reason": "Aktivist reduziert", "gross": 0.184, "bench": 0.031,
                     "net_excess": 0.118, "signal_class": "A", "validated": True})
st["history"] = [{"date": str(t - timedelta(days=7)), "value": 20180.0}, {"date": str(t), "value": 20530.0}]
portfolio.save_state(st)
portfolio.book_shadow({"candidate_id": "x1", "ticker": "BEISPIEL-G", "region": "US",
                       "reason": "verworfen: Anstieg 20T", "date": str(t)})
portfolio.book_shadow({"candidate_id": "x2", "ticker": "BEISPIEL-H", "region": "JP",
                       "reason": "verworfen: Mindestorder (100 Stück) > Position", "date": str(t)})
subj, body = report.weekly(t)
html = report.weekly_html(t)
(tmp / "weekly.html").write_text(html)
send("[TEST – Beispieldaten] " + subj, "*** TEST-MAIL MIT ERFUNDENEN BEISPIELDATEN ***\n\n" + body, html=html)

from activist.notify.templates import buy_mail   # noqa: E402
s2, txt, h2 = buy_mail({
    "ticker": "BEISPIEL", "company": "Beispiel Corp.", "region": "USA", "cls": "A", "activist": "Starboard Value",
    "stake": "7,2 %", "when": f"{t:%d.%m.%Y}, 16:42 Uhr (New York)", "form": "Schedule 13D, Erstmeldung",
    "demand": "Verkauf des Unternehmens", "evidence": "…believe the Board should explore strategic alternatives, including a sale…",
    "url": "https://www.sec.gov/", "campaign": "C-0a1b2c3d4e5f · erstes Ereignis",
    "limit": "12,77 USD", "valid": f"{t + timedelta(days=1):%d.%m.} – {t + timedelta(days=3):%d.%m.%Y}",
    "size": "5 % des Papierdepots = 1.000 €", "size_short": "1.000 €", "costs": "10 € Gebühr + ca. 0,75 % Spread/FX je Seite",
    "conf": {"mean": "+5,1 %", "ci": "+1,2 bis +9,0 %", "n": "84", "q25": "−14 %", "worst": "−61 %", "pos": "57 %"},
    "risk": "Liquidität ok · Spread 1,1 % · keine Kapitalerhöhung · kein Mehrheitsaktionär",
    "after": "5 Positionen · Region 40 % · Branche 18 % · Cash 61 %",
    "against": "Der Kurs ist in den letzten 20 Tagen bereits um 14 % gestiegen; ein Teil des Effekts ist womöglich eingepreist. "
               "Starboard hat bei zwei der letzten fünf Kampagnen ohne Ergebnis verkauft.",
    "hash": "3f9a1c0e7b2d4a55", "banner": "TEST-MAIL mit erfundenen Beispieldaten"})
(tmp / "buy.html").write_text(h2)
send("[TEST – Beispieldaten] " + s2, "*** TEST-MAIL MIT ERFUNDENEN BEISPIELDATEN ***\n\n" + txt, html=h2)
print("2 Test-Mails versendet", tmp)
