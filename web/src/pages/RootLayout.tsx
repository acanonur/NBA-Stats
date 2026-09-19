/**
 * The root route's element: `AuthProvider` (which needs router context for its `401 -> /sign-in`
 * redirect — WEB_DESIGN.md §7.6) wrapping the page chrome every route shares — a header whose
 * nav appears only once signed in, `<Outlet>`, and `Footer` (the NBA attribution string, on
 * every page, per the task brief).
 */
import type { JSX } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "../auth/AuthProvider";
import { Text } from "../design/Text";
import { Footer } from "./Footer";
import styles from "./RootLayout.module.css";

const NAV_LINKS: ReadonlyArray<{ readonly to: string; readonly label: string }> = [
  { to: "/tonight", label: "Tonight" },
  { to: "/today", label: "Today" },
  { to: "/fantasy", label: "Fantasy" },
  { to: "/leaders", label: "Leaders" },
];

function Shell(): JSX.Element {
  const { user, isLoading, logout } = useAuth();
  const navigate = useNavigate();

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
                  void logout().then(() => navigate("/"));
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
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}

export function RootLayout(): JSX.Element {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
