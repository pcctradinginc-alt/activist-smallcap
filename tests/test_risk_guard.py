"""Regel: Das System darf config/risk_limits.yaml nie schreiben (nur manuell änderbar)."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
NAME = "risk_limits.yaml"
WRITE_OPEN = re.compile(r"open\([^)]*['\"][wax][+bt]*['\"]")
OTHER_WRITE = re.compile(r"write_text|write_bytes|\.write\(|yaml\.(safe_)?dump|shutil\.(copy|move)|os\.(replace|rename)")


def offending_lines(text: str) -> list[int]:
    lines = text.splitlines()
    bad = []
    for i, line in enumerate(lines):
        if NAME in line:
            window = "\n".join(lines[max(0, i - 2): i + 3])
            if WRITE_OPEN.search(window) or OTHER_WRITE.search(window):
                bad.append(i + 1)
    return bad


def test_detector_catches_writes():
    assert offending_lines("open('config/risk_limits.yaml', 'w')")
    assert offending_lines("p = CONFIG / 'risk_limits.yaml'\np.write_text('x')")
    assert not offending_lines("load_yaml('risk_limits.yaml')")


def test_no_source_writes_risk_limits():
    py = list(SRC.rglob("*.py"))
    assert py
    problems = {}
    for p in py:
        bad = offending_lines(p.read_text(encoding="utf-8"))
        if bad:
            problems[str(p.relative_to(SRC))] = bad
    assert not problems, f"Schreibzugriff auf {NAME}: {problems}"


def test_no_dynamic_write_with_config_path():
    # Schreibender open()/write auf CONFIG-Pfade generell verboten
    for p in SRC.rglob("*.py"):
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "CONFIG" in line and (WRITE_OPEN.search(line) or "write_text" in line or "write_bytes" in line):
                raise AssertionError(f"{p}:{n} schreibt nach CONFIG")
