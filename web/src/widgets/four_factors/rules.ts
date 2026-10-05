/**
 * The direction rules that make the four factors readable rather than merely drawn — ported from
 * `ios/NBAStats/Widgets/FourFactorsWidget.swift`'s `FourFactorRules`.
 *
 * Dean Oliver's four factors do **not** all point the same way. Shooting, offensive rebounding
 * and free throw rate are better when high; **turnover rate is better when low**. On the
 * defensive side every direction flips again — forcing turnovers is good, conceding offensive
 * rebounds is not. There is no `higherIsBetter` on this payload shape (CONTRACT.md §7.7), so
 * direction is inferred here, per factor, per side, exactly as the Swift original documents.
 */

export type FourFactorFamily = "shooting" | "turnovers" | "rebounding" | "freeThrows" | "unknown";

/** The direction on the offensive side, before any defensive flip. */
const OFFENSIVE_HIGHER_IS_BETTER: Readonly<Record<FourFactorFamily, boolean>> = {
  shooting: true,
  turnovers: false,
  rebounding: true,
  freeThrows: true,
  unknown: true,
};

/** The plausible league-wide span, used to place a bar inside a stable track so two teams' tiles
 * can be compared side by side — never derived from the row's own value. */
const BASE_DOMAIN: Readonly<Record<FourFactorFamily, readonly [number, number]>> = {
  shooting: [0.44, 0.62],
  turnovers: [0.08, 0.2],
  rebounding: [0.14, 0.4],
  freeThrows: [0.1, 0.36],
  unknown: [0, 1],
};

/** Matches the contract's keys (`efg_pct`, `tov_pct`, `oreb_pct`, `ftr`) and their `opp_`
 * counterparts, plus the obvious near-spellings, without ever trapping on a new one. */
export function familyForKey(key: string): FourFactorFamily {
  const normalized = key.toLowerCase();
  if (normalized.includes("efg")) return "shooting";
  if (normalized.includes("tov") || normalized.includes("turnover")) return "turnovers";
  if (normalized.includes("oreb") || normalized.includes("off_reb")) return "rebounding";
  if (normalized.includes("ftr") || normalized.includes("ft_rate") || normalized.includes("ft_rt")) return "freeThrows";
  return "unknown";
}

/** Whether a bigger number is a better number for this factor on this side of the ball. A
 * defensive entry is the mirror of its offensive twin. */
export function higherIsBetterForKey(key: string, isDefense: boolean): boolean {
  const base = OFFENSIVE_HIGHER_IS_BETTER[familyForKey(key)];
  const normalized = key.toLowerCase();
  const isOpponentFacing = isDefense || normalized.startsWith("opp_") || normalized.includes("opp");
  return isOpponentFacing ? !base : base;
}

/** The track a factor's bar is drawn inside, widened when the real numbers fall outside it. */
export function domainForKey(
  key: string,
  value: number | null,
  leagueAverage: number | null,
): readonly [number, number] {
  const base = BASE_DOMAIN[familyForKey(key)];
  const extras = [value, leagueAverage].filter((entry): entry is number => entry !== null && Number.isFinite(entry));
  const low = Math.min(base[0], (extras.length > 0 ? Math.min(...extras) : base[0]) - 0.01);
  const high = Math.max(base[1], (extras.length > 0 ? Math.max(...extras) : base[1]) + 0.01);
  if (high <= low) return base;
  return [low, high];
}

/** The short caption that carries the direction without relying on hue. */
export function directionHint(higherIsBetter: boolean): string {
  return higherIsBetter ? "higher is better" : "lower is better";
}
