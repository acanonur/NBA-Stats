"""EuroLeague configuration: the environment switches, and the guard against sharing a file.

Environment variables
---------------------
``HARDWOOD_EL_ENABLED``        ``0`` skips the EuroLeague entirely: no store, no routes. Default on.
``HARDWOOD_EL_LIVE``           ``0`` turns the live fetch jobs off (the workbook import and the
                               store still work). Default on, and polite: see ``ingest``.
``HARDWOOD_EL_DEMO``           ``1`` seeds the invented league into a *new* store, ``0`` never
                               does. Unset follows the NBA's ``HARDWOOD_DEMO_MODE``, so a demo
                               NBA league and a demo EuroLeague come and go together.
``HARDWOOD_EL_MODE``           Compatibility alias, read so the API and the worker agree:
                               ``off`` switches the EuroLeague off, ``demo`` and ``workbook``
                               turn live fetching off, ``demo`` also asks for the invented
                               league. The four switches above win where they are set.
``HARDWOOD_EL_DATABASE_URL``   SQLAlchemy URL of the EuroLeague store. Unset: the file beside
                               the NBA SQLite file with ``_el`` added to its name
                               (``hardwood.db`` pairs with ``hardwood_el.db``, the pair the Mac
                               install uses); for a server NBA database, ``hardwood_el.db`` in
                               ``HARDWOOD_DATA_DIR``. The worker resolves it the same way.
``HARDWOOD_DATA_DIR``          Where real data lives on the user's machine. The inbox
                               (``<dir>/inbox/*.xlsx``) is read from here.
``HARDWOOD_WORKBOOK_PATH``     One workbook to import at start-up (idempotent by file hash).
``HARDWOOD_EL_INCLUDE_ESTIMATES``
                               Default on: the workbook's ``est.`` per-40 lines are imported,
                               marked as estimates. ``0`` stores only minutes and identity for
                               them and leaves their rates to the position prior.

There is no terms setting of any kind. The posture in ``docs/LEGAL.md`` is applied to the
EuroLeague as it is to the NBA, and the code simply runs; see the package docstring.

Why the guard exists
--------------------
The EuroLeague store and the NBA store must be different files, because the NBA seeder wipes
every table it knows about and because a EuroLeague row beside an NBA row is a leak waiting
for a query. A misconfiguration is easy to make (one ``DATABASE_URL`` copied into two
variables) and expensive (invented rows in a real store), so :func:`check_same_store`
resolves both URLs and compares them: for SQLite by ``os.path.realpath`` of the file (so a
symlink, a relative path and an absolute one are all the same file), for any other database by
the URL with its credentials removed. When they are equal the EuroLeague disables itself with
state ``misconfigured`` and a reason; the NBA side is untouched. ``init_el_db`` independently
refuses any file that holds a ``teams`` table, for the case where the guard was bypassed.

Settings are read from the environment on every call rather than cached. They are tiny, and a
cache would make a test that changes the environment, or a user who edits ``hardwood.env``
between worker ticks, read a stale answer; the worker relies on re-reading to honour the
kill switch.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ..config import DEFAULT_DATABASE_URL, Settings

__all__ = [
    "ElSettings",
    "get_el_settings",
    "is_sqlite_url",
    "is_memory_url",
    "sqlite_file",
    "default_data_dir",
    "default_el_database_url",
    "same_store",
    "check_same_store",
    "inbox_workbooks",
    "EL_DB_FILENAME",
]

EL_DB_FILENAME = "hardwood_el.db"

_TRUTHY = {"1", "true", "t", "yes", "y", "on"}
_FALSEY = {"0", "false", "f", "no", "n", "off"}


def _flag(environ: Mapping[str, str], name: str, default: bool | None) -> bool | None:
    raw = environ.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUTHY:
        return True
    if lowered in _FALSEY:
        return False
    raise ValueError(f"{name}={raw!r} is not a boolean")


def _text(environ: Mapping[str, str], name: str) -> str | None:
    raw = environ.get(name)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


# ------------------------------------------------------------------------------ URLs


def is_sqlite_url(url: str) -> bool:
    return url.strip().lower().startswith("sqlite")


def is_memory_url(url: str) -> bool:
    """True for an in-memory SQLite URL (``sqlite://`` or ``sqlite:///:memory:``)."""
    if not is_sqlite_url(url):
        return False
    try:
        database = make_url(url).database
    except ArgumentError:
        return False
    return database in (None, "", ":memory:") or str(database).startswith("file::memory:")


def sqlite_file(url: str) -> Path | None:
    """The file a SQLite URL names, resolved through symlinks; ``None`` for memory or non-SQLite."""
    if not is_sqlite_url(url) or is_memory_url(url):
        return None
    try:
        database = make_url(url).database
    except ArgumentError:
        return None
    return Path(os.path.realpath(str(database)))


def _comparable(url: str) -> str:
    """A URL with its credentials removed, for comparing non-SQLite stores."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return url.strip()
    # URL.set(username=None) means "leave unchanged"; _replace really removes them.
    return parsed._replace(username=None, password=None).render_as_string(hide_password=True)


def same_store(first: str, second: str) -> bool:
    """True when the two URLs name the same database.

    Two in-memory SQLite URLs are never the same store (each connection has its own), so a
    test that gives both sides ``sqlite://`` is not refused.
    """
    if is_memory_url(first) or is_memory_url(second):
        return False
    first_file, second_file = sqlite_file(first), sqlite_file(second)
    if first_file is not None or second_file is not None:
        return first_file is not None and first_file == second_file
    return _comparable(first) == _comparable(second)


def default_data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Where per-user data lives when ``HARDWOOD_DATA_DIR`` is not set: the macOS application
    support folder, else the XDG location (so a test run on Linux never writes into a Mac-style
    path). The worker resolves it identically; the two must agree on every file they share."""
    source = os.environ if environ is None else environ
    raw = (source.get("HARDWOOD_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Hardwood"
    base = (source.get("XDG_DATA_HOME") or "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return root / "hardwood"


def _raw_sqlite_path(url: str) -> Path | None:
    """The path a SQLite file URL names, as written (symlinks are not resolved)."""
    if not is_sqlite_url(url) or is_memory_url(url):
        return None
    try:
        database = make_url(url).database
    except ArgumentError:
        return None
    return Path(str(database)) if database else None


def default_el_database_url(
    nba_url: str, data_dir: Path | None, environ: Mapping[str, str] | None = None
) -> str:
    """Where the EuroLeague store goes when ``HARDWOOD_EL_DATABASE_URL`` is not set.

    Beside the NBA SQLite file, with ``_el`` added to its name (``hardwood.db`` becomes
    ``hardwood_el.db``). A server NBA database has no file to sit beside, so the store goes
    in the data directory; an in-memory NBA store gets an in-memory EuroLeague store.
    """
    nba_file = _raw_sqlite_path(nba_url)
    if nba_file is not None:
        return f"sqlite:///{nba_file.with_name(f'{nba_file.stem}_el{nba_file.suffix}')}"
    if is_memory_url(nba_url):
        return "sqlite://"
    base = data_dir if data_dir is not None else default_data_dir(environ)
    return f"sqlite:///{base / EL_DB_FILENAME}"


# ------------------------------------------------------------------------------ settings


@dataclass(frozen=True, slots=True)
class ElSettings:
    """Immutable view of the EuroLeague configuration, with every default resolved."""

    enabled: bool
    live: bool
    demo: bool
    database_url: str
    database_url_explicit: bool
    nba_database_url: str
    data_dir: Path | None
    workbook_path: Path | None
    include_estimates: bool
    #: ``HARDWOOD_EL_MODE`` when it names one of ``off``, ``demo``, ``workbook``, ``live``.
    mode: str | None = None

    @property
    def inbox_dir(self) -> Path | None:
        return self.data_dir / "inbox" if self.data_dir is not None else None

    @property
    def is_sqlite(self) -> bool:
        return is_sqlite_url(self.database_url)

    @property
    def store_path(self) -> Path | None:
        """The SQLite file of the store, or ``None`` for memory or a server database."""
        return sqlite_file(self.database_url)

    @property
    def workbook_requested(self) -> bool:
        """True when a workbook is named or the inbox holds one: a real import is wanted."""
        return self.workbook_path is not None or bool(inbox_workbooks(self.inbox_dir))

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        nba_settings: Settings | None = None,
    ) -> "ElSettings":
        env = os.environ if environ is None else environ
        if nba_settings is None:
            # Read afresh rather than through the NBA's memoised ``get_settings()``: the point
            # of re-reading on every call is that a changed environment is seen at once.
            nba_settings = Settings.from_env(env)
        nba_url = nba_settings.database_url or DEFAULT_DATABASE_URL
        data_dir_raw = _text(env, "HARDWOOD_DATA_DIR")
        data_dir = Path(data_dir_raw).expanduser() if data_dir_raw else None
        explicit = _text(env, "HARDWOOD_EL_DATABASE_URL")
        workbook_raw = _text(env, "HARDWOOD_WORKBOOK_PATH")
        demo = _flag(env, "HARDWOOD_EL_DEMO", None)
        mode = (_text(env, "HARDWOOD_EL_MODE") or "").lower()
        mode = mode if mode in ("off", "demo", "workbook", "live") else ""
        enabled = bool(_flag(env, "HARDWOOD_EL_ENABLED", True)) and mode != "off"
        live = bool(_flag(env, "HARDWOOD_EL_LIVE", True)) and mode not in ("demo", "workbook")
        if demo is None and mode == "demo":
            demo = True
        return cls(
            enabled=enabled,
            live=live,
            demo=nba_settings.demo_mode if demo is None else demo,
            database_url=explicit or default_el_database_url(nba_url, data_dir, env),
            database_url_explicit=explicit is not None,
            nba_database_url=nba_url,
            data_dir=data_dir,
            workbook_path=Path(workbook_raw).expanduser() if workbook_raw else None,
            include_estimates=bool(_flag(env, "HARDWOOD_EL_INCLUDE_ESTIMATES", True)),
            mode=mode or None,
        )


def get_el_settings(environ: Mapping[str, str] | None = None) -> ElSettings:
    """The current EuroLeague settings (never cached; see the module docstring)."""
    return ElSettings.from_env(environ)


def check_same_store(settings: ElSettings) -> str | None:
    """``None`` when the stores are different, else the reason the EuroLeague must not run."""
    if same_store(settings.database_url, settings.nba_database_url):
        return (
            "The EuroLeague store and the NBA store are the same database "
            f"({settings.database_url!r}). Set HARDWOOD_EL_DATABASE_URL to a different file: "
            "the two leagues never share one."
        )
    return None


def inbox_workbooks(inbox: Path | None) -> list[Path]:
    """``.xlsx`` files in the inbox, oldest first, so a newer workbook is applied on top.

    Lock files (``~$name.xlsx``, written by Excel while a workbook is open), hidden files and
    anything that is not a regular file are skipped.
    """
    if inbox is None:
        return []
    try:
        entries = [
            path
            for path in inbox.iterdir()
            if path.is_file()
            and path.suffix.lower() == ".xlsx"
            and not path.name.startswith(("~$", "."))
        ]
    except OSError:
        return []
    return sorted(entries, key=lambda p: (p.stat().st_mtime, p.name))
