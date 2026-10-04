"""Availability rows: from a parsed report to stored statuses, plus overrides and the review queue.

This module applies the availability vocabulary (:mod:`nbastats.shared.availability`) to rows. The
parser (:mod:`nbastats.nba_intel.report_pdf`) says what a PDF printed; this module decides which
team, game and player each line is about, writes it down with its source, and keeps the places
where a person can disagree (overrides) or has to decide (the review queue).

Matching is conservative, and a miss is a feature
-------------------------------------------------
The report prints a team's full name, a game as ``AWAY@HOME`` abbreviations and a player as
``Last, First``. None of those is an id. The rule throughout is **a match must be unique, or there
is no match**:

* *Game*: the stats store's game on that date between those two abbreviations. If the store has no
  such game (a preseason game it never ingested, an abbreviation that differs), ``game_id`` is
  left ``NULL`` and the status still stands as a fact about that team and date.
* *Team*: among the two teams of the matched game when there is one, otherwise among all teams; the
  report's team name must equal a team's name, or end with a nickname that only one team has. A
  line whose team cannot be placed is not stored, and the snapshot becomes ``partial``.
* *Player*: "Last, First" is turned into "First Last", folded (accents, case and punctuation gone;
  "Jr." and friends tried both ways) and looked up in the team's own pool: first players with a line
  for that team this season, then players listed on that team's roster this season (so the first
  report of a season, before anyone has played, still matches), then players with a line for that
  team in the most recent earlier season. The first pool that contains the name decides, and only if
  the name appears **once** in it. Two Jaylens on one roster, or a name nobody has, leaves
  ``player_id`` NULL and the row goes to the review queue. A player is never guessed.

A row with a NULL player is still stored (it is what the league said), shown with the name as
printed, and listed by :func:`review_queue` until a person links it (:func:`resolve_review_item`).

Append-only, including corrections
----------------------------------
Linking a player to a stored row does not edit it. It appends two rows: a retraction (a manual row
naming the one it retracts) and a replacement carrying the same facts with the player set. The
shared selection rules already understand retractions, so the corrected row simply wins. History is
never rewritten.

Overrides
---------
An override is a status a person typed for one player, with an optional note and link, in force
until cleared or until a newer league report supersedes it (the rule lives in
:func:`nbastats.shared.availability.select_effective_entry`). The status must be one of the five;
there is no way to override with "nothing". A link to a host on the source denylist is dropped and
the withheld notice is appended to the note instead, so the entry keeps its date and its words but
not its link.

The source denylist
-------------------
``data/source_denylist.json`` lists hosts owned by operators whose business Hardwood will not point
at. :func:`apply_denylist` is the one function that applies it: a status or headline citing such a
host keeps its label and date and loses its link, and the label gains the suffix from the JSON. It
matches on the **host of a URL**, never on text, so a club whose name happens to contain one of
those words is never affected. If the file cannot be read the module raises rather than silently
letting every link through.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import current_season_string
from ..db import utcnow
from ..intel.feeds import clean_link, host_matches
from ..shared.availability import StatusEntry, normalise_reason_category, normalise_status
from . import store
from .models import (
    NO_GAME,
    NbaIntelOverride,
    NbaIntelStatus,
    NbaIntelTeamReport,
)
from .report_pdf import ParsedReport, reason_category

__all__ = [
    "REPORT_SOURCE_LABEL",
    "EASTERN",
    "Denylist",
    "UnknownPlayerError",
    "InvalidOverrideError",
    "IngestResult",
    "ReviewItem",
    "fold_name",
    "report_name_to_display",
    "load_denylist",
    "is_denied",
    "apply_denylist",
    "PlayerDirectory",
    "TeamDirectory",
    "ingest_report",
    "review_queue",
    "resolve_review_item",
    "add_override",
    "clear_override",
    "active_overrides",
    "status_entry",
    "override_entry",
]

REPORT_SOURCE_LABEL = "NBA official injury report"
EASTERN = ZoneInfo("America/New_York")

_DATA = Path(__file__).with_name("data")
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})
_SPECIAL = str.maketrans(
    {
        "đ": "d",
        "Đ": "D",
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ß": "ss",
        "þ": "th",
        "ð": "d",
        "Ð": "D",
        "Þ": "Th",
        "ı": "i",
    }
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


class UnknownPlayerError(ValueError):
    """An override or link names a player id the stats store does not hold."""


class InvalidOverrideError(ValueError):
    """An override that cannot be recorded, with the reason."""


# --------------------------------------------------------------------------- names


def fold_name(value: str) -> str:
    """A name reduced to lower-case ASCII words: accents, punctuation and case removed.

    ``"Dončić, Luka"`` and ``"Doncic Luka"`` fold to ``"doncic luka"``. This is the comparison key
    for players and teams on both sides of a match.
    """
    decomposed = unicodedata.normalize("NFKD", value.translate(_SPECIAL))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).split())


def report_name_to_display(raw: str) -> str:
    """``"Porter Jr., Michael"`` gives ``"Michael Porter Jr."``; a name with no comma is kept."""
    if "," not in raw:
        return " ".join(raw.split())
    last, _, first = raw.partition(",")
    return " ".join(f"{first.strip()} {last.strip()}".split())


def _keys(display: str) -> tuple[str, str]:
    """The exact folded key and the key with generational suffixes removed."""
    exact = fold_name(display)
    bare = " ".join(token for token in exact.split() if token not in _SUFFIXES)
    return exact, bare


# --------------------------------------------------------------------------- denylist


@dataclass(frozen=True, slots=True)
class Denylist:
    domains: tuple[str, ...]
    suffix: str


@lru_cache(maxsize=1)
def load_denylist() -> Denylist:
    """The source denylist from ``data/source_denylist.json``. Raises if it cannot be read."""
    path = _DATA / "source_denylist.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        domains = tuple(str(d).strip().lower() for d in document["domains"] if str(d).strip())
        suffix = str(document["linkWithheldSuffix"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"the source denylist {path} could not be read: {exc}") from exc
    if not domains:
        raise RuntimeError(f"the source denylist {path} lists no domains")
    return Denylist(domains, suffix)


def is_denied(url: str | None) -> bool:
    """True when ``url``'s host is on the denylist (or a subdomain of a listed host)."""
    return bool(url) and host_matches(url, load_denylist().domains)  # type: ignore[arg-type]


