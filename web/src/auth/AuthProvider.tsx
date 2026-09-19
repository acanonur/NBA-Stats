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
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type JSX,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
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
  const navigate = useNavigate();

  const bootstrap = useCallback(async (): Promise<void> => {
    try {
      const session = await getSession();
      setUser(session.user);
      setMethods(session.methods);
      setCsrfToken(session.csrfToken);
    } catch {
      // `GET /v1/auth/session` requires a live session and 401s for a signed-out visitor — the
      // ordinary case here, not a failure. `GET /v1/auth/methods` needs no session at all, so
      // the sign-in/sign-up screens still learn which providers to offer.
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
  }, []);

  useEffect(() => {
    // `bootstrap` only ever calls `setUser`/`setMethods`/`setCsrfToken`/`setIsLoading` after an
    // `await` (a genuine network round trip), never synchronously — this is the standard
    // "fetch on mount" effect, not state that could instead be derived during render.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    setUnauthorizedHandler((nextPath) => {
      setUser(null);
      setCsrfToken(null);
      // Never bounce someone already headed to an auth screen back into a redirect loop.
      if (!nextPath.startsWith("/sign-in")) {
        void navigate(`/sign-in?next=${encodeURIComponent(nextPath)}`);
      }
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate]);

  const login = useCallback(async (email: string, password: string): Promise<SessionUser> => {
    const result = await apiLogin(email, password);
    setUser(result.user);
    setCsrfToken(result.csrfToken);
    return result.user;
  }, []);

  const logout = useCallback(async (all = false): Promise<void> => {
    await apiLogout(all);
    setUser(null);
    setCsrfToken(null);
  }, []);

  const updateProfile = useCallback(async (body: PatchMeBody): Promise<SessionUser> => {
    const updated = await patchMe(body);
    setUser(updated);
    return updated;
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, methods, isLoading, login, logout, refresh: bootstrap, updateProfile }),
    [user, methods, isLoading, login, logout, bootstrap, updateProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}
