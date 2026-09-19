/**
 * One grid tile: sets the `--span-c`/`--span-r` custom properties `design/theme.css`'s
 * `.hw-grid > .hw-tile` reads (WEB_DESIGN.md §7.5 — both spans, always, the media query swaps
 * them), reads its own resolved payload from the cache `DashboardResolveContext` fills, and
 * renders the right `StateViews` placeholder or the registered widget component.
 *
 * Edit-mode affordances are the Move-up/Move-down/Resize/Duplicate/Remove buttons WEB_DESIGN.md
 * §7.5 names as the mechanism drag reorder may be deleted without breaking ("the buttons are the
 * supported mechanism"). This build ships buttons only — see `EditingToolbar.tsx`'s docstring
 * for why `@dnd-kit` drag is not wired up here.
 */
import { useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";
import clsx from "clsx";
import { REGISTRY } from "../generated/registry";
import { SIZE_SPANS, type WidgetSizeKey } from "../generated/tokens";
import type { WidgetKind } from "../generated/contracts";
import type { ResolveWidgetRequest } from "../api/types";
import { isResultStale } from "../api/resolve";
import { Text } from "../design/Text";
import { TileSurface } from "../design/TileSurface";
import { StalenessDot } from "../design/StalenessDot";
import { ErrorTile, LoadingTile, PendingTile } from "../design/StateViews";
import { TileErrorBoundary } from "../design/ErrorBoundary";
import { useDashboardResolveActions, useWidgetResult } from "./DashboardResolveContext";
import styles from "./WidgetContainer.module.css";

export interface WidgetContainerProps {
  readonly widget: ResolveWidgetRequest;
  readonly isEditing?: boolean;
  readonly isFirst?: boolean;
  readonly isLast?: boolean;
  readonly onConfigure?: (widgetId: string) => void;
  readonly onDuplicate?: (widgetId: string) => void;
  readonly onRemove?: (widgetId: string) => void;
  readonly onMoveUp?: (widgetId: string) => void;
  readonly onMoveDown?: (widgetId: string) => void;
  readonly onResize?: (widgetId: string, size: WidgetSizeKey) => void;
}

/** A widget kind whose `web/src/widgets/<kind>/index.tsx` has not shipped yet — every kind, as
 * of this build (WP5's territory). Composed from design-system primitives rather than
 * `StateViews.tsx`'s `PendingTile`, whose copy ("This widget is on iOS only for now") means
 * something more permanent than "not built yet in this in-progress web app". */
function UnbuiltTile({ size, kind }: { readonly size: WidgetSizeKey; readonly kind: WidgetKind }): JSX.Element {
  return (
    <TileSurface size={size}>
      <Text style="widgetTitle" color="secondary">
        {kind}
      </Text>
      <Text as="p" style="tableCell" color="tertiary">
        This widget has not shipped in the web app yet.
      </Text>
    </TileSurface>
  );
}

/** When this result stops being fresh, as an epoch millisecond, or `null` when it never does
 * (no `generatedAt`, no `ttlSeconds`, or an unparseable timestamp — the three cases
 * `isResultStale` also treats as never-stale). */
function expiryOf(result: { readonly generatedAt: string | null; readonly ttlSeconds: number | null } | undefined): number | null {
  if (!result || !result.generatedAt || result.ttlSeconds === null) return null;
  const generatedAt = Date.parse(result.generatedAt);
  if (Number.isNaN(generatedAt)) return null;
  return generatedAt + result.ttlSeconds * 1000;
}

function nextSize(currentSize: WidgetSizeKey, allowed: readonly WidgetSizeKey[]): WidgetSizeKey {
  if (allowed.length === 0) return currentSize;
  const index = allowed.indexOf(currentSize);
  return allowed[(index + 1) % allowed.length];
}

export function WidgetContainer({
  widget,
  isEditing = false,
  isFirst = false,
  isLast = false,
  onConfigure,
  onDuplicate,
  onRemove,
  onMoveUp,
  onMoveDown,
  onResize,
}: WidgetContainerProps): JSX.Element {
  const size: WidgetSizeKey = widget.size ?? "medium";
  const span = SIZE_SPANS[size];
  const entry = REGISTRY[widget.kind];
  const result = useWidgetResult(widget);
  const { refetchOne } = useDashboardResolveActions();

  // Staleness used to be a plain render-time computation, and nothing re-rendered a tile as
  // time passed: `refetchOnWindowFocus` is off globally, `useWidgetResult` sets
  // `staleTime: Infinity`, and the only timer in the app exists solely when a `scoreboard` or
  // `daily_movers` tile is on the page. A dashboard left open at 7pm still read "Updated just
  // now" at 11pm, over four-hour-old numbers, and had never refetched. `tick` is a one-shot
  // timer armed for the exact moment this result expires, so the tile wakes itself.
  const [, setTick] = useState(0);
  const staleAt = useMemo(() => expiryOf(result), [result]);
  useEffect(() => {
    if (staleAt === null) return;
    const delay = staleAt - Date.now();
    if (delay <= 0) return;
    // `setTimeout` clamps at ~24.8 days; a longer TTL than that is not worth arming for.
    if (delay > 2_000_000_000) return;
    const id = window.setTimeout(() => setTick((value) => value + 1), delay + 250);
    return () => window.clearTimeout(id);
  }, [staleAt]);

  const stale = result ? isResultStale(result) : false;
  useEffect(() => {
    if (stale) refetchOne(widget);
    // Refetch once per staleness transition, not on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stale, widget.id]);

  const tileStyle = useMemo(
    () => ({ "--span-c": span.compact, "--span-r": span.regular }) as CSSProperties,
    [span.compact, span.regular],
  );

  const title = widget.title ?? widget.kind;

  return (
    <div className="hw-tile" style={tileStyle} data-widget-id={widget.id} data-widget-kind={widget.kind}>
      <div className={styles.tile}>
        {renderBody()}
        {isEditing && (
          <div className={styles.editControls} role="group" aria-label={`Edit ${title ?? widget.kind}`}>
            <button
              type="button"
              className={styles.editButton}
              onClick={() => onMoveUp?.(widget.id)}
              disabled={isFirst}
            >
              Move up
            </button>
            <button
              type="button"
              className={styles.editButton}
              onClick={() => onMoveDown?.(widget.id)}
              disabled={isLast}
            >
              Move down
            </button>
            {entry && entry.sizes.length > 1 && (
              <button
                type="button"
                className={styles.editButton}
                onClick={() => onResize?.(widget.id, nextSize(size, entry.sizes))}
              >
                Resize
              </button>
            )}
            <button type="button" className={styles.editButton} onClick={() => onConfigure?.(widget.id)}>
              Configure
            </button>
            <button type="button" className={styles.editButton} onClick={() => onDuplicate?.(widget.id)}>
              Duplicate
            </button>
            <button
              type="button"
              className={clsx(styles.editButton, styles.editButtonDanger)}
              onClick={() => onRemove?.(widget.id)}
            >
              Remove
            </button>
          </div>
        )}
      </div>
    </div>
  );

  function renderBody(): JSX.Element {
    if (!entry) return <UnbuiltTile size={size} kind={widget.kind} />;
    if (!result) return <LoadingTile size={size} />;

    if (result.status === "error") {
      return (
        <ErrorTile
          size={size}
          message={result.error?.message ?? "This widget could not be loaded."}
          isRetryable={result.error?.recoverable ?? true}
          onRetry={() => refetchOne(widget)}
        />
      );
    }

    const Component = entry.component;
    if (!Component) return <PendingTile size={size} />;

    return (
      <TileSurface size={size}>
        <div className={styles.header}>
          <div className={styles.headerStart}>
            <Text style="widgetTitle" truncate>
              {title ?? widget.kind}
            </Text>
          </div>
          <StalenessDot isStale={stale} updatedAt={result.generatedAt} />
        </div>
        <div className={styles.body}>
          {/* Two layers, because eleven of the sixteen widgets decode inside their own
              try/catch and five (`scoreboard`, `stat_tile`, `comparison`, `daily_movers`,
              `shot_profile`) called their decoder bare in render. A decoder throws on any
              shape deviation — a score serialised as the string "112", a `topPerformers` that
              came back null for a game with no box score yet — and with no boundary anywhere
              in the app that throw unmounted RootLayout, taking the header, the nav, every
              other tile and the NBA attribution footer with it. This boundary is the one that
              cannot be forgotten per widget. */}
          <TileErrorBoundary size={size} resetKey={result} onRetry={() => refetchOne(widget)}>
            <Component kind={widget.kind} size={size} payload={result.payload} />
          </TileErrorBoundary>
        </div>
        {result.status === "partial" && result.notes.length > 0 && (
          <Text as="p" style="caption" color="secondary">
            {result.notes.join(" ")}
          </Text>
        )}
      </TileSurface>
    );
  }
}
