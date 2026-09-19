/**
 * Which sign-in methods the server says are available right now — WEB_DESIGN.md task brief:
 * "the Google and Apple buttons render ONLY when the server says that provider is configured."
 * `AuthProvider` already fetches `GET /v1/auth/methods` (directly, or as part of the session
 * bootstrap) as the one source of truth; this hook just reads it back.
 */
import { useAuth, type AuthContextValue } from "./AuthProvider";
import type { AuthMethods } from "../api/session";

export interface ProvidersState {
  readonly methods: AuthMethods | null;
  readonly isLoading: boolean;
  readonly isGoogleEnabled: boolean;
  readonly isAppleEnabled: boolean;
  readonly signupMode: "open" | "invite" | "closed" | null;
}

export function useProviders(): ProvidersState {
  const { methods, isLoading }: AuthContextValue = useAuth();
  return {
    methods,
    isLoading,
    isGoogleEnabled: methods?.google.enabled ?? false,
    isAppleEnabled: methods?.apple.enabled ?? false,
    signupMode: methods?.password.signupMode ?? null,
  };
}
