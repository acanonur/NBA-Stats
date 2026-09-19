"""Auth/web configuration, read from the environment — deliberately its own frozen
dataclass, separate from :class:`nbastats.config.Settings`.

Why a second settings object
-----------------------------
``nbastats.config.Settings`` answers "how is the stats service configured" and is read from
every request path, demo or not. ``AuthSettings`` answers a different question — "is the web
sign-in feature configured, and how" — that a pure stats deployment never needs to ask: it
sets none of the ``HARDWOOD_*`` variables below, and folding cookies, OIDC client secrets and
SMTP credentials into ``Settings`` would force every caller of ``get_settings()`` to reason
about them regardless. Splitting them also matches how the two fail: a missing stats setting
is a misconfigured deployment; a missing or wrong auth setting is a security question, and
this module is where "refuse to start" versus "start, but warn" gets decided (see
:func:`startup_refusals` / :func:`startup_warnings`).

Why refusals exist at all, and why they are not just log lines
------------------------------------------------------------------
Every refusal below fires once, at process startup, before a single request is served, and
every one names the exact environment variable to change and what to change it to — there is
no request context to attach a better error to later, and "the server didn't start" with a
clear reason is a far better failure mode than "the server started and quietly shipped
plaintext session cookies over Wi-Fi". The loopback exception on the HTTP refusal is
deliberate, not an oversight: ``Secure`` cookies are silently dropped by the browser on plain
HTTP, so a non-developer running this on ``http://127.0.0.1:8000`` would otherwise see
"login does nothing" with no error anywhere; a *non-loopback* HTTP origin gets no such
leniency; see :func:`AuthSettings.is_loopback`.

Environment variables
----------------------
``HARDWOOD_PUBLIC_BASE_URL``       The *only* source of scheme/host/port for building
                                   redirect URIs, cookie flags and CSRF's Origin check.
                                   ``request.url`` is never used for this — it reports
                                   ``http`` behind a TLS-terminating proxy with no error.
``HARDWOOD_WEB_DIST``              Where the built SPA lives (default: ``<repo>/web/dist``).
``HARDWOOD_WEB``                   Set false to disable mounting the SPA entirely.
``HARDWOOD_SIGNUP_MODE``           ``open`` | ``invite`` (default) | ``closed``.
``HARDWOOD_INVITE_CODE``           A single well-known invite code, mainly for ``open`` mode's
                                   non-loopback backstop.
``HARDWOOD_SESSION_DAYS``          Sliding idle-session window (default 30).
``HARDWOOD_SESSION_ABSOLUTE_DAYS`` Hard session cap that is never extended (default 90).
``HARDWOOD_SCRYPT_N``              scrypt's CPU/memory cost exponent (default 14: 2**14, about
                                   60 ms and 16 MiB here).
``HARDWOOD_COOKIE_SECURE``         Force the ``Secure`` cookie flag on or off; unset derives
                                   it from whether the base URL is https.
``HARDWOOD_ALLOW_INSECURE_COOKIES`` Accept cleartext session cookies on a non-loopback HTTP
                                   origin (a trusted-LAN escape hatch; see the refusal above).
``HARDWOOD_DEV_LINKS``             Include a password-reset link directly in the API response
                                   when the caller is also loopback (no mail server needed in
                                   local development; never enabled by a base-URL check alone).
``HARDWOOD_MAILER``                ``log`` (default) | ``file`` | ``smtp``.
``HARDWOOD_SMTP_URL``              ``smtps://`` or ``smtp+starttls://`` only; ``smtp://`` is a
                                   startup refusal.
``HARDWOOD_MAIL_FROM``             The ``From:`` header for outgoing mail.
``HARDWOOD_GOOGLE_CLIENT_ID`` / ``HARDWOOD_GOOGLE_CLIENT_SECRET``
                                   Google sign-in; both must be set for the button to appear.
``HARDWOOD_APPLE_SERVICES_ID`` / ``HARDWOOD_APPLE_TEAM_ID`` / ``HARDWOOD_APPLE_KEY_ID`` /
``HARDWOOD_APPLE_KEY_FILE``        Sign in with Apple; all four are required, plus an https
                                   base URL (Apple's ``form_post`` needs a ``SameSite=None``
                                   cookie, which needs ``Secure``, which needs https).
``HARDWOOD_TRUSTED_PROXY_CIDRS`` / ``HARDWOOD_TRUSTED_PROXY_HOPS``
                                   Reverse-proxy trust for ``X-Forwarded-*`` headers; the
                                   default (empty / 0) strips them from every request.
``HARDWOOD_CSP_IMG_SRC``           Extra comma-separated ``img-src`` origins for the Content-
                                   Security-Policy header (default: NBA's own CDN, needed for
                                   headshots and logos).

The last two, ``HARDWOOD_SESSION_DAYS`` and ``HARDWOOD_SESSION_ABSOLUTE_DAYS``, and
``HARDWOOD_CSP_IMG_SRC``, are not spelled out as literal ``HARDWOOD_*`` strings anywhere in
the design's field-by-field table — they follow its naming convention for every other field
(``HARDWOOD_<UPPER_SNAKE_CASE_FIELD_NAME>``) rather than inventing a different one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

__all__ = [
    "AuthSettings",
    "get_auth_settings",
    "reset_auth_settings_cache",
    "load_dotenv",
    "load_env_file",
    "default_env_file",
    "startup_warnings",
    "startup_refusals",
    "LOOPBACK_HOSTS",
    "DEFAULT_PUBLIC_BASE_URL",
    "SIGNUP_MODES",
    "MAILERS",
]

#: Hostnames :attr:`AuthSettings.is_loopback` treats as "this machine, nobody else" — the
#: line between the HTTP leniency for local development and the non-loopback HTTP refusal.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8000"

SIGNUP_MODES = ("open", "invite", "closed")
MAILERS = ("log", "file", "smtp")

_TRUTHY = {"1", "true", "t", "yes", "y", "on"}
_FALSEY = {"0", "false", "f", "no", "n", "off", ""}

#: Fields whose values are secrets and must never appear in a log line or a traceback.
_MASKED_FIELDS = frozenset({"google_client_secret", "smtp_url", "invite_code"})


def _get(environ: Mapping[str, str], name: str) -> str | None:
    raw = environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _env_str(environ: Mapping[str, str], name: str, default: str) -> str:
    return _get(environ, name) or default


def _env_optional(environ: Mapping[str, str], name: str) -> str | None:
    return _get(environ, name)


def _env_bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _get(environ, name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean")


def _env_optional_bool(environ: Mapping[str, str], name: str) -> bool | None:
    raw = _get(environ, name)
    if raw is None:
        return None
    lowered = raw.lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean")


def _env_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = _get(environ, name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


def _env_tuple(environ: Mapping[str, str], name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = _get(environ, name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class AuthSettings:
    """Immutable view of the web/auth configuration. See the module docstring for the
    environment variable each field reads."""

    public_base_url: str = DEFAULT_PUBLIC_BASE_URL
    web_dist: str | None = None
    web_enabled: bool = True
    signup_mode: str = "invite"
    invite_code: str | None = None
    session_days: int = 30
    session_absolute_days: int = 90
    scrypt_n_exp: int = 14
    cookie_secure: bool | None = None
    allow_insecure_cookies: bool = False
    dev_links: bool = False
    mailer: str = "log"
    smtp_url: str | None = None
    mail_from: str | None = None
    google_client_id: str | None = None
    google_client_secret: str | None = None
    apple_services_id: str | None = None
    apple_team_id: str | None = None
    apple_key_id: str | None = None
    apple_key_file: str | None = None
    trusted_proxy_cidrs: tuple[str, ...] = ()
    trusted_proxy_hops: int = 0
    csp_img_src: tuple[str, ...] = ("https://cdn.nba.com",)

    @property
    def public_origin(self) -> str:
        """``scheme://host[:port]`` — no path, no trailing slash, whatever ``public_base_url``
        looks like. Computed with :func:`urllib.parse.urlsplit` rather than string surgery so
        a stray path or trailing slash on ``HARDWOOD_PUBLIC_BASE_URL`` cannot make every CSRF
        Origin check fail."""
        parsed = urlsplit(self.public_base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @property
    def is_loopback(self) -> bool:
        """True when the configured host is this machine and nobody else — see
        :data:`LOOPBACK_HOSTS`."""
        host = urlsplit(self.public_base_url).hostname or ""
        return host.lower() in LOOPBACK_HOSTS

    @property
    def use_secure_cookies(self) -> bool:
        """``cookie_secure`` when explicitly set, else derived from the base URL's scheme —
        computed once at startup, never per-request from ``request.url.scheme`` (see the
        module docstring)."""
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.public_base_url.startswith("https://")

    def __repr__(self) -> str:
        """Same shape as the dataclass-generated repr, with every field in
        :data:`_MASKED_FIELDS` replaced by ``'***'`` when set. ``AuthSettings`` ends up in
        startup logs and, eventually, a traceback; a Google OAuth client secret or an SMTP
        URL with embedded credentials must never appear in either."""
        parts = []
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _MASKED_FIELDS and value is not None:
                value = "***"
            parts.append(f"{f.name}={value!r}")
        return f"AuthSettings({', '.join(parts)})"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AuthSettings":
        """Build settings from the environment (default: ``os.environ``). Mirrors
        :meth:`nbastats.config.Settings.from_env`."""
        env = os.environ if environ is None else environ
        return cls(
            public_base_url=_env_str(env, "HARDWOOD_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
            web_dist=_env_optional(env, "HARDWOOD_WEB_DIST"),
            web_enabled=_env_bool(env, "HARDWOOD_WEB", True),
            signup_mode=_env_str(env, "HARDWOOD_SIGNUP_MODE", "invite").lower(),
            invite_code=_env_optional(env, "HARDWOOD_INVITE_CODE"),
            session_days=_env_int(env, "HARDWOOD_SESSION_DAYS", 30),
            session_absolute_days=_env_int(env, "HARDWOOD_SESSION_ABSOLUTE_DAYS", 90),
            scrypt_n_exp=_env_int(env, "HARDWOOD_SCRYPT_N", 14),
            cookie_secure=_env_optional_bool(env, "HARDWOOD_COOKIE_SECURE"),
            allow_insecure_cookies=_env_bool(env, "HARDWOOD_ALLOW_INSECURE_COOKIES", False),
            dev_links=_env_bool(env, "HARDWOOD_DEV_LINKS", False),
            mailer=_env_str(env, "HARDWOOD_MAILER", "log").lower(),
            smtp_url=_env_optional(env, "HARDWOOD_SMTP_URL"),
            mail_from=_env_optional(env, "HARDWOOD_MAIL_FROM"),
            google_client_id=_env_optional(env, "HARDWOOD_GOOGLE_CLIENT_ID"),
            google_client_secret=_env_optional(env, "HARDWOOD_GOOGLE_CLIENT_SECRET"),
            apple_services_id=_env_optional(env, "HARDWOOD_APPLE_SERVICES_ID"),
            apple_team_id=_env_optional(env, "HARDWOOD_APPLE_TEAM_ID"),
            apple_key_id=_env_optional(env, "HARDWOOD_APPLE_KEY_ID"),
            apple_key_file=_env_optional(env, "HARDWOOD_APPLE_KEY_FILE"),
            trusted_proxy_cidrs=_env_tuple(env, "HARDWOOD_TRUSTED_PROXY_CIDRS"),
            trusted_proxy_hops=_env_int(env, "HARDWOOD_TRUSTED_PROXY_HOPS", 0),
            csp_img_src=_env_tuple(
                env, "HARDWOOD_CSP_IMG_SRC", default=("https://cdn.nba.com",)
            ),
        )


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    """Process-wide auth settings, read once from the environment (mirrors
    ``nbastats.config.get_settings()``)."""
    return AuthSettings.from_env()


def reset_auth_settings_cache() -> None:
    """Drop the memoised settings; tests that mutate the environment call this."""
    get_auth_settings.cache_clear()


def load_dotenv(path: Path) -> None:
    """Populate ``os.environ`` from a simple ``KEY=VALUE`` file, one assignment per line,
    ``#`` comments and blank lines skipped, optional matching quotes stripped.

    Never overrides a variable the process already has: a real ``export`` in the calling
    shell, or a value injected by a process manager or container runtime, must always win
    over a stale line left in ``backend/.env`` — the opposite default would make "it worked
    when I exported it directly" a genuinely confusing bug report.
    """
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def default_env_file() -> Path:
    """The dotenv file this deployment reads: ``HARDWOOD_ENV_FILE`` if set, else
    ``backend/.env`` beside the installed package.

    ``config.py`` lives at ``backend/nbastats/accounts/config.py``, so ``parents[2]`` is
    ``backend/`` — the directory ``scripts/web.sh setup`` writes ``.env`` into and the one
    ``web.sh dev`` cds to before exec'ing uvicorn.
    """
    override = os.environ.get("HARDWOOD_ENV_FILE")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path | None = None) -> Path | None:
    """Populate ``os.environ`` from :func:`default_env_file` (or ``path``) and return the file
    that was read, or ``None`` when there was none.

    This is the *one* entry point both ``create_app()`` and ``scripts/web.sh doctor`` call, so
    a setting written into ``backend/.env`` can never reach one and not the other. It used to
    be called only by ``doctor``, which meant ``doctor`` cheerfully reported ``google:
    enabled`` for credentials the running server had never seen.

    The memoised settings cache is dropped whenever a file is actually read, so a caller that
    happened to touch :func:`get_auth_settings` first still sees the file's values.
    """
    target = default_env_file() if path is None else path
    if not target.is_file():
        return None
    load_dotenv(target)
    reset_auth_settings_cache()
    return target


def startup_warnings(s: AuthSettings) -> list[str]:
    """Non-fatal configuration notes, surfaced at ``/v1/health.authWarnings`` and never
    raised — each one describes a feature that will silently be less useful than the operator
    probably intended, not a security defect worth refusing to start over."""
    warnings: list[str] = []
    if s.public_base_url.startswith("https://") and not s.trusted_proxy_cidrs:
        warnings.append(
            "HARDWOOD_PUBLIC_BASE_URL is https but HARDWOOD_TRUSTED_PROXY_CIDRS is empty. "
            "If this process sits behind a reverse proxy, every visitor will share one "
            "rate-limit bucket keyed on the proxy's own address. Set "
            "HARDWOOD_TRUSTED_PROXY_CIDRS to the proxy's address (and "
            "HARDWOOD_TRUSTED_PROXY_HOPS if there is more than one)."
        )
    apple_configured = bool(
        s.apple_services_id or s.apple_team_id or s.apple_key_id or s.apple_key_file
    )
    if apple_configured and not s.public_base_url.startswith("https://"):
        warnings.append(
            "Apple sign-in is configured (HARDWOOD_APPLE_*), but HARDWOOD_PUBLIC_BASE_URL is "
            "not https, so the Apple button will not be shown. Apple's response_mode="
            "form_post needs a Secure, SameSite=None cookie, which needs https."
        )
    if s.mailer in ("log", "file") and not s.is_loopback:
        warnings.append(
            f"HARDWOOD_MAILER={s.mailer} sends no mail: verification and password-reset links "
            f"are written to this server's log. On a non-loopback deployment "
            f"({s.public_base_url}) that means nobody but the operator can complete a reset, "
            "and anyone who can read the log can take over any account. Set "
            "HARDWOOD_MAILER=smtp, or hand out reset links yourself with "
            "`python3 -m nbastats.accounts.admin reset-password`."
        )
    if s.google_client_id and not s.google_client_secret:
        warnings.append(
            "HARDWOOD_GOOGLE_CLIENT_ID is set but HARDWOOD_GOOGLE_CLIENT_SECRET is not, so "
            "the Google button will not be shown. Set HARDWOOD_GOOGLE_CLIENT_SECRET, or "
            "unset HARDWOOD_GOOGLE_CLIENT_ID."
        )
    return warnings


def startup_refusals(s: AuthSettings) -> list[str]:
    """Reasons the process must refuse to start. A non-empty result means ``create_app()``
    raises ``RuntimeError`` before serving a single request. Every message names the exact
    environment variable to change and what to change it to — these fire once, before there
    is any request to attach a better error to."""
    reasons: list[str] = []

    if (
        s.public_base_url.startswith("http://")
        and not s.is_loopback
        and not s.allow_insecure_cookies
    ):
        reasons.append(
            "Session cookies would travel in clear text, and an on-path device could not "
            "merely read them but *replace* them — injecting Set-Cookie into any plaintext "
            "response signs the victim's tab into an account of the attacker's choosing. Set "
            "HARDWOOD_PUBLIC_BASE_URL to an https URL, or set "
            "HARDWOOD_ALLOW_INSECURE_COOKIES=1 if you accept session takeover by anyone on "
            "the network path."
        )

    if s.signup_mode == "open" and not s.is_loopback and not s.invite_code:
        reasons.append(
            f"HARDWOOD_SIGNUP_MODE=open is not allowed with a non-loopback "
            f"HARDWOOD_PUBLIC_BASE_URL ({s.public_base_url}) unless HARDWOOD_INVITE_CODE is "
            "also set as a backstop. Set HARDWOOD_INVITE_CODE, or set "
            "HARDWOOD_SIGNUP_MODE=invite."
        )

    if s.apple_key_file:
        try:
            mode = Path(s.apple_key_file).stat().st_mode
        except OSError:
            mode = None
        if mode is not None and mode & 0o077:
            reasons.append(
                f"HARDWOOD_APPLE_KEY_FILE ({s.apple_key_file}) is readable or writable by "
                "group or other. An Apple signing key cannot be rotated in place if it "
                "leaks: run `chmod 600` on it and restart."
            )

    if s.mailer in ("log", "file") and s.public_base_url.startswith("https://"):
        reasons.append(
            f"HARDWOOD_MAILER={s.mailer} writes verification, password-reset and email-change "
            "links to the server log (or to a directory on disk) instead of sending them. On a "
            f"public deployment ({s.public_base_url}) that writes live, one-hour, single-use "
            "account-takeover links to stdout, where journald or `docker logs` collects them "
            "and ships them to whatever aggregates your logs — anyone with read access to that "
            "takes over any account by pasting a URL. Set HARDWOOD_MAILER=smtp with "
            "HARDWOOD_SMTP_URL and HARDWOOD_MAIL_FROM."
        )

    if s.smtp_url and s.smtp_url.startswith("smtp://"):
        reasons.append(
            "HARDWOOD_SMTP_URL uses the cleartext smtp:// scheme, which would send account "
            "credentials over an unencrypted connection. Set HARDWOOD_SMTP_URL to an "
            "smtps:// or smtp+starttls:// URL instead."
        )

    return reasons