def apply_denylist(label: str, url: str | None, *, limit: int = 120) -> tuple[str, str | None]:
    """``(label, url)`` with a denylisted link withheld: the link becomes ``None`` and the label
    gains the suffix, truncated so the whole label fits ``limit`` characters. Anything else is
    returned unchanged."""
    if not is_denied(url):
        return label, url
    suffix = load_denylist().suffix
    room = max(0, limit - len(suffix))
    return label[:room].rstrip() + suffix, None


# ----------------------------------------------------------------- stats-store lookups


@dataclass(frozen=True, slots=True)
class _TeamRow:
    team_id: int
    abbr: str
    name: str
    city: str
    nickname: str


class TeamDirectory:
    """The stats store's teams, read once, with the matching rules from the module docstring."""

    def __init__(self, session: Session) -> None:
        rows = session.execute(text("SELECT team_id, abbr, name, city, nickname FROM teams")).all()
        self._teams = [
            _TeamRow(int(r[0]), str(r[1]), str(r[2]), str(r[3]), str(r[4])) for r in rows
        ]
        self._by_id = {t.team_id: t for t in self._teams}

    def abbr(self, team_id: int) -> str | None:
        team = self._by_id.get(team_id)
        return team.abbr if team else None

    def by_abbr(self, abbr: str) -> list[_TeamRow]:
        return [t for t in self._teams if t.abbr.upper() == abbr.upper()]

    def match(self, cell: str, candidates: Iterable[int] | None = None) -> int | None:
        """The one team the report's ``cell`` names, or ``None``."""
        folded = fold_name(cell)
        if not folded:
            return None
        pool = (
            [self._by_id[i] for i in candidates if i in self._by_id]
            if candidates is not None
            else self._teams
        )
        exact = [
            t for t in pool if folded in (fold_name(t.name), fold_name(f"{t.city} {t.nickname}"))
        ]
        if len(exact) == 1:
            return exact[0].team_id
        if exact:
            return None
        nickname = [
            t
            for t in pool
            if fold_name(t.nickname)
            and (folded == fold_name(t.nickname) or folded.endswith(" " + fold_name(t.nickname)))
        ]
        if len(nickname) == 1:
            return nickname[0].team_id
        return None


