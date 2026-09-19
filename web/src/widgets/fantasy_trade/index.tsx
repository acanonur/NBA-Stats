/**
 * `fantasy_trade` — what a trade gains you, what it costs you, and how much of that survives
 * being wrong. Ported from `ios/NBAStats/Widgets/FantasyTradeWidget.swift`.
 *
 * Three decisions carried over verbatim:
 *
 * 1. **The roster-spot adjustment is its own line**, never folded into the net — a freed slot
 *    refills from waivers at below pool average, routinely a bigger number than the players
 *    themselves, and a reader shown only the net cannot tell a bad trade from slot arithmetic.
 * 2. **The sensitivity block is a set of four named scenarios, never a statistical interval.**
 *    There is no probability in it, so it is rendered as a labelled list of numbers — never a
 *    `RangeBar`/`ProjectionIntervalBar`, which would visually claim a distribution nothing here
 *    computed. `"A sweep over four named assumptions, not a probability."` renders every time
 *    the block does, per CONTRACT.md §7.7.
 * 3. **Turnovers read in the direction they are scored.** The server already sign-corrects `net`
 *    (a high zTOV is *few* turnovers), so a positive `net` is coloured as an improvement for
 *    every one of the nine categories without exception.
 *
 * `points` is keyed `espn_points` / `yahoo_points` — snake_case, the one documented exception to
 * this codebase's camelCase convention (CONTRACT.md §7.7) — and is read by those exact keys.
 */
