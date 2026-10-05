/**
 * Google / Apple sign-in buttons. Each renders ONLY when `useProviders` says the server has that
 * provider configured — no greyed-out button for a provider nobody set up
 * (`accounts/providers/__init__.py::provider_status`, WEB_DESIGN.md task brief).
 *
 * `GET /v1/auth/{provider}/start` is a real top-level redirect (it 302s straight to Google/Apple
 * and sets the `hw_oauth` transaction cookie on the way), so this is a plain link, never a
 * `fetch` — the whole OAuth dance is server-side and no browser SDK is involved (WEB_DESIGN.md
 * §7.9).
 */
import type { JSX } from "react";
import { useProviders } from "./useProviders";
import styles from "./ProviderButtons.module.css";

export interface ProviderButtonsProps {
  /** Where to return to after a successful sign-in — becomes `?next=` on the start URL. */
  readonly next?: string;
}

export function ProviderButtons({ next }: ProviderButtonsProps): JSX.Element | null {
  const { isGoogleEnabled, isAppleEnabled } = useProviders();
  if (!isGoogleEnabled && !isAppleEnabled) return null;

  const query = next ? `?next=${encodeURIComponent(next)}` : "";

  return (
    <div className={styles.row}>
      {isGoogleEnabled && (
        <a className={styles.button} href={`/v1/auth/google/start${query}`}>
          Continue with Google
        </a>
      )}
      {isAppleEnabled && (
        <a className={styles.button} href={`/v1/auth/apple/start${query}`}>
          Continue with Apple
        </a>
      )}
    </div>
  );
}
