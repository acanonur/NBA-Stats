/**
 * `GET /v1/sync` and `GET /v1/sync/stream` — `backend/nbastats/api/routes_sync.py`, transcribed
 * field-for-field (not frozen by CONTRACT-FOR-AGENTS.md §4; see `session.ts`'s docstring).
 *
 * `subscribeToSync` is the one place `EventSource` is constructed. Same-origin, so the session
 * cookie rides along automatically — `EventSource` cannot set a header, which is one more reason
 * WEB_DESIGN.md §0's single-origin choice is load-bearing (§7.6).
 */
import type { GameRef } from "./types";
import { fetchJson } from "./client";

export interface SyncResponse {
  readonly syncVersion: number;
  readonly previousVersion: number | null;
  readonly serverTime: string;
  readonly dataThrough: string | null;
  readonly hasChanges: boolean;
  readonly changedDates: readonly string[];
  readonly finalizedGames: readonly GameRef[];
  readonly affectedPlayerIds: readonly number[];
  readonly invalidate: readonly string[];
  readonly nextPollAfterSeconds: number;
}

export async function getSync(since?: number | null): Promise<SyncResponse> {
  const query = since === undefined || since === null ? "" : `?since=${encodeURIComponent(since)}`;
  return fetchJson<SyncResponse>(`/sync${query}`);
}

export type SyncListener = (payload: SyncResponse) => void;

/**
 * Opens `EventSource("/v1/sync/stream")` and calls `onSync` for every `event: sync` frame,
 * starting with the baseline frame the server always sends first. Returns a disposer that closes
 * the connection — call it on unmount. Purely an optimisation over polling `getSync`; nothing in
 * the app requires this to be connected.
 */
export function subscribeToSync(onSync: SyncListener, since?: number | null): () => void {
  const query = since === undefined || since === null ? "" : `?since=${encodeURIComponent(since)}`;
  const source = new EventSource(`/v1/sync/stream${query}`);
  const handleMessage = (event: MessageEvent<string>): void => {
    try {
      onSync(JSON.parse(event.data) as SyncResponse);
    } catch {
      // A malformed frame is dropped; the next poll or frame recovers.
    }
  };
  source.addEventListener("sync", handleMessage);
  return () => {
    source.removeEventListener("sync", handleMessage);
    source.close();
  };
}
