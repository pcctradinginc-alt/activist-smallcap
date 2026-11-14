"""E-Mail-Versand über SMTP (Zugangsdaten nur aus GitHub Secrets / Umgebung)."""
from __future__ import annotations
import os, smtplib
from email.message import EmailMessage

REQUIRED_SECTION = "WAS DAGEGEN SPRICHT"


def send(subject: str, body: str, dry_run: bool | None = None, html: str | None = None) -> bool:
    if "[Papier-Kauf]" in subject and REQUIRED_SECTION not in body:
        raise ValueError("Pflichtabschnitt 'Was dagegen spricht' fehlt – E-Mail wird nicht versendet")
    repo = (os.environ.get("GITHUB_REPOSITORY") or "activist-smallcap").split("/")[-1]
    subject = f"[{repo}] {subject}"   # Repo-Name im Betreff zur Zuordnung
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("MAIL_TO")
    if dry_run or not host or not to:
        print(f"[dry-run] {subject}\n{body}")
        return False
    msg = EmailMessage(); msg["Subject"] = subject; msg["To"] = to
    msg["From"] = os.environ.get("MAIL_FROM", os.environ.get("SMTP_USER", to)); msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")   # Text bleibt als Fallback
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", 465))) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"]); s.send_message(msg)
    return True
