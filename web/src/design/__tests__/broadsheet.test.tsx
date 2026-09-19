/**
 * Coverage for the broadsheet component family: the page shell sets
 * `data-presentation="broadsheet"` (what `theme.css`'s grid rule reads), the leader row draws a
 * dot leader rather than typing literal dots, and the range bar resolves its accent from the
 * broadsheet's own colour tokens.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { BroadsheetPage, BroadsheetKicker } from "../broadsheet/BroadsheetPage";
import { BroadsheetRow, BroadsheetRule } from "../broadsheet/BroadsheetRow";
import { BroadsheetRangeBar } from "../broadsheet/BroadsheetRangeBar";

afterEach(() => cleanup());

describe("BroadsheetPage", () => {
  it('sets data-presentation="broadsheet" on its root and matches its snapshot', () => {
    const { container } = render(
      <BroadsheetPage>
        <BroadsheetKicker>Tonight&apos;s projections</BroadsheetKicker>
      </BroadsheetPage>,
    );
    expect(container.querySelector('[data-presentation="broadsheet"]')).toBeTruthy();
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("BroadsheetRow", () => {
  it("carries one combined accessible label rather than reading dots literally", () => {
    const { getByLabelText, container } = render(<BroadsheetRow label="Projected minutes" value="34.2" />);
    expect(getByLabelText("Projected minutes, 34.2")).toBeTruthy();
    // The leader itself is a drawn element, not a string of "." characters a screen reader
    // would announce one at a time.
    expect(container.textContent).not.toMatch(/\.{3,}/);
  });
});

describe("BroadsheetRule", () => {
  it("renders a heavier rule for a section boundary and a thin one otherwise", () => {
    const { container: thin } = render(<BroadsheetRule />);
    const { container: heavy } = render(<BroadsheetRule heavy />);
    expect(thin.querySelector("hr")?.className).toBe("hw-bs-rule");
    expect(heavy.querySelector("hr")?.className).toBe("hw-bs-rule-heavy");
  });
});

describe("BroadsheetRangeBar", () => {
  it("resolves the above/below/muted accent to the broadsheet's own colour tokens", () => {
    const { container } = render(
      <BroadsheetRangeBar
        model={{ low: 19, high: 38, projection: 28.4, reference: 27.1 }}
        accent="above"
        lowText="19"
        highText="38"
        referenceLabel="season avg"
        referenceText="27.1"
        projectionText="28.4"
      />,
    );
    const tick = container.querySelector('[class*="tick"]') as HTMLElement;
    expect(tick.style.backgroundColor).toBe("var(--hw-bs-accent-above)");
  });
});
