/**
 * `AvailabilityBadge` with availability `"full"` must render zero DOM nodes — WEB_DESIGN.md
 * §7.4 item 6 and the WP3 "done when" criterion, both asserted directly here rather than only
 * implied by a snapshot.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { AvailabilityBadge } from "../AvailabilityBadge";

afterEach(() => cleanup());

describe("AvailabilityBadge", () => {
  it('renders zero DOM nodes for availability "full"', () => {
    // Rendered into a real container, not merely constructed: a JSX expression alone (calling
    // the component function without React actually reconciling it) would prove nothing about
    // what reaches the DOM. `render` mounts it, and a childless `container` is the only way this
    // assertion can pass — an empty `<span></span>` (the thing WEB_DESIGN.md §7.4 item 6
    // explicitly forbids) would fail both checks below.
    const { container } = render(<AvailabilityBadge availability="full" />);
    expect(container.childNodes.length).toBe(0);
    expect(container.innerHTML).toBe("");
  });

  for (const availability of ["estimated", "partial", "unavailable"] as const) {
    it(`renders a visible marker for "${availability}" and matches its snapshot`, () => {
      const { container } = render(
        <AvailabilityBadge availability={availability} metricName="Usage %" season="1971-72" />,
      );
      expect(container.childNodes.length).toBeGreaterThan(0);
      expect(container.innerHTML).toMatchSnapshot();
    });
  }

  it("opens the explainer dialog on click and closes it again", () => {
    const { getByRole, queryByRole } = render(
      <AvailabilityBadge availability="estimated" metricName="Usage %" season="1971-72" />,
    );
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByRole("button"));
    expect(getByRole("dialog")).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Done" }));
    expect(queryByRole("dialog")).toBeNull();
  });

  it("renders a non-interactive marker (no button, no explainer) when isInteractive is false", () => {
    const { container, queryByRole } = render(
      <AvailabilityBadge availability="unavailable" isInteractive={false} />,
    );
    expect(queryByRole("button")).toBeNull();
    expect(container.textContent).toBe("—");
  });

  it("renders a plain dot instead of a text capsule when showsText is false", () => {
    const { container } = render(<AvailabilityBadge availability="estimated" showsText={false} />);
    expect(container.textContent).toBe("");
  });
});
