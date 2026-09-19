/**
 * `next_game_projection` — a projected box score, rendered as an argument rather than an oracle.
 *
 * Ported from `ios/NBAStats/Widgets/NextGameProjectionWidget.swift`. The five honesty rules in
 * `docs/PROJECTION.md` §7 are the whole design of this view:
 *
 * 1. No mean is drawn without its interval. Every stat line carries its low–high beside the
 *    mean, in the same visual weight, and a line that arrived without one says so instead of
 *    quietly showing a bare number.
 * 2. Projected minutes get their own bordered block above the stat lines, with their own
 *    interval bar.
 * 3. The context factors are bars centred on 1.0 (±0.02, widened as needed), each with its own
 *    prose explanation.
 * 4. A rate that leans on the league prior, or one with thin exposure behind it, is flagged on
 *    its own line.
 * 5. Every projected value is `availability: "estimated"`, clamped in `decode.ts` even if a
 *    future server build ever says otherwise — a projection is never a record. The clamp is at
 *    the decode boundary, not here, so nothing downstream of it can see another value.
 *
 * Nothing here translates to a market — no prices, no implied probabilities, nothing anyone
 * could put money behind — and there is no place to put it.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type {
  MetricDescriptor,
  NextGameProjectionCombo,
  NextGameProjectionFactor,
  NextGameProjectionGame,
  NextGameProjectionLine,
  NextGameProjectionPayload,
} from "../../api/types";
import type { MetricFormat } from "../../generated/contracts";
import type { WidgetSizeKey } from "../../generated/tokens";
import { Text } from "../../design/Text";
import { PlayerAvatar } from "../../design/PlayerAvatar";
import { TeamBadge } from "../../design/TeamBadge";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { DeltaChip } from "../../design/DeltaChip";
import { FactorBar } from "../../design/FactorBar";
import { ProjectionIntervalBar } from "../../design/ProjectionIntervalBar";
import type { RangeBarModel } from "../../design/RangeBar";
import { ErrorTile } from "../../design/StateViews";
import { EM_DASH, formatDecimal, formatMagnitude, formatPercent, formatValue, mediumGameDate } from "../../design/format";
import { decodeNextGameProjectionPayload } from "./decode";
import styles from "./index.module.css";

const VISIBLE_LINE_COUNT: Readonly<Record<WidgetSizeKey, number>> = { small: 2, medium: 4, large: 99 };

const MINUTES_RATIONALE =
  "Minutes carry most of the error: supplying true minutes cuts points error by about 19%, far " +
  "more than any change to the production model. Disagree with this number and everything below " +
  "it moves too.";

function hasNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

function matchupText(game: NextGameProjectionGame): string {
  return `${game.isHome ? "vs" : "@"} ${game.opponentAbbr}`;
}

function restText(game: NextGameProjectionGame): string | null {
  if (game.isBackToBack) return "back-to-back";
  if (!hasNumber(game.restDays)) return null;
  if (game.restDays === 0) return "no rest";
  return game.restDays === 1 ? "1 day rest" : `${game.restDays} days rest`;
}

function gameLineText(game: NextGameProjectionGame): string {
  const parts = [matchupText(game), mediumGameDate(game.date)];
  const rest = restText(game);
  if (rest) parts.push(rest);
  return parts.join(" · ");
}

function opponentInputsText(game: NextGameProjectionGame): string | null {
  const parts: string[] = [];
  if (hasNumber(game.opponentDefRtg)) {
    parts.push(`${game.opponentAbbr} defensive rating ${formatDecimal(game.opponentDefRtg, 1)}`);
  }
  if (hasNumber(game.expectedPace)) parts.push(`expected pace ${formatDecimal(game.expectedPace, 1)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function minutesModel(minutes: NextGameProjectionPayload["projectedMinutes"]): RangeBarModel {
  return {
    low: minutes.low ?? NaN,
    high: minutes.high ?? NaN,
    projection: minutes.value ?? NaN,
    reference: minutes.seasonAverage,
  };
}

function lineModel(line: NextGameProjectionLine): RangeBarModel {
  return { low: line.low ?? NaN, high: line.high ?? NaN, projection: line.mean, reference: line.seasonAverage };
}

/** A rate is "thin" when the exposure behind it has not reached its own stabilisation constant
 * (or, absent one, 200 minutes) — `NextGameProjectionWidget.swift`'s `isThin`, minus the field
 * this payload does not carry (`leansOnLeaguePrior`). */
