/**
 * Component-level coverage for `FantasyTradeWidget`: the roster-spot adjustment stays its own
 * line, the sensitivity scenarios never render as a bar or an interval, the permanent "not a
 * probability" disclaimer, and reading `points` by its snake_case keys.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import FantasyTradeWidget from "./index";
import type { FantasyTradePayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_fantasy_trade.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as FantasyTradePayload;

describe("FantasyTradeWidget", () => {
  it("renders the verdict and both sides' players", () => {
    const { getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={fixture} />);
    expect(getByText("Clear Win")).toBeTruthy();
    expect(getByText("AJ Green")).toBeTruthy();
    expect(getByText("Kobe Sanders")).toBeTruthy();
  });

  it("reads points by its snake_case keys rather than a camelCase guess", () => {
    const { getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={fixture} />);
    expect(getByText("ESPN Points")).toBeTruthy();
    expect(getByText("+14.1")).toBeTruthy();
    expect(getByText("Yahoo Points")).toBeTruthy();
    expect(getByText("+12.4")).toBeTruthy();
  });

  it("never draws a bar for the sensitivity scenarios and always shows the disclaimer", () => {
    const { container, getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={fixture} />);
    expect(getByText("A sweep over four named assumptions, not a probability.")).toBeTruthy();
    // No SVG, no [role="img"] anywhere in the sensitivity block — a set of named scenarios is
    // rendered as text, never as a range chart.
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector('[role="img"]')).toBeNull();
    expect(getByText("The player you get misses 22 games")).toBeTruthy();
  });

  it("keeps the roster-spot adjustment its own line, separate from the net", () => {
    const payload: FantasyTradePayload = { ...fixture, rosterAdjustment: -2.5, changeZ: 1.0, netZ: -1.5 };
    const { getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={payload} />);
    expect(getByText(/Includes -2.50 for the roster/)).toBeTruthy();
    expect(getByText(/more than the players themselves/)).toBeTruthy();
  });

  it("does not render a sensitivity block when the server sent null (showSensitivity: false)", () => {
    const payload: FantasyTradePayload = { ...fixture, sensitivity: { ...fixture.sensitivity, scenarios: [] } };
    const { queryByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={payload} />);
    expect(queryByText(/not a probability/)).toBeNull();
  });

  it("shows a getting-started message rather than an empty board when neither side has a player", () => {
    const payload: FantasyTradePayload = {
      ...fixture,
      give: { ...fixture.give, count: 0, players: [] },
      get: { ...fixture.get, count: 0, players: [] },
    };
    const { getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="large" payload={payload} />);
    expect(getByText("Add players to both sides")).toBeTruthy();
  });

  it("falls back to a category summary rather than the full table at medium size", () => {
    const { queryByText, getByText } = render(<FantasyTradeWidget kind="fantasy_trade" size="medium" payload={fixture} />);
    // pts change is +3.067 (a gain); the summary sentence, not the 9-up abbreviation grid, should show.
    expect(getByText(/Gains/)).toBeTruthy();
    expect(queryByText("PTS")).toBeNull();
  });
});
