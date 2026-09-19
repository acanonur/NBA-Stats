/**
 * The NBA attribution string goes in the footer of every page, per the task brief. It is read
 * from `GET /v1/meta` rather than hard-coded, because a seeded demo deployment serves a
 * different, longer disclaimer (`routes_meta.py::DEMO_ATTRIBUTION`) saying the numbers are
 * invented — and `scripts/web.sh dev`, the documented first-run command, sets
 * `HARDWOOD_DEMO_MODE=1` by default, so that is what most first-hour readers are looking at.
 *
 * `placeholderData`, not `initialData`. In TanStack Query v5 `initialData` without
 * `initialDataUpdatedAt` is written into the cache with `dataUpdatedAt = now`, so with
 * `staleTime: 24h` the query was fresh on mount and the `queryFn` never ran — not once, in any
 * deployment. The demo disclaimer was unreachable, and a fully synthetic league was credited to
 * NBA.com on every screen. `placeholderData` is not written to the cache, so the fetch fires.
 *
 * The fallback shown while that fetch is in flight claims nothing about where the numbers came
 * from, because at that moment this component does not know.
 */
import type { JSX } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAttribution } from "../api/dashboards";
import { Text } from "../design/Text";
import styles from "./Footer.module.css";

/** Shown only while `GET /v1/meta` is in flight, or if it fails. Deliberately not the NBA.com
 * credit line: a footer that has not yet been told whose data this is must not guess. */
const FALLBACK_ATTRIBUTION = "Not endorsed by or affiliated with the NBA.";

export function Footer(): JSX.Element {
  const { data: attribution } = useQuery({
    queryKey: ["meta", "attribution"],
    queryFn: getAttribution,
    staleTime: 24 * 60 * 60 * 1000,
    placeholderData: FALLBACK_ATTRIBUTION,
  });

  return (
    <footer className={styles.footer}>
      <Text style="caption" color="tertiary">
        {/* `?? FALLBACK` as well as `placeholderData`: the placeholder covers the pending
            state, but once the query *errors* `data` is undefined again, and a footer that
            silently empties is worse than one that says the minimum truthfully. */}
        {attribution ?? FALLBACK_ATTRIBUTION}
      </Text>
      <nav className={styles.links}>
        <Link className={styles.link} to="/legal/terms">
          Terms
        </Link>
        <Link className={styles.link} to="/legal/privacy">
          Privacy
        </Link>
      </nav>
    </footer>
  );
}
