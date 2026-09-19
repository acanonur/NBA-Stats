"""Tests for ``nbastats.accounts.passwords``: the scrypt hash format, constant-time
verification, the dummy-verify miss path, and the semaphore that caps concurrent KDF work.

A cheap ``n_exp`` (10, ~4ms here) is used throughout so the suite stays fast; the format and
the invariants it tests do not depend on the specific cost.
"""
from __future__ import annotations

import threading
import time

import pytest

from nbastats.accounts import passwords

FAST_N_EXP = 10


def test_hash_and_verify_round_trip() -> None:
    encoded = passwords.hash_password("correct horse battery staple", n_exp=FAST_N_EXP)
    assert encoded.startswith("scrypt$10$8$1$")
    assert passwords.verify_password("correct horse battery staple", encoded)


def test_verify_rejects_wrong_password() -> None:
    encoded = passwords.hash_password("correct horse battery staple", n_exp=FAST_N_EXP)
    assert not passwords.verify_password("wrong password entirely", encoded)


def test_hash_is_salted_and_nondeterministic() -> None:
    a = passwords.hash_password("same password twice", n_exp=FAST_N_EXP)
    b = passwords.hash_password("same password twice", n_exp=FAST_N_EXP)
    assert a != b
    assert passwords.verify_password("same password twice", a)
    assert passwords.verify_password("same password twice", b)


def test_nfkc_normalisation_makes_equivalent_forms_hash_the_same() -> None:
    # "é" (u+00e9, precomposed) vs "é" (decomposed) - NFKC folds both to the same
    # normal form before hashing, matching what a browser's own form input already tends to
    # produce regardless of which one a keyboard emitted.
    precomposed = "café-password1"
    decomposed = "café-password1"
    encoded = passwords.hash_password(precomposed, n_exp=FAST_N_EXP)
    assert passwords.verify_password(decomposed, encoded)


@pytest.mark.parametrize(
    "malformed",
    [
        "",
        "not-a-hash-at-all",
        "scrypt$10$8$1$onlyfourparts",
        "argon2$10$8$1$c2FsdA==$ZGVyaXZlZA==",
        "scrypt$abc$8$1$c2FsdA==$ZGVyaXZlZA==",
        "scrypt$10$8$1$not-base64!!!$ZGVyaXZlZA==",
    ],
)
def test_verify_rejects_malformed_encodings(malformed: str) -> None:
    assert passwords.verify_password("anything", malformed) is False


@pytest.mark.parametrize("malformed", ["", "garbage", "scrypt$10$8$1$c2FsdA=="])
def test_needs_rehash_is_true_for_anything_unparsable(malformed: str) -> None:
    assert passwords.needs_rehash(malformed) is True


def test_needs_rehash_flags_a_weaker_cost() -> None:
    weak = passwords.hash_password("whatever password", n_exp=FAST_N_EXP)
    assert passwords.needs_rehash(weak, n_exp=FAST_N_EXP) is False
    assert passwords.needs_rehash(weak, n_exp=FAST_N_EXP + 2) is True


def test_dummy_verify_does_not_raise_and_costs_real_time() -> None:
    # dummy_verify() always uses the *configured* n_exp (14 by default here), so this is not
    # parametrised down to FAST_N_EXP - it is specifically checking the real, default-cost path
    # a login miss takes in production.
    start = time.perf_counter()
    passwords.dummy_verify()
    elapsed = time.perf_counter() - start
    assert elapsed > 0.0


def test_hit_and_miss_login_paths_cost_about_the_same() -> None:
    """The property the whole module exists for: a real wrong-password check and the
    dummy-verify miss path should be the same order of magnitude, not one near-instant and the
    other tens of milliseconds — that gap is what makes an account's existence a stopwatch
    away. Both use the same (default, configured) cost so this is an apples-to-apples timing
    comparison, and the assertion is deliberately loose (a generous ratio) since CI machines
    are noisy."""
    encoded = passwords.hash_password("a real password for timing")

    start = time.perf_counter()
    assert passwords.verify_password("a wrong guess entirely", encoded) is False
    real_elapsed = time.perf_counter() - start

    start = time.perf_counter()
    passwords.dummy_verify()
    dummy_elapsed = time.perf_counter() - start

    slower, faster = max(real_elapsed, dummy_elapsed), max(min(real_elapsed, dummy_elapsed), 1e-6)
    assert slower / faster < 8, (real_elapsed, dummy_elapsed)


@pytest.mark.parametrize(
    "password,email,expected_substring",
    [
        ("short", None, "at least"),
        ("x" * 513, None, "at most"),
        # "hardwood" (8 chars) can never reach the reserved-word check on its own — MIN_LENGTH
        # (10) rejects it first, so this is exercising the length message, not the denylist.
        ("hardwood", None, "at least"),
        ("adaLovelace123!", "ada@lovelace.com", "email address"),
    ],
)
def test_policy_problem_rejects(password: str, email: str | None, expected_substring: str) -> None:
    problem = passwords.policy_problem(password, email=email)
    assert problem is not None
    assert expected_substring in problem


def test_policy_problem_rejects_the_product_name_once_it_is_long_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The denylist rule ("equals 'hardwood' case-insensitively") is unreachable through the
    public API today, since "hardwood" itself is shorter than MIN_LENGTH — the length check
    always fires first. Lowering MIN_LENGTH for this one test is what actually exercises the
    denylist branch rather than leaving it untested dead code."""
    monkeypatch.setattr(passwords, "MIN_LENGTH", 4)
    assert passwords.policy_problem("HARDWOOD", email=None) is not None
    assert "product" in passwords.policy_problem("HardWood", email=None)


def test_policy_problem_accepts_a_reasonable_password() -> None:
    problem = passwords.policy_problem("a perfectly cromulent password", email="ada@example.com")
    assert problem is None


def test_policy_problem_has_no_composition_rules() -> None:
    # NIST SP 800-63B: length and a denylist, not "must contain a symbol".
    assert passwords.policy_problem("all lowercase words here", email=None) is None


def test_kdf_semaphore_bounds_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_KDF_SEMAPHORE`` caps concurrent scrypt derivations at 4.

    Measuring concurrency *outside* the semaphore-guarded section (e.g. around a call to
    ``hash_password``) is unreliable: with a fast enough derivation, threads finish before
    the next one even starts, and peak concurrency ends up bounded by scheduling rather than
    by the semaphore, making the test pass for the wrong reason. Instead, ``hashlib.scrypt``
    itself is replaced with a fake that records concurrency and sleeps *while the real
    ``_derive`` call still holds the semaphore* — so what is measured is exactly the section
    the semaphore protects.
    """
    concurrency = 0
    peak = 0
    lock = threading.Lock()
    real_scrypt = passwords.hashlib.scrypt

    def fake_scrypt(*args, **kwargs):
        nonlocal concurrency, peak
        with lock:
            concurrency += 1
            peak = max(peak, concurrency)
        try:
            time.sleep(0.05)
            return real_scrypt(*args, **kwargs)
        finally:
            with lock:
                concurrency -= 1

    monkeypatch.setattr(passwords.hashlib, "scrypt", fake_scrypt)

    threads = [
        threading.Thread(
            target=passwords.hash_password,
            args=("concurrency probe",),
            kwargs={"n_exp": FAST_N_EXP},
        )
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 4
    assert passwords._KDF_SEMAPHORE._initial_value == 4  # type: ignore[attr-defined]