function isThinLine(line: NextGameProjectionLine): boolean {
  if (!hasNumber(line.exposureMinutes)) return false;
  if (hasNumber(line.shrinkageK) && line.shrinkageK > 0) return line.exposureMinutes < line.shrinkageK;
  return line.exposureMinutes < 200;
}

function cautionText(line: NextGameProjectionLine, intervalsMissing: boolean): string | null {
  const parts: string[] = [];
  if ((line.low === null || line.high === null) && !intervalsMissing) {
    parts.push("No interval on this line, so the mean alone overstates what is known");
  }
  if (isThinLine(line)) {
    if (hasNumber(line.shrinkageWeight)) {
      const share = Math.round(Math.min(Math.max(line.shrinkageWeight, 0), 1) * 100);
      parts.push(`Only ${share}% of this rate is the player; the rest is the league average`);
    } else {
      parts.push("Thin history behind this rate, so it leans on the league average");
    }
    if (hasNumber(line.exposureMinutes)) parts.push(`${formatDecimal(line.exposureMinutes, 0)} min of exposure`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

function diagnosticsText(line: NextGameProjectionLine): string | null {
  const thin = isThinLine(line);
  const parts: string[] = [];
  if (hasNumber(line.ratePerMinute)) parts.push(`${formatDecimal(line.ratePerMinute, 3)} per minute`);
  if (hasNumber(line.shrinkageWeight) && !thin) {
    parts.push(`${Math.round(Math.min(Math.max(line.shrinkageWeight, 0), 1) * 100)}% player, rest league prior`);
  }
  if (hasNumber(line.shrinkageK)) parts.push(`stabilises at ${formatDecimal(line.shrinkageK, 0)} min`);
  if (hasNumber(line.exposureMinutes) && !thin) parts.push(`${formatDecimal(line.exposureMinutes, 0)} min of exposure`);
  if (hasNumber(line.halfLifeGames)) parts.push(`${line.halfLifeGames}-game half-life`);
  if (hasNumber(line.dispersionMultiplier)) parts.push(`spread multiplier ${formatDecimal(line.dispersionMultiplier, 2)}`);
  if (hasNumber(line.dispersionAlpha)) parts.push(`dispersion ${formatDecimal(line.dispersionAlpha, 3)}`);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function boundText(value: number | null, format: MetricFormat): string {
  if (value === null) return EM_DASH;
  return formatValue(value, format);
}

function lineAccessibleText(line: NextGameProjectionLine, intervalsMissing: boolean): string {
  const descriptor = line.descriptor;
  const format = descriptor.format as MetricFormat;
  const parts: string[] = [descriptor.name, `projected ${line.displayValue.length > 0 ? line.displayValue : EM_DASH}`];
  if (line.low !== null && line.high !== null) {
    const level = hasNumber(line.intervalLevel) ? `${Math.round(line.intervalLevel * 100)}% interval` : "range";
    parts.push(`${level} from ${boundText(line.low, format)} to ${boundText(line.high, format)}`);
  } else {
    parts.push("no interval given");
  }
  if (hasNumber(line.delta) && Math.abs(line.delta) > 1e-9) {
    const magnitude = formatMagnitude(line.delta, format);
    const direction = line.delta > 0 ? "up" : "down";
    if (hasNumber(line.seasonAverage)) {
      parts.push(`${direction} ${magnitude} against a season average of ${formatValue(line.seasonAverage, format)}`);
    } else {
      parts.push(`${direction} ${magnitude} against the season average`);
    }
  }
  parts.push("estimated, not a record");
  const caution = cautionText(line, intervalsMissing);
  if (caution) parts.push(caution);
  return parts.join(", ");
}

function factorEffectText(value: number): string {
  const diff = value - 1;
  if (Math.abs(diff) < 0.0005) return "no effect";
  // `formatPercent` owns the ×100 and the "%" suffix for the whole app (design/format.ts) — the
  // sign is applied here rather than by `formatSigned` because a factor's *magnitude* is what is
  // being described, and a negative multiplier is not a thing this payload can carry.
  return `${diff > 0 ? "+" : "-"}${formatPercent(Math.abs(diff), 1)}`;
}

function factorDomain(value: number): readonly [number, number] {
  const low = Math.min(0.9, value - 0.02);
  const high = Math.max(1.1, value + 0.02);
  return high > low ? [low, high] : [0.9, 1.1];
}

function comboSpreadText(combo: NextGameProjectionCombo): string | null {
  if (!hasNumber(combo.sd)) return null;
  const correlated = formatDecimal(combo.sd, 2);
  if (!hasNumber(combo.sdIfIndependent)) return `Spread (SD) ${correlated}, using the residual correlation.`;
  return `Spread (SD) ${correlated} with the residual correlation · ${formatDecimal(combo.sdIfIndependent, 2)} if the parts were independent.`;
}

function comboGapText(combo: NextGameProjectionCombo): string | null {
  if (!hasNumber(combo.inflation)) return null;
  if (combo.inflation <= 0.0005) return "Here the correlated spread is no wider than the independent one.";
  return `Ignoring how these move together would understate the spread by ${Math.round(combo.inflation * 100)}%.`;
}

function LineHonesty({ text, tone }: { readonly text: string; readonly tone: "warning" | "secondary" }): JSX.Element {
  return (
    <Text as="p" style="caption" color={tone} className={styles.cautionLine}>
      {text}
    </Text>
  );
}

function GameLine({ game }: { readonly game: NextGameProjectionGame | null }): JSX.Element {
  if (!game) {
    return (
      <Text as="p" style="caption" color="warning" className={styles.gameWarning}>
        No scheduled next game. These numbers are projected against a league-average opponent, at
        a neutral venue, on normal rest.
      </Text>
    );
  }
  const inputs = opponentInputsText(game);
  return (
    <div className={styles.gameLine}>
      <div className={styles.gameLineRow}>
        <TeamBadge abbreviation={game.opponentAbbr} name={game.opponent.name} size="small" />
        <Text style="statLabel" tabularNums>
          {gameLineText(game)}
        </Text>
      </div>
      {inputs && (
        <Text as="p" style="caption" tabularNums color="secondary">
          {inputs}
        </Text>
      )}
    </div>
  );
}

function MinutesBlock({ minutes }: { readonly minutes: NextGameProjectionPayload["projectedMinutes"] }): JSX.Element {
  const displayText = minutes.displayValue.length > 0 && minutes.displayValue !== EM_DASH
    ? minutes.displayValue
    : formatDecimal(minutes.value, 1);
  const hasInterval = minutes.low !== null && minutes.high !== null;
  const model = minutesModel(minutes);
  const comparisonText = hasNumber(minutes.seasonAverage)
    ? hasNumber(minutes.value) && Math.abs(minutes.value - minutes.seasonAverage) >= 0.05
      ? `${formatDecimal(minutes.value - minutes.seasonAverage, 1, true)} vs season average ${formatDecimal(minutes.seasonAverage, 1)}`
      : `Season average ${formatDecimal(minutes.seasonAverage, 1)}`
    : null;

  return (
    <div className={styles.minutesBlock}>
      <div className={styles.minutesHeader}>
        <Text style="statLabel">Projected minutes</Text>
        {comparisonText && (
          <Text style="caption" tabularNums color="secondary" className={styles.minutesComparison}>
            {comparisonText}
          </Text>
        )}
      </div>
      <span className={styles.minutesHero}>
        <Text style="displayValue" tabularNums>
          {displayText}
        </Text>
        <AvailabilityBadge availability="estimated" showsText isInteractive={false} metricName="Projected minutes" />
      </span>
      {(hasInterval || hasNumber(minutes.value)) && (
        <ProjectionIntervalBar
          model={model}
          accent="var(--hw-selection)"
          lowText={boundText(minutes.low, "decimal1")}
          highText={boundText(minutes.high, "decimal1")}
          referenceLabel={hasNumber(minutes.seasonAverage) ? "season avg" : null}
          referenceText={hasNumber(minutes.seasonAverage) ? formatDecimal(minutes.seasonAverage, 1) : null}
          projectionText={displayText}
        />
      )}
      <Text as="p" style="caption" color="secondary" className={styles.minutesRationale}>
        {MINUTES_RATIONALE}
      </Text>
    </div>
  );
}

function StatLine({
  line,
  intervalsMissing,
  showsDiagnostics,
}: {
  readonly line: NextGameProjectionLine;
  readonly intervalsMissing: boolean;
  readonly showsDiagnostics: boolean;
}): JSX.Element {
  const descriptor: MetricDescriptor = line.descriptor;
  const format = descriptor.format as MetricFormat;
  const caution = cautionText(line, intervalsMissing);
  const diagnostics = showsDiagnostics ? diagnosticsText(line) : null;
  const hasInterval = line.low !== null && line.high !== null;

  return (
    <div className={styles.lineRow} role="group" aria-label={lineAccessibleText(line, intervalsMissing)}>
      <div className={styles.lineHead}>
        <Text style="statLabel" className={styles.lineLabel} ariaHidden>
          {descriptor.shortName}
        </Text>
        <span className={styles.lineValue} aria-hidden>
          <Text style="statValue" tabularNums>
            {line.displayValue.length > 0 ? line.displayValue : EM_DASH}
          </Text>
          <AvailabilityBadge availability={line.availability} showsText={false} isInteractive={false} metricName={descriptor.name} />
        </span>
        <span aria-hidden>
          <DeltaChip delta={line.delta} higherIsBetter={descriptor.higherIsBetter} format={format} />
        </span>
      </div>
      {(hasInterval || !intervalsMissing) && (
        <ProjectionIntervalBar
          model={lineModel(line)}
          accent="var(--hw-selection)"
          lowText={boundText(line.low, format)}
          highText={boundText(line.high, format)}
          referenceLabel={hasNumber(line.seasonAverage) ? "season avg" : null}
          referenceText={hasNumber(line.seasonAverage) ? formatValue(line.seasonAverage, format) : null}
          projectionText={line.displayValue.length > 0 ? line.displayValue : EM_DASH}
        />
      )}
      {caution && <LineHonesty text={caution} tone="warning" />}
      {diagnostics && <LineHonesty text={diagnostics} tone="secondary" />}
    </div>
  );
}

function ComboBlock({ combo, correlationApplied }: { readonly combo: NextGameProjectionCombo; readonly correlationApplied: boolean }): JSX.Element {
  const spread = comboSpreadText(combo);
  const gap = comboGapText(combo);
  return (
    <div className={styles.comboBlock}>
      <div className={styles.comboRow}>
        <Text style="statLabel">{combo.label.length > 0 ? combo.label : "Combined"}</Text>
        <Text style="statValue" tabularNums>
          {formatDecimal(combo.mean, 1)}
        </Text>
        <AvailabilityBadge availability="estimated" showsText={false} isInteractive={false} metricName={combo.label} />
        {combo.low !== null && combo.high !== null && (
          <Text style="tableCell" tabularNums>
            {`${formatDecimal(combo.low, 0)}–${formatDecimal(combo.high, 0)}`}
          </Text>
        )}
      </div>
      {spread && (
        <Text as="p" style="caption" tabularNums color="secondary">
          {spread}
        </Text>
      )}
      {gap && (
        <Text as="p" style="caption" color="secondary">
          {gap}
        </Text>
      )}
      {!correlationApplied && (
        <LineHonesty text="This combined range was built without the residual correlation, so the spread may be understated." tone="warning" />
      )}
    </div>
  );
}

function FactorRow({ factor, showsExplanation }: { readonly factor: NextGameProjectionFactor; readonly showsExplanation: boolean }): JSX.Element {
  return (
    <div className={styles.factorRow}>
      <FactorBar
        value={factor.value}
        leagueAverage={1}
        domain={factorDomain(factor.value)}
        tint="var(--hw-selection)"
        label={factor.label}
        valueText={`${formatDecimal(factor.value, 3)} · ${factorEffectText(factor.value)}`}
        showsLegend={false}
        barHeight={8}
      />
      {showsExplanation && factor.explanation.length > 0 && (
        <Text as="p" style="caption" color="secondary" className={styles.factorExplanation}>
          {factor.explanation}
        </Text>
      )}
    </div>
  );
}

export default function NextGameProjectionWidget({ size, payload }: WidgetViewProps): JSX.Element {
  let data: NextGameProjectionPayload;
  try {
    data = decodeNextGameProjectionPayload(payload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This projection's data did not match what the app expected." />;
  }

  const visibleLimit = VISIBLE_LINE_COUNT[size] ?? VISIBLE_LINE_COUNT.medium;
  const visibleLines = data.lines.slice(0, Math.max(visibleLimit, 0));
  const hiddenCount = data.lines.length - visibleLines.length;
  const showsDiagnostics = size === "large";
  const showsCombo = size !== "small";
  const showsFactorBars = size !== "small";

  const intervalsMissing = data.lines.length > 0 && data.lines.every((line) => line.low === null || line.high === null);

  const exposureValues = data.lines.map((line) => line.exposureMinutes).filter(hasNumber);
  const exposureText =
    exposureValues.length > 0
      ? (() => {
          const low = Math.min(...exposureValues);
          const high = Math.max(...exposureValues);
          const body = Math.abs(high - low) < 0.5 ? formatDecimal(high, 0) : `${formatDecimal(low, 0)}–${formatDecimal(high, 0)}`;
          return `${body} minutes of this player's own history sit behind these rates.`;
        })()
      : null;

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        {data.player && (
          <div className={styles.subjectRow}>
            <PlayerAvatar player={data.player} size={size === "large" ? "medium" : "small"} />
            <Text style="widgetTitle" truncate>
              {data.player.name}
            </Text>
          </div>
        )}
        <GameLine game={data.game} />
      </div>

      <MinutesBlock minutes={data.projectedMinutes} />

      {visibleLines.length === 0 ? (
        <Text as="p" style="caption" color="secondary">
          This projection carries no statistic lines.
        </Text>
      ) : (
        <div className={styles.linesSection}>
          {intervalsMissing && (
            <LineHonesty
              text="This projection arrived without intervals. A mean on its own is the one number the model is least willing to stand behind."
              tone="warning"
            />
          )}
          {visibleLines.map((line) => (
            <StatLine key={line.metric} line={line} intervalsMissing={intervalsMissing} showsDiagnostics={showsDiagnostics} />
          ))}
          {hiddenCount > 0 && (
            <Text as="p" style="caption" color="secondary">
              {hiddenCount === 1 ? "One more projected stat fits at the large size." : `${hiddenCount} more projected stats fit at the large size.`}
            </Text>
          )}
          {exposureText && (
            <Text as="p" style="caption" tabularNums color="secondary">
              {exposureText}
            </Text>
          )}
        </div>
      )}

      {showsCombo && data.combo && <ComboBlock combo={data.combo} correlationApplied={data.method.correlationApplied} />}

      {data.factors.length > 0 && (
        <div className={styles.factorsBlock}>
          <Text style="statLabel">Context factors</Text>
          {showsFactorBars ? (
            <>
              {data.factors.map((factor) => (
                <FactorRow key={factor.key} factor={factor} showsExplanation={showsDiagnostics} />
              ))}
              <Text as="p" style="caption" color="secondary" ariaHidden>
                Each factor multiplies the projection; the vertical rule is 1.00, no effect either way.
              </Text>
            </>
          ) : (
            <Text as="p" style="caption" tabularNums color="secondary">
              {data.factors.map((factor) => `${factor.label} ${factorEffectText(factor.value)}`).join(" · ")}
            </Text>
          )}
        </div>
      )}

      {data.notes.length > 0 && (
        <ul className={styles.notesList}>
          {data.notes.map((note) => (
            <li key={note} className={styles.noteRow}>
              <Text style="caption" color="warning" ariaHidden>
                {"•"}
              </Text>
              <Text as="p" style="caption" color="secondary">
                {note}
              </Text>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