@dataclass(frozen=True, slots=True)
class _GameRow:
    game_id: str
    season: str
    home_team_id: int
    away_team_id: int


class _GameDirectory:
    def __init__(self, session: Session, teams: TeamDirectory) -> None:
        self._session = session
        self._teams = teams
        self._by_date: dict[date, list[_GameRow]] = {}

    def _on(self, day: date) -> list[_GameRow]:
        if day not in self._by_date:
            rows = self._session.execute(
                text(
                    "SELECT game_id, season, home_team_id, away_team_id FROM games "
                    "WHERE game_date = :d"
                ),
                {"d": day},
            ).all()
            self._by_date[day] = [
                _GameRow(str(r[0]), str(r[1]), int(r[2]), int(r[3])) for r in rows
            ]
        return self._by_date[day]

    def find(self, day: date, away_abbr: str, home_abbr: str) -> _GameRow | None:
        away = {t.team_id for t in self._teams.by_abbr(away_abbr)}
        home = {t.team_id for t in self._teams.by_abbr(home_abbr)}
        found = [g for g in self._on(day) if g.away_team_id in away and g.home_team_id in home]
        return found[0] if len(found) == 1 else None


@dataclass(slots=True)
class _Pool:
    exact: dict[str, set[int]] = field(default_factory=dict)
    bare: dict[str, set[int]] = field(default_factory=dict)

    def add(self, player_id: int, full_name: str) -> None:
        exact, bare = _keys(full_name)
        if exact:
            self.exact.setdefault(exact, set()).add(player_id)
        if bare:
            self.bare.setdefault(bare, set()).add(player_id)


_LINES_SQL = (
    "SELECT DISTINCT p.player_id, p.full_name FROM player_game_basic b "
    "JOIN games g ON g.game_id = b.game_id JOIN players p ON p.player_id = b.player_id "
    "WHERE b.team_id = :team AND g.season = :season"
)
_ROSTER_SQL = (
    "SELECT DISTINCT p.player_id, p.full_name FROM player_position_season s "
    "JOIN players p ON p.player_id = s.player_id WHERE s.team_id = :team AND s.season = :season"
)
_PRIOR_SQL = (
    "SELECT DISTINCT p.player_id, p.full_name FROM player_game_basic b "
    "JOIN games g ON g.game_id = b.game_id JOIN players p ON p.player_id = b.player_id "
    "WHERE b.team_id = :team AND g.season = ("
    "SELECT MAX(g2.season) FROM games g2 JOIN player_game_basic b2 ON b2.game_id = g2.game_id "
    "WHERE b2.team_id = :team AND g2.season < :season)"
)


class PlayerDirectory:
    """Look up a player by a report's "Last, First" within a team's own pool. See the docstring."""

    TIERS = ("lines", "roster", "priorLines")

    def __init__(self, session: Session) -> None:
        self._session = session
        self._pools: dict[tuple[str, int, str], _Pool] = {}
        self._has_roster = store.table_exists(session, "player_position_season")

    def _pool(self, tier: str, team_id: int, season: str) -> _Pool:
        key = (tier, team_id, season)
        if key not in self._pools:
            pool = _Pool()
            sql = {"lines": _LINES_SQL, "roster": _ROSTER_SQL, "priorLines": _PRIOR_SQL}[tier]
            if tier != "roster" or self._has_roster:
                for player_id, name in self._session.execute(
                    text(sql), {"team": team_id, "season": season}
                ):
                    pool.add(int(player_id), str(name))
            self._pools[key] = pool
        return self._pools[key]

    def resolve(self, team_id: int, season: str, report_name: str) -> tuple[int | None, str | None]:
        """``(player_id, tier)`` for a unique match, else ``(None, None)``."""
        exact, bare = _keys(report_name_to_display(report_name))
        for tier in self.TIERS:
            pool = self._pool(tier, team_id, season)
            ids = pool.exact.get(exact) or pool.bare.get(bare) or set()
            if len(ids) == 1:
                return next(iter(ids)), tier
            if len(ids) > 1:
                return None, None  # ambiguous in the pool that knows him: do not look further
        return None, None


# ------------------------------------------------------------------------------ ingest


@dataclass(frozen=True, slots=True)
class IngestResult:
    snapshot_id: int
    parse_status: str
    stored: int
    unmatched_players: int
    unmatched_teams: int
    teams_marked: int
    errors: tuple[str, ...]


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _et_to_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=EASTERN).astimezone(timezone.utc).replace(tzinfo=None)


