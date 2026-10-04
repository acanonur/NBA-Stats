"""Where real responses go: the data directory, probe recordings and raw payloads.

Nothing real is ever committed
------------------------------
``github.com/acanonur/NBA-Stats`` is a public repository, and an injury report, a recorded
EuroLeague response or an RSS body is somebody else's data about real people. Everything this
module writes therefore goes under ``HARDWOOD_DATA_DIR`` on the Mac (``recordings/`` for probe
output, ``raw/`` for payloads the pipeline keeps), never inside the source tree. The one in-repo
exception is ``backend/tests/local/``, which is git-ignored, and tests that want real recordings
read from there and skip when it is empty. Committed fixtures are authored look-alikes with
invented names.

Two kinds of file, for two different reasons
--------------------------------------------
**Recordings** are for *diagnosis*. When a parser fails on the first real PDF, the person (or an
agent looking over their shoulder) needs the exact bytes the server sent and the headers that
came with them. A probe saves ``recordings/<kind>/<timestamp>_<name>`` and a ``.json`` sidecar
holding the URL, the status, the validators and the hash. They are never read back by the
service.

**Raw payloads** are for *provenance*: a row in ``nba_intel_raw_fetch`` points at a file here so
the exact input behind a stored status can be re-parsed after a parser fix. They are
content-addressed (``raw/<source>/<aa>/<sha256>.<ext>``), so fetching an unchanged report twice
stores it once, and they are never served by any route.

Both are written atomically (a temporary file in the same directory, then a rename) and readable
by the owner only, because the folder can also hold ``hardwood.env`` with a key in it.

The data directory is resolved here rather than imported from ``nbastats.worker`` because the
worker is the scheduler's module and sits above this package; the rule is the same (the
environment variable, else ``~/Library/Application Support/Hardwood`` on a Mac, else the XDG
location so a test run never writes into a Mac-style path).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

__all__ = [
    "data_dir",
    "recordings_dir",
    "raw_dir",
    "sha256_hex",
    "safe_name",
    "RawFile",
    "save_raw",
    "save_recording",
    "prune_older_than",
]

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def data_dir(env: Mapping[str, str] | None = None) -> Path:
    """The folder that holds Hardwood's databases, raw payloads, recordings and settings."""
    source = os.environ if env is None else env
    raw = (source.get("HARDWOOD_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hardwood"
    base = (source.get("XDG_DATA_HOME") or "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "hardwood"


def recordings_dir(base: Path | None = None) -> Path:
    return (base if base is not None else data_dir()) / "recordings"


def raw_dir(base: Path | None = None) -> Path:
    return (base if base is not None else data_dir()) / "raw"


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def safe_name(text: str, *, fallback: str = "item", limit: int = 80) -> str:
    """A file-name fragment made only of letters, digits, dot, dash and underscore.

    Path separators and everything else collapse to ``-``, leading dots are stripped (no hidden
    files, no ``..``), and the result is bounded, so a name that came from a URL or a header can
    never escape the folder it is written into.
    """
    cleaned = _SAFE.sub("-", text).strip("-.")[:limit].strip("-.")
    return cleaned or fallback


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


@dataclass(frozen=True, slots=True)
class RawFile:
    """A stored raw payload: where, how big, and its hash."""

    path: Path
    sha256: str
    bytes: int
    #: False when an identical file was already there and nothing was written.
    created: bool


def save_raw(
    source: str,
    body: bytes,
    *,
    extension: str = "bin",
    base: Path | None = None,
) -> RawFile:
    """Store ``body`` content-addressed under ``raw/<source>/`` and return where it went.

    A file that is already there is not rewritten, but its modification time is refreshed, so the
    age-based pruning in the jobs measures from the *latest* fetch of that content.
    """
    digest = sha256_hex(body)
    folder = raw_dir(base) / safe_name(source, fallback="source") / digest[:2]
    path = folder / f"{digest}.{safe_name(extension, fallback='bin', limit=8)}"
    if path.is_file() and path.stat().st_size == len(body):
        try:
            os.utime(path)  # seen again: age-based pruning must count from the latest fetch
        except OSError:
            pass
        return RawFile(path, digest, len(body), created=False)
    _write_atomic(path, body)
    return RawFile(path, digest, len(body), created=True)


def save_recording(
    kind: str,
    name: str,
    body: bytes,
    *,
    meta: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    base: Path | None = None,
) -> Path:
    """Save one response for diagnosis, with a ``.json`` sidecar, and return the body's path.

    ``kind`` groups recordings (``nba_injury``, ``euroleague``); ``name`` should say what was
    fetched. A UTC timestamp prefixes the file so repeated probes never overwrite each other.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    folder = recordings_dir(base) / safe_name(kind, fallback="recording")
    stem = f"{stamp}_{safe_name(name)}"
    path = folder / stem
    counter = 1
    while path.exists():  # two probes in one second: keep both
        counter += 1
        path = folder / f"{stem}-{counter}"
    _write_atomic(path, body)
    sidecar: dict[str, Any] = {"sha256": sha256_hex(body), "bytes": len(body), "recordedAt": stamp}
    sidecar.update(dict(meta or {}))
    _write_atomic(
        path.with_name(path.name + ".json"),
        json.dumps(sidecar, indent=2, sort_keys=True, default=str).encode("utf-8") + b"\n",
    )
    return path


def prune_older_than(folder: Path, cutoff: datetime) -> int:
    """Delete files under ``folder`` last modified before ``cutoff``; return how many.

    Only regular files are removed, never the folder tree, and a path that is not under
    ``folder`` after resolving symlinks is skipped. Used to keep raw PDFs from growing without
    bound; a missing folder is not an error.
    """
    if not folder.is_dir():
        return 0
    stamp = (cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)).timestamp()
    root = folder.resolve()
    removed = 0
    for path in folder.rglob("*"):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if root not in path.resolve().parents:
                continue
            if path.stat().st_mtime < stamp:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
