#!/usr/bin/env python3
"""Make sure ``hardwood.env`` holds an API key, creating one only when there is none.

``install.sh`` runs this once per install, with the settings file's path as the only argument. It
prints one word and nothing else (never the key, which would end up in a terminal scrollback and
in anything that captures the installer's output):

``created``  there was no key: a random one was written
``filled``   the file had an *empty* ``HARDWOOD_API_KEY=`` line; the key went into that line
``kept``     the file already holds a key; nothing about it was touched

Why there is a key at all
-------------------------
The Mac app is a native client. Its writes (a typed injury status, a pasted headline, a model
setting) are authorised by ``X-API-Key``, and by nothing else: the server has no keyless path, and
it does not treat ``X-Hardwood-Client`` as a credential (``nbastats/api/deps.py`` says why). A key
the person has to invent and copy by hand would mean most installs have none and no writes, so the
installer makes one. With a key set the server also asks for it on reads (``/v1/health`` excepted),
which is the right default for a laptop that runs a local web server: a web page open in the
person's browser can no longer read the statistics store either.

The rules, each one a test (``tests/test_native_write_auth.py``)
------------------------------------------------------------------
* **Never overwrite a key.** The first *active* assignment decides, exactly as the server reads
  the file (:func:`nbastats.accounts.config.load_dotenv`: ``#`` lines skipped, ``key=value`` split
  at the first ``=``, both sides stripped, one pair of matching quotes removed, the first
  assignment of a name wins). If that value is non-empty the file is left byte-for-byte alone,
  apart from its mode.
* **An empty assignment is "no key".** ``HARDWOOD_API_KEY=`` is what the server reads as unset, and
  because the first assignment wins, appending a second line would have no effect. So the empty
  line itself is replaced.
* **Random and long.** ``secrets.token_urlsafe(32)``: 256 bits from the operating system's
  generator, in characters (``A-Za-z0-9_-``) that need no quoting in a dotenv file or a header.
* **The file is private before it holds the secret and after.** The new content is written to a
  sibling file created with mode ``0600`` and moved over the original, so there is no moment when
  the key sits in a file others can read, and a half-written file is never what the server
  starts with. The final file is ``0600`` whatever it was before (a settings file that now holds a
  secret is not allowed to stay world-readable).
* **A symlinked settings file is written through**, not replaced by a regular file.

Standard library only, and runs on any Python 3.8+, so it works whichever interpreter the
installer has found.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
from pathlib import Path

KEY_NAME = "HARDWOOD_API_KEY"

HEADER = (
    "# ---------------------------------------------------------------------------------------\n"
    "# The API key. Written by install.sh because there was none.\n"
    "#   * The Hardwood Mac app reads it from this file and sends it with every request.\n"
    "#   * The server refuses changes without it, and refuses reads without it too (a signed-in\n"
    "#     browser session is accepted instead), so a web page cannot use your server.\n"
    "#   * Keep this file private. It is readable by you only; do not paste the key anywhere.\n"
    "# To use a key of your own, replace the value. To make a new one, delete the line and run\n"
    "# install.sh again (then restart the app and the server).\n"
    "# ---------------------------------------------------------------------------------------\n"
)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def first_assignment(lines: list[str]) -> tuple[int, str] | None:
    """``(index, value)`` of the first active ``HARDWOOD_API_KEY=`` line, read the way the server
    reads it, or ``None``. The value is what the server would see (quotes removed, stripped)."""
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == KEY_NAME:
            return index, _unquote(value)
    return None


def new_key() -> str:
    return secrets.token_urlsafe(32)


def _write_private(path: Path, text: str) -> None:
    """Replace ``path`` with ``text``, mode 0600 from the first byte, atomically."""
    handle, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)


def ensure_api_key(env_file: Path) -> str:
    """Do the work for ``env_file`` and return ``created``, ``filled`` or ``kept``."""
    target = Path(os.path.realpath(env_file))  # write through a symlink
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_private(target, f"{HEADER}{KEY_NAME}={new_key()}\n")
        return "created"

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    found = first_assignment(lines)
    if found is not None and found[1]:
        os.chmod(target, 0o600)
        return "kept"

    if found is not None:  # an empty assignment: the first one wins, so replace it in place
        index = found[0]
        ending = "\r\n" if lines[index].endswith("\r\n") else "\n"
        lines[index] = f"{KEY_NAME}={new_key()}{ending}"
        _write_private(target, "".join(lines))
        return "filled"

    if text and not text.endswith("\n"):
        text += "\n"
    separator = "\n" if text else ""
    _write_private(target, f"{text}{separator}{HEADER}{KEY_NAME}={new_key()}\n")
    return "created"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: ensure_api_key.py PATH_TO_hardwood.env", file=sys.stderr)
        return 2
    try:
        print(ensure_api_key(Path(argv[1])))
    except OSError as exc:
        print(f"could not write {argv[1]}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through install.sh
    sys.exit(main(sys.argv))
