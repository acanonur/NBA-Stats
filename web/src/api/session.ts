/**
 * `/v1/auth/*` and the account half of `/v1/me/*` — CONTRACT-FOR-AGENTS.md §4 leaves this
 * module's exact shape unfrozen ("ordinary CRUD wrappers around endpoints CONTRACT.md §3 already
 * fixes"), so these types are transcribed field-by-field from
 * `backend/nbastats/api/routes_auth.py` and `routes_me.py` — the wire shapes actually served,
 * not a guess from the design doc where the two differ.
 *
 * Every mutating call here reaches `fetchJson` with the default `onUnauthorized: "redirect"`
 * EXCEPT the two noted inline, for the reason given in `client.ts`'s module docstring.
 *
 * Also re-exports {@link ApiError}, {@link setCsrfToken} and {@link setUnauthorizedHandler}:
 * `eslint.config.js` restricts importing `api/client.ts` to `src/api/**`, and `AuthProvider` (the
 * one place that manages the in-memory CSRF token and the `401` redirect handler, WEB_DESIGN.md
 * §7.3/§7.6) is not part of `src/api/**`, so it reaches both through this module instead.
 */
import { ApiError, fetchJson, setCsrfToken, setUnauthorizedHandler } from "./client";

export { ApiError, setCsrfToken, setUnauthorizedHandler };

export type ProviderName = "google" | "apple";

export interface PasswordMethodStatus {
  readonly enabled: true;
  readonly signupMode: "open" | "invite" | "closed";
}

export interface ProviderMethodStatus {
  readonly enabled: boolean;
  readonly reason: string | null;
}

export interface MailStatus {
  /** `false` on the default `HARDWOOD_MAILER=log`: the message is written to the server log,
   * not sent. Every screen that would otherwise say "check your email" reads this first. */
  readonly delivers: boolean;
}

/** `GET /v1/auth/methods` — read fresh on every call; never cache this across a page load. */
export interface AuthMethods {
  readonly password: PasswordMethodStatus;
  readonly google: ProviderMethodStatus;
  readonly apple: ProviderMethodStatus;
  readonly mail: MailStatus;
}

/** `routes_auth.py::_user_out` / `routes_me.py::_user_out` — the one `User` wire shape both
 * modules serialise. */
export interface SessionUser {
  readonly userId: string;
  readonly email: string | null;
  readonly emailVerified: boolean;
  readonly displayName: string | null;
  readonly isPrivateRelay: boolean;
  readonly hasPassword: boolean;
  readonly identities: readonly ProviderName[];
  readonly favoritePlayerId: number | null;
  readonly favoriteTeamId: number | null;
  readonly selectedDashboardId: string | null;
  readonly theme: "system" | "light" | "dark";
  readonly createdAt: string;
}

export interface SessionInfo {
  readonly user: SessionUser;
  readonly csrfToken: string;
  readonly methods: AuthMethods;
}

export interface LoginResult {
  readonly user: SessionUser;
  readonly csrfToken: string;
}

/**
 * The SPA's entire "who am I, and what can I show" bootstrap. `onUnauthorized: "ignore"`: a
 * signed-out visitor on `/` is the ordinary case for this call, not a session that expired —
 * redirecting every anonymous page load to `/sign-in` would make the landing page and the legal
 * pages unreachable while signed out.
 */
export async function getSession(): Promise<SessionInfo> {
  return fetchJson<SessionInfo>("/auth/session", { onUnauthorized: "ignore" });
}

export async function getAuthMethods(): Promise<AuthMethods> {
  return fetchJson<AuthMethods>("/auth/methods", { onUnauthorized: "ignore" });
}

/**
 * A wrong password answers `401 invalid_credentials` — an ordinary in-place form error, not a
 * session-expiry event, so this call also opts out of the global redirect (`client.ts`'s module
 * docstring, deviation 2).
 */
export async function login(email: string, password: string): Promise<LoginResult> {
  return fetchJson<LoginResult>("/auth/login", {
    method: "POST",
    body: { email, password },
    onUnauthorized: "ignore",
  });
}

export async function logout(all = false): Promise<void> {
  await fetchJson<void>("/auth/logout", { method: "POST", body: { all } });
}

