/**
 * The placeholder every widget shows while it is resolving, has failed, is genuinely empty, or
 * whose metric does not exist for the requested era — `ios/NBAStats/DesignSystem/StateViews.swift`,
 * ported, plus `PendingTile` for a `WidgetKind` `PENDING.txt` has not shipped a component for
 * yet.
 */
import type { JSX, ReactNode } from "react";
import type { WidgetSizeKey } from "../generated/tokens";
import { TileSurface } from "./TileSurface";
import { Text } from "./Text";
import { EM_DASH } from "./format";
import { AvailabilityExplainer } from "./AvailabilityExplainer";
import { useState } from "react";
import styles from "./StateViews.module.css";
import clsx from "clsx";

function WarningIcon({ className }: { readonly className?: string }): JSX.Element {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" className={className} aria-hidden focusable="false">
      <path
        d="M10 2.5 18 17H2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <line x1="10" y1="8" x2="10" y2="12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="10" cy="14.5" r="0.9" fill="currentColor" />
    </svg>
  );
}

function EmptyIcon({ className }: { readonly className?: string }): JSX.Element {
  return (
    <svg width="20" height="20" viewBox="0 0 20 20" className={className} aria-hidden focusable="false">
      <rect x="2.5" y="3.5" width="15" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <line x1="2.5" y1="7.5" x2="17.5" y2="7.5" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

// ------------------------------------------------------------------------------------------
// Loading
// ------------------------------------------------------------------------------------------

function SkeletonBar({ flex }: { readonly flex?: boolean }): JSX.Element {
  return <div className={clsx(styles.skeletonBar, flex && styles.skeletonBarFlex)} style={flex ? undefined : { width: "100%" }} />;
}

export interface LoadingTileProps {
  readonly size: WidgetSizeKey;
}

/** The per-size skeleton composition. Sized identically to the payload it stands in for
 * (`TileSurface`'s `minHeight`), so a widget resolving never changes the height of the grid row
 * it is standing in for. */
export function LoadingTile({ size }: LoadingTileProps): JSX.Element {
  return (
    <TileSurface size={size}>
      <div className={styles.skeletonColumn} role="status" aria-label="Loading" aria-busy="true">
        {size === "small" && (
          <>
            <div style={{ width: 62, height: 10 }}>
              <SkeletonBar />
            </div>
            <div style={{ width: 104, height: 26 }}>
              <SkeletonBar />
            </div>
            <div className={styles.skeletonSpacer} />
            <div style={{ width: 84, height: 10 }}>
              <SkeletonBar />
            </div>
          </>
        )}
        {size === "medium" && (
          <>
            <div style={{ width: 96, height: 12 }}>
              <SkeletonBar />
            </div>
            <div style={{ width: 148, height: 30 }}>
              <SkeletonBar />
            </div>
            <div style={{ height: 10 }}>
              <SkeletonBar />
            </div>
            <div style={{ height: 10 }}>
              <SkeletonBar />
            </div>
            <div className={styles.skeletonSpacer} />
            <div style={{ width: 120, height: 10 }}>
              <SkeletonBar />
            </div>
          </>
        )}
        {size === "large" && (
          <>
            <div style={{ width: 120, height: 12 }}>
              <SkeletonBar />
            </div>
            <div style={{ width: 180, height: 30 }}>
              <SkeletonBar />
            </div>
            {[0, 1, 2, 3, 4].map((row) => (
              <div className={styles.skeletonRow} key={row}>
                <div style={{ width: 26, height: 12 }}>
                  <SkeletonBar />
                </div>
                <div style={{ flex: "1 1 auto", height: 12 }}>
                  <SkeletonBar flex />
                </div>
                <div style={{ width: 44, height: 12 }}>
                  <SkeletonBar />
                </div>
              </div>
            ))}
            <div className={styles.skeletonSpacer} />
          </>
        )}
      </div>
    </TileSurface>
  );
}

// ------------------------------------------------------------------------------------------
// Error
// ------------------------------------------------------------------------------------------

export interface ErrorTileProps {
  readonly message: string;
  readonly isRetryable?: boolean;
  readonly size?: WidgetSizeKey;
  readonly onRetry?: () => void;
}

/** A single failed widget, contained inside its own tile so the rest of the dashboard is
 * untouched. A retry button only appears when the server marked the failure `recoverable`. */
export function ErrorTile({ message, isRetryable = true, size = "small", onRetry }: ErrorTileProps): JSX.Element {
  return (
    <TileSurface size={size}>
      <div className={styles.centered} role="alert" aria-label={`Could not load this widget. ${message}`}>
        <WarningIcon className={clsx(styles.icon, styles.iconWarning)} />
        <Text as="p" style="tableCell" color="secondary" className={styles.centeredText} ariaHidden>
          {message}
        </Text>
        {isRetryable && onRetry && (
          <button type="button" className={styles.retryButton} onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    </TileSurface>
  );
}

// ------------------------------------------------------------------------------------------
// Empty
// ------------------------------------------------------------------------------------------

export interface EmptyTileProps {
  readonly message: string;
  readonly icon?: ReactNode;
  readonly size?: WidgetSizeKey;
}

/** A widget that resolved successfully but has nothing to show — no games on the slate, no
 * qualified players for the filter. */
export function EmptyTile({ message, icon, size = "small" }: EmptyTileProps): JSX.Element {
  return (
    <TileSurface size={size}>
      <div className={styles.centered} role="status" aria-label={message}>
        {icon ?? <EmptyIcon className={styles.icon} />}
        <Text as="p" style="tableCell" color="secondary" className={styles.centeredText} ariaHidden>
          {message}
        </Text>
      </div>
    </TileSurface>
  );
}

// ------------------------------------------------------------------------------------------
// Unavailable
// ------------------------------------------------------------------------------------------

export interface UnavailableTileProps {
  readonly reason: string;
  readonly metricName?: string | null;
  readonly season?: string | null;
  readonly size?: WidgetSizeKey;
}

/** A widget whose metric simply did not exist for the requested era — the whole reason for the
 * em-dash rule: rather than a zero, the reader gets a dash and a tap target that explains the
 * gap in the league's record. */
export function UnavailableTile({ reason, metricName, season, size = "small" }: UnavailableTileProps): JSX.Element {
  const [isExplaining, setIsExplaining] = useState(false);
  return (
    <TileSurface size={size}>
      <div className={styles.centered} role="status" aria-label={`Not available. ${reason}`}>
        <Text style="displayValue" color="tertiary" ariaHidden>
          {EM_DASH}
        </Text>
        <Text as="p" style="tableCell" color="secondary" className={styles.centeredText} ariaHidden>
          {reason}
        </Text>
        <button type="button" className={styles.retryButton} onClick={() => setIsExplaining(true)}>
          Why?
        </button>
      </div>
      {isExplaining && (
        <AvailabilityExplainer
          availability="unavailable"
          metricName={metricName}
          season={season}
          notes={[reason]}
          onClose={() => setIsExplaining(false)}
        />
      )}
    </TileSurface>
  );
}

// ------------------------------------------------------------------------------------------
// Pending (kind not yet shipped for the web)
// ------------------------------------------------------------------------------------------

export interface PendingTileProps {
  readonly size?: WidgetSizeKey;
}

/** For a `WidgetKind` still listed in `web/src/widgets/PENDING.txt`. */
export function PendingTile({ size = "small" }: PendingTileProps): JSX.Element {
  return (
    <TileSurface size={size}>
      <div className={styles.centered} role="status">
        <Text as="p" style="tableCell" color="secondary" className={styles.centeredText}>
          This widget is on iOS only for now.
        </Text>
      </div>
    </TileSurface>
  );
}
