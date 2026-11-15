"""Systemstatus: Lauf-Protokoll der Quellen und Gesamtstatus (Datenqualität, Alpha-Nachweis, Fehler, Lernfortschritt)."""
from __future__ import annotations
import glob, json
from datetime import datetime, timezone
from pathlib import Path
from .common import REPORTS, ROOT

HEALTH = REPORTS / "health.json"
KEEP = 60


def _load(path) -> list:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def record_run(name: str, results: dict[str, dict], path=None) -> dict:
    path = Path(path or HEALTH)
    srcs = {}
    for k, r in (results or {}).items():
        r = r or {}
        err = r.get("error")
        srcs[k] = {"ok": bool(r.get("ok", err is None)), "n_new": int(r.get("n_new") or 0),
                   "error": str(err) if err else None}
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "name": name, "sources": srcs}
    runs = (_load(path) + [rec])[-KEEP:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8")
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


def status(path=None, study_json_glob=None, freeze_path=None, min_campaigns_learning: int = 200) -> dict:
    path = Path(path or HEALTH)
    pattern = study_json_glob or REPORTS / "event_study_*.json"
    freeze = Path(freeze_path or ROOT / "PROTOCOL_FREEZE.json")
    study = _latest_study(pattern)
    dq = {"verified_share": None, "n_events": None, "n_campaigns_valid": None, "n_campaigns_unverified": None}
    alpha = {"status": "kein Bericht", "per_combo": {}}
    learning = {"closed_campaigns": 0, "needed": min_campaigns_learning, "status": "zu wenig Daten"}
    if study:
        dq.update({k: v for k, v in (study.get("data_quality") or {}).items() if k in dq})
        a = study.get("alpha") or {}
        hold = a.get("holdout") or {}
        alpha["per_combo"] = hold
        sts = [str(v.get("status")) for v in hold.values()]
        if not a.get("frozen") or not hold:
            alpha["status"] = "nicht beurteilbar"
        elif any(s == "nachgewiesen" for s in sts):
            alpha["status"] = "nachgewiesen"
        elif any(s == "nicht beurteilbar" for s in sts) and not all(s == "nicht nachgewiesen" for s in sts):
            alpha["status"] = "nicht beurteilbar"
        else:
            alpha["status"] = "nicht nachgewiesen"
        lr = study.get("learning") or {}
        learning["closed_campaigns"] = int(lr.get("closed_campaigns") or dq.get("n_campaigns_valid") or 0)
        learning["status"] = lr.get("status") or learning["status"]
    errors = []
    runs = _load(path)
    last: dict[str, dict] = {}
    for r in runs:
        for k, v in (r.get("sources") or {}).items():
            last[k] = v
    for k, v in sorted(last.items()):
        if not v.get("ok"):
            errors.append(f"{k}: {v.get('error') or 'Lauf fehlgeschlagen'}")
    if not freeze.exists():
        errors.append("Protokoll nicht eingefroren")
    return {"data_quality": dq, "alpha": alpha, "errors": errors, "learning": learning}
