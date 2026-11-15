"""Aktivistenliste je Stichtag und Ereignisübersicht (Zuordnung zu Kampagnen: study/dataset.py)."""
from __future__ import annotations
from ..common import load_yaml

def known_activists(asof: str | None = None) -> dict[str, str]:
    """Aktivisten-Liste je Stichtag (Regel 2)."""
    out = {}
    for a in load_yaml("activists.yaml")["activists"]:
        if asof and str(a.get("since", "1900-01-01")) > asof[:10]:
            continue
        for k in ("cik", "edinet"):
            if a.get(k):
                out[str(a[k]).lstrip("0")] = a["name"]
    return out


def summary(events: list[dict]) -> dict:
    """Übersicht für `status`: Anzahl je Region/Quelle/Formular, Ereignisse ohne Uhrzeit."""
    from collections import Counter
    return {"n": len(events),
            "by_region": dict(Counter(e.get("region") or "?" for e in events)),
            "by_source": dict(Counter(e.get("source") or "?" for e in events)),
            "by_form": dict(Counter(e.get("form") or "?" for e in events)),
            "no_time": sum(1 for e in events if not e.get("has_time"))}
