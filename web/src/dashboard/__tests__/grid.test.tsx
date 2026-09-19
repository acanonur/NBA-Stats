/**
 * WEB_DESIGN.md §7.5: a `large` tile spans 2 columns on compact and 4 on regular, so
 * `WidgetContainer` must emit BOTH `--span-c` and `--span-r` on every tile rather than picking
 * one — `design/theme.css`'s media query is what actually swaps between them at 834px. jsdom
 * does not run layout or evaluate `calc()`, so this test asserts the one thing that is under
 * this package's control and is exactly what the bug fix depends on: both span values are
 * present, correct per size, and unconditional — never recomputed from a guessed breakpoint.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WidgetFlowLayout } from "../WidgetFlowLayout";
import { SIZE_SPANS } from "../../generated/tokens";
import type { ResolveWidgetRequest } from "../../api/types";

afterEach(() => cleanup());

const FIXTURE_WIDGETS: readonly ResolveWidgetRequest[] = [
  { id: "a", kind: "stat_tile", size: "small" },
  { id: "b", kind: "player_snapshot", size: "medium" },
  { id: "c", kind: "leaderboard", size: "large" },
];

function renderGrid(): ReturnType<typeof render> {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <WidgetFlowLayout widgets={FIXTURE_WIDGETS} />
    </QueryClientProvider>,
  );
}

function tileStyle(container: HTMLElement, index: number): CSSStyleDeclaration {
  const tiles = container.querySelectorAll(".hw-tile");
  return (tiles[index] as HTMLElement).style;
}

describe("WidgetFlowLayout: the grid emits both spans per tile", () => {
  it("gives every size its documented compact AND regular span, on the same element", () => {
    const { container } = renderGrid();
    (["small", "medium", "large"] as const).forEach((size, index) => {
      const style = tileStyle(container, index);
      expect(style.getPropertyValue("--span-c")).toBe(String(SIZE_SPANS[size].compact));
      expect(style.getPropertyValue("--span-r")).toBe(String(SIZE_SPANS[size].regular));
    });
  });

  it("a large tile is exactly the fix WEB_DESIGN.md §0.1-E describes: 2 compact, 4 regular", () => {
    expect(SIZE_SPANS.large).toEqual({ compact: 2, regular: 4 });
    const { container } = renderGrid();
    const largeTileStyle = tileStyle(container, 2);
    expect(largeTileStyle.getPropertyValue("--span-c")).toBe("2");
    expect(largeTileStyle.getPropertyValue("--span-r")).toBe("4");
  });

  it("matches the compact-breakpoint span snapshot (what --span-c resolves to on every tile)", () => {
    const { container } = renderGrid();
    const compactSpans = Array.from(container.querySelectorAll(".hw-tile")).map((tile) =>
      (tile as HTMLElement).style.getPropertyValue("--span-c"),
    );
    expect(compactSpans).toMatchInlineSnapshot(`
      [
        "1",
        "2",
        "2",
      ]
    `);
  });

  it("matches the regular-breakpoint span snapshot (what --span-r resolves to on every tile)", () => {
    const { container } = renderGrid();
    const regularSpans = Array.from(container.querySelectorAll(".hw-tile")).map((tile) =>
      (tile as HTMLElement).style.getPropertyValue("--span-r"),
    );
    expect(regularSpans).toMatchInlineSnapshot(`
      [
        "1",
        "2",
        "4",
      ]
    `);
  });

  it("renders widgets in document order without rearranging to fill a gap", () => {
    const { container } = renderGrid();
    const order = Array.from(container.querySelectorAll(".hw-tile")).map((tile) =>
      tile.getAttribute("data-widget-id"),
    );
    expect(order).toEqual(["a", "b", "c"]);
  });
});
