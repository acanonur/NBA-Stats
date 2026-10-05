/** A tiny debounced wrapper over `api/dashboards.ts::searchPlayers`, shared by `PlayerField`,
 * `PlayerListField` and `SubjectField`. */
import { useEffect, useState } from "react";
import { searchPlayers, type PlayerSearchResult } from "../../api/dashboards";

const MIN_QUERY_LENGTH = 2;

export function usePlayerSearch(query: string): {
  readonly results: readonly PlayerSearchResult[];
  readonly isSearching: boolean;
} {
  const trimmed = query.trim();
  const [results, setResults] = useState<readonly PlayerSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    // A too-short query needs no state at all — the hook derives `[]`/`false` for it directly
    // below, rather than an effect syncing `results`/`isSearching` back to their own defaults.
    if (trimmed.length < MIN_QUERY_LENGTH) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setIsSearching(true);
      void searchPlayers(trimmed)
        .then((found) => {
          if (!cancelled) setResults(found);
        })
        .catch(() => {
          if (!cancelled) setResults([]);
        })
        .finally(() => {
          if (!cancelled) setIsSearching(false);
        });
    }, 250);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [trimmed]);

  if (trimmed.length < MIN_QUERY_LENGTH) {
    return { results: [], isSearching: false };
  }
  return { results, isSearching };
}
