/**
 * Component-level coverage for `ShotProfileWidget`: the era/no-data placeholder never reads as a
 * zero-shooting player, the accuracy chart only appears at `large`, and the charts' accessible
 * names carry the real numbers rather than just announcing "chart".
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import ShotProfileWidget from "./index";
import type { ShotProfilePayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_shot_profile.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as ShotProfilePayload;

describe("ShotProfileWidget", () => {
  it("renders the subject name and season context", () => {
    const { getAllByText, getByText } = render(
      <ShotProfileWidget kind="shot_profile" size="medium" payload={fixture} />,
    );
    // Appears twice — once in the header, once as the chart legend's subject swatch label.
    expect(getAllByText("AJ Green").length).toBeGreaterThanOrEqual(1);
    expect(getByText("2025-26 · Regular Season")).toBeTruthy();
  });

  it("shows the field goal percentage chart only at large, and a text footnote otherwise", () => {
    const { container: medium, getByText } = render(
      <ShotProfileWidget kind="shot_profile" size="medium" payload={fixture} />,
    );
    expect(medium.querySelector('[aria-label^="Field goal percentage"]')).toBeNull();
    expect(getByText(/Accuracy, league in brackets/)).toBeTruthy();

    const { container: large } = render(<ShotProfileWidget kind="shot_profile" size="large" payload={fixture} />);
    expect(large.querySelector('[aria-label^="Field goal percentage"]')).toBeTruthy();
  });

  it("gives the share-of-attempts chart an accessible name with the real numbers, not just 'chart'", () => {
    const { container } = render(<ShotProfileWidget kind="shot_profile" size="large" payload={fixture} />);
    const chart = container.querySelector('[aria-label^="Share of attempts"]');
    expect(chart).toBeTruthy();
    const label = chart?.getAttribute("aria-label") ?? "";
    expect(label).toContain("At Rim");
    expect(label).toContain("27.4%");
    expect(label).toContain("64.7%");
  });

  it("reads a pre-1996-97 season as 'not tracked' rather than a zero-shooting player", () => {
    const eraPayload: ShotProfilePayload = {
      ...fixture,
      season: "1961-62",
      zones: fixture.zones.map((zone) => ({
        ...zone,
        fga: null,
        fgPct: null,
        shareOfFga: null,
        pointsPerShot: null,
        leagueFgPct: null,
        leagueShareOfFga: null,
      })),
      threePointRate: null,
      freeThrowRate: null,
      note: "Shot locations begin in 1996-97, with league-wide play-by-play; the 1961-62 season has box scores but no shot chart.",
    };
    const { container, getByText, queryByText } = render(
      <ShotProfileWidget kind="shot_profile" size="large" payload={eraPayload} />,
    );
    expect(getByText(/Shot locations begin in 1996-97/)).toBeTruthy();
    // No chart, and nothing in the DOM reads as a rendered zero.
    expect(container.querySelector("svg")).toBeNull();
    expect(queryByText("0.0%")).toBeNull();
    expect(container.textContent).not.toMatch(/\b0%\b/);
  });

  it("formats the 3PA and FT rates as percentages, never a raw fraction", () => {
    const { getByText } = render(<ShotProfileWidget kind="shot_profile" size="large" payload={fixture} />);
    expect(getByText("31.7%")).toBeTruthy();
    expect(getByText("28.5%")).toBeTruthy();
  });
});