export interface SignupResult {
  readonly status: "checkYourEmail";
}

export async function signup(params: {
  readonly email: string;
  readonly password: string;
  readonly displayName?: string | null;
  readonly inviteCode?: string | null;
}): Promise<SignupResult> {
  return fetchJson<SignupResult>("/auth/signup", {
    method: "POST",
    body: params,
    onUnauthorized: "ignore",
  });
}

export async function resendVerification(email: string): Promise<SignupResult> {
  return fetchJson<SignupResult>("/auth/verify/resend", {
    method: "POST",
    body: { email },
    onUnauthorized: "ignore",
  });
}

export interface ForgotPasswordResult {
  readonly status: "checkYourEmail";
  /** Only present when `HARDWOOD_DEV_LINKS=1` and the caller is on loopback — a local dev
   * convenience, never something a real deployment sends. */
  readonly devLink?: string;
}

export async function forgotPassword(email: string): Promise<ForgotPasswordResult> {
  return fetchJson<ForgotPasswordResult>("/auth/password/forgot", {
    method: "POST",
    body: { email },
    onUnauthorized: "ignore",
  });
}

export async function resetPassword(token: string, password: string): Promise<void> {
  await fetchJson<void>("/auth/password/reset", {
    method: "POST",
    body: { token, password },
    onUnauthorized: "ignore",
  });
}

// --------------------------------------------------------------------------------------- /v1/me

export interface PatchMeBody {
  readonly displayName?: string | null;
  readonly favoritePlayerId?: number | null;
  readonly favoriteTeamId?: number | null;
  readonly theme?: "system" | "light" | "dark";
  readonly selectedDashboardId?: string | null;
}

export async function getMe(): Promise<SessionUser> {
  return fetchJson<SessionUser>("/me");
}

export async function patchMe(body: PatchMeBody): Promise<SessionUser> {
  return fetchJson<SessionUser>("/me", { method: "PATCH", body });
}

export interface ChangePasswordResult {
  readonly csrfToken: string;
}

export async function changePassword(params: {
  readonly currentPassword?: string | null;
  readonly newPassword: string;
}): Promise<ChangePasswordResult> {
  return fetchJson<ChangePasswordResult>("/me/password", { method: "POST", body: params });
}

export async function changeEmail(params: {
  readonly newEmail: string;
  readonly currentPassword?: string | null;
}): Promise<SignupResult> {
  return fetchJson<SignupResult>("/me/email", { method: "POST", body: params });
}

export interface SessionRow {
  readonly sessionId: string;
  readonly createdAt: string;
  readonly lastSeenAt: string;
  readonly userAgent: string | null;
  readonly ipPrefix: string | null;
  readonly authMethod: string;
  readonly current: boolean;
}

export async function listSessions(): Promise<readonly SessionRow[]> {
  const result = await fetchJson<{ readonly sessions: readonly SessionRow[] }>("/me/sessions");
  return result.sessions;
}

export async function revokeSession(sessionId: string): Promise<void> {
  await fetchJson<void>(`/me/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
}

export async function startIdentityLink(provider: ProviderName): Promise<{ readonly redirectUrl: string }> {
  return fetchJson(`/me/identities/${provider}/start`, { method: "POST" });
}

/** `DELETE /v1/me/identities/{provider}` answers `200` with a fresh `csrfToken` — NOT `204` —
 * because unlinking rotates the caller's session (WEB_DESIGN.md task brief; `routes_me.py`). */
export async function unlinkIdentity(provider: ProviderName): Promise<ChangePasswordResult> {
  return fetchJson<ChangePasswordResult>(`/me/identities/${provider}`, { method: "DELETE" });
}

export interface ExportedAccount {
  readonly account: SessionUser;
  readonly dashboards: readonly unknown[];
  readonly exportedAt: string;
}

export async function exportAccount(): Promise<ExportedAccount> {
  return fetchJson<ExportedAccount>("/me/export");
}

export async function deleteAccount(): Promise<void> {
  await fetchJson<void>("/me", { method: "DELETE", body: { confirm: "DELETE" } });
}
