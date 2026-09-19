/**
 * `four_factors` — Dean Oliver's four factors for a team, drawn as horizontal bars against the
 * league average. Ported from `ios/NBAStats/Widgets/FourFactorsWidget.swift`: the defensive
 * mirror only appears at `size === "large"`, and every bar states its own direction in words,
 * never only in colour, because turnover rate (and the whole defensive side) does not point the
 * same way as the rest — there is no `higherIsBetter` on this payload shape (CONTRACT.md §7.7),
 * so direction is inferred client-side by `./rules.ts`.
 */
import type { CSSProperties, JSX } from "react";
import type { WidgetViewProps } from "../../generated/registry";
import type { Availability, FourFactorEntry, FourFactorsPayload } from "../../api/types";
import { Text } from "../../design/Text";
import { TeamBadge } from "../../design/TeamBadge";
import { FactorBar } from "../../design/FactorBar";
import { RankChip } from "../../design/RankChip";
import { AvailabilityBadge } from "../../design/AvailabilityBadge";
import { ErrorTile } from "../../design/StateViews";
import { chartColorVar, comparisonColorVar, comparisonOutcomeAgainst } from "../../design/valueColor";
import { formatPercent, seasonContext } from "../../design/format";
import { decodeFourFactorsPayload } from "./payload";
import { directionHint, domainForKey, higherIsBetterForKey } from "./rules";
import styles from "./index.module.css";

interface Row {
  readonly entry: FourFactorEntry;
  readonly higherIsBetter: boolean;
  readonly domain: readonly [number, number];
  readonly tint: string;
  readonly isDefense: boolean;
}

function buildRows(entries: readonly FourFactorEntry[], isDefense: boolean): readonly Row[] {
  return entries.map((entry) => {
    const higherIsBetter = higherIsBetterForKey(entry.key, isDefense);
    const tint =
      entry.leagueAverage === null
        ? chartColorVar(isDefense ? 2 : 0)
        : comparisonColorVar(comparisonOutcomeAgainst(entry.value, entry.leagueAverage, higherIsBetter));
    return { entry, higherIsBetter, domain: domainForKey(entry.key, entry.value, entry.leagueAverage), tint, isDefense };
  });
}

function entryAvailability(entry: FourFactorEntry): Availability {
  return entry.value === null ? "unavailable" : "full";
}

function rowAccessibleLabel(row: Row): string {
  const parts: string[] = [row.isDefense ? "Defense" : "Offense", row.entry.label, row.entry.displayValue];
  if (row.entry.value === null) parts.push("not available for this era");
  if (row.entry.leagueAverage !== null) parts.push(`league average ${formatPercent(row.entry.leagueAverage, 1)}`);
  if (row.entry.rank !== null && row.entry.rank > 0) parts.push(`ranked ${row.entry.rank}`);
  parts.push(directionHint(row.higherIsBetter));
  return parts.join(", ");
}

function FactorRowView({ row }: { readonly row: Row }): JSX.Element {
  const valueText = row.entry.displayValue.length > 0 ? row.entry.displayValue : formatPercent(row.entry.value, 1);
  const valueColorStyle: CSSProperties | undefined = row.entry.value === null ? undefined : { color: row.tint };
  return (
    <div className={styles.factorRow} role="group" aria-label={rowAccessibleLabel(row)}>
      <div className={styles.factorHeader} aria-hidden="true">
        <Text style="statLabel" truncate className={styles.factorLabel}>
          {row.entry.label}
        </Text>
        {row.entry.weight > 0 && (
          <Text style="caption" color="tertiary">
            {formatPercent(row.entry.weight, 0)}
          </Text>
        )}
        {!row.higherIsBetter && (
          <Text style="caption" color="warning" className={styles.directionHint}>
            lower is better
          </Text>
        )}
        <span className={styles.factorSpacer} />
        <RankChip rank={row.entry.rank} />
        <Text style="statValue" tabularNums color={row.entry.value === null ? "tertiary" : undefined} htmlStyle={valueColorStyle}>
          {valueText}
        </Text>
        <AvailabilityBadge availability={entryAvailability(row.entry)} showsText={false} isInteractive={false} metricName={row.entry.label} />
      </div>
      {row.entry.value !== null && Number.isFinite(row.entry.value) ? (
        <FactorBar value={row.entry.value} leagueAverage={row.entry.leagueAverage} domain={row.domain} tint={row.tint} showsLegend={false} />
      ) : (
        <div className={styles.emptyTrack} aria-hidden="true" />
      )}
    </div>
  );
}

function Section({ title, rows, isDefense }: { readonly title: string; readonly rows: readonly Row[]; readonly isDefense: boolean }): JSX.Element {
  return (
    <div className={styles.section}>
      <div className={styles.sectionHeader}>
        <Text style="statLabel" color="secondary">
          {title}
        </Text>
        {isDefense && (
          <Text style="caption" color="tertiary">
            opponent&rsquo;s numbers
          </Text>
        )}
      </div>
      {rows.map((row) => (
        <FactorRowView key={`${row.isDefense ? "d" : "o"}|${row.entry.key}`} row={row} />
      ))}
    </div>
  );
}

function FourFactorsWidgetView({ size, payload: rawPayload }: WidgetViewProps): JSX.Element {
  let payload: FourFactorsPayload;
  try {
    payload = decodeFourFactorsPayload(rawPayload);
  } catch {
    return <ErrorTile size={size} isRetryable={false} message="This team's four factors did not match what the app expected." />;
  }

  const offenseRows = buildRows(payload.offense, false);
  const defenseRows = buildRows(payload.defense, true);
  const showsDefense: boolean = size === "large" && defenseRows.length > 0;
  const hasLeagueComparison = [...payload.offense, ...payload.defense].some((entry) => entry.leagueAverage !== null);
  const contextText = seasonContext(payload.season, payload.seasonType);

  const footnoteLines: string[] = [];
  if (hasLeagueComparison) {
    footnoteLines.push("The vertical rule on each bar is the league average; the percentage after a factor is Oliver's weight.");
  } else {
    footnoteLines.push("The percentage after a factor is Oliver's weight for it.");
  }
  if (!showsDefense && defenseRows.length > 0) footnoteLines.push("Resize to large to see the defensive side.");

  return (
    <div className={styles.root}>
      <div className={styles.header}>
        <TeamBadge abbreviation={payload.team.abbr} name={payload.team.name} size="small" />
        <div className={styles.headerText}>
          <Text style="statLabel" truncate>
            {payload.team.name}
          </Text>
          {contextText.length > 0 && (
            <Text as="span" style="caption">
              {contextText}
            </Text>
          )}
        </div>
      </div>
      {offenseRows.length === 0 && defenseRows.length === 0 ? (
        <Text as="p" style="caption" className={styles.emptyNote}>
          The four factors need possession data, which the league did not record before 1996-97.
        </Text>
      ) : (
        <>
          <Section title="Offense" rows={offenseRows} isDefense={false} />
          {showsDefense && (
            <>
              <div className={styles.rule} aria-hidden="true" />
              <Section title="Defense" rows={defenseRows} isDefense />
            </>
          )}
          <div className={styles.footnote}>
            {footnoteLines.map((line) => (
              <Text key={line} as="p" style="caption" color="tertiary">
                {line}
              </Text>
            ))}
          </div>
        </>
      )}
      <div className={styles.spacer} />
    </div>
  );
}

export default FourFactorsWidgetView;
