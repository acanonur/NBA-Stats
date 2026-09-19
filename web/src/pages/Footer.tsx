/**
 * The NBA attribution string goes in the footer of every page, per the task brief. It is read
 * from `GET /v1/meta` rather than hard-coded, because a seeded demo deployment serves a
 * different, longer disclaimer (`routes_meta.py::DEMO_ATTRIBUTION`) that must not be replaced by
 * the real-data credit line — `getAttribution`'s `initialData` is that real-data string, so nothing
 * ever renders blank while the fetch is in flight, and the demo string still overwrites it once
 * the real one comes back.
 */
import type { JSX } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getAttribution } from "../api/dashboards";
import { Text } from "../design/Text";
import styles from "./Footer.module.css";

const FALLBACK_ATTRIBUTION = "Stats via NBA.com. Not endorsed by or affiliated with the NBA.";

export function Footer(): JSX.Element {
  const { data: attribution } = useQuery({
    queryKey: ["meta", "attribution"],
    queryFn: getAttribution,
    staleTime: 24 * 60 * 60 * 1000,
    initialData: FALLBACK_ATTRIBUTION,
  });

  return (
    <footer className={styles.footer}>
      <Text style="caption" color="tertiary">
        {attribution}
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
