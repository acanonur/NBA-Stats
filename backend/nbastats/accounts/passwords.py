"""Password hashing, verification and policy — stdlib ``hashlib.scrypt`` only.

Why stdlib scrypt and not argon2
----------------------------------
``argon2-cffi`` is not installed in this environment and needs a C toolchain to build (see
``WEB_DESIGN.md`` §0.1-G and the judge table in §0). ``hashlib.scrypt`` has shipped in the
stdlib since Python 3.6, is memory-hard in the same family as argon2, and its cost parameters
(``n``, ``r``, ``p``) are exactly the ones this module's PHC-ish encoded string records, so a
later migration to argon2 (or a bigger ``n``) is a ``needs_rehash`` check on the next
successful login, never a flag day.

The two non-negotiable properties, and why each exists
----------------------------------------------------------
1. **A constant-time miss path.** :func:`dummy_verify` performs exactly one real scrypt
   derivation against a fixed, module-level throwaway hash. Every caller that would otherwise
   skip hashing entirely — an unknown email, a provider-only account with no password, a
   locked account, a soft-deleted account — calls it instead, so all five failure shapes cost
   the same wall-clock time as a wrong password on a real account. Without this, an attacker
   with a stopwatch (not even a statistical timing attack — a literal stopwatch) can enumerate
   which email addresses have accounts by noticing which login attempts return in ~0ms instead
   of ~60ms.
2. **A hard cap on concurrent derivations.** scrypt's whole point is to cost memory, and that
   cost is indifferent to who is paying for it. FastAPI's threadpool (40 workers by default)
   times a 16 MiB derivation is 640 MB an unauthenticated caller can pin on a single-worker
   laptop just by firing off enough concurrent login attempts — the memory-hardness feature
   becomes the denial-of-service lever. ``_KDF_SEMAPHORE`` bounds how many scrypt calls run at
   once in this process, independent of however many requests are queued behind it; a request
   that has to wait its turn for the semaphore is still bounded and safe, whereas one that
   starts an unbounded pile of concurrent derivations is not.

Every route that calls into this module must be declared ``def``, not ``async def`` — FastAPI
then runs it in the threadpool, so a ~60 ms scrypt derivation blocks one worker thread, never
the event loop, and therefore never stalls a concurrent ``/v1/sync/stream`` connection.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import unicodedata
from functools import lru_cache

from .config import get_auth_settings

__all__ = [
    "MIN_LENGTH",
    "MAX_LENGTH",
    "hash_password",
    "verify_password",
    "needs_rehash",
    "dummy_verify",
    "policy_problem",
]

MIN_LENGTH = 10
#: A 10 MB password submitted to an unauthenticated endpoint is a memory-cost denial-of-service
#: lever all by itself, independent of the semaphore below (scrypt's memory cost is a function
#: of ``n``/``r``/``p``, not input length, but hashing itself still has to read the whole
#: string, and there is no legitimate reason for a password longer than this).
MAX_LENGTH = 512

_SCRYPT_R = 8
_SCRYPT_P = 1
_DKLEN = 32

#: Caps concurrent scrypt derivations in this process — see the module docstring, point 2.
#: ``BoundedSemaphore`` (not a plain ``Semaphore``) so a programming error that releases twice
#: raises immediately instead of silently widening the cap.
_KDF_SEMAPHORE = threading.BoundedSemaphore(4)

_RESERVED_PASSWORD = "hardwood"

#: A password NFKC-normalises before hashing so two byte-for-byte-different strings that a
#: user's keyboard or IME could equally well have produced (e.g. a precomposed vs. a
#: decomposed accented character) hash identically — matching what most sites' clients already
#: do to text before it reaches a form.
def _normalize(password: str) -> bytes:
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def _maxmem(n_exp: int, r: int, p: int) -> int:
    """A ``maxmem`` comfortably above what ``hashlib.scrypt`` actually needs (~``128*r*N``
    bytes) for these parameters, so raising ``HARDWOOD_SCRYPT_N`` in production doesn't
    immediately trip ``hashlib.scrypt``'s own "memory limit exceeded" guard. The default
    ``n_exp=14`` needs ~16 MiB; this doubles that with headroom to spare."""
    n = 1 << n_exp
    return 128 * r * (n + p) * 2


def _derive(password: str, salt: bytes, *, n_exp: int, r: int, p: int, dklen: int) -> bytes:
    n = 1 << n_exp
    with _KDF_SEMAPHORE:
        return hashlib.scrypt(
            _normalize(password), salt=salt, n=n, r=r, p=p, dklen=dklen,
            maxmem=_maxmem(n_exp, r, p),
        )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def hash_password(password: str, *, n_exp: int | None = None) -> str:
    """Hash ``password``, returning ``'scrypt$<n_exp>$<r>$<p>$<b64salt>$<b64dk>'``.

    ``n_exp`` defaults to the configured :class:`~nbastats.accounts.config.AuthSettings`
    value so an operator's ``HARDWOOD_SCRYPT_N`` takes effect on every new hash without a code
    change; a caller that needs a specific cost (tests; :func:`needs_rehash` callers upgrading
    a stored hash) passes it explicitly.
    """
    if n_exp is None:
        n_exp = get_auth_settings().scrypt_n_exp
    salt = os.urandom(16)
    derived = _derive(password, salt, n_exp=n_exp, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DKLEN)
    return f"scrypt${n_exp}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(derived)}"


def _parse(encoded: str) -> tuple[int, int, int, bytes, bytes] | None:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return None
    _, n_str, r_str, p_str, salt_b64, dk_b64 = parts
    try:
        n_exp, r, p = int(n_str), int(r_str), int(p_str)
        salt = _b64d(salt_b64)
        derived = _b64d(dk_b64)
    except (ValueError, TypeError):
        return None
    if n_exp <= 0 or r <= 0 or p <= 0 or not salt or not derived:
        return None
    return n_exp, r, p, salt, derived


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verification, re-deriving with exactly the parameters ``encoded`` stores
    (never the currently-configured ones) so a running server can lower or raise
    ``HARDWOOD_SCRYPT_N`` without invalidating every existing hash — see :func:`needs_rehash`
    for the upgrade path that follows a successful verification like this one."""
    parsed = _parse(encoded)
    if parsed is None:
        return False
    n_exp, r, p, salt, expected = parsed
    candidate = _derive(password, salt, n_exp=n_exp, r=r, p=p, dklen=len(expected))
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, *, n_exp: int | None = None) -> bool:
    """True when ``encoded`` was hashed with weaker parameters than we would use today.

    Callers only ever reach this after a successful :func:`verify_password` on the same
    string, so returning ``True`` for anything unparsable is the safe default: a plausibly
    corrupt or foreign-looking stored hash should be replaced with a fresh one at the next
    opportunity rather than silently kept.
    """
    if n_exp is None:
        n_exp = get_auth_settings().scrypt_n_exp
    parsed = _parse(encoded)
    if parsed is None:
        return True
    stored_n_exp, r, p, _salt, _derived = parsed
    return stored_n_exp < n_exp or r != _SCRYPT_R or p != _SCRYPT_P


