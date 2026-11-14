from __future__ import annotations
import hashlib, json, os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import yaml

ROOT = Path(os.environ.get("ACTIVIST_ROOT", Path(__file__).resolve().parents[2]))
CONFIG = ROOT / "config"
DATA = ROOT / "data"
JOURNAL = ROOT / "journal"
REPORTS = ROOT / "reports"
USER_AGENT = os.environ.get("SEC_USER_AGENT", "activist-smallcap research bot admin@example.invalid")


def load_yaml(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as f:
        return yaml.safe_load(f)


def params() -> dict:
    return load_yaml("params.yaml")


def risk_limits() -> dict:
    return load_yaml("risk_limits.yaml")


def effective_decision_time(published: str | None, date_only: str | None = None) -> datetime:
    """Regel 1: Veröffentlichungszeitpunkt. Fehlt die Uhrzeit, gilt der Folgetag (00:00 UTC)."""
    if published:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    d = datetime.fromisoformat(date_only).replace(tzinfo=timezone.utc)
    return d + timedelta(days=1)


def event_id(source: str, accession: str) -> str:
    return f"{source}:{accession}"


def campaign_id(filer: str, issuer: str) -> str:
    return "C-" + hashlib.sha1(f"{filer.strip().upper()}|{issuer.strip().upper()}".encode()).hexdigest()[:12]


class EventStore:
    """Append-only JSONL je Monat. Dubletten (gleiche event_id) werden nie doppelt geschrieben."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or DATA / "events")
        self.root.mkdir(parents=True, exist_ok=True)

    def _ids(self) -> set[str]:
        ids = set()
        for p in self.root.glob("*.jsonl"):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ids.add(json.loads(line)["event_id"])
        return ids

    def append(self, events: list[dict]) -> int:
        known, n = self._ids(), 0
        for ev in events:
            if ev["event_id"] in known:
                continue
            path = self.root / f"{ev['published'][:7]}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
            known.add(ev["event_id"]); n += 1
        return n

    def all(self) -> list[dict]:
        out = []
        for p in sorted(self.root.glob("*.jsonl")):
            out += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        return sorted(out, key=lambda e: e["published"])


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
