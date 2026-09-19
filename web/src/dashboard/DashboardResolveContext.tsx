/**
 * Wires a list of widgets to `api/resolve.ts`'s batch resolver and hands every `WidgetContainer`
 * underneath a way to (a) read its own cached result reactively and (b) ask for a fresh one —
 * without any of them being allowed to call `POST /v1/dashboard/resolve` themselves
 * (`eslint.config.js` restricts that call to `api/resolve.ts` alone).
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type JSX,
  type ReactNode,
} from "react";
import { skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CLOCK_DEPENDENT_KINDS,
  CLOCK_DEPENDENT_REFRESH_MS,
  resolveDashboard,
  widgetQueryKey,
  type ResolveDashboardOptions,
} from "../api/resolve";
import type { ResolveResult, ResolveWidgetRequest } from "../api/types";
import type { WidgetKind } from "../generated/contracts";

interface DashboardResolveContextValue {
  readonly refetchOne: (widget: ResolveWidgetRequest) => void;
  readonly refetchAll: () => void;
}

const DashboardResolveContext = createContext<DashboardResolveContextValue | null>(null);

export interface DashboardResolveProviderProps {
  readonly widgets: readonly ResolveWidgetRequest[];
  readonly options?: ResolveDashboardOptions;
  readonly children?: ReactNode;
}

/** Resolves every widget in `widgets` on mount and whenever the widget list or its context
 * changes, keeps clock-dependent tiles (`scoreboard`, `daily_movers`) refreshing on their own
 * 60s cadence regardless of anything else, and gives descendants `useWidgetResult` /
 * `useDashboardResolveActions`. */
export function DashboardResolveProvider({
  widgets,
  options,
  children,
}: DashboardResolveProviderProps): JSX.Element {
  const queryClient = useQueryClient();
  const widgetsRef = useRef(widgets);
  const optionsRef = useRef(options);
  // Refreshed after every render rather than assigned during render: `refetchAll`/`refetchOne`
  // below are only ever invoked later (from an effect, a timer or an event handler), never
  // synchronously while this component is rendering, so committing the latest props here is
  // exactly as fresh as mutating the ref inline would have been.
  useEffect(() => {
    widgetsRef.current = widgets;
    optionsRef.current = options;
  });

  const widgetsKey = useMemo(
    () => widgets.map((widget) => `${widget.id}:${JSON.stringify(widget.config ?? {})}`).join("|"),
    [widgets],
  );
  const optionsKey = JSON.stringify(options ?? {});

  const refetchAll = useCallback((): void => {
    void resolveDashboard(queryClient, widgetsRef.current, optionsRef.current);
  }, [queryClient]);

  const refetchOne = useCallback(
    (widget: ResolveWidgetRequest): void => {
      void resolveDashboard(queryClient, [widget], optionsRef.current);
    },
    [queryClient],
  );

  useEffect(() => {
    // Re-runs whenever the widget set or its resolve context actually changes (widgetsKey/
    // optionsKey), not on every render — a new-but-equal array/object from the caller must not
    // restart every tile. `refetchAll` itself only changes when `queryClient` does.
    refetchAll();
  }, [widgetsKey, optionsKey, refetchAll]);

  useEffect(() => {
    const hasClockDependent = widgets.some((widget) => CLOCK_DEPENDENT_KINDS.has(widget.kind));
    if (!hasClockDependent) return;
    const id = window.setInterval(refetchAll, CLOCK_DEPENDENT_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [widgetsKey, refetchAll, widgets]);

  const value = useMemo<DashboardResolveContextValue>(
    () => ({ refetchOne, refetchAll }),
    [refetchOne, refetchAll],
  );

  return (
    <DashboardResolveContext.Provider value={value}>{children}</DashboardResolveContext.Provider>
  );
}

/** Actions a widget tile (or its retry button) can take, without ever calling the resolve
 * endpoint directly. Usable outside a {@link DashboardResolveProvider} too (a single-widget
 * preview), in which case both actions are no-ops. */
export function useDashboardResolveActions(): DashboardResolveContextValue {
  const context = useContext(DashboardResolveContext);
  return context ?? { refetchOne: () => undefined, refetchAll: () => undefined };
}

/** Reads one widget's cached {@link ResolveResult} reactively — never fetches itself; the
 * provider (or a direct `resolveDashboard` call) is what populates the cache. Generic over the
 * widget's own `kind` literal, so `result.payload` narrows to that one kind's payload type
 * instead of the sixteen-way union `ResolveResult` carries by default. */
export function useWidgetResult<K extends WidgetKind>(
  widget: ResolveWidgetRequest & { readonly kind: K },
): ResolveResult<K> | undefined {
  const { data } = useQuery<ResolveResult<K>>({
    queryKey: widgetQueryKey(widget),
    queryFn: skipToken,
    staleTime: Infinity,
  });
  return data;
}