import type { JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { FantasyTradeCategoryDelta, FantasyTradePayload, FantasyTradeSide } from "../../api/types";
import { Text } from "../../design/Text";
import type { TextColorToken } from "../../design/Text";
import { ErrorTile } from "../../design/StateViews";
import { comparisonOutcome } from "../../design/valueColor";
import { EM_DASH, formatDecimal, formatSigned } from "../../design/format";
import { decodeFantasyTradePayload } from "./decode";
import styles from "./index.module.css";

const CATEGORY_ABBR: Readonly<Record<string, string>> = {
  pts: "PTS",
  fg3m: "3PM",
  reb: "REB",
  ast: "AST",
  stl: "STL",
  blk: "BLK",
  tov: "TOV",
  fg_pct: "FG%",
  ft_pct: "FT%",
};

const CATEGORY_NAME: Readonly<Record<string, string>> = {
  pts: "points",
  fg3m: "threes",
  reb: "rebounds",
  ast: "assists",
  stl: "steals",
  blk: "blocks",
  tov: "turnovers",
  fg_pct: "FG%",
  ft_pct: "FT%",
};

const POINTS_SYSTEM_LABEL: Readonly<Record<string, string>> = {
  espn_points: "ESPN Points",
  yahoo_points: "Yahoo Points",
};

function hasNumber(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && Number.isFinite(value);
}

function categoryAbbr(category: string): string {
  return CATEGORY_ABBR[category] ?? category.toUpperCase();
}

function categoryName(category: string): string {
  return CATEGORY_NAME[category] ?? category;
}

/** A `net`/`change` value here is already sign-corrected by the server (a high zTOV is *few*
 * turnovers), so colouring it is exactly {@link comparisonOutcome} with a fixed, always-true
 * `higherIsBetter` — never a second, hand-rolled sign check. */
function signColor(value: number | null | undefined): TextColorToken {
  return comparisonOutcome(value, true);
}

function isEmpty(payload: FantasyTradePayload): boolean {
  return payload.give.count === 0 && payload.get.count === 0;
}

function verdictColor(payload: FantasyTradePayload): TextColorToken {
  if (!hasNumber(payload.netZ)) return "neutral";
  if (Math.abs(payload.netZ) < payload.bands.fair) return "neutral";
  return payload.netZ > 0 ? "positive" : "negative";
}

/** `"clear win"` becomes `"Clear Win"` — Swift's `String.capitalized` (`payload.verdict
 * .capitalized` in `FantasyTradeWidget.swift`) title-cases every word, not only the first. */
function titleCase(text: string): string {
  return text.replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function rosterDominates(payload: FantasyTradePayload): boolean {
  if (!hasNumber(payload.rosterAdjustment) || !hasNumber(payload.changeZ)) return false;
  return Math.abs(payload.rosterAdjustment) > Math.abs(payload.changeZ);
}

function SideColumn({ title, side }: { readonly title: string; readonly side: FantasyTradeSide }): JSX.Element {
  return (
    <div className={styles.sideColumn}>
      <Text style="tableHeader">{title}</Text>
      {side.players.length === 0 ? (
        <Text style="caption" color="secondary">
          {EM_DASH}
        </Text>
      ) : (
        <ul className={styles.playerList}>
          {side.players.map((entry) => (
            <li key={entry.player.playerId} className={styles.playerRow}>
              <Text style="tableCell" truncate className={styles.playerName}>
                {entry.player.name}
              </Text>
              <Text style="caption" tabularNums color="secondary">
                {formatSigned(entry.totalZ, "decimal1", true)}
              </Text>
            </li>
          ))}
        </ul>
      )}
      <Text style="caption" tabularNums color="secondary" className={styles.sideTotal}>
        {`total ${formatSigned(side.totalZ, "decimal2", true)}`}
      </Text>
    </div>
  );
}

function CategoryTable({ categories }: { readonly categories: readonly FantasyTradeCategoryDelta[] }): JSX.Element {
  return (
    <div className={styles.categoryTable} role="group" aria-label="Category change, give for get">
      {categories.map((entry) => (
        <div
          key={entry.category}
          className={styles.categoryCell}
          role="group"
          aria-label={`${categoryName(entry.category)} ${formatSigned(entry.net, "decimal2", true)}${entry.punted ? ", punted" : ""}`}
        >
          <Text style="tableHeader" color="tertiary" ariaHidden>
            {categoryAbbr(entry.category)}
          </Text>
          <Text
            style="tableCell"
            tabularNums
            ariaHidden
            color={entry.punted ? "tertiary" : signColor(entry.net)}
            className={entry.punted ? styles.punted : undefined}
          >
            {formatSigned(entry.net, "decimal2", true)}
          </Text>
        </div>
      ))}
    </div>
  );
}

/** `0` only for the purpose of ranking by magnitude — an explicit check rather than `net ?? 0`,
 * which the nullable-zero-fallback lint rule cannot tell apart from rendering a real zero (see
 * `StatValue.tsx`'s own docstring on the same trade-off). */
function magnitude(value: number | null | undefined): number {
  return hasNumber(value) ? Math.abs(value) : 0;
}

function CategorySummary({ categories }: { readonly categories: readonly FantasyTradeCategoryDelta[] }): JSX.Element {
  const sorted = categories
    .filter((entry) => !entry.punted)
    .slice()
    .sort((a, b) => magnitude(b.net) - magnitude(a.net));
  const gains = sorted.filter((entry) => hasNumber(entry.net) && entry.net > 0.25).slice(0, 2);
  const losses = sorted.filter((entry) => hasNumber(entry.net) && entry.net < -0.25).slice(0, 2);
  if (gains.length === 0 && losses.length === 0) {
    return (
      <Text as="p" style="caption" color="secondary">
        No category moves by much.
      </Text>
    );
  }
  return (
    <div className={styles.categorySummary}>
      {gains.length > 0 && (
        <Text as="p" style="caption" color="positive">
          {`Gains ${gains.map((entry) => categoryName(entry.category)).join(", ")}`}
        </Text>
      )}
      {losses.length > 0 && (
        <Text as="p" style="caption" color="negative">
          {`Costs ${losses.map((entry) => categoryName(entry.category)).join(", ")}`}
        </Text>
      )}
    </div>
  );
}

function SensitivityBlock({ payload }: { readonly payload: FantasyTradePayload }): JSX.Element | null {
  const sweep = payload.sensitivity;
  if (sweep.scenarios.length === 0) return null;
  return (
    <div className={styles.sensitivity}>
      <div className={styles.sensitivityHeader}>
        <Text style="statLabel" color={sweep.flips ? "warning" : "secondary"}>
          {sweep.flips ? "Depends on who holds up" : "Holds up across scenarios"}
        </Text>
        {hasNumber(sweep.low) && hasNumber(sweep.high) && (
          <Text style="caption" tabularNums color="secondary">
            {`${formatSigned(sweep.low, "decimal2", true)} to ${formatSigned(sweep.high, "decimal2", true)}`}
          </Text>
        )}
      </div>
      <ul className={styles.scenarioList}>
        {sweep.scenarios.map((scenario) => (
          <li key={scenario.key} className={styles.scenarioRow}>
            <Text style="caption" color="secondary" className={styles.scenarioLabel}>
              {scenario.label}
            </Text>
            <Text style="caption" tabularNums color={signColor(scenario.net)}>
              {formatSigned(scenario.net, "decimal2", true)}
            </Text>
          </li>
        ))}
      </ul>
      {sweep.flips && (
        <Text as="p" style="caption" color="warning">
          It wins under some assumptions and loses under others, so the midpoint is not the
          answer.
        </Text>
      )}
      {/* CONTRACT.md §7.7: this sentence renders every time this block does. */}
      <Text as="p" style="caption" color="tertiary">
        A sweep over four named assumptions, not a probability.
      </Text>
    </div>
  );
}

function PointsBlock({ payload }: { readonly payload: FantasyTradePayload }): JSX.Element | null {
  const entries = Object.entries(payload.points);
  if (entries.length === 0) return null;
  return (
    <div className={styles.pointsBlock}>
      {entries.map(([system, delta]) => (
        <span key={system} className={styles.pointsEntry}>
          <Text style="caption" color="tertiary">
            {POINTS_SYSTEM_LABEL[system] ?? system}
          </Text>
          <Text style="caption" tabularNums color={signColor(delta.net)}>
            {formatSigned(delta.net, "decimal1", true)}
          </Text>
        </span>
      ))}
    </div>
  );
}

export default function FantasyTradeWidget({ size, payload }: WidgetViewProps): JSX.Element {
  let data: FantasyTradePayload;
  try {
    data = decodeFantasyTradePayload(payload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This trade's data did not match what the app expected." />;
  }

  if (isEmpty(data)) {
    return (
      <div className={styles.root}>
        <Text style="widgetTitle">Add players to both sides</Text>
        <Text as="p" style="caption" color="secondary">
          Pick up to four each way. You will get the change per category, what the roster spots
          are worth, and how much of it survives a player losing his role.
        </Text>
      </div>
    );
  }

  const showsTable = size === "large";
  const dominates = rosterDominates(data);

  return (
    <div className={styles.root}>
      <div className={styles.verdictRow}>
        <Text style="widgetTitle" color={verdictColor(data)} className={styles.verdictWord}>
          {titleCase(data.verdict)}
        </Text>
        <Text style="statValue" tabularNums color={verdictColor(data)}>
          {formatSigned(data.netZ, "decimal2", true)}
        </Text>
        <Text style="caption" color="secondary">
          net z
        </Text>
      </div>
      {hasNumber(data.poolSpread) && data.poolSpread > 0 && (
        <Text as="p" style="caption" tabularNums color="secondary">
          {`Fair inside ±${formatDecimal(data.bands.fair, 2)} · league spread ${formatDecimal(data.poolSpread, 2)}`}
        </Text>
      )}

      <div className={styles.sides}>
        <SideColumn title="You give" side={data.give} />
        <div className={styles.divider} aria-hidden />
        <SideColumn title="You get" side={data.get} />
      </div>

      {showsTable ? <CategoryTable categories={data.categories} /> : <CategorySummary categories={data.categories} />}

      <PointsBlock payload={data} />

      <SensitivityBlock payload={data} />

      {hasNumber(data.rosterAdjustment) && Math.abs(data.rosterAdjustment) > 0.001 && (
        <Text as="p" style="caption" tabularNums color="secondary">
          {`Includes ${formatSigned(data.rosterAdjustment, "decimal2", true)} for the roster ` +
            `${Math.abs(data.give.count - data.get.count) === 1 ? "spot" : "spots"} this moves` +
            (dominates ? " — more than the players themselves." : ".")}
        </Text>
      )}

      {data.note && (
        <Text as="p" style="caption" color="tertiary">
          {data.note}
        </Text>
      )}
    </div>
  );
}
