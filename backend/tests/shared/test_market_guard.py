"""Tests for what goes on the wire: the market-vocabulary guard and the payload builders.

Two halves, because they answer the same question from both ends:

* **The guard.** The forbidden-word list is asserted *exactly* against the designed list (so
  neither a quiet addition nor a quiet deletion survives), key splitting is tested on the
  awkward cases (acronyms, digits, snake case), and the guard's central promise is checked:
  whole words, never substrings, so ``coverage``, ``headline`` and ``baseline`` need no
  exception while ``projectedLine`` is caught.
* **The builders.** ``refs.py`` writes the league-neutral shapes every payload is made of.
  They are tested for exact key sets, the league-only identifiers (an NBA team never carries a
  club code, even as null), the rule that a half-written result is not a result, the
  ``resultPending`` derivation at its exact three-hour edge, and the timestamp format. Then every
  payload any ``shared`` module can produce is run through the guard, which is the check that
  makes "no betting machinery in a payload" a property of the code rather than of its authors.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nbastats.shared import _generated_leagues as G
from nbastats.shared import market_guard as M
from nbastats.shared import refs

UTC = timezone.utc

# ---------------------------------------------------------------------------- the lists

DESIGNED_FORBIDDEN = {
    "line",
    "lines",
    "odds",
    "moneyline",
    "spread",
    "over",
    "under",
    "lean",
    "edge",
    "pick",
    "picks",
    "push",
    "implied",
    "cover",
    "vig",
    "juice",
    "stake",
    "wager",
    "bookmaker",
    "market",
    "parlay",
    "handicap",
    "ats",
    "probability",
}


def test_the_forbidden_words_are_exactly_the_designed_list() -> None:
    assert set(M.FORBIDDEN_KEY_WORDS) == DESIGNED_FORBIDDEN
    assert len(M.FORBIDDEN_KEY_WORDS) == len(DESIGNED_FORBIDDEN) == 24
    assert len(G.FORBIDDEN_PAYLOAD_KEY_WORDS) == 24  # no duplicates hiding in the tuple


def test_every_forbidden_word_is_one_lower_case_word() -> None:
    for word in M.FORBIDDEN_KEY_WORDS:
        assert M.split_words(word) == (word,)


def test_no_allowed_parameter_contains_a_forbidden_word() -> None:
    assert M.ALLOWED_PARAMETERS == set(G.ALLOWED_PARAMETERS)
    assert len(M.ALLOWED_PARAMETERS) == len(G.ALLOWED_PARAMETERS)
    for name in M.ALLOWED_PARAMETERS:
        assert M.forbidden_words_in(name) == (), name


def test_the_allowed_parameters_cover_the_designed_routes() -> None:
    expected = {
        # query and path names from design sections 9.2 and 9.3
        "homeTeamId",
        "awayTeamId",
        "season",
        "seasonType",
        "window",
        "teamId",
        "date",
        "teamIds",
        "statuses",
        "playerId",
        "limit",
        "basis",
        "scheme",
        "round",
        "clubCode",
        "asOfRound",
        "perClub",
        "perMode",
        "sort",
        "minGames",
        "gameId",
        "personCode",
        "statusId",
        "overrideId",
        # the availability write
        "playerName",
        "status",
        "reasonCategory",
        "reasonText",
        "expectedReturnText",
        "sourceUrl",
        "sourceLabel",
        "sourcePublishedAt",
        # the pasted-link write
        "title",
        "link",
        "publishedAt",
        "sourceName",
        "playerIds",
        # the model-settings write
        "settings",
        "key",
        "value",
    }
    assert expected <= M.ALLOWED_PARAMETERS


def test_nothing_that_could_carry_an_external_number_is_an_allowed_parameter() -> None:
    suspicious = {
        "line",
        "total",
        "threshold",
        "target",
        "price",
        "odds",
        "bet",
        "stake",
        "over",
        "under",
        "margin",
        "points",
        "spread",
        "edge",
        "cutoff",
        "bound",
    }
    assert not suspicious & M.ALLOWED_PARAMETERS


# --------------------------------------------------------------------- splitting words


@pytest.mark.parametrize(
    "key, words",
    [
        ("overtimePeriods", ("overtime", "periods")),
        ("homeAdvantagePoints", ("home", "advantage", "points")),
        ("pointsAllowedPerGame", ("points", "allowed", "per", "game")),
        ("HTTPStatus", ("http", "status")),
        ("tvCode", ("tv", "code")),
        ("snake_case_key", ("snake", "case", "key")),
        ("kebab-case-key", ("kebab", "case", "key")),
        ("fg3Pct", ("fg", "3", "pct")),
        ("pts40", ("pts", "40")),
        ("E2026", ("e", "2026")),
        ("line2", ("line", "2")),
        ("lastN", ("last", "n")),
        ("a", ("a",)),
        ("", ()),
        ("___", ()),
    ],
)
def test_keys_split_into_lower_case_words(key, words) -> None:
    assert M.split_words(key) == words


@pytest.mark.parametrize(
    "key",
    [
        "overtimePeriods",
        "headline",
        "coverage",
        "baseline",
        "inForce",
        "tvCode",
        "isTossUp",
        "marginRange80",
        "keyAbsences",
        "intervalBasis",
        "leagueSignal",
        "pickup",
        "pushy",
        "marketing",
        "edgeless",
        "leaner",
        "underlying",
        "overall",
        "stakeholder",
        "lineup",
        "linesman",
        "probabilistic",
        "homeAdvantagePoints",
        "teamSd",
        "marginSd",
        "statusChance",
        "pointsPer40",
    ],
)
def test_substrings_of_forbidden_words_are_not_violations(key) -> None:
    assert M.forbidden_words_in(key) == ()


@pytest.mark.parametrize(
    "key, words",
    [
        ("line", ("line",)),
        ("projectedLine", ("line",)),
        ("homeWinProbability", ("probability",)),
        ("modelLine", ("line",)),
        ("pOver", ("over",)),
        ("overUnder", ("over", "under")),
        ("edge", ("edge",)),
        ("leanDirection", ("lean",)),
        ("pick_of_the_day", ("pick",)),
        ("impliedPrice", ("implied",)),
        ("line2", ("line",)),
        ("moneyline", ("moneyline",)),
        ("MARKET", ("market",)),
        ("bookmakerLine", ("bookmaker", "line")),
        ("lineLine", ("line",)),  # each forbidden word is reported once
    ],
)
def test_whole_word_matches_are_violations(key, words) -> None:
    assert M.forbidden_words_in(key) == words


# --------------------------------------------------------------------------- scanning


def test_scan_finds_keys_at_any_depth_and_reports_where() -> None:
    payload = {
        "league": "nba",
        "games": [{"home": {"name": "x"}, "projectedLine": 1}, {"fine": {"edge": 2}}],
        "model": {"constants": [{"key": "a", "pOver": 0.5}]},
    }
    found = M.scan_keys(payload)
    assert [(v.path, v.key, v.words) for v in found] == [
        ("$.games[0].projectedLine", "projectedLine", ("line",)),
        ("$.games[1].fine.edge", "edge", ("edge",)),
        ("$.model.constants[0].pOver", "pOver", ("over",)),
    ]
    assert "projectedLine" in str(found[0])


def test_scan_ignores_values() -> None:
    payload = {
        "headline": "Odds are the team covers the spread, pick the over",
        "notes": ["a market for the line", "implied by the edge"],
        "tags": {"free": "probability of the over"},
    }
    assert M.scan_keys(payload) == []


def test_scan_handles_scalars_empties_and_tuples() -> None:
    assert M.scan_keys(None) == [] and M.scan_keys(3) == [] and M.scan_keys("line") == []
    assert M.scan_keys({}) == [] and M.scan_keys([]) == []
    assert [v.key for v in M.scan_keys(({"line": 1},))] == ["line"]
    assert M.scan_keys({"line": 1}, root="$.body")[0].path == "$.body.line"


def test_assert_clean_keys() -> None:
    M.assert_clean_keys({"pointsPerGame": 1, "games": [{"form": []}]})
    with pytest.raises(AssertionError, match=r"\$\.a\.lean"):
        M.assert_clean_keys({"a": {"lean": 1}})


# --------------------------------------------------------------------------- parameters


def test_parameter_violations() -> None:
    assert M.parameter_violations(["season", "window", "clubCode"]) == []
    assert M.parameter_violations(["season", "line", "odds", "line", "threshold"]) == [
        "line",
        "odds",
        "threshold",
    ]
    assert M.parameter_violations([]) == []
    assert M.is_allowed_parameter("homeTeamId") and not M.is_allowed_parameter("hometeamid")
    assert not M.is_allowed_parameter("line")


# ------------------------------------------------------------------- the builders: refs


def test_timestamps_are_rfc3339_utc_with_z() -> None:
    assert refs.rfc3339(datetime(2026, 10, 1, 18, 0, tzinfo=UTC)) == "2026-10-01T18:00:00Z"
    assert refs.rfc3339(datetime(2026, 10, 1, 18, 0)) == "2026-10-01T18:00:00Z"  # naive is UTC
    berlin = datetime(2026, 10, 1, 20, 0, tzinfo=ZoneInfo("Europe/Berlin"))  # CEST, UTC+2
    assert refs.rfc3339(berlin) == "2026-10-01T18:00:00Z"
    assert (
        refs.rfc3339(datetime(2026, 10, 1, 18, 0, 59, 999999, tzinfo=UTC)) == "2026-10-01T18:00:59Z"
    )
    assert refs.rfc3339(None) is None


def test_dates_are_iso_calendar_dates() -> None:
    assert refs.iso_date(date(2026, 10, 1)) == "2026-10-01"
    assert refs.iso_date(datetime(2026, 10, 1, 23, 59)) == "2026-10-01"
    assert refs.iso_date(" 2026-10-01 ") == "2026-10-01"
    assert refs.iso_date(None) is None
    with pytest.raises(ValueError):
        refs.iso_date("1 October")
    with pytest.raises(TypeError):
        refs.iso_date(20261001)


def test_an_nba_team_ref() -> None:
    ref = refs.nba_team_ref(1610612738, "BOS", "Boston Example", "Example")
    assert ref == {
        "league": "nba",
        "id": "1610612738",
        "abbr": "BOS",
        "name": "Boston Example",
        "shortName": "Example",
        "teamId": 1610612738,
    }
    assert "clubCode" not in ref and "tvCode" not in ref  # absent, not null
    assert refs.nba_team_ref(5, "XYZ", "Name")["shortName"] is None


def test_a_euroleague_team_ref() -> None:
    ref = refs.el_team_ref("ZZA", "Example Athens", "Athens", tv_code="ZA")
    assert ref == {
        "league": "euroleague",
        "id": "ZZA",
        "abbr": "ZZA",
        "name": "Example Athens",
        "shortName": "Athens",
        "clubCode": "ZZA",
        "tvCode": "ZA",
    }
    assert "teamId" not in ref
    assert refs.el_team_ref("ZZB", "Example")["tvCode"] is None  # tvCode is nullable, not absent


def test_a_team_ref_refuses_the_other_leagues_identifier() -> None:
    with pytest.raises(ValueError):
        refs.team_ref("nba", id="1", abbr="A", name="A", club_code="ZZA")
    with pytest.raises(ValueError):
        refs.team_ref("nba", id="1", abbr="A", name="A", tv_code="ZA")
    with pytest.raises(ValueError):
        refs.team_ref("euroleague", id="ZZA", abbr="ZZA", name="A", team_id=7)
    for bad_id in ("", None, 7):
        with pytest.raises(ValueError):
            refs.team_ref("nba", id=bad_id, abbr="A", name="A")
    with pytest.raises(ValueError):
        refs.team_ref("wnba", id="1", abbr="A", name="A")


def test_player_refs() -> None:
    nba = refs.nba_player_ref(
        201939,
        "Sample Player",
        position="G",
        position_raw="G-F",
        jersey="30",
        headshot_url="https://example.org/x.png",
    )
    assert nba == {
        "league": "nba",
        "id": "201939",
        "name": "Sample Player",
        "position": "G",
        "positionRaw": "G-F",
        "jersey": "30",
        "headshotUrl": "https://example.org/x.png",
        "playerId": 201939,
    }
    el = refs.el_player_ref("wb-1a2b3c4d", "Invented Person", position="F", position_raw="Forward")
    assert el["id"] == "wb-1a2b3c4d" and el["personCode"] == "wb-1a2b3c4d"
    assert "playerId" not in el
    assert el["jersey"] is None and el["headshotUrl"] is None  # unknown stays null, not ""
    assert refs.el_player_ref("demo-1", "X")["position"] is None


def test_a_player_ref_validates_position_and_identifiers() -> None:
    for bad in ("PG", "g", "Guard", ""):
        with pytest.raises(ValueError):
            refs.player_ref("nba", id="1", name="x", position=bad)
    with pytest.raises(ValueError):
        refs.player_ref("nba", id="1", name="x", person_code="abc")
    with pytest.raises(ValueError):
        refs.player_ref("euroleague", id="abc", name="x", player_id=1)
    with pytest.raises(ValueError):
        refs.player_ref("nba", id="", name="x")


def test_a_source() -> None:
    published = datetime(2026, 9, 28, 0, 0, tzinfo=UTC)
    src = refs.source_ref(
        "pressArticle",
        "example.org",
        published_at=published,
        url="https://example.org/a",
        fetched_at=datetime(2026, 10, 1, 9, 30),
        snapshot_id=None,
    )
    assert src == {
        "kind": "pressArticle",
        "label": "example.org",
        "url": "https://example.org/a",
        "publishedAt": "2026-09-28T00:00:00Z",
        "asOf": None,
        "fetchedAt": "2026-10-01T09:30:00Z",
        "snapshotId": None,
    }
    manual = refs.source_ref("manual", "Typed by hand", published_at="2026-10-02T08:00:00Z")
    assert manual["url"] is None and manual["publishedAt"] == "2026-10-02T08:00:00Z"
    with pytest.raises(ValueError):
        refs.source_ref("rumour", "x", published_at=published)
    with pytest.raises(ValueError):
        refs.source_ref("manual", "x", published_at=None)


def test_freshness() -> None:
    block = refs.freshness_block(
        "euroleague",
        sync_version=3,
        data_through=date(2026, 9, 29),
        generated_at=datetime(2026, 10, 4, 12, 0),
        is_demo=True,
        sources=[{"key": "el.workbook", "state": "ok"}],
    )
    assert block == {
        "league": "euroleague",
        "syncVersion": 3,
        "dataThrough": "2026-09-29",
        "generatedAt": "2026-10-04T12:00:00Z",
        "isDemo": True,
        "sources": [{"key": "el.workbook", "state": "ok"}],
    }
    assert (
        refs.freshness_block(
            "nba",
            sync_version=1,
            data_through=None,
            generated_at="2026-10-04T12:00:00Z",
            is_demo=False,
        )["sources"]
        == []
    )
    stamped = refs.freshness_block(
        "nba",
        sync_version=1,
        data_through=datetime(2026, 10, 3, 6, 5, 4),
        generated_at="x",
        is_demo=False,
    )
    assert stamped["dataThrough"] == "2026-10-03T06:05:04Z"
    with pytest.raises(ValueError):
        refs.freshness_block(
            "mls", sync_version=1, data_through=None, generated_at="x", is_demo=False
        )


# ----------------------------------------------------------------- game status and refs

TIP = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)


def status(now, **kwargs):
    kwargs.setdefault("league", "euroleague")
    kwargs.setdefault("has_result", False)
    return refs.game_status(now=now, **kwargs)


def test_a_game_is_scheduled_until_three_hours_after_tip_off() -> None:
    assert status(TIP - timedelta(hours=1), tipoff_utc=TIP) == "scheduled"
    assert status(TIP, tipoff_utc=TIP) == "scheduled"
    assert status(TIP + timedelta(hours=3), tipoff_utc=TIP) == "scheduled"  # exactly 3 h: not yet
    assert status(TIP + timedelta(hours=3, seconds=1), tipoff_utc=TIP) == "resultPending"
    assert status(TIP + timedelta(days=3), tipoff_utc=TIP) == "resultPending"


def test_a_stored_result_or_a_postponement_settles_it_whatever_the_clock() -> None:
    assert status(TIP + timedelta(days=30), tipoff_utc=TIP, has_result=True) == "final"
    assert status(TIP - timedelta(days=30), tipoff_utc=TIP, has_result=True) == "final"
    assert status(TIP + timedelta(days=1), tipoff_utc=TIP, postponed=True) == "postponed"
    assert status(TIP + timedelta(days=1), tipoff_utc=TIP, postponed=True, has_result=True) == (
        "postponed"
    )


def test_without_a_tip_off_the_game_date_at_23_59_local_is_the_reference() -> None:
    # 2026-10-01 is CEST: 23:59 Berlin is 21:59 UTC. Three hours later is 00:59 UTC on the 2nd.
    day = date(2026, 10, 1)
    before = datetime(2026, 10, 2, 0, 58, tzinfo=UTC)
    after = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)
    assert status(before, game_date=day) == "scheduled"
    assert status(after, game_date=day) == "resultPending"
    # the NBA's day is US Eastern (EDT, UTC-4): 23:59 is 03:59 UTC on the next day
    assert status(datetime(2026, 10, 2, 6, 58, tzinfo=UTC), league="nba", game_date=day) == (
        "scheduled"
    )
    assert status(datetime(2026, 10, 2, 7, 0, tzinfo=UTC), league="nba", game_date=day) == (
        "resultPending"
    )


def test_a_tip_off_outranks_the_date_and_a_game_with_neither_stays_scheduled() -> None:
    assert status(TIP + timedelta(hours=4), tipoff_utc=TIP, game_date=date(2026, 12, 25)) == (
        "resultPending"
    )
    assert status(datetime(2030, 1, 1, tzinfo=UTC)) == "scheduled"


def test_a_naive_tip_off_and_clock_are_taken_as_utc() -> None:
    assert status(datetime(2026, 10, 1, 21, 0, 1), tipoff_utc=datetime(2026, 10, 1, 18, 0)) == (
        "resultPending"
    )


def test_a_game_ref() -> None:
    home = refs.el_team_ref("ZZA", "Example Athens")
    away = refs.el_team_ref("ZZB", "Example Berlin")
    ref = refs.game_ref(
        "euroleague",
        game_id="E2026-R03-01",
        date=date(2026, 10, 1),
        home=home,
        away=away,
        status="final",
        tipoff_utc=TIP,
        venue="Example Arena",
        is_neutral=False,
        round_number=3,
        phase="RS",
        home_pts=88,
        away_pts=81,
        overtime_periods=0,
    )
    assert list(ref) == [
        "league",
        "gameId",
        "date",
        "tipoffUtc",
        "venue",
        "isNeutral",
        "round",
        "phase",
        "status",
        "home",
        "away",
        "homePts",
        "awayPts",
        "overtimePeriods",
    ]
    assert ref["tipoffUtc"] == "2026-10-01T18:00:00Z" and ref["date"] == "2026-10-01"
    assert ref["homePts"] == 88 and ref["awayPts"] == 81 and ref["isNeutral"] is False
    assert ref["home"] == home and ref["home"] is not home  # copied, not aliased


def test_a_game_that_is_not_final_carries_no_score() -> None:
    home, away = refs.el_team_ref("ZZA", "A"), refs.el_team_ref("ZZB", "B")
    for state in ("scheduled", "resultPending", "postponed"):
        ref = refs.game_ref(
            "euroleague",
            game_id="g",
            date="2026-10-01",
            home=home,
            away=away,
            status=state,
            home_pts=88,
            away_pts=81,
            overtime_periods=1,
        )
        assert ref["homePts"] is None and ref["awayPts"] is None and ref["overtimePeriods"] is None
    with pytest.raises(ValueError):
        refs.game_ref(
            "euroleague", game_id="g", date="2026-10-01", home=home, away=away, status="live"
        )


def test_neutrality_stays_unknown_when_unknown() -> None:
    home, away = refs.nba_team_ref(1, "A", "A"), refs.nba_team_ref(2, "B", "B")
    ref = refs.game_ref(
        "nba", game_id="g", date="2026-10-01", home=home, away=away, status="scheduled"
    )
    assert ref["isNeutral"] is None and ref["tipoffUtc"] is None and ref["venue"] is None


# ------------------------------------------------ every shared payload passes the guard


def test_every_payload_the_shared_package_builds_is_free_of_forbidden_keys() -> None:
    from nbastats.shared import availability as A
    from nbastats.shared import defense_position as D
    from nbastats.shared import league_profile as LP
    from nbastats.shared import positions as P
    from nbastats.shared import team_form as TF

    home = refs.nba_team_ref(1, "AAA", "Alpha")
    away = refs.nba_team_ref(2, "BBB", "Beta")
    payloads = [
        home,
        refs.nba_player_ref(9, "Invented Player", position="C"),
        refs.el_team_ref("ZZA", "Example"),
        refs.el_player_ref("demo-1", "Invented", position="G"),
        refs.source_ref("manual", "x", published_at=datetime(2026, 10, 1)),
        refs.freshness_block(
            "nba",
            sync_version=1,
            data_through=date(2026, 10, 1),
            generated_at=datetime(2026, 10, 2),
            is_demo=True,
        ),
        refs.game_ref(
            "nba",
            game_id="g",
            date="2026-10-01",
            home=home,
            away=away,
            status="final",
            home_pts=100,
            away_pts=90,
        ),
        LP.NBA.to_contract(),
        LP.EUROLEAGUE.to_contract(),
        A.vocabulary(),
        P.scheme_contract(),
    ]
    games = []
    for i in range(6):
        games += [
            TF.TeamGame(
                game_id=f"g{i}",
                date=date(2026, 10, 1 + i),
                team="A",
                opponent="B",
                is_home=True,
                pts=90 + i,
                opp_pts=80,
            ),
            TF.TeamGame(
                game_id=f"g{i}",
                date=date(2026, 10, 1 + i),
                team="B",
                opponent="A",
                is_home=False,
                pts=80,
                opp_pts=90 + i,
            ),
        ]
    form = TF.compute_team_form(TF.LeagueIndex(games), "A", profile=LP.NBA)
    payloads.append(form.to_payload(lambda key: {"id": str(key)}))
    payloads.append(TF.LeagueIndex(games).league_average().to_payload())

    def allocation(team, n):
        lines = [
            D.OpponentLine(30, 20 + n, {"G": 1.0}),
            D.OpponentLine(30, 15, {"F": 1.0}),
            D.OpponentLine(30, 10, {"C": 1.0}),
        ]
        return D.allocate_game(
            game_id=f"{team}{n}", date=date(2026, 10, 1 + n), team=team, opp_pts=45 + n, lines=lines
        )

    league = {t: [allocation(t, n) for n in range(8)] for t in ("A", "B", "C", "D")}
    table = D.compute_defense(league, profile=LP.EUROLEAGUE)
    for team in table.teams:
        payloads += [team.to_payload(), team.to_table_row(), team.to_summary()]
    payloads.append(table.method.to_payload())

    for payload in payloads:
        assert M.scan_keys(payload) == [], payload
        assert json.loads(json.dumps(payload)) == payload  # and every one is plain JSON
