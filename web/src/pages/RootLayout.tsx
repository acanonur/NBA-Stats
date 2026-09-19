/**
 * The root route's element: `AuthProvider` (which needs router context for its `401 -> /sign-in`
 * redirect — WEB_DESIGN.md §7.6) wrapping the page chrome every route shares — a header whose
 * nav appears only once signed in, `<Outlet>`, and `Footer` (the NBA attribution string, on
 * every page, per the task brief).
 */
import { useState, type JSX, type ReactNode } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";
import { Footer } from "./Footer";
import { RouteError } from "./NotFound";
import styles from "./RootLayout.module.css";

const NAV_LINKS: ReadonlyArray<{ readonly to: string; readonly label: string }> = [
  { to: "/tonight", label: "Tonight" },
  { to: "/today", label: "Today" },
  { to: "/fantasy", label: "Fantasy" },
  { to: "/leaders", label: "Leaders" },
];

function Shell({ children }: { readonly children?: ReactNode }): JSX.Element {
  const { user, isLoading, logout } = useAuth();
  const navigate = useNavigate();
  const [signOutError, setSignOutError] = useState<string | null>(null);

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <NavLink className={styles.wordmark} to="/">
          <Text style="sectionTitle">Hardwood</Text>
        </NavLink>
        {user && (
          <nav className={styles.nav}>
            {NAV_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                className={({ isActive }) => (isActive ? styles.navLinkActive : styles.navLink)}
              >
                {link.label}
              </NavLink>
            ))}
            <NavLink
              to="/settings"
              className={({ isActive }) => (isActive ? styles.navLinkActive : styles.navLink)}
            >
              Settings
            </NavLink>
          </nav>
        )}
        <div className={styles.spacer} />
        {!isLoading && (
          <div className={styles.authLinks}>
            {user ? (
              <button
                type="button"
                className={styles.signOutButton}
                onClick={() => {
                  setSignOutError(null);
                  void logout()
                    .then(() => navigate("/"))
                    // `logout` goes through `fetchJson`, which throws on any non-2xx and on a
                    // dropped connection. Unhandled, a failed sign-out looked exactly like a
                    // successful one that had not navigated yet.
                    .catch(() => setSignOutError("Could not sign out. Check your connection and try again."));
                }}
              >
                Sign out
              </button>
            ) : (
              <>
                <NavLink className={styles.navLink} to="/sign-in">
                  Sign in
                </NavLink>
                <NavLink className={styles.navLink} to="/sign-up">
                  Sign up
                </NavLink>
              </>
            )}
          </div>
        )}
      </header>
      <main className={styles.main}>
        {signOutError && (
          <p role="alert">
            <Text style="caption" color="negative">
              {signOutError}
            </Text>
          </p>
        )}
        {children ?? <Outlet />}
      </main>
      <Footer />
    </div>
  );
}

export interface RootLayoutProps {
  /** Set by `routes.tsx`'s root `errorElement`: render the error page *inside* the shell
   * rather than the matched route, so the header, the nav and the NBA attribution footer
   * survive whatever threw. */
  readonly hasError?: boolean;
}

export function RootLayout({ hasError = false }: RootLayoutProps = {}): JSX.Element {
  return (
    <AuthProvider>
      <Shell>{hasError ? <RouteError /> : undefined}</Shell>
    </AuthProvider>
  );
}
