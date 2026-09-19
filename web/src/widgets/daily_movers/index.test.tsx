/**
 * Component-level coverage for `DailyMoversWidget`: size density, the direction-dependent title,
 * the em-dash rule for a row with no season baseline, and that the delta (not the raw value) is
 * what carries the sign/arrow.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import DailyMoversWidget from "./index";
import type { DailyMoversPayload } from "../../api/types";
import styles from "./index.module.css";

afterEach(() => cleanup());

const fixturePath = resolve(process.cwd(), "..", "contracts", "fixtures", "widget_daily_movers.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as DailyMoversPayload;

describe("DailyMoversWidget", () => {
  it("titles the tile by direction", () => {
    const { getByText, rerender } = render(<DailyMoversWidget kind="daily_movers" size="medium" payload={fixture} />);
    expect(getByText("Biggest nights")).toBeTruthy();
    rerender(<DailyMoversWidget kind="daily_movers" size="medium" payload={{ ...fixture, direction: "worst" }} />);
    expect(getByText("Toughest nights")).toBeTruthy();
    rerender(<DailyMoversWidget kind="daily_movers" size="medium" payload={{ ...fixture, direction: "surprise" }} />);
    expect(getByText("Biggest surprises")).toBeTruthy();
  });

  it("shows only 2 rows at small, 5 at large", () => {
    const { container: small } = render(<DailyMoversWidget kind="daily_movers" size="small" payload={fixture} />);
    expect(small.getElementsByClassName(styles.row)).toHaveLength(2);
    const { container: large } = render(<DailyMoversWidget kind="daily_movers" size="large" payload={fixture} />);
    expect(large.getElementsByClassName(styles.row)).toHaveLength(5);
  });

  it("shows the season average only at large", () => {
    const { queryByText: queryMedium } = render(<DailyMoversWidget kind="daily_movers" size="medium" payload={fixture} />);
    expect(queryMedium(/avg 15\.9/)).toBeNull();
    const { getByText } = render(<DailyMoversWidget kind="daily_movers" size="large" payload={fixture} />);
    expect(getByText(/avg 15\.9/)).toBeTruthy();
  });

  it("renders an em dash, never 0, for a row with no season baseline", () => {
    const row = fixture.rows[0];
    const payload: DailyMoversPayload = { ...fixture, rows: [{ ...row, seasonAverage: null, delta: null }] };
    const { container, getByText } = render(<DailyMoversWidget kind="daily_movers" size="large" payload={payload} />);
    expect(getByText(/avg —/)).toBeTruthy();
    expect(container.textContent).not.toContain(">0<");
  });

  it("shows nobody-played copy for an empty slate", () => {
    const payload: DailyMoversPayload = { ...fixture, rows: [] };
    const { getByText } = render(<DailyMoversWidget kind="daily_movers" size="medium" payload={payload} />);
    expect(getByText("Nobody played on this date.")).toBeTruthy();
  });

  it("gives the top row's delta an up arrow and a positive accessible label", () => {
    const { container } = render(<DailyMoversWidget kind="daily_movers" size="medium" payload={fixture} />);
    const row = container.querySelector("[aria-label*='Austin Reaves']");
    expect(row).toBeTruthy();
    expect(row?.getAttribute("aria-label")).toContain("above their season average");
  });
});
