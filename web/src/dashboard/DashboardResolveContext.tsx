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
  applyResolveFailure,
  CLOCK_DEPENDENT_KINDS,
  CLOCK_DEPENDENT_REFRESH_MS,
  nextResolveGeneration,
  observedSyncVersion,
  resolveDashboard,
  widgetQueryKey,
  type ResolveDashboardOptions,
} from "../api/resolve";
import { ApiError } from "../api/session";
import { subscribeToSync } from "../api/sync";
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

  // `POST /v1/dashboard/resolve` failing — the process restarted, the SQLite file is locked by
  // an ingest run, the wifi dropped for one request — used to be a `void` on a promise that
  // rejects. Nothing wrote an error into the cache, so `useWidgetResult` kept returning
  // `undefined` and every tile shimmered indefinitely: no message, no retry, no way to tell a
  // slow server from a dead one. The rejection is now turned into the `status: "error"` result
  // `WidgetContainer` already renders as a retryable `ErrorTile`.
  const run = useCallback(
    (widgets: readonly ResolveWidgetRequest[]): void => {
      if (widgets.length === 0) return;
      const generation = nextResolveGeneration();
      void resolveDashboard(queryClient, widgets, optionsRef.current, generation).catch(
        (cause: unknown) => {
          const message =
            cause instanceof ApiError
              ? cause.message
              : "Could not reach Hardwood. Check your connection and try again.";
          applyResolveFailure(queryClient, widgets, message, generation);
        },
      );
    },
    [queryClient],
  );

  const refetchAll = useCallback((): void => {
    run(widgetsRef.current);
  }, [run]);

  const refetchOne = useCallback(
    (widget: ResolveWidgetRequest): void => {
      run([widget]);
    },
    [run],
  );

  useEffect(() => {
    // Re-runs whenever the widget set or its resolve context actually changes (widgetsKey/
    // optionsKey), not on every render — a new-but-equal array/object from the caller must not
    // restart every tile. `refetchAll` itself only changes when `queryClient` does.
    refetchAll();
  }, [widgetsKey, optionsKey, refetchAll]);

  // `hasClockDependent` is derived here rather than inside the effect so `widgets` — rebuilt
  // fresh on every render by `Dashboard.tsx` — stays out of the dependency array. With it in,
  // any state change in the editor (toggling ?edit=1, opening the config sheet, an isSaving
  // flip from the debounced PUT) tore the interval down and re-armed it, so an actively edited
  // dashboard's scoreboard could go a long time without its guaranteed 60s refresh.
  const hasClockDependent = useMemo(
    () => widgets.some((widget) => CLOCK_DEPENDENT_KINDS.has(widget.kind)),
    [widgets],
  );
  useEffect(() => {
    if (!hasClockDependent) return;
    const id = window.setInterval(refetchAll, CLOCK_DEPENDENT_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [widgetsKey, refetchAll, hasClockDependent]);

  // `GET /v1/sync/stream` — the server tells us when an ingest lands, instead of every tab
  // guessing on a timer. `api/sync.ts` shipped with no importer at all, so the whole
  // freshness path §7.6 describes was dead code that read as implemented. Guarded on
  // `EventSource` existing (it does not in jsdom, and not in every embedded webview) and on
  // the constructor not throwing, because a dashboard must work without it — it is an
  // optimisation over the per-tile expiry timers, never a requirement.
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let dispose: (() => void) | null = null;
    try {
      dispose = subscribeToSync((payload) => {
        if (payload.hasChanges) refetchAll();
      }, observedSyncVersion());
    } catch {
      return;
    }
    return () => dispose?.();
  }, [refetchAll]);

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
