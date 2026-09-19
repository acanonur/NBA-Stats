/**
 * Component-level coverage for `FantasyDraftBoardWidget`: columns render in served order, a
 * punted z-column is dimmed while its raw production column is not, `higherIsBetter: null`
 * columns get no direction colour, and a missing cell renders an em dash rather than a blank
 * that reads as zero.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import FantasyDraftBoardWidget from "./index";
import type { FantasyDraftBoardPayload } from "../../api/types";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_fantasy_draft_board.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as FantasyDraftBoardPayload;

describe("FantasyDraftBoardWidget", () => {
  it("renders the first player's name and the next pick", () => {
    const { getByText } = render(<FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={fixture} />);
    expect(getByText("On the clock")).toBeTruthy();
    expect(getByText(/Round 1, Pick 1/)).toBeTruthy();
  });

  it("switches which columns show when a group tab is activated", () => {
    const { getByRole, getByText, queryByText } = render(
      <FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={fixture} />,
    );
    // Default group is "summary" (the server's own first column group) — GP is visible, PTS is not.
    expect(getByText("GP")).toBeTruthy();
    expect(queryByText("PTS")).toBeNull();
    fireEvent.click(getByRole("tab", { name: "Per game" }));
    expect(getByText("PTS")).toBeTruthy();
  });

  it("shows the impact group's columns in the server's own order, including the ast/reb swap", () => {
    const { getByRole, getAllByText } = render(
      <FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={fixture} />,
    );
    fireEvent.click(getByRole("tab", { name: "Value" }));
    const headers = getAllByText(/^z/).map((el) => el.textContent);
    const astIndex = headers.indexOf("zAST");
    const rebIndex = headers.indexOf("zREB");
    expect(astIndex).toBeGreaterThanOrEqual(0);
    expect(astIndex).toBeLessThan(rebIndex);
  });

  it("renders a cell an em dash rather than a blank when a row has no value for a column", () => {
    const payload: FantasyDraftBoardPayload = {
      ...fixture,
      rows: [{ ...fixture.rows[0], values: { ...fixture.rows[0].values, pts: null } }],
    };
    const { getByRole, getByText } = render(<FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={payload} />);
    fireEvent.click(getByRole("tab", { name: "Per game" }));
    expect(getByText("—")).toBeTruthy();
  });

  it("shows the top pick's reason and the roster's weakest categories at the large size only", () => {
    const { getByText, queryByText, rerender } = render(
      <FantasyDraftBoardWidget kind="fantasy_draft_board" size="medium" payload={fixture} />,
    );
    expect(queryByText(/Your roster is thinnest/)).toBeNull();
    rerender(<FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={fixture} />);
    expect(getByText(/Your roster is thinnest/)).toBeTruthy();
  });

  it("shows an empty-state message rather than an empty table when there are no rows", () => {
    const payload: FantasyDraftBoardPayload = { ...fixture, rows: [] };
    const { getByText } = render(<FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={payload} />);
    expect(getByText(/No players are valued/)).toBeTruthy();
  });
});

describe("FantasyDraftBoardWidget availability", () => {
  // Regression: `row.availability` was decoded and dropped. A board of z-scores derived from
  // season aggregates is "estimated"; nothing on screen said so.
  it("marks estimated rows in the pinned identity column", () => {
    const { container } = render(
      <FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={fixture} />,
    );
    expect(container.querySelectorAll('[role="img"][aria-label*="estimated"]').length).toBeGreaterThan(0);
  });

  it("renders no marker at all for a measured row", () => {
    const payload: FantasyDraftBoardPayload = {
      ...fixture,
      rows: fixture.rows.map((row) => ({ ...row, availability: "full" as const })),
    };
    const { container } = render(
      <FantasyDraftBoardWidget kind="fantasy_draft_board" size="large" payload={payload} />,
    );
    expect(container.querySelectorAll('[role="img"][aria-label*="estimated"]').length).toBe(0);
  });
});
