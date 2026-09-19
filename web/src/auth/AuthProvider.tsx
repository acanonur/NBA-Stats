/**
 * One context over `GET /v1/auth/session` — WEB_DESIGN.md §7.3. Holds the signed-in user (or
 * `null`), the provider-availability map, and the CSRF token **in memory only** (never
 * `localStorage`, never a cookie). Also the one place `client.ts`'s `setUnauthorizedHandler` is
 * registered, so a `401` anywhere in the app clears this context and navigates to
 * `/sign-in?next=…` — WEB_DESIGN.md §7.6: "auth failure is a page-level outcome, never a grey
 * tile."
 *
 * Must render underneath `<RouterProvider>` (routes.tsx makes it the root route's element) so
 * `useNavigate` resolves against the real router.
 *
 * Why this component pins the account it bootstrapped with
 * ------------------------------------------------------------------
 * Cookies are scoped by host, not by port. Any other process serving HTTP on any other port of
 * `127.0.0.1` — a second dev server, a container's published port, a compromised desktop app —
 * can answer one request with `Set-Cookie: hw_session=<attacker's session>; Path=/` and replace
 * the cookie outright; `HttpOnly` stops `document.cookie`, not `Set-Cookie`. On a
 * `HARDWOOD_ALLOW_INSECURE_COOKIES=1` LAN deployment an on-path device does the same by
 * injecting the header into any plaintext response. The request that follows is then genuinely
 * authenticated as the attacker, and because `accounts/sessions.py::csrf_token_for` derives the
 * CSRF token from whichever cookie arrived, the token matches too. No server-side check can
 * separate that from an honest sign-in — the browser presents one cookie jar.
 *
 * So the check lives here: `pinnedUserId` records who this tab signed in as, every
 * re-bootstrap compares it, and a change is treated as a forced sign-out with a visible
 * notice rather than a silent identity swap. It is a tripwire, not a fix; the fix is https.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type JSX,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { resetObservedSyncVersion } from "../api/resolve";
import { setCsrfToken, setUnauthorizedHandler } from "../api/session";
import {
  getAuthMethods,
  getSession,
  login as apiLogin,
  logout as apiLogout,
  patchMe,
  type AuthMethods,
  type PatchMeBody,
  type SessionUser,
} from "../api/session";

export interface AuthContextValue {
  readonly user: SessionUser | null;
  readonly methods: AuthMethods | null;
  /** `true` until the initial `GET /v1/auth/session` bootstrap resolves. */
  readonly isLoading: boolean;
  /** Set when the session cookie started naming a *different* account than the one this tab
   * signed in as — see {@link AuthProvider}'s docstring. Until it is cleared by signing in
   * again, the app refuses to act. */
  readonly sessionConflict: boolean;
  readonly login: (email: string, password: string) => Promise<SessionUser>;
  readonly logout: (all?: boolean) => Promise<void>;
  readonly refresh: () => Promise<void>;
  readonly updateProfile: (body: PatchMeBody) => Promise<SessionUser>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { readonly children?: ReactNode }): JSX.Element {
  const [user, setUser] = useState<SessionUser | null>(null);
  const [methods, setMethods] = useState<AuthMethods | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionConflict, setSessionConflict] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  // The account this tab believes it is. Compared on every re-bootstrap; see the module
  // docstring for what a mismatch means and why nothing server-side can catch it.
  const pinnedUserId = useRef<string | null>(null);

  const bootstrap = useCallback(async (): Promise<void> => {
    try {
      const session = await getSession();
      if (pinnedUserId.current !== null && pinnedUserId.current !== session.user.userId) {
        // Forced login. Some other process on this host, or an on-path device on a plaintext
        // LAN deployment, answered a request with `Set-Cookie: hw_session=…` and replaced the
        // cookie. Every write from here would land in the attacker's account, and because the
        // CSRF token is derived from whichever cookie arrived, it matches — there is no
        // server-side check that can tell this apart from an honest sign-in.
        pinnedUserId.current = null;
        setUser(null);
        setCsrfToken(null);
        setSessionConflict(true);
        queryClient.clear();
        resetObservedSyncVersion();
        return;
      }
      pinnedUserId.current = session.user.userId;
      setUser(session.user);
      setMethods(session.methods);
      setCsrfToken(session.csrfToken);
    } catch {
      // `GET /v1/auth/session` requires a live session and 401s for a signed-out visitor — the
      // ordinary case here, not a failure. `GET /v1/auth/methods` needs no session at all, so
      // the sign-in/sign-up screens still learn which providers to offer.
      pinnedUserId.current = null;
      setUser(null);
      setCsrfToken(null);
      try {
        setMethods(await getAuthMethods());
      } catch {
        setMethods(null);
      }
    } finally {
      setIsLoading(false);
    }
  }, [queryClient]);

  useEffect(() => {
    // `bootstrap` only ever calls `setUser`/`setMethods`/`setCsrfToken`/`setIsLoading` after an
    // `await` (a genuine network round trip), never synchronously — this is the standard
    // "fetch on mount" effect, not state that could instead be derived during render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    setUnauthorizedHandler((nextPath) => {
      pinnedUserId.current = null;
      setUser(null);
      setCsrfToken(null);
      // A 401 is a sign-out too. Without this, the previous account's cached dashboards,
      // device list and widget payloads stayed in the module-singleton QueryClient and were
      // served synchronously to whoever signed in next in the same tab.
      queryClient.clear();
      resetObservedSyncVersion();
      // Never bounce someone already headed to an auth screen back into a redirect loop.
      if (!nextPath.startsWith("/sign-in")) {
        void navigate(`/sign-in?next=${encodeURIComponent(nextPath)}`);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, queryClient]);

  const login = useCallback(
    async (email: string, password: string): Promise<SessionUser> => {
      // Whoever was cached before this sign-in is not this person. Clearing first means no
      // component can paint the previous account's data while the new one loads.
      queryClient.clear();
      resetObservedSyncVersion();
      const result = await apiLogin(email, password);
      pinnedUserId.current = result.user.userId;
      setSessionConflict(false);
      setUser(result.user);
      setCsrfToken(result.csrfToken);
      return result.user;
    },
    [queryClient],
  );

  const logout = useCallback(
    async (all = false): Promise<void> => {
      await apiLogout(all);
      pinnedUserId.current = null;
      setUser(null);
      setCsrfToken(null);
      // The QueryClient is a module singleton created once in `App.tsx` and never recreated,
      // and this is an SPA: without this, the next person to sign in on a shared browser saw
      // the previous account's dashboard names in the switcher and their device list — with
      // user-agent strings and last-seen times — on /settings, each with a "Sign out" button.
      queryClient.clear();
      resetObservedSyncVersion();
    },
    [queryClient],
  );

  const updateProfile = useCallback(async (body: PatchMeBody): Promise<SessionUser> => {
    const updated = await patchMe(body);
    setUser(updated);
    return updated;
  }, []);

  // The theme preference round-tripped to SQLite and back and was then applied to nothing:
  // `data-theme` was never set on any element, so `generated/tokens.css`'s two overrides —
  // `:root:not([data-theme="light"])` inside the dark media query, and `:root[data-theme=
  // "dark"]` — were both unreachable, and picking "Light" on a dark-mode laptop did nothing
  // but persist. Deleting the attribute for "system" is what lets `prefers-color-scheme` win.
  const theme = user?.theme ?? "system";
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
  }, [theme]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      methods,
      isLoading,
      sessionConflict,
      login,
      logout,
      refresh: bootstrap,
      updateProfile,
    }),
    [user, methods, isLoading, sessionConflict, login, logout, bootstrap, updateProfile],
  );

  if (sessionConflict) {
    return (
      <AuthContext.Provider value={value}>
        <SessionConflictNotice />
      </AuthContext.Provider>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** The whole app, replaced. Nothing below this point may render, because everything below it
 * would be writing into an account the reader did not sign in to. */
function SessionConflictNotice(): JSX.Element {
  return (
    <div
      role="alert"
      style={{
        maxWidth: 520,
        margin: "0 auto",
        padding: "var(--hw-space-lg)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--hw-space-sm)",
      }}
    >
      <h1>You have been signed out</h1>
      <p>
        The account this tab is signed in to changed while it was open. That is not something
        Hardwood does on its own, so this tab has stopped rather than save anything to an
        account that may not be yours.
      </p>
      <p>
        Close this tab, open Hardwood again and sign in. If this keeps happening, something
        else on this machine or this network is setting cookies for this address — serving
        Hardwood over https is what stops it.
      </p>
      <p>
        <a href="/sign-in">Go to sign in</a>
      </p>
    </div>
  );
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}
