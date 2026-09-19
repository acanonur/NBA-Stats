/**
 * One projection drawn as a range: the band, the dot, and the mark it is read against —
 * `ios/NBAStats/DesignSystem/RangeBar.swift`'s `RangeBar`, ported.
 *
 * ```
 *   |-----------------------------------------------|   baseline rule
 *             #################                        band            <- p10...p90
 *                     o                                 dot            <- projection
 *                  |                                    tick           <- reference mark
 * ```
 *
 * **The tick is a projection's own season average, never a market line.** Nothing in this
 * component knows what odds are, and nothing here should ever be asked to learn — see the house
 * rule against gambling vocabulary. The caller supplies the caption that names the tick
 * ({@link ProjectionIntervalBar} does exactly that); this component only draws.
 *
 * The band is drawn with plain percentage-based CSS offsets rather than a `GeometryReader`-style
 * manual width measurement: a CSS percentage is already relative to the element's own box, which
 * is what the Swift original needs `GeometryReader` to get.
 */
import type { CSSProperties } from "react";
import styles from "./RangeBar.module.css";

export interface RangeBarModel {
  /** The band's bounds — typically the 10th and 90th percentile of a published interval. */
  readonly low: number;
  readonly high: number;
  /** The projection, drawn as the dot. Clamped onto the axis when it sits outside the band. */
  readonly projection: number;
  /** The mark, drawn as the tick. `null`/`undefined` draws no tick — the `reference: "none"`
   * case. */
  readonly reference?: number | null;
}

/** A bar can only be drawn when every value is a real number and the band has positive width. */
export function isRangeBarDrawable(model: RangeBarModel): boolean {
  return (
    Number.isFinite(model.low) &&
    Number.isFinite(model.high) &&
    Number.isFinite(model.projection) &&
    model.high > model.low
  );
}

/**
 * The axis a bar is drawn on: the band padded by a tenth of its width on each side, widened
 * further if the reference mark falls outside it — a tick drawn hard against the edge (or worse,
 * clamped invisibly onto it) would hide exactly the case a reader most wants to see.
 */
export function rangeBarAxis(model: RangeBarModel): readonly [number, number] {
  const padding = Math.max((model.high - model.low) * 0.1, 0.5);
  let lower = model.low - padding;
  let upper = model.high + padding;
  if (model.reference !== null && model.reference !== undefined && Number.isFinite(model.reference)) {
    lower = Math.min(lower, model.reference - padding * 0.5);
    upper = Math.max(upper, model.reference + padding * 0.5);
  }
  return [lower, Math.max(upper, lower + 0.001)];
}

/** Where `value` sits on the model's axis, as a fraction in `[0, 1]`. */
export function rangeBarPosition(model: RangeBarModel, value: number): number {
  const [lower, upper] = rangeBarAxis(model);
  const span = upper - lower;
  if (span <= 0 || !Number.isFinite(value)) return 0.5;
  return Math.min(Math.max((value - lower) / span, 0), 1);
}

export type RangeBarVariant = "tile" | "broadsheet";

const VARIANT_STYLE: Readonly<Record<RangeBarVariant, CSSProperties>> = {
  tile: {},
  broadsheet: {
    "--hw-rangebar-rule": "var(--hw-bs-rule)",
    "--hw-rangebar-band": "var(--hw-bs-band)",
    "--hw-rangebar-dot": "var(--hw-bs-text)",
    "--hw-rangebar-page": "var(--hw-bs-background)",
  } as CSSProperties,
};

export interface RangeBarProps {
  readonly model: RangeBarModel;
  /** The tick's colour — cyan-above/magenta-below in the broadsheet handoff, carried over
   * without its betting meaning (WEB_DESIGN.md §7.7's `next_game_projection`/`projection_board`
   * notes: a signed comparison against a reference, not an odds display). */
  readonly accent: string;
  readonly height?: number;
  readonly variant?: RangeBarVariant;
  readonly className?: string;
}

export function RangeBar({
  model,
  accent,
  height = 26,
  variant = "tile",
  className,
}: RangeBarProps): JSX.Element {
  const drawable = isRangeBarDrawable(model);
  const style = { ...VARIANT_STYLE[variant], height } as CSSProperties;

  if (!drawable) {
    // Not drawable is not the same as zero: an empty axis draws the baseline alone rather than a
    // bar pinned at one end, which would read as a real measurement.
    return (
      <div className={className} style={style} aria-hidden>
        <div className={styles.frame} style={{ height }}>
          <div className={styles.baseline} />
        </div>
      </div>
    );
  }

  const lowPct = rangeBarPosition(model, model.low) * 100;
  const highPct = rangeBarPosition(model, model.high) * 100;
  const projectionPct = rangeBarPosition(model, model.projection) * 100;
  const hasReference = model.reference !== null && model.reference !== undefined && Number.isFinite(model.reference);
  const referencePct = hasReference ? rangeBarPosition(model, model.reference) * 100 : null;

  return (
    <div className={className} style={style} aria-hidden>
      <div className={styles.frame} style={{ height }}>
        <div className={styles.baseline} />
        <div
          className={styles.band}
          style={{ left: `${lowPct}%`, width: `max(${highPct - lowPct}%, 2px)` }}
        />
        {referencePct !== null && (
          <div className={styles.tick} style={{ left: `${referencePct}%`, backgroundColor: accent }} />
        )}
        <div className={styles.dot} style={{ left: `${projectionPct}%` }} />
      </div>
    </div>
  );
}
