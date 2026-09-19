/**
 * The cross-widget conformance test: every kind in `WIDGET_KINDS` is mounted in the generated
 * registry, has a golden payload fixture, and renders from it at every size it advertises.
 *
 * This exists because of a failure the other 447 tests could not see. `generated/registry.ts` is
 * produced by `contracts/tools/gen_web_contracts.py`, which decides each kind's `component` by
 * looking for `web/src/widgets/<kind>/index.tsx` **on disk at generation time**. The sixteen
 * widgets were built in parallel *after* the last sync, so every one of them shipped with
 * `component: null` in the registry — `WidgetContainer` renders `PendingTile` ("This widget is on
 * iOS only for now") for a null component, so the whole app was sixteen grey placeholders while
 * typecheck, lint, the build and every per-widget test passed. Each widget's own
 * `index.test.tsx` imports its component directly and so never touches the registry; nothing
 * asserted the wiring in between. `check_contracts.py` check (i) did catch the stale file, but
 * only as a byte diff against a fresh generator run, which says nothing about what the app
 * renders.
 *
 * So the assertion that matters here is `component` being non-null, reached through `REGISTRY`
 * exactly as `WidgetContainer` reaches it. The fixture render on top of that is what makes the
 * test fail loudly rather than subtly if a kind is registered but cannot survive its own
 * contract payload.
 *
 * Named `fixtures.test.ts` (no JSX, hence `createElement`) because `check_contracts.py` check (k)
 * looks for this exact path and requires it to name every `widget_<kind>` fixture — see that
 * check's docstring. The per-kind literals below are what it greps for.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createElement } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { REGISTRY } from "../../generated/registry";
import { WIDGET_KINDS, type WidgetKind } from "../../generated/contracts";

afterEach(() => cleanup());

/**
 * The fixture basename per kind, written out rather than interpolated: check (k) greps this file
 * for the literal string `widget_<kind>`, and a template literal would satisfy the renderer while
 * leaving the check unable to tell a covered kind from a forgotten one.
 */
const FIXTURE_FILE: Readonly<Record<WidgetKind, string>> = {
  stat_tile: "widget_stat_tile.json",
  player_snapshot: "widget_player_snapshot.json",
  leaderboard: "widget_leaderboard.json",
  game_log: "widget_game_log.json",
  trend_chart: "widget_trend_chart.json",
  four_factors: "widget_four_factors.json",
  shot_profile: "widget_shot_profile.json",
  comparison: "widget_comparison.json",
  scoreboard: "widget_scoreboard.json",
  daily_movers: "widget_daily_movers.json",
  team_efficiency: "widget_team_efficiency.json",
  next_game_projection: "widget_next_game_projection.json",
  projection_board: "widget_projection_board.json",
  fantasy_draft_board: "widget_fantasy_draft_board.json",
  fantasy_trade: "widget_fantasy_trade.json",
  career_arc: "widget_career_arc.json",
};

function loadFixture(kind: WidgetKind): unknown {
  const path = resolve(process.cwd(), "..", "contracts", "fixtures", FIXTURE_FILE[kind]);
  return JSON.parse(readFileSync(path, "utf-8"));
}

describe("every widget kind is wired into the registry", () => {
  it("covers all sixteen kinds with no gaps and no strays", () => {
    expect(WIDGET_KINDS.length).toBe(16);
    expect(Object.keys(FIXTURE_FILE).sort()).toEqual([...WIDGET_KINDS].sort());
  });

  for (const kind of WIDGET_KINDS) {
    describe(kind, () => {
      it("has a component in REGISTRY, not a null placeholder", () => {
        // The exact read `WidgetContainer.renderBody` performs. A null here is a grey
        // PendingTile in production, which is why this is asserted before anything renders.
        expect(REGISTRY[kind].component).not.toBeNull();
      });

      it("declares a defaultSize it also lists in sizes", () => {
        expect(REGISTRY[kind].sizes).toContain(REGISTRY[kind].defaultSize);
      });

      it("renders its golden fixture at every advertised size", () => {
        const entry = REGISTRY[kind];
        const component = entry.component;
        if (!component) throw new Error(`${kind} has no registered component`);
        const payload = loadFixture(kind);

        expect(entry.sizes.length).toBeGreaterThan(0);
        for (const size of entry.sizes) {
          const { container } = render(createElement(component, { kind, size, payload }));
          // Something was actually drawn: a registered component that renders an empty tile from
          // its own contract fixture is as broken as an unregistered one, just less obviously.
          expect(container.textContent?.trim().length ?? 0).toBeGreaterThan(0);
          cleanup();
        }
      });
    });
  }
});
