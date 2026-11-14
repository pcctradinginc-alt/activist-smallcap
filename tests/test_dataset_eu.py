import pandas as pd
import pytest
from activist.study import dataset_eu as d

ISIN = "GB0007980591"


def ev(i, pub, filer="Elliott Advisors (UK) Limited", isin=ISIN, ratio=0.05, region="UK", has_time=True, src="NSM"):
    return {"event_id": f"{src}:{i}", "source": src, "region": region, "form": "MajorHolding", "published": pub,
            "has_time": has_time, "filer_name": filer, "issuer_id": isin, "issuer_name": "Issuer plc", "url": "u",
            "holding_ratio": ratio}


def _px(price=300.0, vol=1e6, hl=0.003):
    idx = pd.bdate_range("2025-11-01", "2027-03-01")
    return pd.DataFrame({"open": price, "high": price * (1 + hl), "low": price * (1 - hl), "close": price,
                         "close_raw": price, "volume": vol}, index=idx)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "DATA", tmp_path)
    monkeypatch.setattr(d.openfigi, "isin_to_yahoo", lambda isin, net=True: {ISIN: "BP.L", "NO0010096985": "EQNR.OL", "XX": None}.get(isin))
    state = {"px": _px()}

    class Cache(dict):
        def __setitem__(self, k, v):                 # Preiswechsel im Test -> Kurs-Cache leeren
            import shutil
            shutil.rmtree(tmp_path / "prices", ignore_errors=True)
            super().__setitem__(k, v)
    state = Cache(state)

    def fake_yahoo(t, s, e):
        if t.endswith("=X"):
            return pd.DataFrame({"close": 1.15 if t.startswith("GBP") else 0.085}, index=state["px"].index)
        return state["px"]
    monkeypatch.setattr(d.prices, "yahoo", fake_yahoo)
    return state


END = pd.Timestamp("2027-03-01").date()
T0 = "2026-06-10T10:00:00+00:00"


def test_pence_conversion_and_fx(env):
    ds, f = d.build_eu([ev(1, T0)], end=END)
    r = ds.iloc[0]
    # 300 GBp = 3,00 GBP = 3,45 EUR ; ADV = 1e6 * 3 * 1.15
    assert r.status == "qualifiziert" and r.ticker == "BP.L"
    assert r.price == pytest.approx(3.45) and r.adv == pytest.approx(3.45e6)
    assert f["EU Marktkapitalisierung ungeprüft"] == 1 and f["EU qualifiziert"] == 1 and r.signal_class == "B"


def test_non_activist_and_untimed_and_unmapped(env):
    evs = [ev(1, T0, filer="Morgan Stanley"),
           ev(2, T0, has_time=False, isin="NO0010096985"),
           ev(3, T0, filer="Saba Capital Management", isin="XX"),
           ev(4, T0, filer=None)]
    evs[2]["issuer_id"] = "FR0000120271"          # gültige ISIN-Form, aber FIGI kennt nichts
    ds, f = d.build_eu(evs, end=END)
    st = dict(zip(ds.issuer_id, ds.status))
    assert len(ds) == 2 and st["NO0010096985"] == "keine Uhrzeit" and st["FR0000120271"] == "kein Ticker"
    assert f["EU Ereignisse ohne Melder (PDF/Text nötig)"] == 1 and f["EU kampagnen"] == 2


def test_universe_and_risk_rules(env):
    env["px"] = _px(price=60.0, vol=1e6)            # 0,60 GBP = 0,69 EUR < 1 EUR Mindestkurs
    ds, f = d.build_eu([ev(1, T0)], end=END)
    assert ds.iloc[0].status.startswith("außerhalb Universum") and "Mindestkurs" in ds.iloc[0].status
    env["px"] = _px(price=300.0, vol=100)           # ADV ~ 345 EUR < 100k
    ds, _ = d.build_eu([ev(1, T0)], end=END)
    assert "Tagesumsatz" in ds.iloc[0].status
    env["px"] = _px(hl=0.03)                         # Spread-Schätzer hoch
    ds, _ = d.build_eu([ev(1, T0)], end=END)
    assert ds.iloc[0].status.startswith("verworfen") and "Spread" in ds.iloc[0].status


def test_first_event_only_and_exit_thresholds(env):
    evs = [ev(1, T0, ratio=0.031), ev(2, "2026-08-01T10:00:00+00:00", ratio=0.035),
           ev(3, "2026-09-01T10:00:00+00:00", ratio=0.029)]
    ds, _ = d.build_eu(evs, end=END)
    assert len(ds) == 1 and ds.iloc[0].event_id == "NSM:1"
    assert ds.iloc[0].exit_events == ["2026-09-01T10:00:00+00:00"]                    # UK: < 3 %
    nordic = [ev(1, T0, region="NORDIC", ratio=0.06, isin="NO0010096985", src="OSLO"),
              ev(2, "2026-09-01T10:00:00+00:00", region="NORDIC", ratio=0.04, isin="NO0010096985", src="OSLO")]
    ds, _ = d.build_eu(nordic, end=END)
    assert ds.iloc[0].exit_events == ["2026-09-01T10:00:00+00:00"]                    # sonst < 5 %
    ds, _ = d.build_eu([ev(1, T0, ratio=0.02)], end=END)                              # erste Meldung unter Schwelle
    assert ds.empty


def test_no_prices_status(env, monkeypatch):
    monkeypatch.setattr(d.prices, "yahoo", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    ds, f = d.build_eu([ev(1, T0)], end=END)
    assert ds.iloc[0].status == "keine Kurse" and f["EU keine Kurse (delistet/Survivorship)"] == 1


def test_since_date_respected(env):
    ds, _ = d.build_eu([ev(1, "2014-06-10T10:00:00+00:00")], end=END)
    assert ds.empty                                   # Elliott Advisors since 2015-01-01
