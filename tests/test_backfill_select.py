import time
from activist.sources import edgar_backfill as bf


def row(form, company, path, cik="1"):
    return {"form": form, "company": company, "cik": cik, "date": "2024-01-05", "path": path}


def test_select_groups_duplicates_and_keeps_13d():
    rows = [row("SC 13D", "Some Corp", "edgar/data/1/a.txt"), row("SC 13D", "Some Filer", "edgar/data/1/a.txt", "2"),
            row("SCHEDULE 13D/A", "X", "edgar/data/1/b.txt")]
    out = bf.select(rows, set())
    assert set(out) == {"edgar/data/1/a.txt", "edgar/data/1/b.txt"}
    assert out["edgar/data/1/a.txt"]["company"] == "Some Corp"  # first row per path


def test_select_13g_only_with_activist_name():
    keys = bf.activist_name_keys()
    assert bf._norm("Starboard Value LP")[:10] in keys
    rows = [row("SC 13G", "RANDOM HOLDINGS INC", "p/1.txt"),
            row("SC 13G", "Issuer Inc", "p/2.txt"), row("SC 13G", "STARBOARD VALUE LP", "p/2.txt", "9"),
            row("SC 13G/A", "Engaged Capital LLC", "p/3.txt")]
    out = bf.select(rows, keys)
    assert set(out) == {"p/2.txt", "p/3.txt"}


def test_select_empty_and_13g_not_kept_without_keys():
    assert bf.select([], set()) == {}
    assert bf.select([row("SC 13G", "Starboard Value", "p/1.txt")], set()) == {}


def test_ratelimiter_spacing(monkeypatch):
    sleeps = []
    clock = [100.0]
    monkeypatch.setattr(bf.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(bf.time, "sleep", lambda s: sleeps.append(s))
    rl = bf.RateLimiter(10)
    for _ in range(4):
        rl.wait()
    assert sleeps[0] == 0.0
    assert [round(s, 6) for s in sleeps] == [0.0, 0.1, 0.2, 0.3]


def test_ratelimiter_real_time_spacing():
    rl = bf.RateLimiter(50)
    t0 = time.monotonic()
    for _ in range(6):
        rl.wait()
    assert time.monotonic() - t0 >= 5 * 0.02 - 0.005

