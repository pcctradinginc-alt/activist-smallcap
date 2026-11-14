"""Konfidenzfaktor Betriebsstufe 1: vier Zahlen direkt aus der Ereignisstudie (kein Modell)."""
from __future__ import annotations
import math


def shrink_to_mean(value: float, n: int, global_mean: float, k: int = 10) -> float:
    """Aktivisten-Kennzahl zum Gesamtdurchschnitt schrumpfen, solange < k abgeschlossene Kampagnen."""
    w = min(n / k, 1.0)
    return global_mean + w * (value - global_mean)


def final_probability(p: float, base_rate: float, quality: float) -> float:
    """Lernstufe: p_final = b + (p - b) * q."""
    q = max(0.0, min(1.0, quality))
    return base_rate + (p - base_rate) * q


def logistic(z: float) -> float:
    return 1 / (1 + math.exp(-z))


def position_size(expected: float, ci_low: float, n: int) -> float:
    """Tabelle der Lernstufe; in Stufe 1 erhalten alle qualifizierten Signale 5 %."""
    if expected < 0.03 or ci_low < 0:
        return 0.0
    if expected < 0.06:
        return 0.025
    if expected < 0.10 or n < 50:
        return 0.05
    return 0.08


def format_confidence(s: dict) -> str:
    thin = " – dünne Datenlage" if s.get("n", 0) < 20 else ""
    return (f"Erwartete Nettoüberrendite: {s['mean']:+.1%} (90-%-Bereich {s['ci_low']:+.1%} bis {s['ci_high']:+.1%})\n"
            f"Unabhängige Vergleichskampagnen: {s['n']}{thin}\n"
            f"Schlechtestes Viertel: {s['worst_quartile']:+.0%} oder schlechter | schlechtester Fall: {s['worst']:+.0%}\n"
            f"Anteil mit positiver Nettoüberrendite: {s['p_positive']:.0%}")
