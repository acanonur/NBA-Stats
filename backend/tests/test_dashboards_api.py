"""Integration tests for ``nbastats.api.routes_me`` (``/v1/me/*``, ``/v1/dashboards/*``) and
``nbastats.api.routes_fantasy`` (``GET /v1/fantasy/night``).

The app under test carries ``routes_auth`` (to sign up and log in — WP1's router, used here only
as a way to obtain a real session and CSRF token, exactly the way ``test_auth_routes.py`` does),
``routes_me`` and ``routes_fantasy``, on a fresh, empty database per test — never the shared
``seeded_engine`` fixture other test modules use, because these tests write real account rows
through routes that commit directly, and a session-scoped engine shared with unrelated test
modules is not a store to write into.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nbastats import config as stats_config
from nbastats import db as db_module
from nbastats.accounts import config as auth_config
from nbastats.api import errors as api_errors
from nbastats.api import routes_auth, routes_fantasy, routes_me
from nbastats.models import Game, Player, PlayerGameBasic, Team

ORIGIN = {"Origin": "http://127.0.0.1:8000"}


@pytest.fixture()
def auth_db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A fresh, empty database (both metadatas) for each test."""
    db_path = tmp_path / "dashboards-api.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("HARDWOOD_SIGNUP_MODE", "open")
    stats_config.reset_settings_cache()
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    db_module.init_db()
    routes_auth.reset_auth_route_limiters()
    routes_me.reset_me_route_limiters()
    yield
    db_module.dispose_engine()
    auth_config.reset_auth_settings_cache()
    stats_config.reset_settings_cache()


@pytest.fixture()
def client(auth_db: None) -> TestClient:
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_auth.router, prefix="/v1")
    app.include_router(routes_me.router, prefix="/v1")
    app.include_router(routes_fantasy.router, prefix="/v1")
    return TestClient(app)


def _signup_and_login(client: TestClient, email: str, password: str = "a reasonable password") -> str:
    """Sign up, log in, and return the CSRF token — the session cookie is already on
    ``client`` (``TestClient`` persists cookies across calls on the same instance)."""
    signup = client.post("/v1/auth/signup", json={"email": email, "password": password}, headers=ORIGIN)
    assert signup.status_code == 202, signup.text
    login = client.post("/v1/auth/login", json={"email": email, "password": password}, headers=ORIGIN)
    assert login.status_code == 200, login.text
    return login.json()["csrfToken"]


def _headers(csrf: str) -> dict[str, str]:
    return {**ORIGIN, "X-Hardwood-CSRF": csrf}


SIMPLE_LAYOUT = {
    "id": "layout-1",
    "name": "My Board",
    "accent": "teal",
    "widgets": [
        {"id": "w1", "kind": "stat_tile", "title": "Points", "size": "small"},
    ],
}


# --------------------------------------------------------------------------- /v1/me


def test_get_me_requires_a_session(client: TestClient) -> None:
    response = client.get("/v1/me")
    assert response.status_code == 401


