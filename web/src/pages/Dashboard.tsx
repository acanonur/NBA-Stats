/**
 * `/d/:layoutId` — the product. Loads the saved dashboard, drives `useLayoutStore` (remounted
 * per `layoutId` via `key`), syncs edit mode with `?edit=1` (WEB_DESIGN.md §7.2: "a refresh
 * keeps you where you were and the back button leaves edit mode"), and resolves every widget
 * through `DashboardResolveProvider` — never calling `POST /v1/dashboard/resolve` itself.
 */
import { useEffect, useState, type JSX } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ApiError, getDashboard } from "../api/dashboards";
import { useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";
import { LoadingTile, ErrorTile } from "../design/StateViews";
import { LayoutSwitcher } from "../dashboard/LayoutSwitcher";
import { EditingToolbar } from "../dashboard/EditingToolbar";
import { WidgetFlowLayout } from "../dashboard/WidgetFlowLayout";
import { DashboardResolveProvider } from "../dashboard/DashboardResolveContext";
import { ConfigSheet } from "../dashboard/config/ConfigSheet";
import { useLayoutStore, type EditableWidget } from "../dashboard/useLayoutStore";
import type { ResolveWidgetRequest } from "../api/types";
import type { WidgetSizeKey } from "../generated/tokens";
import styles from "./Dashboard.module.css";

export default function Dashboard(): JSX.Element {
  const { layoutId } = useParams<{ layoutId: string }>();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["dashboard", layoutId],
    queryFn: () => getDashboard(layoutId!),
    enabled: !!layoutId,
  });

  if (!layoutId) return <ErrorTile message="No dashboard was named." isRetryable={false} />;
  if (isLoading) return <LoadingTile size="large" />;
  if (error) {
    return (
      <ErrorTile
        message={error instanceof ApiError ? error.message : "This dashboard could not be loaded."}
        isRetryable
        onRetry={() => void refetch()}
      />
    );
  }
  if (!data) return <ErrorTile message="This dashboard could not be loaded." isRetryable={false} />;

  return <DashboardEditor key={layoutId} layoutId={layoutId} document={data.layout} revision={data.revision} />;
}

function toResolveWidget(widget: EditableWidget): ResolveWidgetRequest {
  return { id: widget.id, kind: widget.kind, size: widget.size, title: widget.title, config: widget.config };
}

function DashboardEditor({
  layoutId,
  document,
  revision,
}: {
  readonly layoutId: string;
  readonly document: Record<string, unknown>;
  readonly revision: number;
}): JSX.Element {
  const store = useLayoutStore(layoutId, document, revision);
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [configuringWidgetId, setConfiguringWidgetId] = useState<string | null>(null);

  const urlWantsEditing = searchParams.get("edit") === "1";

  // The URL is the source of truth for edit mode; the store just needs to know so it can
  // snapshot for undo.
  useEffect(() => {
    if (store.isEditing !== urlWantsEditing) store.setEditing(urlWantsEditing);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-directional sync from the URL
  }, [urlWantsEditing]);

  useEffect(() => {
    if (store.isEditing === urlWantsEditing) return;
    const next = new URLSearchParams(searchParams);
    if (store.isEditing) next.set("edit", "1");
    else next.delete("edit");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- one-directional sync into the URL
  }, [store.isEditing]);

  const widgets = store.layout.widgets.map(toResolveWidget);
  const configuringWidget = store.layout.widgets.find((widget) => widget.id === configuringWidgetId) ?? null;

  return (
    <DashboardResolveProvider
      widgets={widgets}
      options={{
        layoutId,
        context: { favoritePlayerId: user?.favoritePlayerId, favoriteTeamId: user?.favoriteTeamId },
      }}
    >
      <div className={styles.header}>
        <LayoutSwitcher currentLayoutId={layoutId} />
        <Text style="caption" color="tertiary">
          {store.layout.name}
        </Text>
      </div>
      <EditingToolbar store={store} />
      <WidgetFlowLayout
        widgets={widgets}
        presentation={store.layout.presentation}
        isEditing={store.isEditing}
        onConfigure={setConfiguringWidgetId}
        onDuplicate={store.duplicateWidget}
        onRemove={store.removeWidget}
        onMoveUp={(id) => {
          const index = store.layout.widgets.findIndex((widget) => widget.id === id);
          if (index > 0) store.moveWidget(id, index - 1);
        }}
        onMoveDown={(id) => {
          const index = store.layout.widgets.findIndex((widget) => widget.id === id);
          if (index >= 0) store.moveWidget(id, index + 1);
        }}
        onResize={(id, size: WidgetSizeKey) => store.resizeWidget(id, size)}
      />
      {configuringWidget && (
        <ConfigSheet
          kind={configuringWidget.kind}
          title={configuringWidget.title ?? null}
          config={configuringWidget.config}
          onSave={(config, title) => {
            store.configureWidget(configuringWidget.id, config, title);
            setConfiguringWidgetId(null);
          }}
          onClose={() => setConfiguringWidgetId(null)}
        />
      )}
    </DashboardResolveProvider>
  );
}
