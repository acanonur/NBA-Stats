/**
 * `/auth/link-conflict` — landed on when `_finish_oauth` (`routes_auth.py`) raises `LinkConflict`
 * (`accounts/linking.py`). The three reasons match that module's `LinkConflict.kind` exactly.
 */
import type { JSX } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Text } from "../design/Text";

const PROVIDER_NAME: Record<string, string> = { google: "Google", apple: "Apple" };

const REASON_COPY: Record<string, (provider: string) => string> = {
  password_account: (provider) =>
    `An account already exists with this email and a password. Sign in with your password, ` +
    `then add ${provider} from Settings.`,
  unverified_account: (provider) =>
    `An account already exists with this email, but it has not been verified yet. Check that ` +
    `inbox for a verification link, sign in, then add ${provider} from Settings.`,
  identity_taken: (provider) =>
    `That ${provider} account is already linked to a different Hardwood account. Sign in to ` +
    `that account instead, or contact support if that is not you.`,
};

export default function LinkConflict(): JSX.Element {
  const [params] = useSearchParams();
  const provider = params.get("provider") ?? "";
  const reason = params.get("reason") ?? "";
  const email = params.get("email");
  const providerName = PROVIDER_NAME[provider] ?? provider;
  const explain = REASON_COPY[reason];

  return (
    <div style={{ maxWidth: 480, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        {providerName || "Sign-in"} could not be linked
      </Text>
      <Text as="p" style="tableCell">
        {explain ? explain(providerName || "that provider") : "That sign-in could not be completed."}
      </Text>
      {email && (
        <Text style="caption" color="secondary">
          Account: {email}
        </Text>
      )}
      <Text as="p" style="caption">
        <Link to="/sign-in">Sign in</Link> · <Link to="/forgot">Forgot your password?</Link>
      </Text>
    </div>
  );
}
