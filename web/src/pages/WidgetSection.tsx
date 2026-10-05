/**
 * The shared host every full-page widget section goes through.
 *
 * `/player/:id`, `/leaders`, `/today` and friends resolve the same widget payloads a dashboard
 * tile does, but each page had re-implemented a thinner rendering of them by hand — and each
 * hand-rolled version dropped the honesty apparatus the widget carries:
 *
 * * `result.notes`. `routes_dashboard.py` sets `status="partial"` exactly when there are notes,
 *   and the note *is* the caveat ("Player Efficiency Rating for 1990-91 is derived from
 *   box-score formulas rather than possession data, which the league only recorded from
 *   1996-97."). `WidgetContainer` prints it; no page host did, so the same widget told two
 *   different honesty stories depending on whether it was on a tile or on the page the nav
 *   links to.
 * * Each value's `availability`. A page that renders `{value.displayValue}` bare shows an
 *   estimated pre-1997 number in the same typography as a measured one.
 *
 * Both are fixed here rather than in each page, so the next page cannot forget. The body is
 * the widget's own registered component by default — the same code the tile renders — which is
 * also why `/player/:id`'s "Next game" section now carries the thin-rate caution, the minutes
 * block and the estimated markers that `next_game_projection`'s docstring calls
 * "the whole design of this view".
 */
import type { JSX, ReactNode } from "react";
import { REGISTRY } from "../generated/registry";
import type { ResolveResult, ResolveWidgetRequest } from "../api/types";
import type { WidgetKind } from "../generated/contracts";
import type { WidgetSizeKey } from "../generated/tokens";
import { Text } from "../design/Text";
import { TileErrorBoundary } from "../design/ErrorBoundary";
import { ErrorTile, LoadingTile } from "../design/StateViews";
import { useDashboardResolveActions, useWidgetResult } from "../dashboard/DashboardResolveContext";

export interface WidgetSectionProps<K extends WidgetKind> {
  readonly widget: ResolveWidgetRequest & { readonly kind: K };
  /** A heading above the body. Omitted for a section that is its own header (the snapshot). */
  readonly title?: string;
  readonly errorMessage?: string;
  /** Renders the body from the resolved payload. Defaults to the widget's registered
   * component — prefer that; a custom body must handle availability itself. */
  readonly children?: (result: ResolveResult<K>) => ReactNode;
}

/** The notes line, verbatim, in the same place on every page. */
export function WidgetNotes({ notes }: { readonly notes: readonly string[] }): JSX.Element | null {
  if (notes.length === 0) return null;
  return (
    <Text as="p" style="caption" color="warning">
      {notes.join(" ")}
    </Text>
  );
}

export function WidgetSection<K extends WidgetKind>({
  widget,
  title,
  errorMessage,
  children,
}: WidgetSectionProps<K>): JSX.Element {
  const result = useWidgetResult(widget);
  const { refetchOne } = useDashboardResolveActions();
  const size: WidgetSizeKey = widget.size ?? "large";

  const heading = title ? (
    <Text as="p" style="sectionTitle">
      {title}
    </Text>
  ) : null;

  if (!result) {
    return (
      <>
        {heading}
        <LoadingTile size={size} />
      </>
    );
  }

  if (result.status === "error") {
    return (
      <>
        {heading}
        <ErrorTile
          size={size}
          message={result.error?.message ?? errorMessage ?? "This section could not be loaded."}
          isRetryable={result.error?.recoverable ?? true}
          onRetry={() => refetchOne(widget)}
        />
      </>
    );
  }

  const Component = REGISTRY[widget.kind]?.component;
  const body = children
    ? children(result)
    : Component
      ? <Component kind={widget.kind} size={size} payload={result.payload} />
      : null;

  return (
    <>
      {heading}
      <TileErrorBoundary size={size} resetKey={result} onRetry={() => refetchOne(widget)}>
        {body}
      </TileErrorBoundary>
      <WidgetNotes notes={result.notes} />
    </>
  );
}