_DUMMY_PASSWORD = "hardwood-dummy-password-used-only-for-timing-parity"


@lru_cache(maxsize=8)
def _dummy_hash(n_exp: int) -> str:
    """One throwaway hash per distinct ``n_exp`` this process has needed, memoised so we
    salt-and-hash the dummy password only once per cost setting rather than once per miss —
    the *verification* :func:`dummy_verify` performs is the real, uncached scrypt call whose
    cost this whole module exists to make constant-time."""
    return hash_password(_DUMMY_PASSWORD, n_exp=n_exp)


def dummy_verify() -> None:
    """Perform one real scrypt derivation that goes nowhere.

    Called on every login miss — unknown email, wrong password, provider-only account, a
    locked account, or a soft-deleted account — so all five take the same wall-clock time as a
    real, failed password check. See the module docstring, point 1.
    """
    n_exp = get_auth_settings().scrypt_n_exp
    verify_password(_DUMMY_PASSWORD, _dummy_hash(n_exp))


def policy_problem(password: str, *, email: str | None) -> str | None:
    """``None`` when ``password`` is acceptable; otherwise a message to show the user.

    Deliberately no composition rules (no "must contain a symbol") — NIST SP 800-63B's advice,
    which the industry has mostly caught up to: length and a denylist of the handful of
    passwords everyone will otherwise pick (the product's own name; the address's own local
    part) do more for real accounts than punishing users into "P@ssw0rd1".
    """
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return f"Password must be at most {MAX_LENGTH} characters."
    normalized = unicodedata.normalize("NFKC", password).casefold()
    if normalized == _RESERVED_PASSWORD:
        return "Choose a password that isn't just the product's name."
    if email:
        local_part = email.split("@", 1)[0]
        local_normalized = unicodedata.normalize("NFKC", local_part).casefold()
        if local_normalized and local_normalized in normalized:
            return "Password must not contain part of your email address."
    return None
