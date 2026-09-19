/**
 * The layout-editing state machine — WEB_DESIGN.md §7.3's "one `useReducer` in
 * `useLayoutStore.ts` mirroring `DashboardStore.swift`'s API one-to-one", scoped to the ONE
 * dashboard currently open (the list of dashboards, and switching between them, is
 * `LayoutSwitcher.tsx` + `api/dashboards.ts`'s job).
 *
 * `DashboardStore.swift`'s mutators all go through one private `edit(_:_:)` that forks a preset
 * into an editable copy first (new layout id, new widget ids, `presetKey` retained) and records
 * one level of undo while editing. This hook does the same: every mutating action forks first if
 * `state.layout.isPreset` is true, and the fork's `old id -> new id` map is exposed as
 * `lastForkIdMap` so a caller holding an id from before the fork (a config sheet open on a
 * widget, say) can resolve it — WEB_DESIGN.md §7.3: "returns an id map so a widget id captured
 * before the fork still resolves."
 *
 * Persistence is server-side (`PUT /v1/dashboards/{id}`, WEB_DESIGN.md §7.3): mutations apply
 * optimistically to local state immediately, and a write is debounced 600ms after the last one,
 * flushed early on `visibilitychange`/`pagehide` so a closed tab does not lose the last edit. A
 * `409 stale_write` is surfaced as `staleConflict` rather than silently overwriting or discarding
 * anything — the caller renders the "[Keep mine] [Use theirs]" banner.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import type { WidgetKind } from "../generated/contracts";
import type { WidgetSizeKey } from "../generated/tokens";
import { REGISTRY } from "../generated/registry";
import {
  StaleWriteError,
  updateDashboard,
  type LayoutDocument,
} from "../api/dashboards";

export interface EditableWidget {
  readonly id: string;
  readonly kind: WidgetKind;
  readonly size: WidgetSizeKey;
  readonly title?: string | null;
  readonly config: Readonly<Record<string, unknown>>;
}

export interface EditableLayout {
  readonly id: string;
  readonly name: string;
  readonly icon?: string | null;
  readonly accent?: string;
  readonly presentation: "tiles" | "broadsheet";
  readonly presetKey?: string | null;
  readonly isPreset: boolean;
  readonly widgets: readonly EditableWidget[];
}

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

/** Reads the fields this editor understands out of a raw `LayoutDocument`, tolerating a shape
 * this build has never seen before rather than throwing. */
export function toEditableLayout(document: LayoutDocument): EditableLayout {
  const rawWidgets = Array.isArray(document.widgets) ? document.widgets : [];
  const widgets: EditableWidget[] = rawWidgets.filter(isRecord).map((raw) => ({
    id: typeof raw.id === "string" ? raw.id : newId(),
    kind: (typeof raw.kind === "string" ? raw.kind : "stat_tile") as WidgetKind,
    size: (typeof raw.size === "string" ? raw.size : "medium") as WidgetSizeKey,
    title: typeof raw.title === "string" ? raw.title : null,
    config: isRecord(raw.config) ? raw.config : {},
  }));
  return {
    id: typeof document.id === "string" ? document.id : newId(),
    name: typeof document.name === "string" ? document.name : "Dashboard",
    icon: typeof document.icon === "string" ? document.icon : null,
    accent: typeof document.accent === "string" ? document.accent : "orange",
    presentation: document.presentation === "broadsheet" ? "broadsheet" : "tiles",
    presetKey: typeof document.presetKey === "string" ? document.presetKey : null,
    isPreset: document.isPreset === true,
    widgets,
  };
}

/** Merges an edited {@link EditableLayout} back over its original `LayoutDocument`, preserving
 * every field this editor does not model (`schemaVersion`, `createdAt`, …). */
export function fromEditableLayout(layout: EditableLayout, original: LayoutDocument): LayoutDocument {
  return {
    ...original,
    id: layout.id,
    name: layout.name,
    icon: layout.icon ?? null,
    accent: layout.accent ?? "orange",
    presentation: layout.presentation,
    presetKey: layout.presetKey ?? null,
    isPreset: layout.isPreset,
    widgets: layout.widgets.map((widget) => ({
      id: widget.id,
      kind: widget.kind,
      size: widget.size,
      title: widget.title ?? null,
      config: widget.config,
    })),
  };
}

function defaultSizeFor(kind: WidgetKind): WidgetSizeKey {
  return REGISTRY[kind]?.defaultSize ?? "medium";
}

function forkEditableCopy(layout: EditableLayout): { readonly layout: EditableLayout; readonly idMap: Record<string, string> } {
  const idMap: Record<string, string> = {};
  const widgets = layout.widgets.map((widget) => {
    const freshId = newId();
    idMap[widget.id] = freshId;
    return { ...widget, id: freshId };
  });
  return {
    layout: { ...layout, id: newId(), isPreset: false, widgets },
    idMap,
  };
}

interface State {
  readonly layout: EditableLayout;
  readonly undoSnapshot: EditableLayout | null;
  readonly isEditing: boolean;
  readonly lastForkIdMap: Readonly<Record<string, string>> | null;
  readonly dirty: boolean;
}

