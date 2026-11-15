"""Konfidenzfaktor Betriebsstufe 1: vier Zahlen direkt aus der Ereignisstudie (kein Modell)."""
from __future__ import annotations


def shrink_to_mean(value: float, n: int, global_mean: float, k: int = 10) -> float:
    """Aktivisten-Kennzahl zum Gesamtdurchschnitt schrumpfen, solange < k abgeschlossene Kampagnen."""
    w = min(n / k, 1.0)
    return global_mean + w * (value - global_mean)
