/**
 * Component-level coverage for `NextGameProjectionWidget`: the em-dash rule for a missing
 * interval, the thin-history caution, the no-scheduled-game warning, and that the interval bar's
 * accessible label carries real numbers rather than "chart".
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import NextGameProjectionWidget from "./index";
import type { NextGameProjectionPayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_next_game_projection.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as NextGameProjectionPayload;

describe("NextGameProjectionWidget", () => {
  it("renders the player name and the projected minutes headline", () => {
    const { getByText } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={fixture} />);
    expect(getByText("AJ Green")).toBeTruthy();
    expect(getByText("38.6")).toBeTruthy();
  });

  it("shows fewer stat lines at medium than at large", () => {
    const { container: medium } = render(<NextGameProjectionWidget kind="next_game_projection" size="medium" payload={fixture} />);
    const { container: large } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={fixture} />);
    expect(medium.textContent).toContain("more projected stat");
    expect(large.textContent).not.toContain("more projected stat");
  });

  it("warns instead of drawing a bar when a line has no interval, never a bare mean", () => {
    const payload: NextGameProjectionPayload = {
      ...fixture,
      lines: fixture.lines.map((line) => ({ ...line, low: null, high: null })),
    };
    const { getAllByText } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={payload} />);
    expect(getAllByText(/arrived without intervals/).length).toBeGreaterThan(0);
  });

  it("flags thin history behind a rate rather than presenting it with confidence", () => {
    const payload: NextGameProjectionPayload = {
      ...fixture,
      lines: [{ ...fixture.lines[0], exposureMinutes: 40, shrinkageK: 81, shrinkageWeight: 0.3 }],
    };
    const { getByText } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={payload} />);
    expect(getByText(/rest is the league average/)).toBeTruthy();
  });

  it("shows a warning instead of a matchup when there is no scheduled game", () => {
    const payload: NextGameProjectionPayload = { ...fixture, game: null };
    const { getByText } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={payload} />);
    expect(getByText(/No scheduled next game/)).toBeTruthy();
  });

  it("gives the interval bar an accessible label carrying the real numbers, not just 'chart'", () => {
    const { getAllByRole } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={fixture} />);
    const bars = getAllByRole("group").filter((el) => el.getAttribute("aria-label")?.includes("Points"));
    expect(bars.length).toBeGreaterThan(0);
    const label = bars[0].getAttribute("aria-label") ?? "";
    expect(label).toMatch(/projected 19\.0/);
    expect(label).not.toBe("chart");
  });

  it("renders an em dash rather than 0 when the payload arrives shaped-but-empty (era gate)", () => {
    const payload: NextGameProjectionPayload = {
      player: fixture.player,
      game: null,
      projectedMinutes: { value: null, displayValue: "—", halfLifeGames: null, seasonAverage: null, low: null, high: null },
      lines: [],
      factors: [],
      combo: null,
      method: fixture.method,
      notes: ["Pace and defensive rating begin in 1996-97."],
    };
    const { container, getAllByText } = render(<NextGameProjectionWidget kind="next_game_projection" size="large" payload={payload} />);
    expect(getAllByText("—").length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain(">0<");
  });
});
