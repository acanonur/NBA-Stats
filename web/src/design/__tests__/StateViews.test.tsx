/**
 * Snapshot + behavioural coverage for `StateViews.tsx`'s five tile states.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { LoadingTile, ErrorTile, EmptyTile, UnavailableTile, PendingTile } from "../StateViews";

afterEach(() => cleanup());

describe("LoadingTile", () => {
  for (const size of ["small", "medium", "large"] as const) {
    it(`renders the ${size} skeleton and matches its snapshot`, () => {
      const { container, getByRole } = render(<LoadingTile size={size} />);
      expect(getByRole("status", { name: "Loading" })).toBeTruthy();
      expect(container.innerHTML).toMatchSnapshot();
    });
  }
});

describe("ErrorTile", () => {
  it("shows a retry button only when retryable, and calls onRetry when clicked", () => {
    const onRetry = vi.fn();
    const { getByRole } = render(<ErrorTile message="The ingest source is unreachable." onRetry={onRetry} />);
    fireEvent.click(getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders no retry button when isRetryable is false", () => {
    const { queryByRole } = render(
      <ErrorTile message="No player with that id." isRetryable={false} onRetry={vi.fn()} />,
    );
    expect(queryByRole("button")).toBeNull();
  });
});

describe("EmptyTile", () => {
  it("renders the message accessibly", () => {
    const { getByRole } = render(<EmptyTile message="No games on this date." />);
    expect(getByRole("status", { name: "No games on this date." })).toBeTruthy();
  });
});

describe("UnavailableTile", () => {
  it("shows the em dash and opens the explainer on Why?", () => {
    const { getByText, getByRole, queryByRole } = render(
      <UnavailableTile reason="Steals were not recorded before 1973-74." metricName="Steals" season="1965-66" />,
    );
    expect(getByText("—")).toBeTruthy();
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByRole("button", { name: "Why?" }));
    expect(getByRole("dialog")).toBeTruthy();
  });
});

describe("PendingTile", () => {
  it("reads the iOS-only note", () => {
    const { getByText } = render(<PendingTile />);
    expect(getByText("This widget is on iOS only for now.")).toBeTruthy();
  });
});