def ingest_report(
    session: Session,
    parsed: ParsedReport,
    *,
    url: str | None,
    slot_at_utc: datetime | None,
    sha256: str | None,
    fetched_at: datetime,
    now: datetime | None = None,
) -> IngestResult:
    """Store a parsed report as a snapshot, its team states and one status row per entry.

    The snapshot is always written, whatever the parse status, so "the report at 5:30 was
    unreadable" is a recorded fact with a time on it. Entries and team states are written only for
    ``ok`` and ``partial`` parses. The caller commits.
    """
    stamp = _naive_utc(now) if now is not None else utcnow()
    fetched = _naive_utc(fetched_at)
    slot = _naive_utc(slot_at_utc) if slot_at_utc is not None else None
    as_of = _et_to_utc(parsed.as_of_et) if parsed.as_of_et is not None else None
    if as_of is not None and slot is not None and abs(as_of - slot) > timedelta(hours=24):
        as_of = None  # a title that disagrees with the file's own slot by a day is not trusted
    published = as_of or slot or fetched

    snapshot = store.insert_snapshot(
        session,
        source_kind="leagueReport",
        fetched_at=fetched,
        parse_status=parsed.status,
        row_count=0,
        url=url,
        slot_at_utc=slot,
        report_as_of_utc=as_of or slot,
        sha256=sha256,
        parse_error=parsed.parse_error,
    )
    errors = list(parsed.errors)
    if parsed.status not in ("ok", "partial"):
        return IngestResult(snapshot.snapshot_id, parsed.status, 0, 0, 0, 0, tuple(errors))

    teams = TeamDirectory(session)
    games = _GameDirectory(session, teams)
    players = PlayerDirectory(session)
    stored = unmatched_players = unmatched_teams = 0

    for row in parsed.rows:
        game = games.find(row.game_date, row.away_abbr, row.home_abbr)
        candidates = [game.home_team_id, game.away_team_id] if game else None
        team_id = teams.match(row.team_name, candidates)
        if team_id is None:
            unmatched_teams += 1
            if len(errors) < 40:
                errors.append(f"team {row.team_name!r} ({row.matchup}) is not a known team")
            continue
        season = game.season if game else current_season_string(row.game_date)
        player_id, basis = players.resolve(team_id, season, row.player_name_raw)
        if player_id is None:
            unmatched_players += 1
        category = reason_category(row.reason_raw)
        note = f"Injury report page {row.page} line {row.line}"
        if basis:
            note += f"; player matched on {basis}"
        session.add(
            NbaIntelStatus(
                team_id=team_id,
                player_id=player_id,
                player_name_raw=row.player_name_raw[:96],
                game_id=game.game_id if game else None,
                game_date=row.game_date,
                status=row.status,
                status_raw=row.status_raw[:40],
                reason_category=normalise_reason_category(category),
                reason_text=row.reason_raw[:200] if row.reason_raw else None,
                source_kind="leagueReport",
                source_label=REPORT_SOURCE_LABEL,
                source_url=url,
                source_published_at=published,
                as_of=published,
                recorded_at=stamp,
                snapshot_id=snapshot.snapshot_id,
                provenance_note=note[:200],
            )
        )
        stored += 1

    marked = 0
    seen: set[tuple[int, str]] = set()
    for marker in parsed.teams:
        if not marker.clean:
            continue
        game = games.find(marker.game_date, *_split(marker.matchup))
        candidates = [game.home_team_id, game.away_team_id] if game else None
        team_id = teams.match(marker.team_name, candidates)
        if team_id is None:
            continue
        key = (team_id, game.game_id if game else NO_GAME)
        if key in seen:
            continue
        seen.add(key)
        session.add(
            NbaIntelTeamReport(
                snapshot_id=snapshot.snapshot_id, team_id=key[0], game_id=key[1], state=marker.state
            )
        )
        marked += 1

    status = parsed.status
    if unmatched_teams and status == "ok":
        status = "partial"
    snapshot.parse_status = status
    snapshot.row_count = stored
    if unmatched_teams:
        snapshot.parse_error = "; ".join(errors)[:2000]
    session.flush()
    return IngestResult(
        snapshot.snapshot_id,
        status,
        stored,
        unmatched_players,
        unmatched_teams,
        marked,
        tuple(errors),
    )


