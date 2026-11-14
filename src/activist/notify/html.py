"""Schlichte HTML-Mails im Apple-Stil. Nur Inline-CSS und Tabellen-Layout (Gmail/Outlook ignorieren <style>)."""
from __future__ import annotations
from html import escape

FONT = "-apple-system,BlinkMacSystemFont,'SF Pro Text','Helvetica Neue',Helvetica,Arial,sans-serif"
INK, SUB, LINE, BG = "#1d1d1f", "#86868b", "#e8e8ed", "#f5f5f7"
GREEN, RED, BLUE, ORANGE = "#1e8e3e", "#d70015", "#0066cc", "#b25000"


def e(x) -> str:
    return escape("" if x is None else str(x))


def signed(v: float | None, pct: bool = True, digits: int = 1) -> str:
    """Farbig formatierter Wert (grün/rot), deutsches Zahlenformat."""
    if v is None:
        return f'<span style="color:{SUB}">–</span>'
    s = f"{v * 100:+.{digits}f} %" if pct else f"{v:+,.0f} €"
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".").replace("-", "−")
    col = GREEN if v > 0 else RED if v < 0 else SUB
    return f'<span style="color:{col};font-weight:600">{s}</span>'


def eur(v: float | None, digits: int = 0) -> str:
    if v is None:
        return "–"
    return f"{v:,.{digits}f} €".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pill(text: str, color: str = SUB) -> str:
    return (f'<span style="display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600;'
            f'color:{color};background:{color}14;letter-spacing:.2px">{e(text)}</span>')


def kpis(items: list[tuple[str, str]]) -> str:
    """Kennzahlen-Zeile: [(Label, Wert-HTML), ...]."""
    w = int(100 / max(len(items), 1))
    cells = "".join(f'<td width="{w}%" style="padding:4px 0;vertical-align:top">'
                    f'<div style="font-size:12px;color:{SUB};margin-bottom:4px">{e(k)}</div>'
                    f'<div style="font-size:22px;font-weight:600;color:{INK};letter-spacing:-.3px">{v}</div></td>'
                    for k, v in items)
    return f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>{cells}</tr></table>'


def table(head: list[str], rows: list[list[str]], align: list[str] | None = None) -> str:
    """Zellen in rows sind bereits HTML; head wird escaped."""
    if not rows:
        return f'<div style="color:{SUB};font-size:14px">Keine</div>'
    align = align or ["left"] * len(head)
    th = "".join(f'<th align="{a}" style="font-size:11px;font-weight:600;color:{SUB};text-transform:uppercase;'
                 f'letter-spacing:.4px;padding:0 8px 8px 0;border-bottom:1px solid {LINE}">{e(h)}</th>'
                 for h, a in zip(head, align))
    tr = "".join("<tr>" + "".join(f'<td align="{a}" style="font-size:14px;color:{INK};padding:10px 8px 10px 0;'
                                   f'border-bottom:1px solid {LINE};vertical-align:top">{c}</td>'
                                   for c, a in zip(r, align)) + "</tr>" for r in rows)
    return f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation"><tr>{th}</tr>{tr}</table>'


def card(title: str | None, inner: str) -> str:
    t = (f'<div style="font-size:13px;font-weight:600;color:{SUB};text-transform:uppercase;letter-spacing:.5px;'
         f'margin-bottom:14px">{e(title)}</div>') if title else ""
    return (f'<tr><td style="padding:0 0 16px 0"><table width="100%" cellpadding="0" cellspacing="0" role="presentation" '
            f'style="background:#ffffff;border-radius:18px"><tr><td style="padding:24px 24px">{t}{inner}'
            f'</td></tr></table></td></tr>')


def text(s: str, size: int = 15, color: str = INK) -> str:
    return f'<div style="font-size:{size}px;line-height:1.5;color:{color}">{s}</div>'


def page(eyebrow: str, title: str, subtitle: str, cards: list[str], footer: str, banner: str | None = None) -> str:
    ban = (f'<tr><td style="padding:0 0 16px 0"><div style="background:#fff4e5;color:{ORANGE};border-radius:12px;'
           f'padding:10px 14px;font-size:13px;font-weight:500">{e(banner)}</div></td></tr>') if banner else ""
    return f"""<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<title>{e(title)}</title></head>
<body style="margin:0;padding:0;background:{BG};-webkit-font-smoothing:antialiased">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:{BG}"><tr><td align="center" style="padding:32px 16px">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:640px;font-family:{FONT}">
<tr><td style="padding:0 4px 24px 4px">
<div style="font-size:13px;font-weight:600;color:{SUB};letter-spacing:.3px">{e(eyebrow)}</div>
<div style="font-size:32px;font-weight:700;color:{INK};letter-spacing:-.6px;line-height:1.15;margin-top:6px">{e(title)}</div>
<div style="font-size:17px;color:{SUB};margin-top:6px">{e(subtitle)}</div></td></tr>
{ban}{''.join(cards)}
<tr><td style="padding:8px 4px 0 4px;font-size:12px;line-height:1.6;color:{SUB}">{footer}</td></tr>
</table></td></tr></table></body></html>"""
