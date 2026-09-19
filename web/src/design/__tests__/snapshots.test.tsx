/**
 * The remaining direct, standalone snapshots — WP3's "done when" criterion is that *every*
 * primitive has one. A component whose markup is already captured inside another primitive's
 * composed snapshot (e.g. `RankChip`/`DeltaChip` inside `StatValue.test.tsx`, `Monogram` inside
 * `PlayerAvatar`'s) still gets its own direct snapshot here, so a reviewer can find each
 * primitive's baseline without having to know which composite happens to render it.
 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { Text } from "../Text";
import { RankChip } from "../RankChip";
import { DeltaChip } from "../DeltaChip";
import { Monogram } from "../Monogram";
import { TileSurface } from "../TileSurface";
import { ProjectionIntervalBar } from "../ProjectionIntervalBar";
import { GroupedBars } from "../svg/GroupedBars";
import { ErrorTile, EmptyTile, UnavailableTile, PendingTile } from "../StateViews";
import { BroadsheetKicker } from "../broadsheet/BroadsheetPage";
import { BroadsheetRow } from "../broadsheet/BroadsheetRow";
import { BroadsheetRangeBar } from "../broadsheet/BroadsheetRangeBar";

afterEach(() => cleanup());

it("Text matches its snapshot", () => {
  const { container } = render(
    <Text style="statValue" color="positive" tabularNums>
      +7.8
    </Text>,
  );
  expect(container.innerHTML).toMatchSnapshot();
});

it("RankChip matches its snapshot", () => {
  const { container } = render(<RankChip rank={12} outOf={482} />);
  expect(container.innerHTML).toMatchSnapshot();
});

it("DeltaChip matches its snapshot", () => {
  const { container } = render(<DeltaChip delta={0.048} higherIsBetter format="percent1" />);
  expect(container.innerHTML).toMatchSnapshot();
});

it("Monogram matches its snapshot", () => {
  const { container } = render(<Monogram initials="LJ" color="var(--hw-mono-0)" diameter={44} />);
  expect(container.innerHTML).toMatchSnapshot();
});

it("TileSurface matches its snapshot", () => {
  const { container } = render(<TileSurface size="small">content</TileSurface>);
  expect(container.innerHTML).toMatchSnapshot();
});

it("ProjectionIntervalBar matches its snapshot", () => {
  const { container } = render(
    <ProjectionIntervalBar
      model={{ low: 19, high: 38, projection: 28.4, reference: 27.1 }}
      accent="var(--hw-bs-accent-above)"
      lowText="19"
      highText="38"
      referenceLabel="season avg"
      referenceText="27.1"
      projectionText="28.4"
    />,
  );
  expect(container.innerHTML).toMatchSnapshot();
});

it("GroupedBars matches its snapshot", () => {
  const { container } = render(
    <GroupedBars
      groups={[
        { label: "Rim", values: [0.68, 0.62] },
        { label: "Mid", values: [0.41, 0.44] },
      ]}
      seriesColors={["var(--hw-chart-0)", "var(--hw-chart-1)"]}
      domain={[0, 1]}
      width={220}
      height={80}
      valueLabel={(value) => `${Math.round(value * 100)}%`}
    />,
  );
  expect(container.innerHTML).toMatchSnapshot();
});

describe("StateViews' remaining tiles", () => {
  it("ErrorTile matches its snapshot", () => {
    const { container } = render(<ErrorTile message="The ingest source is unreachable." onRetry={() => {}} />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("EmptyTile matches its snapshot", () => {
    const { container } = render(<EmptyTile message="No games on this date." />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("UnavailableTile matches its snapshot", () => {
    const { container } = render(<UnavailableTile reason="Steals were not recorded before 1973-74." />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("PendingTile matches its snapshot", () => {
    const { container } = render(<PendingTile />);
    expect(container.innerHTML).toMatchSnapshot();
  });
});

describe("Broadsheet furniture", () => {
  it("BroadsheetKicker matches its snapshot", () => {
    const { container } = render(<BroadsheetKicker>Tonight&apos;s projections</BroadsheetKicker>);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("BroadsheetRow matches its snapshot", () => {
    const { container } = render(<BroadsheetRow label="Pace" value="+0.6" valueColor="above" />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("BroadsheetRangeBar matches its snapshot", () => {
    const { container } = render(
      <BroadsheetRangeBar
        model={{ low: 4, high: 12, projection: 6.9, reference: 7.8 }}
        accent="below"
        lowText="4"
        highText="12"
        referenceLabel="season avg"
        referenceText="7.8"
        projectionText="6.9"
      />,
    );
    expect(container.innerHTML).toMatchSnapshot();
  });
});
