/**
 * Explains, in plain English, why a stat is missing, estimated, or partial for an era —
 * `ios/NBAStats/DesignSystem/AvailabilityBadge.swift`'s `AvailabilityExplainer`, ported as a
 * modal dialog (the closest web equivalent of a SwiftUI sheet presented from a tap).
 *
 * The record-keeping timeline is read from `ERA_BOUNDARIES` in `generated/contracts.ts` — which
 * is itself generated from `contracts/metrics.json#/eraBoundaries` — **never** hand-written here
 * (WEB_DESIGN.md §0.1-F: "do not hand-write it in TypeScript"), so a change to the league's
 * record-keeping history in one place reaches every platform through the same contract.
 */
import { useEffect, useId } from "react";
import type { Availability } from "../api/types";
import { ERA_BOUNDARIES } from "../generated/contracts";
import { Text } from "./Text";
import { explanation, headline, PRE_1997_DISCLAIMER } from "./availability";
import styles from "./AvailabilityExplainer.module.css";

export interface AvailabilityExplainerProps {
  readonly availability: Availability;
  readonly metricName?: string | null;
  readonly season?: string | null;
  /** Widget-specific detail beyond the generic explanation — e.g. which named inputs a
   * `"partial"` value is missing. */
  readonly notes?: readonly string[];
  readonly onClose: () => void;
}

export function AvailabilityExplainer({
  availability,
  metricName,
  season,
  notes = [],
  onClose,
}: AvailabilityExplainerProps): JSX.Element {
  const headingId = useId();

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className={styles.backdrop}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className={styles.dialog} role="dialog" aria-modal="true" aria-labelledby={headingId}>
        <div className={styles.header}>
          <div className={styles.headerText}>
            <Text as="div" style="sectionTitle" id={headingId}>
              {headline(availability)}
            </Text>
            {metricName && (
              <Text as="div" style="caption">
                {season ? `${metricName} · ${season}` : metricName}
              </Text>
            )}
          </div>
          <button type="button" className={styles.closeButton} onClick={onClose}>
            Done
          </button>
        </div>

        <Text as="p" style="tableCell" color="secondary" className={styles.paragraph}>
          {explanation(availability, metricName, season)}
        </Text>

        {notes.length > 0 && (
          <div className={styles.notes}>
            <Text style="statLabel">What is missing here</Text>
            {notes.map((note) => (
              <div className={styles.note} key={note}>
                <Text style="caption" color="warning">
                  {"•"}
                </Text>
                <Text style="tableCell" color="secondary">
                  {note}
                </Text>
              </div>
            ))}
          </div>
        )}

        <div className={styles.timeline}>
          <Text style="statLabel">When the record changed</Text>
          {ERA_BOUNDARIES.map((fact) => (
            <div className={styles.timelineRow} key={fact.season}>
              <Text style="tableHeader" tabularNums className={styles.timelineSeason}>
                {fact.season}
              </Text>
              <div className={styles.timelineBody}>
                <Text as="div" style="tableCell">
                  {fact.label}
                </Text>
                <Text as="div" style="caption">
                  {fact.detail}
                </Text>
              </div>
            </div>
          ))}
        </div>

        <Text as="p" style="caption" className={styles.paragraph}>
          {PRE_1997_DISCLAIMER}
        </Text>
      </div>
    </div>
  );
}
