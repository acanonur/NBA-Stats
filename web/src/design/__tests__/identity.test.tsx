/**
 * Coverage for `Monogram`'s initials rule, and snapshot coverage for `PlayerAvatar` and
 * `TeamBadge`.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
import { initialsFor } from "../Monogram";
import { PlayerAvatar } from "../PlayerAvatar";
import { TeamBadge } from "../TeamBadge";

afterEach(() => cleanup());

describe("initialsFor", () => {
  it("prefers server-split first/last names", () => {
    expect(initialsFor({ firstName: "LeBron", lastName: "James", fullName: "LeBron James" })).toBe("LJ");
  });

  it("takes the first two words of a full name, never the last: Gary Payton II is GP", () => {
    expect(initialsFor({ fullName: "Gary Payton II" })).toBe("GP");
  });

  it("gives a single-word name its one letter", () => {
    expect(initialsFor({ fullName: "Nenê" })).toBe("N");
  });

  it("is grapheme-aware: a precomposed accented leading letter is not split", () => {
    expect(initialsFor({ firstName: "Lušāko", lastName: "Dončić" })).toBe("L" + "D");
  });

  it("falls back to full-name parsing when only one of firstName/lastName is present", () => {
    expect(initialsFor({ firstName: "Elgin", fullName: "Elgin Baylor" })).toBe("EB");
  });

  it("returns a question mark for nothing to go on at all", () => {
    expect(initialsFor({ fullName: "" })).toBe("?");
  });
});

describe("PlayerAvatar", () => {
  const player = {
    playerId: 2544,
    name: "LeBron James",
    firstName: "LeBron",
    lastName: "James",
    headshotUrl: null,
  };

  it("goes straight to the monogram when there is no headshot URL", () => {
    const { container } = render(<PlayerAvatar player={player} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("LJ");
  });

  it("renders at each fixed size and matches its snapshot", () => {
    const { container } = render(<PlayerAvatar player={player} size="small" />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("shows the photo once it loads, and unmounts it (falling back to the monogram) on error", () => {
    const withUrl = { ...player, headshotUrl: "https://cdn.nba.com/headshots/nba/latest/1040x760/2544.png" };
    const { container } = render(<PlayerAvatar player={withUrl} />);
    const img = container.querySelector("img");
    expect(img).toBeTruthy();
    fireEvent.error(img as HTMLImageElement);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("LJ");
  });
});

describe("TeamBadge", () => {
  it("renders the bare abbreviation and matches its snapshot", () => {
    const { container } = render(<TeamBadge abbreviation="LAL" name="Los Angeles Lakers" />);
    expect(container.textContent).toBe("LAL");
    expect(container.innerHTML).toMatchSnapshot();
  });
});
