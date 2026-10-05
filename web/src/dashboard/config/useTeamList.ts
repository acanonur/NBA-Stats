/** The 30-franchise directory, fetched once and shared by `TeamField`/`TeamListField`/
 * `SubjectField`. */
import { useEffect, useState } from "react";
import { listTeams } from "../../api/dashboards";
import type { TeamRef } from "../../api/types";

let cache: readonly TeamRef[] | null = null;

export function useTeamList(): readonly TeamRef[] {
  const [teams, setTeams] = useState<readonly TeamRef[]>(cache ?? []);

  useEffect(() => {
    if (cache) return;
    let cancelled = false;
    void listTeams()
      .then((result) => {
        cache = result;
        if (!cancelled) setTeams(result);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return teams;
}
