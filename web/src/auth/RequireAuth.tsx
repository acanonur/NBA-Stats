/**
 * Route guard for every `session`-tier route in the routing table (WEB_DESIGN.md §7.2). Renders
 * nothing (a loading placeholder) while the auth bootstrap is in flight, so a signed-in reader on
 * a slow connection never flashes the sign-in page before their session is confirmed.
 */
import type { JSX } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import { Text } from "../design/Text";

export function RequireAuth(): JSX.Element {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return (
      <div style={{ padding: "var(--hw-space-xl)" }}>
        <Text style="caption" color="secondary">
          Loading…
        </Text>
      </div>
    );
  }

  if (!user) {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/sign-in?next=${encodeURIComponent(next)}`} replace />;
  }

  return <Outlet />;
}
