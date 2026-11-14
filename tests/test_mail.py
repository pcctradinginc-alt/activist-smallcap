import pytest
from activist.notify import mail


@pytest.fixture(autouse=True)
def no_smtp(monkeypatch):
    for k in ("SMTP_HOST", "MAIL_TO", "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def test_paper_buy_requires_counter_section():
    with pytest.raises(ValueError):
        mail.send("[Papier-Kauf] ABC", "Nur Argumente dafür")


def test_paper_buy_requires_counter_section_even_dry_run():
    with pytest.raises(ValueError):
        mail.send("[Papier-Kauf] ABC", "x", dry_run=True)


def test_paper_buy_with_section_dry_run(capsys):
    assert mail.send("[Papier-Kauf] ABC", "WAS DAGEGEN SPRICHT: viel") is False
    assert "[dry-run]" in capsys.readouterr().out


def test_dry_run_without_smtp_env(capsys):
    assert mail.send("[Status] x", "body") is False
    assert "[dry-run]" in capsys.readouterr().out


def test_dry_run_flag_even_with_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.invalid"); monkeypatch.setenv("MAIL_TO", "a@b.invalid")
    assert mail.send("[Status] x", "body", dry_run=True) is False


def test_sends_via_smtp_mocked(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port): sent["host"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def login(self, u, p): sent["login"] = (u, p)
        def send_message(self, m): sent["subject"] = m["Subject"]

    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    for k, v in {"SMTP_HOST": "h", "MAIL_TO": "a@b.invalid", "SMTP_USER": "u", "SMTP_PASSWORD": "p"}.items():
        monkeypatch.setenv(k, v)
    assert mail.send("[Status] x", "body") is True
    assert sent["subject"].endswith("] [Status] x") and sent["host"] == ("h", 465)
