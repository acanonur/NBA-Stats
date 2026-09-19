/** Which saved dashboard is open, and a way to switch — `GET /v1/dashboards`, one `<select>`. */
import type { JSX } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { listDashboards } from "../api/dashboards";
import styles from "./LayoutSwitcher.module.css";

export interface LayoutSwitcherProps {
  readonly currentLayoutId: string;
}

export function LayoutSwitcher({ currentLayoutId }: LayoutSwitcherProps): JSX.Element {
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["dashboards"], queryFn: listDashboards });
  const dashboards = data ?? [];

  return (
    <div className={styles.row}>
      <select
        className={styles.select}
        value={currentLayoutId}
        onChange={(event) => void navigate(`/d/${encodeURIComponent(event.target.value)}`)}
        aria-label="Switch dashboard"
      >
        {dashboards.map((dashboard) => (
          <option key={dashboard.layoutId} value={dashboard.layoutId}>
            {dashboard.name}
          </option>
        ))}
      </select>
      <Link className={styles.link} to="/presets">
        New from a preset
      </Link>
    </div>
  );
}