def test_get_and_patch_me(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    me = client.get("/v1/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"
    assert me.headers["cache-control"] == "no-store"

    patched = client.patch(
        "/v1/me",
        json={"displayName": "Ada", "favoritePlayerId": 2544, "theme": "dark"},
        headers=_headers(csrf),
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["displayName"] == "Ada"
    assert body["favoritePlayerId"] == 2544
    assert body["theme"] == "dark"


def test_patch_me_rejects_an_unknown_theme(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.patch("/v1/me", json={"theme": "neon"}, headers=_headers(csrf))
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "theme"


def test_patch_me_requires_csrf(client: TestClient) -> None:
    _signup_and_login(client, "ada@example.com")
    response = client.patch("/v1/me", json={"displayName": "Ada"}, headers=ORIGIN)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_sessions_list_shows_the_current_session(client: TestClient) -> None:
    _signup_and_login(client, "ada@example.com")
    response = client.get("/v1/me/sessions")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


# --------------------------------------------------------------------------- dashboards


def test_create_get_list_a_dashboard(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")

    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["revision"] == 1
    layout_id = body["layout"]["id"]
    assert body["layout"]["name"] == "My Board"
    assert body["layout"]["accent"] == "teal"

    listing = client.get("/v1/dashboards")
    assert listing.status_code == 200
    names = [d["name"] for d in listing.json()["dashboards"]]
    assert names == ["My Board"]

    fetched = client.get(f"/v1/dashboards/{layout_id}")
    assert fetched.status_code == 200
    assert fetched.headers["etag"] == '"1"'
    assert fetched.json()["layout"]["id"] == layout_id


def test_create_from_preset(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.post("/v1/dashboards", json={"presetKey": "daily_recap"}, headers=_headers(csrf))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["layout"]["presetKey"] == "daily_recap"
    assert body["layout"]["isPreset"] is False  # forked, not the shipped preset itself
    assert len(body["layout"]["widgets"]) > 0


def test_create_with_unknown_preset_key_is_404(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    response = client.post(
        "/v1/dashboards", json={"presetKey": "does_not_exist"}, headers=_headers(csrf)
    )
    assert response.status_code == 404


def test_create_requires_csrf(client: TestClient) -> None:
    _signup_and_login(client, "ada@example.com")
    response = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=ORIGIN)
    assert response.status_code == 403


def test_update_without_if_match_is_428(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))
    layout_id = created.json()["layout"]["id"]

    response = client.put(
        f"/v1/dashboards/{layout_id}", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf)
    )
    assert response.status_code == 428
    assert response.json()["error"]["code"] == "precondition_required"


def test_update_with_correct_if_match_succeeds_and_bumps_revision(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))
    layout_id = created.json()["layout"]["id"]

    updated_layout = {**SIMPLE_LAYOUT, "id": layout_id, "name": "Renamed Board"}
    response = client.put(
        f"/v1/dashboards/{layout_id}",
        json={"layout": updated_layout},
        headers={**_headers(csrf), "If-Match": "1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2
    assert body["layout"]["name"] == "Renamed Board"
    assert response.headers["etag"] == '"2"'


def test_update_with_stale_if_match_is_409_and_carries_the_current_document(
    client: TestClient,
) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))
    layout_id = created.json()["layout"]["id"]

    response = client.put(
        f"/v1/dashboards/{layout_id}",
        json={"layout": {**SIMPLE_LAYOUT, "id": layout_id, "name": "Someone Else's Edit"}},
        headers={**_headers(csrf), "If-Match": "99"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "stale_write"
    assert body["revision"] == 1
    assert body["layout"]["name"] == "My Board"  # the server's own current document


def test_delete_and_restore_a_dashboard(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))
    layout_id = created.json()["layout"]["id"]

    deleted = client.delete(f"/v1/dashboards/{layout_id}", headers=_headers(csrf))
    assert deleted.status_code == 204

    gone = client.get(f"/v1/dashboards/{layout_id}")
    assert gone.status_code == 404

    restored = client.post(f"/v1/dashboards/{layout_id}/restore", headers=_headers(csrf))
    assert restored.status_code == 200

    back = client.get(f"/v1/dashboards/{layout_id}")
    assert back.status_code == 200


def test_export_and_import_round_trip(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf))

    export = client.get("/v1/dashboards/export")
    assert export.status_code == 200
    envelope = export.json()
    assert envelope["schemaVersion"] == 1
    assert len(envelope["layouts"]) == 1
    assert export.headers["content-disposition"] == 'attachment; filename="Layouts.json"'

    # Importing the export back in creates a second, independent dashboard (a fresh id, since
    # the original id is already in use on this account).
    imported = client.post("/v1/dashboards/import", json=envelope, headers=_headers(csrf))
    assert imported.status_code == 200, imported.text
    assert len(imported.json()["imported"]) == 1

    listing = client.get("/v1/dashboards").json()["dashboards"]
    assert len(listing) == 2


def test_reorder_dashboards(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    first = client.post(
        "/v1/dashboards", json={"layout": {**SIMPLE_LAYOUT, "id": "a", "name": "A"}},
        headers=_headers(csrf),
    ).json()["layout"]["id"]
    second = client.post(
        "/v1/dashboards", json={"layout": {**SIMPLE_LAYOUT, "id": "b", "name": "B"}},
        headers=_headers(csrf),
    ).json()["layout"]["id"]

    reordered = client.put(
        "/v1/dashboards/order", json={"layoutIds": [second, first]}, headers=_headers(csrf)
    )
    assert reordered.status_code == 204

    listing = client.get("/v1/dashboards").json()["dashboards"]
    assert [d["layoutId"] for d in listing] == [second, first]


def test_another_users_layout_id_is_404_not_403(client: TestClient) -> None:
    """The invariant ``accounts/store.py`` exists to hold: an id that belongs to somebody else
    answers exactly like an id that does not exist at all."""
    csrf_a = _signup_and_login(client, "ada@example.com")
    created = client.post("/v1/dashboards", json={"layout": SIMPLE_LAYOUT}, headers=_headers(csrf_a))
    layout_id = created.json()["layout"]["id"]

    # A second account, same client (its login replaces the session cookie).
    csrf_b = _signup_and_login(client, "bob@example.com")

    get_response = client.get(f"/v1/dashboards/{layout_id}")
    assert get_response.status_code == 404

    put_response = client.put(
        f"/v1/dashboards/{layout_id}",
        json={"layout": {**SIMPLE_LAYOUT, "id": layout_id}},
        headers={**_headers(csrf_b), "If-Match": "1"},
    )
    assert put_response.status_code == 404

    delete_response = client.delete(f"/v1/dashboards/{layout_id}", headers=_headers(csrf_b))
    assert delete_response.status_code == 404


def test_too_many_widgets_are_clipped_with_a_note(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    from nbastats.accounts import store as store_module

    widgets = [
        {"id": f"w{i}", "kind": "stat_tile", "size": "small"}
        for i in range(store_module.MAX_WIDGETS_PER_DASHBOARD + 5)
    ]
    layout = {"id": "big-board", "name": "Big Board", "widgets": widgets}
    response = client.post("/v1/dashboards", json={"layout": layout}, headers=_headers(csrf))
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["layout"]["widgets"]) == store_module.MAX_WIDGETS_PER_DASHBOARD
    assert any("Kept the first" in note for note in body["notes"])


def test_a_layout_from_a_newer_schema_version_is_rejected(client: TestClient) -> None:
    csrf = _signup_and_login(client, "ada@example.com")
    layout = {**SIMPLE_LAYOUT, "schemaVersion": 99}
    response = client.post("/v1/dashboards", json={"layout": layout}, headers=_headers(csrf))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "layout_too_new"


# --------------------------------------------------------------------------- /v1/fantasy/night


def _seed_one_game(db) -> None:
    team_a = Team(team_id=1, abbr="AAA", name="Team A", city="A City", nickname="As")
    team_b = Team(team_id=2, abbr="BBB", name="Team B", city="B City", nickname="Bs")
    game = Game(
        game_id="0000000001",
        game_date=date(2026, 1, 15),
        season="2025-26",
        season_type="Regular Season",
        home_team_id=1,
        away_team_id=2,
        home_pts=110,
        away_pts=100,
        status="final",
    )
    scorer = Player(
        player_id=101, full_name="Full Scorer", first_name="Full", last_name="Scorer",
        is_active=True,
    )
    incomplete = Player(
        player_id=102, full_name="Missing Steals", first_name="Missing", last_name="Steals",
        is_active=True,
    )
    db.add_all([team_a, team_b, game, scorer, incomplete])
    db.flush()
    db.add_all(
        [
            PlayerGameBasic(
                game_id="0000000001", player_id=101, team_id=1, started=True, minutes=32.0,
                pts=30, reb=8, ast=6, stl=2, blk=1, tov=3, fgm=11, fga=20, fg3m=2, fg3a=5,
                ftm=6, fta=7, plus_minus=8.0, fantasy_pts=52.4,
            ),
            PlayerGameBasic(
                # Steals unrecorded: era-honesty null guard must drop this player from every
                # scoring system, never score them as though a null steal count were zero.
                game_id="0000000001", player_id=102, team_id=2, started=True, minutes=28.0,
                pts=18, reb=4, ast=3, stl=None, blk=0, tov=2, fgm=7, fga=14, fg3m=1, fg3a=3,
                ftm=3, fta=4, plus_minus=-8.0, fantasy_pts=None,
            ),
        ]
    )
    db.commit()


def test_fantasy_night_scores_the_slate_and_omits_incomplete_rows(auth_db: None) -> None:
    with db_module.get_sessionmaker()() as db:
        _seed_one_game(db)

    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_fantasy.router, prefix="/v1")
    client = TestClient(app)

    response = client.get("/v1/fantasy/night", params={"date": "2026-01-15", "minMinutes": 0})
    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2026-01-15"
    assert body["scoring"] == "nba"
    player_ids = [row["player"]["playerId"] for row in body["rows"]]
    assert 101 in player_ids
    assert 102 not in player_ids  # fantasy_pts is null on this row: omitted, never scored as 0
    top = body["rows"][0]
    assert top["rank"] == 1
    assert top["points"] == 52.4

    espn = client.get(
        "/v1/fantasy/night",
        params={"date": "2026-01-15", "scoring": "espn_points", "minMinutes": 0},
    )
    assert espn.status_code == 200
    espn_ids = [row["player"]["playerId"] for row in espn.json()["rows"]]
    assert 101 in espn_ids
    assert 102 not in espn_ids  # missing steals also disqualifies the ESPN/Yahoo re-scoring


def test_fantasy_night_rejects_an_unknown_scoring_system(auth_db: None) -> None:
    app = FastAPI()
    api_errors.install_error_handlers(app)
    app.include_router(routes_fantasy.router, prefix="/v1")
    client = TestClient(app)
    response = client.get("/v1/fantasy/night", params={"scoring": "moneyline"})
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "scoring"
