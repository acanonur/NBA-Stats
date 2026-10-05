"""Outbound account mail: verification, password-reset and email-change links, and the
signup-conflict notice — sent through whichever of three backends
:class:`~nbastats.accounts.config.AuthSettings` names (``HARDWOOD_MAILER``), all built on the
stdlib so this feature needs no new dependency:

* ``log`` (the default) — write the message to the uvicorn log at ``INFO``. This is the whole
  story for local development: there is no mail server, and there does not need to be one —
  copy the link out of the terminal.
* ``file`` — also drop a ``.eml`` into ``backend/.hardwood-mail/`` (created on first use), so a
  developer without a terminal handy, or an integration test, can read the message back.
* ``smtp`` — send it for real over :mod:`smtplib`, ``smtps://`` (implicit TLS) or
  ``smtp+starttls://`` only. ``smtp://`` is refused at process startup
  (``accounts/config.py::startup_refusals``), never here — by the time a caller reaches this
  module the settings have already been validated once.

Every send in this module goes through :class:`BackgroundTasks` from the calling route,
**after** the HTTP response has been composed (never awaited inline) — a signup or
password-reset endpoint that always answers in the same amount of time whether or not the
address exists is the whole point of the anti-enumeration design in ``WEB_DESIGN.md`` §4.9,
and a slow mail server must never become a stopwatch that gives the game away.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlsplit

from ..db import utcnow
from .config import AuthSettings, get_auth_settings

__all__ = [
    "Mail",
    "delivers_mail",
    "send",
    "send_verification_email",
    "send_reset_email",
    "send_signup_conflict_email",
    "send_email_change_email",
]

logger = logging.getLogger("nbastats.accounts.mail")

#: ``backend/.hardwood-mail/`` — a sibling of ``backend/nbastats/``, matching
#: ``WEB_DESIGN.md``'s directory tree (and the ``.gitignore`` entry WP0 adds for it).
_MAIL_DIR = Path(__file__).resolve().parents[2] / ".hardwood-mail"


@dataclass(frozen=True, slots=True)
class Mail:
    """One outgoing message. ``text`` is plain text on purpose — there is no HTML mail
    template in this product, and a plain-text account-security email is also the one least
    likely to be mangled by a spam filter."""

    to: str
    subject: str
    text: str


def delivers_mail(settings: AuthSettings | None = None) -> bool:
    """True when a message handed to :func:`send` actually leaves this machine.

    ``log`` and ``file`` do not deliver anything: they write the message where the *operator*
    can read it. The SPA reads this (through ``GET /v1/auth/methods``) so "Check your email"
    is never shown to someone whose reset link is sitting in a terminal they cannot see.
    """
    settings = settings or get_auth_settings()
    return settings.mailer == "smtp"


def send(mail: Mail, *, settings: AuthSettings | None = None) -> None:
    """Dispatch ``mail`` through the configured backend."""
    settings = settings or get_auth_settings()
    mailer = settings.mailer
    if mailer == "log":
        _send_log(mail, settings)
    elif mailer == "file":
        _send_log(mail, settings)
        _send_file(mail)
    elif mailer == "smtp":
        _send_smtp(mail, settings)
    else:  # pragma: no cover - config.py already restricts this to MAILERS
        raise ValueError(f"unknown HARDWOOD_MAILER {mailer!r}")


def _logger_would_emit() -> bool:
    """True when an ``INFO`` record on this module's logger actually reaches a handler.

    ``uvicorn`` configures only its *own* loggers, and nothing in this codebase calls
    ``logging.basicConfig``, so under a plain ``uvicorn nbastats.api.app:app`` a
    ``logger.info(...)`` here goes to the root logger, finds no handler, and is dropped by
    ``logging.lastResort`` (which is fixed at ``WARNING``). The link is then written nowhere at
    all — and with the default ``HARDWOOD_MAILER=log`` and no SMTP server, that link is the only
    way anybody verifies an address or resets a password on a fresh laptop.
    """
    if not logger.isEnabledFor(logging.INFO):
        return False
    current: logging.Logger | None = logger
    while current is not None:
        if current.handlers:
            return True
        current = current.parent if current.propagate else None
    return False


def _send_log(mail: Mail, settings: AuthSettings) -> None:
    logger.info("mail -> %s: %s\n%s", mail.to, mail.subject, mail.text)
    if not _logger_would_emit() and settings.is_loopback:
        # Last resort, so the console still shows the link. `print` rather than a handler this
        # module installs on the root logger: a library that reconfigures global logging on
        # import is a worse surprise than one extra line on stdout.
        #
        # Gated on a loopback deployment. Off localhost, an unconditional `print` wrote live
        # single-use reset links to stdout *outside* logging configuration entirely, so an
        # operator who had turned this module's logger down could not stop them reaching
        # journald. A non-loopback deployment with `HARDWOOD_MAILER=log` is now a startup
        # refusal anyway (`config.startup_refusals`); this is the second belt.
        print(f"mail -> {mail.to}: {mail.subject}\n{mail.text}", flush=True)


def _send_file(mail: Mail) -> None:
    _MAIL_DIR.mkdir(parents=True, exist_ok=True)
    message = _build_message(mail, from_addr="hardwood@localhost")
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
    safe_to = "".join(ch if ch.isalnum() else "_" for ch in mail.to)[:64] or "unknown"
    path = _MAIL_DIR / f"{stamp}-{safe_to}.eml"
    path.write_bytes(bytes(message))


def _build_message(mail: Mail, *, from_addr: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = mail.subject
    message["From"] = from_addr
    message["To"] = mail.to
    message.set_content(mail.text)
    return message


def _send_smtp(mail: Mail, settings: AuthSettings) -> None:
    if not settings.smtp_url:
        raise RuntimeError("HARDWOOD_MAILER=smtp requires HARDWOOD_SMTP_URL")
    parsed = urlsplit(settings.smtp_url)
    if parsed.scheme not in ("smtps", "smtp+starttls"):
        # Defence in depth: accounts/config.py::startup_refusals already refuses a bare
        # smtp:// at process startup, so reaching here with the wrong scheme means the
        # settings object was built by hand (a test, a script) rather than from the
        # environment. Refuse rather than silently sending credentials in clear text.
        raise RuntimeError(
            f"HARDWOOD_SMTP_URL scheme {parsed.scheme!r} is not smtps:// or smtp+starttls://"
        )
    host = parsed.hostname or "localhost"
    port = parsed.port or (465 if parsed.scheme == "smtps" else 587)
    from_addr = settings.mail_from or f"hardwood@{host}"
    message = _build_message(mail, from_addr=from_addr)

    context = ssl.create_default_context()
    if parsed.scheme == "smtps":
        with smtplib.SMTP_SSL(host, port, timeout=10, context=context) as client:
            _authenticate_and_send(client, parsed, message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as client:
            client.starttls(context=context)
            _authenticate_and_send(client, parsed, message)


def _authenticate_and_send(client: smtplib.SMTP, parsed, message: EmailMessage) -> None:
    if parsed.username:
        client.login(parsed.username, parsed.password or "")
    client.send_message(message)


# --------------------------------------------------------------------------- convenience


def send_verification_email(email: str, link: str) -> None:
    send(
        Mail(
            to=email,
            subject="Verify your Hardwood account",
            text=(
                "Confirm this is your email address to finish setting up your Hardwood "
                f"account:\n\n{link}\n\nThis link expires in 3 days. If you didn't request "
                "this, you can ignore it."
            ),
        )
    )


def send_reset_email(email: str, link: str) -> None:
    send(
        Mail(
            to=email,
            subject="Reset your Hardwood password",
            text=(
                f"Someone asked to reset the password on this account:\n\n{link}\n\nThis "
                "link expires in 1 hour and can only be used once. If you didn't request "
                "this, you can ignore it — your password will not change."
            ),
        )
    )


def send_signup_conflict_email(email: str, signin_link: str) -> None:
    """Sent instead of a verification email when someone tries to sign up with an address
    that already has an account — the address's real owner learns about the attempt without
    the caller ever finding out the address was taken (``WEB_DESIGN.md`` §4.9)."""
    send(
        Mail(
            to=email,
            subject="Someone tried to sign up with your Hardwood email",
            text=(
                "Someone just tried to create a new Hardwood account with this email "
                "address, which already has one. If that was you, use this link to sign in "
                f"or reset your password instead:\n\n{signin_link}\n\nIf it wasn't you, no "
                "action is needed — your account is unaffected."
            ),
        )
    )


def send_email_change_email(new_email: str, link: str) -> None:
    send(
        Mail(
            to=new_email,
            subject="Confirm your new Hardwood email address",
            text=(
                f"Confirm this address to finish changing the email on your Hardwood "
                f"account:\n\n{link}\n\nThis link expires in 1 hour. If you didn't request "
                "this, you can ignore it — your email will not change."
            ),
        )
    )
