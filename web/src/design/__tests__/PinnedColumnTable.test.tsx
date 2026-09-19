/**
 * Coverage for `PinnedColumnTable`'s grid mechanics: the pinned column stays out of the
 * scrolling body, columns render in the given order, and the hairline rule is sized explicitly
 * rather than left at `100%` (see the stylesheet's own docstring for why that distinction
 * matters inside an `overflow-x: auto` container).
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { PinnedColumnTable } from "../PinnedColumnTable";

interface Row {
  readonly gameId: string;
  readonly date: string;
  readonly pts: number;
  readonly reb: number;
}

const rows: Row[] = [
  { gameId: "1", date: "Jan 2", pts: 32, reb: 8 },
  { gameId: "2", date: "Jan 4", pts: 28, reb: 11 },
];

afterEach(() => cleanup());

describe("PinnedColumnTable", () => {
  it("renders the pinned column and every data column, in order, and matches its snapshot", () => {
    const { container, getByText } = render(
      <PinnedColumnTable
        rows={rows}
        rowKey={(row) => row.gameId}
        pinnedColumn={{ key: "date", header: "Date", render: (row) => row.date }}
        columns={[
          { key: "pts", header: "PTS", align: "trailing", render: (row) => String(row.pts) },
          { key: "reb", header: "REB", align: "trailing", render: (row) => String(row.reb) },
        ]}
      />,
    );
    expect(getByText("Jan 2")).toBeTruthy();
    expect(getByText("32")).toBeTruthy();
    expect(getByText("REB")).toBeTruthy();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("sizes the scrolling rule explicitly from --col-w/--col-n rather than 100%", () => {
    const { container } = render(
      <PinnedColumnTable
        rows={rows}
        rowKey={(row) => row.gameId}
        pinnedColumn={{ key: "date", header: "Date", render: (row) => row.date }}
        columns={[{ key: "pts", header: "PTS", render: (row) => String(row.pts) }]}
        columnWidth={64}
      />,
    );
    // `width: calc(var(--col-w) * var(--col-n))` itself lives in PinnedColumnTable.module.css
    // (jsdom does not resolve CSS custom properties or calc() for getComputedStyle, so that
    // declaration is not re-asserted here); what this component is responsible for is setting
    // the two custom properties that expression depends on.
    const rule = container.querySelector('[class*="rule"]:not([class*="pinnedRule"])') as HTMLElement;
    expect(rule.style.getPropertyValue("--col-w")).toBe("64px");
    expect(rule.style.getPropertyValue("--col-n")).toBe("1");
  });

  it("marks a highlighted cell with the selection pill class", () => {
    const { container } = render(
      <PinnedColumnTable
        rows={rows}
        rowKey={(row) => row.gameId}
        pinnedColumn={{ key: "date", header: "Date", render: (row) => row.date }}
        columns={[
          {
            key: "pts",
            header: "PTS",
            render: (row) => String(row.pts),
            isHighlighted: (row) => row.pts === 32,
          },
        ]}
      />,
    );
    expect(container.querySelector('[class*="selectionPill"]')).toBeTruthy();
  });
});
