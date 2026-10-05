/**
 * The anti-drift half of WP5's contract (WEB_DESIGN.md §9): asserts against the real fixture,
 * field for field, that (1) every field the decoder requires is present, and (2) every field
 * the fixture actually carries is known to the decoder. #2 is the one that matters most — it is
 * what turns a new `backend/nbastats/widgets/scoreboard.py` field into a red test here instead
 * of a silently-dropped key.
 *
 * The fixture is read straight off disk with `node:fs` rather than imported as a JSON module —
 * `design/__tests__/fnv.test.ts` does the same and explains why (no `resolveJsonModule` needed
 * for a file only tests touch, and a regenerated fixture needs no rebuild).
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { decodeScoreboardPayload } from "./decode";

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_scoreboard.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as Record<string, unknown>;
const fixtureGames = fixture.games as readonly Record<string, unknown>[];

/** A shallow copy of `obj` without `key` — avoids the unused-binding lint noise of
 * `const { key: _key, ...rest } = obj`. */
function omit(obj: Record<string, unknown>, key: string): Record<string, unknown> {
  const copy = { ...obj };
  delete copy[key];
  return copy;
}

describe("decodeScoreboardPayload", () => {
  it("decodes the golden fixture without dropping or inventing a field", () => {
    const payload = decodeScoreboardPayload(fixture);
    expect(payload.date).toBe("2026-01-02");
    expect(payload.games).toHaveLength(5);
    expect(payload.games[0]?.home.abbr).toBe("HOU");
    expect(payload.games[0]?.away.abbr).toBe("CLE");
    expect(payload.games[0]?.topPerformers).toHaveLength(2);
    expect(payload.games[0]?.topPerformers[0]?.player.name).toBe("Obi Toppin");
    expect(payload.games[0]?.topPerformers[0]?.value.metric).toBe("game_score");
  });

  it("throws — not a silently-dropped field — when the payload carries an unknown top-level key", () => {
    expect(() => decodeScoreboardPayload({ ...fixture, newField: true })).toThrow(/unrecognized field "newField"/);
  });

  it("throws when a game object carries an unknown key", () => {
    const withExtra = { ...fixture, games: [{ ...fixtureGames[0], attendance: 18997 }] };
    expect(() => decodeScoreboardPayload(withExtra)).toThrow(/unrecognized field "attendance"/);
  });

  it("throws when a top performer's value object carries an unknown key", () => {
    const firstGame = fixtureGames[0];
    const performers = firstGame.topPerformers as readonly Record<string, unknown>[];
    const firstPerformer = performers[0];
    const withExtra = {
      ...fixture,
      games: [
        {
          ...firstGame,
          topPerformers: [{ ...firstPerformer, value: { ...(firstPerformer.value as object), gameId: "x" } }],
        },
      ],
    };
    expect(() => decodeScoreboardPayload(withExtra)).toThrow(/unrecognized field "gameId"/);
  });

  it("throws when a required top-level field is missing", () => {
    expect(() => decodeScoreboardPayload(omit(fixture, "date"))).toThrow(/missing required field "date"/);
  });

  it("throws when a required nested field (team.abbr) is missing", () => {
    const firstGame = fixtureGames[0];
    const home = firstGame.home as Record<string, unknown>;
    const withBadHome = { ...fixture, games: [{ ...firstGame, home: omit(home, "abbr") }] };
    expect(() => decodeScoreboardPayload(withBadHome)).toThrow(/missing required field "abbr"/);
  });

  it("accepts a null score, clock and finalizedAt (a scheduled or live game)", () => {
    const firstGame = fixtureGames[0];
    const scheduled = {
      date: "2026-01-03",
      isLatestCompleted: false,
      allFinal: false,
      games: [
        {
          gameId: "0022500601",
          date: "2026-01-03",
          season: "2025-26",
          seasonType: "Regular Season",
          home: firstGame.home,
          away: firstGame.away,
          homePts: null,
          awayPts: null,
          status: "scheduled",
          period: null,
          clock: null,
          finalizedAt: null,
          topPerformers: [],
        },
      ],
    };
    const payload = decodeScoreboardPayload(scheduled);
    expect(payload.games[0]?.homePts).toBeNull();
    expect(payload.games[0]?.status).toBe("scheduled");
  });

  it("rejects an empty slate note shape with the wrong type for a field", () => {
    expect(() => decodeScoreboardPayload({ ...fixture, allFinal: "yes" })).toThrow(/allFinal/);
  });
});
