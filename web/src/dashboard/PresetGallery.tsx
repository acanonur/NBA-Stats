/**
 * `/presets` — `GET /v1/presets`'s nine (well, twelve today) starter dashboards, read from the
 * bundled `PRESETS_DOCUMENT` (WP0-generated, verbatim from `contracts/presets.json`) so the
 * gallery needs no round trip to render. "Use this" is the one call that actually reaches the
 * server: `POST /v1/dashboards {presetKey}`.
 */
import { useState, type JSX } from "react";
import { useNavigate } from "react-router-dom";
import { PRESETS_DOCUMENT } from "../generated/contracts";
import { Text } from "../design/Text";
import { ApiError, createDashboardFromPreset } from "../api/dashboards";
import styles from "./PresetGallery.module.css";

interface PresetSummary {
  readonly presetKey: string;
  readonly name: string;
  readonly tagline: string;
  readonly widgets: readonly unknown[];
}

export function PresetGallery(): JSX.Element {
  const navigate = useNavigate();
  const [pendingKey, setPendingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const presets = PRESETS_DOCUMENT.presets as readonly PresetSummary[];

  // Not named `useXxx`: that prefix marks a function as a Hook to the linter's naming
  // convention, and this one is called from a plain `onClick`, not a component body.
  async function applyPreset(presetKey: string): Promise<void> {
    setPendingKey(presetKey);
    setError(null);
    try {
      const result = await createDashboardFromPreset(presetKey);
      const layoutId = (result.layout as { readonly id?: string }).id;
      if (layoutId) await navigate(`/d/${encodeURIComponent(layoutId)}`);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "That preset could not be created.");
    } finally {
      setPendingKey(null);
    }
  }

  return (
    <div>
      <Text as="p" style="sectionTitle">
        Presets
      </Text>
      {error && (
        <p role="alert">
          <Text style="caption" color="negative">
            {error}
          </Text>
        </p>
      )}
      <div className={styles.grid}>
        {presets.map((preset) => (
          <div key={preset.presetKey} className={styles.card}>
            <Text style="widgetTitle">{preset.name}</Text>
            <Text as="p" style="caption" color="secondary">
              {preset.tagline}
            </Text>
            <Text style="caption" color="tertiary">
              {preset.widgets.length} widget{preset.widgets.length === 1 ? "" : "s"}
            </Text>
            <button
              type="button"
              className={styles.button}
              disabled={pendingKey === preset.presetKey}
              onClick={() => void applyPreset(preset.presetKey)}
            >
              {pendingKey === preset.presetKey ? "Adding…" : "Use this"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
