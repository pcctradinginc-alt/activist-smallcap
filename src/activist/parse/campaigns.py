"""Ereignis -> Kampagne + Signalklasse (Regel 5: Kennungen; Klassen überschneidungsfrei)."""
from __future__ import annotations
from ..common import campaign_id, load_yaml

ACTIVE_FORMS = {"SC 13D", "SCHEDULE 13D"}
AMEND_FORMS = {"SC 13D/A", "SCHEDULE 13D/A"}
PASSIVE_FORMS = {"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
JP_ACTIVE_PURPOSE = "重要提案行為"


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


def event_type(ev: dict, prior_forms: list[str]) -> str:
    f = ev["form"]
    if f in ACTIVE_FORMS:
        return "switch_13g_13d" if any(p in PASSIVE_FORMS for p in prior_forms) else "initial_13d"
    if f in AMEND_FORMS:
        return "amendment"
    if f in PASSIVE_FORMS:
        return "passive"
    if ev["source"] == "EDINET":
        return "jp_active_purpose" if JP_ACTIVE_PURPOSE in (ev.get("purpose") or "") else "jp_threshold"
    return "other"


def signal_class(etype: str, filer_is_activist: bool) -> str | None:
    """A = erklärte Einflussabsicht, B = reine Schwellenmeldung eines bekannten Aktivisten, C = öffentl. Kampagne ohne Meldung."""
    if etype in ("initial_13d", "switch_13g_13d", "jp_active_purpose"):
        return "A"
    if etype in ("passive", "jp_threshold") and filer_is_activist:
        return "B"
    return None


def assign(events: list[dict]) -> list[dict]:
    """Ordnet Ereignisse chronologisch Kampagnen zu. Erstes Ereignis bestimmt die Klasse."""
    seen: dict[str, dict] = {}
    forms_by_pair: dict[str, list[str]] = {}
    out = []
    for ev in sorted(events, key=lambda e: e["published"]):
        filer, issuer = ev.get("filer_id") or ev.get("filer_name") or "?", ev.get("issuer_id") or "?"
        cid = campaign_id(str(filer).lstrip("0"), str(issuer).lstrip("0"))
        prior = forms_by_pair.setdefault(cid, [])
        etype = event_type(ev, prior)
        is_act = str(filer).lstrip("0") in known_activists(ev["published"])
        cls = signal_class(etype, is_act)
        e = dict(ev, campaign_id=cid, event_type=etype, filer_is_activist=is_act)
        if cid not in seen and cls:
            seen[cid] = {"first_event": ev["event_id"], "class": cls}
            e.update(signal_class=cls, is_first_event=True)
        else:
            e.update(signal_class=seen.get(cid, {}).get("class"), is_first_event=False)
        prior.append(ev["form"])
        out.append(e)
    return out