type Action =
  | { readonly type: "SET_EDITING"; readonly isEditing: boolean }
  | { readonly type: "ADD_WIDGET"; readonly kind: WidgetKind }
  | { readonly type: "REMOVE_WIDGET"; readonly widgetId: string }
  | { readonly type: "DUPLICATE_WIDGET"; readonly widgetId: string }
  | { readonly type: "MOVE_WIDGET"; readonly widgetId: string; readonly toIndex: number }
  | { readonly type: "RESIZE_WIDGET"; readonly widgetId: string; readonly size: WidgetSizeKey }
  | {
      readonly type: "CONFIGURE_WIDGET";
      readonly widgetId: string;
      readonly config: Readonly<Record<string, unknown>>;
      readonly title?: string | null;
    }
  | { readonly type: "UNDO" }
  | { readonly type: "REPLACE"; readonly layout: EditableLayout };

/** The single path every mutator takes — forks a preset first, records one level of undo while
 * editing, then applies `body`. Mirrors `DashboardStore.swift`'s private `edit(_:_:)`. */
function edit(
  state: State,
  body: (layout: EditableLayout, idMap: Readonly<Record<string, string>>) => EditableLayout,
): State {
  let working = state.layout;
  let idMap: Readonly<Record<string, string>> = {};
  if (working.isPreset) {
    const fork = forkEditableCopy(working);
    working = fork.layout;
    idMap = fork.idMap;
  }
  const next = body(working, idMap);
  return {
    layout: next,
    undoSnapshot: state.isEditing ? state.layout : state.undoSnapshot,
    isEditing: state.isEditing,
    lastForkIdMap: Object.keys(idMap).length > 0 ? idMap : null,
    dirty: true,
  };
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "SET_EDITING":
      return { ...state, isEditing: action.isEditing, undoSnapshot: null };
    case "ADD_WIDGET":
      return edit(state, (layout) => ({
        ...layout,
        widgets: [
          ...layout.widgets,
          { id: newId(), kind: action.kind, size: defaultSizeFor(action.kind), title: null, config: {} },
        ],
      }));
    case "REMOVE_WIDGET":
      return edit(state, (layout, idMap) => {
        const resolved = idMap[action.widgetId] ?? action.widgetId;
        return { ...layout, widgets: layout.widgets.filter((widget) => widget.id !== resolved) };
      });
    case "DUPLICATE_WIDGET":
      return edit(state, (layout, idMap) => {
        const resolved = idMap[action.widgetId] ?? action.widgetId;
        const index = layout.widgets.findIndex((widget) => widget.id === resolved);
        if (index < 0) return layout;
        const copy = { ...layout.widgets[index], id: newId() };
        const widgets = [...layout.widgets];
        widgets.splice(index + 1, 0, copy);
        return { ...layout, widgets };
      });
    case "MOVE_WIDGET":
      return edit(state, (layout, idMap) => {
        const resolved = idMap[action.widgetId] ?? action.widgetId;
        const from = layout.widgets.findIndex((widget) => widget.id === resolved);
        if (from < 0) return layout;
        const clamped = Math.max(0, Math.min(action.toIndex, layout.widgets.length - 1));
        if (clamped === from) return layout;
        const widgets = [...layout.widgets];
        const [moved] = widgets.splice(from, 1);
        widgets.splice(clamped, 0, moved);
        return { ...layout, widgets };
      });
    case "RESIZE_WIDGET":
      return edit(state, (layout, idMap) => {
        const resolved = idMap[action.widgetId] ?? action.widgetId;
        const allowedSizes = REGISTRY[layout.widgets.find((w) => w.id === resolved)?.kind as WidgetKind]?.sizes;
        return {
          ...layout,
          widgets: layout.widgets.map((widget) =>
            widget.id === resolved
              ? { ...widget, size: allowedSizes?.includes(action.size) ? action.size : widget.size }
              : widget,
          ),
        };
      });
    case "CONFIGURE_WIDGET":
      return edit(state, (layout, idMap) => {
        const resolved = idMap[action.widgetId] ?? action.widgetId;
        return {
          ...layout,
          widgets: layout.widgets.map((widget) =>
            widget.id === resolved
              ? { ...widget, config: action.config, title: action.title ?? widget.title ?? null }
              : widget,
          ),
        };
      });
    case "UNDO":
      if (!state.undoSnapshot) return state;
      return { ...state, layout: state.undoSnapshot, undoSnapshot: null, dirty: true };
    case "REPLACE":
      return { layout: action.layout, undoSnapshot: null, isEditing: state.isEditing, lastForkIdMap: null, dirty: false };
    default:
      return state;
  }
}

export interface StaleConflict {
  readonly theirs: LayoutDocument;
  readonly revision: number;
}

