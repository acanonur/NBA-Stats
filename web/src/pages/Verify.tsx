/**
 * `/verify` — landed on after `GET /v1/auth/verify?token=…` 303s here (either straight to
 * `/settings?verified=1` on success, or here with `?expired=1` on a spent/expired token). This
 * page itself makes no request: `routes_auth.py::verify_email` is explicit that a `GET` never
 * signs anyone in (WEB_DESIGN.md §4.6 — "an unverified email account can sign in and use every
 * feature"), so the only thing left to do here is explain and offer to resend.
 */
import { useState, type JSX } from "react";
import { useSearchParams } from "react-router-dom";
import { resendVerification } from "../api/session";
import { Text } from "../design/Text";
import styles from "../auth/forms.module.css";

export default function Verify(): JSX.Element {
  const [params] = useSearchParams();
  const expired = params.get("expired") === "1";
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sent">("idle");

  async function resend(): Promise<void> {
    // `202 checkYourEmail` either way (WEB_DESIGN.md §4.9) — there is no error branch to show
    // that would not itself leak whether the address exists.
    await resendVerification(email);
    setStatus("sent");
  }

  return (
    <div style={{ maxWidth: 420, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Verify your email
      </Text>
      {expired ? (
        <>
          <Text as="p" style="tableCell">
            That verification link has expired or was already used. You can still sign in and
            use Hardwood — a verified email is only needed for account-recovery email, never for
            everyday use.
          </Text>
          <label className={styles.field}>
            <Text style="tableHeader" as="span">
              Resend the link to
            </Text>
            <input
              className={styles.input}
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <button className={styles.submit} type="button" onClick={() => void resend()}>
            Resend verification email
          </button>
          {status === "sent" && (
            <Text style="caption" color="positive">
              If that address has an account, a new link is on its way.
            </Text>
          )}
        </>
      ) : (
        <Text as="p" style="tableCell">
          Check your email for a verification link.
        </Text>
      )}
    </div>
  );
}
