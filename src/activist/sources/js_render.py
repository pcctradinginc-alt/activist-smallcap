"""Headless-Chromium-Rendering (Playwright) fuer IR-Seiten, deren Listen per JavaScript nachgeladen werden (status 'js_only').

Konformitaet (wie eu_de_ir.Client): nur Hosts, deren robots.txt die Seite fuer Token 'activist-smallcap' erlaubt (Client.allowed:
robots 404 = erlaubt, unlesbar = gesperrt); nie Abruf von BLOCKED_HOSTS (auch nicht als Unterressource: Request wird abgebrochen);
XHR/fetch/Dokument-Unterressourcen auf FREMDEN Hosts nur, wenn deren robots.txt sie erlaubt; UA 'activist-smallcap research contact@example.org';
>= 1 s Abstand je Host (Client._wait); keine Bilder/Medien/Fonts; kein Login, kein CAPTCHA-Umgehen (HTTP >= 400 oder Challenge-Seite -> Skip).

Playwright ist optionale Abhaengigkeit: ohne Installation liefert available() False und render_html() wirft Skip.
CI: 'python -m playwright install --with-deps chromium'.
"""
from __future__ import annotations
import re
from urllib.parse import urlsplit
from .eu_common import UA

CHALLENGE_RE = re.compile(r"captcha|are you a robot|verify you are human|checking your browser|access denied|attention required|just a moment|cf-browser-verification", re.I)


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def render_html(url: str, client, timeout_ms: int = 35000, settle_ms: int = 1500) -> str:
    """Rendert url (nach robots-/Blockpruefung) in headless Chromium, wartet auf 'networkidle' und liefert das DOM-HTML."""
    from .eu_de_ir import Skip, host_blocked          # lokal: vermeidet Zirkularimport
    if not available():
        raise Skip("playwright nicht installiert")
    if host_blocked(url) or not client.allowed(url):
        raise Skip(f"robots/Blockliste: {url}")
    from playwright.sync_api import sync_playwright, Error as PWError
    page_host = (urlsplit(url).hostname or "").lower()
    verdict: dict[str, bool] = {}

    def sub_ok(u: str) -> bool:
        h = (urlsplit(u).hostname or "").lower()
        if not h or host_blocked(u):
            return False
        if h == page_host or u.startswith(("data:", "blob:")):
            return True
        if h not in verdict:
            verdict[h] = client.allowed(u)
        return verdict[h]

    def handler(route):
        req = route.request
        if req.resource_type in ("image", "media", "font"):
            return route.abort()
        if req.resource_type in ("xhr", "fetch", "document", "script", "stylesheet", "other") and not sub_ok(req.url):
            return route.abort()
        if req.resource_type in ("xhr", "fetch", "document"):          # Daten-/Dokumentabrufe getaktet; statische Assets zaehlen zum selben Seitenaufruf
            client._wait((urlsplit(req.url).hostname or "").lower())
        return route.continue_()

    client._wait(page_host)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                ctx = browser.new_context(user_agent=UA, locale="de-DE", java_script_enabled=True)
                page = ctx.new_page()
                page.route("**/*", handler)
                resp = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                if resp is not None and resp.status >= 400:
                    raise Skip(f"HTTP {resp.status}: {url}")
                page.wait_for_timeout(settle_ms)
                final = page.url
                if host_blocked(final) or not client.allowed(final):
                    raise Skip(f"Redirect auf gesperrtes Ziel: {final}")
                html = page.content()
            finally:
                browser.close()
    except PWError as e:
        raise Skip(f"Rendering-Fehler: {str(e)[:120]}")
    if CHALLENGE_RE.search(re.sub(r"<[^>]+>", " ", html)[:3000]) and len(html) < 20000:
        raise Skip("Bot-Schutz/Challenge-Seite erkannt (wird nicht umgangen)")
    return html