def _split(matchup: str) -> tuple[str, str]:
    away, _, home = matchup.partition("@")
    return away, home


# ------------------------------------------------------------------- the review queue


@dataclass(frozen=True, slots=True)
class ReviewItem:
    status_id: int
    player_name: str
    team_id: int
    game_id: str | None
    game_date: date | None
    status: str | None
    source_label: str
    source_url: str | None
    source_published_at: datetime


def review_queue(session: Session, *, limit: int = 200) -> list[ReviewItem]:
    """Rows from the newest usable snapshot whose player could not be matched uniquely.

    Rows already retracted (by a manual link) are not listed; the replacement carries the player.
    """
    snapshot = store.latest_snapshot(session)
    if snapshot is None:
        return []
    retracted = select(NbaIntelStatus.retracts_status_id).where(
        NbaIntelStatus.retracts_status_id.is_not(None)
    )
    rows = (
        session.execute(
            select(NbaIntelStatus)
            .where(
                NbaIntelStatus.snapshot_id == snapshot.snapshot_id,
                NbaIntelStatus.player_id.is_(None),
                NbaIntelStatus.retracts_status_id.is_(None),
                NbaIntelStatus.status_id.not_in(retracted),
            )
            .order_by(NbaIntelStatus.team_id, NbaIntelStatus.status_id)
            .limit(max(1, min(limit, 1000)))
        )
        .scalars()
        .all()
    )
    return [
        ReviewItem(
            r.status_id,
            r.player_name_raw,
            r.team_id,
            r.game_id,
            r.game_date,
            r.status,
            r.source_label,
            r.source_url,
            r.source_published_at,
        )
        for r in rows
    ]


