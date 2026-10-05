/**
 * The single door to the Hardwood API — CONTRACT-FOR-AGENTS.md §4. Every other `api/*.ts`
 * module (`resolve.ts`, `session.ts`, `dashboards.ts`, `sync.ts`) calls {@link fetchJson}; no
 * component or widget may import this file directly (`eslint.config.js` enforces that with a
 * directory-scoped `no-restricted-imports` rule). That indirection is what makes the
 * single-origin, cookie-based session model (WEB_DESIGN.md §0) enforceable in exactly one place:
 *
 *   - `credentials: "same-origin"` always — there is no cross-origin case in this app.
 *   - The path is joined to `/v1` — callers pass `"/auth/session"`, never `"/v1/auth/session"`.
 *   - A mutating method always sends `X-Hardwood-CSRF` (`accounts/csrf.py`'s `CSRF_HEADER`) from
 *     the in-memory token {@link setCsrfToken} was last given — never a cookie, never
 *     `localStorage` (WEB_DESIGN.md §7.3).
 *   - A non-2xx response is parsed as `ErrorEnvelope` and thrown as {@link ApiError}; a body that
 *     fails to parse that way throws `ApiError` with `code: "bad_request"` and the response's own
 *     status, never a raw `SyntaxError`.
 *   - A `401` clears the in-memory auth context and routes to `/sign-in?next=<path>` — the one
 *     place that redirect happens, so every caller gets it for free (WEB_DESIGN.md §7.6: "auth
 *     failure is a page-level outcome, never a grey tile").
 *
 * DEVIATION from CONTRACT-FOR-AGENTS.md §4, recorded rather than silent (that document invites
 * exactly this back-to-WP0 note when the frozen shape does not fit what shipped):
 *
 *   1. `FetchJsonOptions.method` gained `"PATCH"` — `routes_me.py::patch_me` is a real, already
 *      landed `PATCH /v1/me` route, and a client that cannot spell that verb cannot implement
 *      Settings' "save display name" without smuggling a partial update through `PUT`.
 *   2. `FetchJsonOptions` gained `onUnauthorized?: "redirect" | "ignore"` (default `"redirect"`).
 *      The frozen text says a `401` always redirects, but two real call sites need to be able to
 *      answer `401` *without* firing that redirect: the auth bootstrap read of
 *      `GET /v1/auth/session` (a signed-out visitor on `/` is not a session that "expired"), and
 *      `POST /v1/auth/login` itself (a wrong password is a normal, in-place form error, not a
 *      session-expiry event — redirecting the sign-in page to `/sign-in` on a failed sign-in
 *      would be absurd). `session.ts` passes `"ignore"` at exactly those two call sites and
 *      nowhere else, so every other 401 in the app still gets the page-level redirect for free.
 *   3. `FetchJsonOptions` gained `headers?: Readonly<Record<string, string>>`, and `ApiError`
 *      gained `readonly raw: unknown` (the whole parsed non-2xx body, not just its `.error`
 *      half). `PUT /v1/dashboards/{id}` needs an `If-Match` request header the frozen shape has
 *      no room for, and its `409 stale_write` response carries `layout`/`revision` fields
 *      alongside `error` that only `dashboards.ts` knows how to interpret — `raw` is what lets
 *      that one caller read them without widening `ErrorBody` itself for everyone else.
 */
import type { ErrorBody, ErrorEnvelope } from "./types";

/** Every non-2xx response, and every {@link ApiError} thrown by {@link fetchJson}, carries this
 * shape (CONTRACT-FOR-AGENTS.md §4). */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly recoverable: boolean;
  readonly field: string | null;
  readonly requestId: string | null;
  /** The whole parsed non-2xx body, not just its `.error` half — see deviation 3 above. */
  readonly raw: unknown;

  constructor(status: number, body: ErrorBody, raw: unknown = { error: body }) {
    super(body.message);
    this.name = "ApiError";
    this.code = body.code;
    this.status = status;
    this.recoverable = body.recoverable;
    this.field = body.field;
    this.requestId = body.requestId;
    this.raw = raw;
  }
}

export interface FetchJsonOptions {
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** JSON-serialised; omit for GET/DELETE. */
  readonly body?: unknown;
  readonly signal?: AbortSignal;
  /** See the module docstring's DEVIATION note. Defaults to `"redirect"`. */
  readonly onUnauthorized?: "redirect" | "ignore";
  /** Extra request headers beyond `Content-Type` and the CSRF header this function already
   * sends — see deviation 3 above. Never a way to override either of those two. */
  readonly headers?: Readonly<Record<string, string>>;
}

/** `accounts/csrf.py`'s `CSRF_HEADER` — the exact header name the server checks. */
export const CSRF_HEADER = "X-Hardwood-CSRF";

const MUTATING_METHODS: ReadonlySet<string> = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;

/** Stores the CSRF token an authenticated response handed back, in memory only — never a
 * cookie, never `localStorage` (WEB_DESIGN.md §7.3). Call with `null` on sign-out. */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

/** For tests and for `AuthProvider` to read back what it just set. */
export function getCsrfToken(): string | null {
  return csrfToken;
}

export type UnauthorizedHandler = (nextPath: string) => void;

let unauthorizedHandler: UnauthorizedHandler | null = null;

/** `AuthProvider` registers the one handler that turns a `401` into "clear auth context, then
 * navigate to `/sign-in?next=…`" (WEB_DESIGN.md §7.6). Passing `null` unregisters it — used by
 * tests so one test's handler is never called by the next. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler;
}

function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}

async function parseErrorBody(response: Response): Promise<{ body: ErrorBody; raw: unknown }> {
  try {
    const parsed: unknown = await response.json();
    if (
      parsed &&
      typeof parsed === "object" &&
      "error" in parsed &&
      parsed.error &&
      typeof parsed.error === "object"
    ) {
      return { body: (parsed as ErrorEnvelope).error, raw: parsed };
    }
  } catch {
    // fall through to the synthesised body below
  }
  const body: ErrorBody = {
    code: "bad_request",
    message: `The server answered ${response.status} with a body this app could not read.`,
    recoverable: false,
    field: null,
    requestId: null,
  };
  return { body, raw: { error: body } };
}

/**
 * The one function every other `api/*.ts` module calls to reach the service. See the module
 * docstring for the guarantees this makes and the two documented deviations from
 * CONTRACT-FOR-AGENTS.md §4.
 */
export async function fetchJson<T>(path: string, options: FetchJsonOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { ...options.headers };
  let body: string | undefined;

  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  if (MUTATING_METHODS.has(method) && csrfToken) {
    headers[CSRF_HEADER] = csrfToken;
  }

  let response: Response;
  try {
    response = await fetch(`/v1${path}`, {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, {
      code: "network_error",
      message: "Could not reach Hardwood. Check your connection and try again.",
      recoverable: true,
      field: null,
      requestId: null,
    });
  }

  if (!response.ok) {
    const { body: errorBody, raw } = await parseErrorBody(response);
    if (response.status === 401 && (options.onUnauthorized ?? "redirect") === "redirect") {
      unauthorizedHandler?.(currentPath());
    }
    throw new ApiError(response.status, errorBody, raw);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const contentLength = response.headers.get("content-length");
  if (contentLength === "0") {
    return undefined as T;
  }

  const text = await response.text();
  if (text.length === 0) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

/** The origin this app is served from — always same-origin per WEB_DESIGN.md §0, exposed only
 * so a component can build an absolute URL (e.g. a copy-link button) without hard-coding
 * `location.origin` in six different files. */
export function apiOrigin(): string {
  return typeof window === "undefined" ? "" : window.location.origin;
}
