/**
 * A small multiple of grouped bars, one row (or column) per group and one bar per series within
 * it — WEB_DESIGN.md §7.4 item 15, built for `shot_profile`'s two horizontal grouped-bar charts
 * (subject vs. a dimmed league bar, per zone) but generic over any number of series.
 *
 * A `null` value in a group draws **no bar for that series** — not a zero-length one — matching
 * the em-dash rule extended to a chart: `shot_profile`'s pre-1996-97 zones arrive shaped-but-null
 * (CONTRACT.md §4), and a missing zone must read as "no data", never as "this zone was empty".
 */
import type { JSX } from "react";
import { normalizeInDomain } from "./CartesianFrame";

export interface GroupedBarGroup {
  readonly label: string;
  /** One value per entry in `seriesColors`, in the same order. */
  readonly values: readonly (number | null)[];
}

export interface GroupedBarsProps {
  readonly groups: readonly GroupedBarGroup[];
  readonly seriesColors: readonly string[];
  /** `[min, max]` for the shared value axis every bar is drawn against. */
  readonly domain: readonly [number, number];
  readonly width: number;
  readonly height: number;
  readonly orientation?: "horizontal" | "vertical";
  /** A label drawn at the end of each bar (e.g. a formatted percentage). Omit for no labels. */
  readonly valueLabel?: (value: number, seriesIndex: number) => string;
  readonly className?: string;
}

const GROUP_GAP = 6;
const BAR_GAP = 2;
const LABEL_WIDTH = 72;

export function GroupedBars({
  groups,
  seriesColors,
  domain,
  width,
  height,
  orientation = "horizontal",
  valueLabel,
  className,
}: GroupedBarsProps): JSX.Element {
  const seriesCount = Math.max(seriesColors.length, 1);
  const isHorizontal = orientation === "horizontal";
  const trackLength = isHorizontal ? Math.max(width - LABEL_WIDTH, 1) : height;
  const groupExtent = (isHorizontal ? height : width - LABEL_WIDTH) / Math.max(groups.length, 1);
  const barThickness = Math.max((groupExtent - GROUP_GAP - BAR_GAP * (seriesCount - 1)) / seriesCount, 1);

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="presentation"
    >
      {groups.map((group, groupIndex) => {
        const groupStart = groupIndex * groupExtent + GROUP_GAP / 2;
        return (
          <g key={group.label}>
            {isHorizontal && (
              <text
                x={0}
                y={groupStart + groupExtent / 2 - GROUP_GAP / 2}
                fontSize={11}
                dominantBaseline="middle"
                fill="var(--hw-text-secondary)"
              >
                {group.label}
              </text>
            )}
            {group.values.map((value, seriesIndex) => {
              if (value === null || !Number.isFinite(value)) return null;
              const fraction = normalizeInDomain(value, domain);
              const barStart = groupStart + seriesIndex * (barThickness + BAR_GAP);
              const color = seriesColors[seriesIndex] ?? "var(--hw-neutral)";
              const label = valueLabel?.(value, seriesIndex);

              if (isHorizontal) {
                const barLength = Math.max(fraction * trackLength, 1);
                return (
                  <g key={seriesIndex}>
                    <rect
                      x={LABEL_WIDTH}
                      y={barStart}
                      width={barLength}
                      height={barThickness}
                      rx={Math.min(barThickness / 2, 3)}
                      fill={color}
                    />
                    {label && (
                      <text
                        x={LABEL_WIDTH + barLength + 4}
                        y={barStart + barThickness / 2}
                        fontSize={10}
                        dominantBaseline="middle"
                        fill="var(--hw-text-secondary)"
                      >
                        {label}
                      </text>
                    )}
                  </g>
                );
              }

              const barLength = Math.max(fraction * trackLength, 1);
              return (
                <rect
                  key={seriesIndex}
                  x={LABEL_WIDTH + barStart}
                  y={height - barLength}
                  width={barThickness}
                  height={barLength}
                  rx={Math.min(barThickness / 2, 3)}
                  fill={color}
                />
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
