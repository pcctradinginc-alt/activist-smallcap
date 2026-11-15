"""Systemstatus: Lauf-Protokoll der Quellen und Gesamtstatus (Datenqualität, Alpha-Nachweis, Fehler, Lernfortschritt)."""
from __future__ import annotations
import glob, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .common import REPORTS, ROOT, atomic_write_text

HEALTH = REPORTS / "health.json"      # Altlast: gemeinsame Datei, wird nur noch gelesen
KEEP = 60
STALE_DAYS = 4                          # Quelle ohne Lauf seit > 4 Tagen (deckt ein Wochenende) = veraltet
CORE = ("EDGAR", "EDINET")               # Kernquellen: Ausfall = FAILED


def _load(path) -> list:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def record_run(name: str, results: dict[str, dict], path=None) -> dict:
    path = Path(path or REPORTS / f"health_{name}.json")     # eine Datei je Lauf-Name -> keine Git-Konflikte zwischen Workflows
    srcs = {}
    for k, r in (results or {}).items():
        r = r or {}
        err = r.get("error")
        srcs[k] = {"ok": bool(r.get("ok", err is None)), "n_new": int(r.get("n_new") or 0),
                   "error": str(err) if err else None}
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "name": name, "sources": srcs}
    runs = (_load(path) + [rec])[-KEEP:]
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(runs, ensure_ascii=False, indent=1))
    return rec


def _latest_study(pattern) -> dict | None:
    for f in sorted(glob.glob(str(pattern)), reverse=True):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            continue
    return None


def status(path=None, study_json_glob=None, freeze_path=None, min_campaigns_learning: int = 200, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    pattern = study_json_glob or REPORTS / "event_study_*.json"
    freeze = Path(freeze_path or ROOT / "PROTOCOL_FREEZE.json")
    study = _latest_study(pattern)
    dq = {"verified_share": None, "n_events": None, "n_campaigns_valid": None, "n_campaigns_unverified": None}
    alpha = {"status": "kein Bericht", "per_combo": {}}
    learning = {"closed_campaigns": 0, "needed": min_campaigns_learning, "status": "zu wenig Daten"}
    if study:
        dq.update({k: v for k, v in (study.get("data_quality") or {}).items() if k in dq})
        a = study.get("alpha") or {}
        alpha["per_combo"] = a.get("holdout") or {}
        alpha["status"] = a.get("status") or "nicht beurteilbar"      # maßgeblich ist der Studienstatus, nicht die Einzelkombis
        lr = study.get("learning") or {}
        learning["closed_campaigns"] = int(lr.get("closed_campaigns") or dq.get("n_campaigns_valid") or 0)
        learning["status"] = lr.get("status") or learning["status"]
        learning["evaluation"] = lr.get("evaluation")
        learning["holdout"] = (lr.get("holdout") or {}).get("status")
        learning["robust_features"] = [f for f, v in (lr.get("features") or {}).items() if v.get("status") == "belastbar"]
    errors, sources = [], {}
    runs = sorted(_load(path) if path else [r for f in sorted(REPORTS.glob("health*.json")) for r in _load(f)], key=lambda r: r.get("ts") or "")
    for r in runs:
        for k, v in (r.get("sources") or {}).items():
            if k != "tage":
                sources[k] = {"ok": bool(v.get("ok")), "ts": r.get("ts") or "", "error": v.get("error")}
    for k, v in sorted(sources.items()):
        try:
            v["stale"] = datetime.fromisoformat(v["ts"]) < now - timedelta(days=STALE_DAYS)
        except ValueError:
            v["stale"] = True
        if v["stale"]:
            v["ok"], v["error"] = False, f"{k}: veraltet (letzter Lauf {v['ts'][:10]})"
            errors.append(v["error"])
        elif not v["ok"]:
            errors.append(f"{k}: {v.get('error') or 'Lauf fehlgeschlagen'}")
    bad = [k for k, v in sources.items() if not v["ok"]]
    overall = "FAILED" if any(k in CORE for k in bad) else "DEGRADED" if bad else "OK"
    if freeze_path is None:                       # Standard: eingefroren UND unverändert (Prüfsummen)
        from .study.alpha import freeze_state
        fs = freeze_state()
        if not fs["frozen"]:
            errors.append(fs["reason"])
    elif not freeze.exists():
        errors.append("Protokoll nicht eingefroren")
    return {"data_quality": dq, "alpha": alpha, "errors": errors, "learning": learning, "sources": sources, "overall": overall}


def gate(st: dict, core: list[str], pool: list[str]) -> list[str]:
    """Probleme, die einen Workflow rot färben sollen: Kernquelle fehlt/fehlerhaft oder weniger als die Hälfte des Pools ok."""
    src, out = st.get("sources") or {}, []
    for k in core:
        if k not in src:
            out.append(f"{k}: nie gelaufen")
        elif not src[k]["ok"]:
            out.append(f"{k}: {src[k].get('error') or 'Lauf fehlgeschlagen'}")
    n_ok = sum(1 for k in pool if k in src and src[k]["ok"])
    if pool and n_ok * 2 < len(pool):
        out.append(f"nur {n_ok} von {len(pool)} Quellen erfolgreich")
    return out