export interface UseLayoutStoreResult {
  readonly layout: EditableLayout;
  readonly isEditing: boolean;
  readonly canUndo: boolean;
  readonly isSaving: boolean;
  readonly saveError: string | null;
  readonly staleConflict: StaleConflict | null;
  readonly lastForkIdMap: Readonly<Record<string, string>> | null;
  readonly setEditing: (isEditing: boolean) => void;
  readonly addWidget: (kind: WidgetKind) => void;
  readonly removeWidget: (widgetId: string) => void;
  readonly duplicateWidget: (widgetId: string) => void;
  readonly moveWidget: (widgetId: string, toIndex: number) => void;
  readonly resizeWidget: (widgetId: string, size: WidgetSizeKey) => void;
  readonly configureWidget: (
    widgetId: string,
    config: Readonly<Record<string, unknown>>,
    title?: string | null,
  ) => void;
  readonly undoLastEdit: () => void;
  readonly keepMine: () => void;
  readonly useTheirs: () => void;
  readonly flush: () => void;
}

const SAVE_DEBOUNCE_MS = 600;

/** `layoutId`/`initialDocument`/`initialRevision` are read once, on mount — a caller switching
 * dashboards should remount this hook with a fresh `key`, exactly as switching a document editor
 * to a different document does, rather than trying to reconcile two open edit sessions. */
export function useLayoutStore(
  layoutId: string,
  initialDocument: LayoutDocument,
  initialRevision: number,
): UseLayoutStoreResult {
  const [state, dispatch] = useReducer(reducer, undefined, () => ({
    layout: toEditableLayout(initialDocument),
    undoSnapshot: null,
    isEditing: false,
    lastForkIdMap: null,
    dirty: false,
  }));

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [staleConflict, setStaleConflict] = useState<StaleConflict | null>(null);

  const originalDocumentRef = useRef(initialDocument);
  const revisionRef = useRef(initialRevision);
  const stateRef = useRef(state);
  const timerRef = useRef<number | null>(null);
  // `save` (below) only ever reads `stateRef.current` from a timer callback or an event handler,
  // never synchronously during this render, so committing the latest `state` here — instead of
  // writing the ref inline during render — is just as fresh when it is actually read.
  useEffect(() => {
    stateRef.current = state;
  });

  const save = useCallback(async (): Promise<void> => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!stateRef.current.dirty) return;
    setIsSaving(true);
    setSaveError(null);
    const document = fromEditableLayout(stateRef.current.layout, originalDocumentRef.current);
    try {
      const result = await updateDashboard(layoutId, document, revisionRef.current);
      originalDocumentRef.current = result.layout;
      revisionRef.current = result.revision;
    } catch (error) {
      if (error instanceof StaleWriteError) {
        setStaleConflict({ theirs: error.layout, revision: error.revision });
      } else {
        setSaveError(error instanceof Error ? error.message : "This dashboard could not be saved.");
      }
    } finally {
      setIsSaving(false);
    }
  }, [layoutId]);

  const scheduleSave = useCallback((): void => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      void save();
    }, SAVE_DEBOUNCE_MS);
  }, [save]);

  useEffect(() => {
    if (state.dirty) scheduleSave();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires once per dirty edit, keyed on layout identity
  }, [state.layout, state.dirty]);

  useEffect(() => {
    const flushNow = (): void => {
      void save();
    };
    document.addEventListener("visibilitychange", flushNow);
    window.addEventListener("pagehide", flushNow);
    return () => {
      document.removeEventListener("visibilitychange", flushNow);
      window.removeEventListener("pagehide", flushNow);
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, [save]);

  return {
    layout: state.layout,
    isEditing: state.isEditing,
    canUndo: state.undoSnapshot !== null,
    isSaving,
    saveError,
    staleConflict,
    lastForkIdMap: state.lastForkIdMap,
    setEditing: (isEditing) => dispatch({ type: "SET_EDITING", isEditing }),
    addWidget: (kind) => dispatch({ type: "ADD_WIDGET", kind }),
    removeWidget: (widgetId) => dispatch({ type: "REMOVE_WIDGET", widgetId }),
    duplicateWidget: (widgetId) => dispatch({ type: "DUPLICATE_WIDGET", widgetId }),
    moveWidget: (widgetId, toIndex) => dispatch({ type: "MOVE_WIDGET", widgetId, toIndex }),
    resizeWidget: (widgetId, size) => dispatch({ type: "RESIZE_WIDGET", widgetId, size }),
    configureWidget: (widgetId, config, title) =>
      dispatch({ type: "CONFIGURE_WIDGET", widgetId, config, title }),
    undoLastEdit: () => dispatch({ type: "UNDO" }),
    keepMine: () => {
      setStaleConflict(null);
      // Bump the held revision to the server's, so the next debounced save wins the race rather
      // than bouncing off `stale_write` a second time.
      if (staleConflict) revisionRef.current = staleConflict.revision;
      scheduleSave();
    },
    useTheirs: () => {
      if (!staleConflict) return;
      originalDocumentRef.current = staleConflict.theirs;
      revisionRef.current = staleConflict.revision;
      setStaleConflict(null);
      dispatch({ type: "REPLACE", layout: toEditableLayout(staleConflict.theirs) });
    },
    flush: () => {
      void save();
    },
  };
}