def resolve_review_item(
    session: Session,
    status_id: int,
    player_id: int,
    *,
    user_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Link a player to an unmatched row by appending a retraction and a replacement.

    Returns the replacement's ``status_id``. Raises :class:`ValueError` if the row does not exist,
    already has a player, has already been linked, or is itself a retraction, and
    :class:`UnknownPlayerError` if the stats store has no such player.
    """
    original = session.get(NbaIntelStatus, status_id)
    if original is None:
        raise ValueError(f"no status row {status_id}")
    if original.retracts_status_id is not None:
        raise ValueError("a retraction cannot be linked to a player")
    if original.player_id is not None:
        raise ValueError(f"status row {status_id} already has a player")
    already = session.execute(
        select(NbaIntelStatus.status_id).where(NbaIntelStatus.retracts_status_id == status_id)
    ).first()
    if already is not None:
        raise ValueError(f"status row {status_id} has already been linked to a player")
    _require_player(session, player_id)
    stamp = _naive_utc(now) if now is not None else utcnow()

    session.add(
        NbaIntelStatus(
            team_id=original.team_id,
            player_id=None,
            player_name_raw=original.player_name_raw,
            game_id=original.game_id,
            game_date=original.game_date,
            status=None,
            source_kind="manual",
            source_label="Manual match of an unmatched name",
            source_url=None,
            source_published_at=original.source_published_at,
            as_of=original.as_of,
            recorded_at=stamp,
            snapshot_id=original.snapshot_id,
            entered_by_user_id=user_id,
            retracts_status_id=original.status_id,
            provenance_note=f"Retracts {original.status_id}: player linked by hand"[:200],
        )
    )
    replacement = NbaIntelStatus(
        team_id=original.team_id,
        player_id=player_id,
        player_name_raw=original.player_name_raw,
        game_id=original.game_id,
        game_date=original.game_date,
        status=original.status,
        status_raw=original.status_raw,
        model_status=original.model_status,
        reason_category=original.reason_category,
        reason_text=original.reason_text,
        expected_return_text=original.expected_return_text,
        expected_return_round_from=original.expected_return_round_from,
        expected_return_round_to=original.expected_return_round_to,
        expected_return_date=original.expected_return_date,
        source_kind=original.source_kind,
        source_label=original.source_label,
        source_url=original.source_url,
        source_published_at=original.source_published_at,
        as_of=original.as_of,
        recorded_at=stamp,
        snapshot_id=original.snapshot_id,
        entered_by_user_id=user_id,
        provenance_note=f"Replaces {original.status_id}: player linked by hand"[:200],
    )
    session.add(replacement)
    session.flush()
    return replacement.status_id


# ---------------------------------------------------------------------------- overrides


def _require_player(session: Session, player_id: int) -> None:
    if not store.table_exists(session, "players"):
        raise UnknownPlayerError(f"player {player_id} is not in the stats store")
    found = session.execute(
        text("SELECT 1 FROM players WHERE player_id = :p"), {"p": player_id}
    ).first()
    if found is None:
        raise UnknownPlayerError(f"player {player_id} is not in the stats store")


def add_override(
    session: Session,
    *,
    player_id: int,
    status: str,
    user_id: str | None = None,
    team_id: int | None = None,
    game_id: str | None = None,
    note: str | None = None,
    source_url: str | None = None,
    source_published_at: datetime | None = None,
    now: datetime | None = None,
) -> NbaIntelOverride:
    """Record a status a person typed for one player. The caller commits.

    ``status`` must be one of the five (:class:`~nbastats.shared.availability.InvalidStatusError`
    otherwise). A link must be an absolute http(s) URL; one on the source denylist is dropped and
    the withheld notice is appended to the note.
    """
    if status is None:
        raise InvalidOverrideError("an override needs a status")
    clean_status = normalise_status(status)
    assert clean_status is not None
    _require_player(session, player_id)
    cleaned_note = _CONTROL.sub("", note or "").strip() or None
    url: str | None = None
    if source_url:
        url = clean_link(source_url)
        if url is None:
            raise InvalidOverrideError("the link is not a valid http or https URL")
        if is_denied(url):
            suffix = load_denylist().suffix.strip()
            cleaned_note = f"{cleaned_note} {suffix}".strip() if cleaned_note else suffix
            url = None
    if cleaned_note is not None:
        cleaned_note = cleaned_note[:200]
    stamp = _naive_utc(now) if now is not None else utcnow()
    row = NbaIntelOverride(
        player_id=player_id,
        team_id=team_id,
        game_id=game_id,
        status=clean_status,
        note=cleaned_note,
        source_url=url,
        source_published_at=_naive_utc(source_published_at) if source_published_at else None,
        created_at=stamp,
        entered_by_user_id=user_id,
    )
    session.add(row)
    session.flush()
    return row


def clear_override(
    session: Session, override_id: int, *, now: datetime | None = None
) -> NbaIntelOverride | None:
    """Clear an override (its one permitted edit). Idempotent; ``None`` if it does not exist."""
    row = session.get(NbaIntelOverride, override_id)
    if row is None:
        return None
    if row.cleared_at is None:
        row.cleared_at = _naive_utc(now) if now is not None else utcnow()
        session.flush()
    return row


def active_overrides(
    session: Session, *, player_ids: Sequence[int] | None = None
) -> list[NbaIntelOverride]:
    """Overrides not yet cleared, newest first, optionally for some players only."""
    stmt = select(NbaIntelOverride).where(NbaIntelOverride.cleared_at.is_(None))
    if player_ids is not None:
        stmt = stmt.where(NbaIntelOverride.player_id.in_(list(player_ids)))
    return list(session.execute(stmt.order_by(NbaIntelOverride.created_at.desc())).scalars().all())


# ------------------------------------------------- the shared selection rules' input


def status_entry(row: NbaIntelStatus) -> StatusEntry:
    """A stored status as the league-neutral entry the shared rules select among."""
    return StatusEntry(
        entry_id=row.status_id,
        status=row.status,
        source_kind=row.source_kind,
        source_published_at=row.source_published_at,
        player_key=row.player_id,
        game_id=row.game_id,
        is_override=False,
        recorded_at=row.recorded_at,
        retracts=row.retracts_status_id,
        expected_return_text=row.expected_return_text,
        expected_return_round_from=row.expected_return_round_from,
        expected_return_round_to=row.expected_return_round_to,
        expected_return_date=row.expected_return_date,
    )


def override_entry(row: NbaIntelOverride) -> StatusEntry:
    """An override as a shared entry. Its id is prefixed so it cannot collide with a status id."""
    return StatusEntry(
        entry_id=f"override:{row.override_id}",
        status=row.status,
        source_kind="manual",
        source_published_at=row.source_published_at or row.created_at,
        player_key=row.player_id,
        game_id=row.game_id,
        is_override=True,
        recorded_at=row.created_at,
        cleared_at=row.cleared_at,
    )
