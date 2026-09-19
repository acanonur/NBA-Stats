/**
 * Component-level coverage for `ProjectionBoardWidget`: heading it with `date` (never
 * `selectionDate`), rendering rows in served order, the no-interval fallback, and the broadsheet
 * detection that switches which row idiom renders.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import ProjectionBoardWidget from "./index";
import type { ProjectionBoardPayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_projection_board.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as ProjectionBoardPayload;

describe("ProjectionBoardWidget", () => {
  it("heads the board with date, not selectionDate, and mentions the selection slate separately", () => {
    const { getByText } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />);
    // date is 2026-01-05, selectionDate is 2026-01-02 — both should appear, but distinctly.
    expect(getByText(/Jan 5/)).toBeTruthy();
    expect(getByText(/chosen from the.*Jan 2.*slate/)).toBeTruthy();
  });

  it("renders rows in served order (already sorted by |deltaZ|), not re-sorted by |delta|", () => {
    const { container } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />);
    const groups = Array.from(container.querySelectorAll('[role="group"]'));
    const renderedOrder = groups.map((group) => group.getAttribute("aria-label")?.split(",")[0]);
    const fixtureOrder = fixture.rows.map((row) => row.player.name);
    expect(renderedOrder).toEqual(fixtureOrder);
    // The fixture's largest |delta| (James Harden's -1.244 pts) is NOT first — its |deltaZ|
    // (-0.266) is smaller than the first row's (0.361), so a re-sort by raw delta would move it.
    expect(fixtureOrder[0]).not.toBe("James Harden");
  });

  it("shows fewer rows at medium than at large", () => {
    const { container: medium } = render(<ProjectionBoardWidget kind="projection_board" size="medium" payload={fixture} />);
    const { container: large } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />);
    const mediumRows = medium.querySelectorAll('[role="group"]').length;
    const largeRows = large.querySelectorAll('[role="group"]').length;
    expect(mediumRows).toBeLessThan(largeRows);
  });

  it("says there is no interval rather than drawing an empty bar when low/high are missing", () => {
    const payload: ProjectionBoardPayload = {
      ...fixture,
      rows: [{ ...fixture.rows[0], low: null, high: null }],
    };
    const { getByText } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={payload} />);
    expect(getByText("No interval for this projection.")).toBeTruthy();
  });

  it("labels the reference tick as the player's own average, every time", () => {
    const { getAllByRole } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />);
    const groups = getAllByRole("group");
    const first = groups[0].getAttribute("aria-label") ?? "";
    expect(first).toMatch(/season avg/);
  });

  it("renders the broadsheet row idiom when the tile sits inside a broadsheet-presented grid", () => {
    const { container } = render(
      <div data-presentation="broadsheet">
        <ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />
      </div>,
    );
    expect(container.querySelector(".hw-bs-rule, [class*='bsRow']")).toBeTruthy();
  });

  it("shows an empty-state message rather than nothing when no row cleared the filters", () => {
    const payload: ProjectionBoardPayload = { ...fixture, rows: [] };
    const { getByText } = render(<ProjectionBoardWidget kind="projection_board" size="large" payload={payload} />);
    expect(getByText(/No projection cleared/)).toBeTruthy();
  });
});

describe("ProjectionBoardWidget availability", () => {
  // Regression: the decoder always read `row.availability`, but nothing rendered it, so a board
  // of projections — `"estimated"` for every row, by construction — was indistinguishable from
  // recorded numbers. `next_game_projection` badges the identical rows; the two now agree.
  it("badges every estimated row and names the treatment once in a footnote", () => {
    const { container, getByText } = render(
      <ProjectionBoardWidget kind="projection_board" size="large" payload={fixture} />,
    );
    const markers = container.querySelectorAll('[role="img"][aria-label*="estimated"]');
    expect(markers.length).toBe(fixture.rows.filter((row) => row.availability === "estimated").length);
    expect(markers.length).toBeGreaterThan(0);
    expect(getByText(/Estimated from the box score/)).toBeTruthy();
  });

  it("adds no chrome when a row is fully measured", () => {
    const payload: ProjectionBoardPayload = {
      ...fixture,
      rows: fixture.rows.map((row) => ({ ...row, availability: "full" as const })),
    };
    const { container } = render(
      <ProjectionBoardWidget kind="projection_board" size="large" payload={payload} />,
    );
    expect(container.querySelectorAll('[role="img"][aria-label*="estimated"]').length).toBe(0);
  });
});
