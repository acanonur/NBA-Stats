/**
 * One four-factors bar: a value inside a fixed domain, with the league average marked as a
 * separate tick — `ios/NBAStats/DesignSystem/PercentileBar.swift`'s `FactorBar`, ported.
 *
 * The league tick is a vertical rule in `--hw-text-primary`, not a second hue, so the comparison
 * survives both a colourblind reader and a greyscale screenshot. A `null` value draws a **dashed
 * empty track**, never a zero-length bar — the em-dash rule, extended to a bar chart: a bar with
 * no length reads as "the worst possible value ever recorded", which is not what a missing
 * number means.
 */
import type { CSSProperties } from "react";
import clsx from "clsx";
import { Text } from "./Text";
import { formatValue } from "./format";
import styles from "./FactorBar.module.css";

export interface FactorBarProps {
  readonly value: number | null;
  readonly leagueAverage?: number | null;
  /** `[min, max]` — the family's fixed domain (e.g. eFG% at `[0.45, 0.62]`), never derived from
   * the data itself: a four-factors bar's whole point is comparability across rows, which a
   * per-row autoscaled domain would defeat. */
  readonly domain: readonly [number, number];
  readonly tint: string;
  readonly label?: string | null;
  readonly valueText?: string | null;
  /** Draws the "vertical rule marks the league average" caption below the bar. Set on only one
   * bar in a stack of them to avoid repeating it. */
  readonly showsLegend?: boolean;
  readonly barHeight?: number;
}

function fractionOf(raw: number | null | undefined, domain: readonly [number, number]): number | null {
  if (raw === null || raw === undefined || !Number.isFinite(raw)) return null;
  const span = domain[1] - domain[0];
  if (span <= 1e-12) return null;
  return Math.min(Math.max((raw - domain[0]) / span, 0), 1);
}

export function FactorBar({
  value,
  leagueAverage,
  domain,
  tint,
  label,
  valueText,
  showsLegend = true,
  barHeight = 10,
}: FactorBarProps): JSX.Element {
  const filled = fractionOf(value, domain);
  const average = fractionOf(leagueAverage, domain);
  const resolvedValueText = valueText ?? formatValue(value, "decimal2");

  const accessibleParts: string[] = [];
  if (label) accessibleParts.push(label);
  accessibleParts.push(resolvedValueText);
  if (leagueAverage !== null && leagueAverage !== undefined && value !== null) {
    const leagueText = formatValue(leagueAverage, "decimal2");
    accessibleParts.push(
      value >= leagueAverage ? `above the league average of ${leagueText}` : `below the league average of ${leagueText}`,
    );
  }

  return (
    <div className={styles.root} role="img" aria-label={accessibleParts.join(", ")}>
      {(label || valueText) && (
        <div className={styles.header}>
          {label && <Text style="statLabel">{label}</Text>}
          <span className={styles.headerSpacer} />
          {valueText && (
            <Text style="statValue" tabularNums>
              {valueText}
            </Text>
          )}
        </div>
      )}
      <div className={styles.track} style={{ height: barHeight + 6 }}>
        {filled === null ? (
          <div className={styles.trackEmpty} style={{ height: barHeight }} />
        ) : (
          <>
            <div className={styles.trackFill} style={{ height: barHeight }} />
            <div
              className={clsx(styles.fill)}
              style={
                {
                  height: barHeight,
                  width: `max(${filled * 100}%, ${barHeight}px)`,
                  "--hw-bar-color": tint,
                } as CSSProperties
              }
            />
          </>
        )}
        {average !== null && (
          <div
            className={styles.averageTick}
            style={{ left: `${average * 100}%`, height: barHeight + 6 }}
            aria-hidden
          />
        )}
      </div>
      {showsLegend && leagueAverage !== null && leagueAverage !== undefined && (
        <Text as="div" style="caption" className={styles.legend} ariaHidden>
          Vertical rule marks the league average
        </Text>
      )}
    </div>
  );
}
