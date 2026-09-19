/**
 * Sign-up is invite-only by default (`HARDWOOD_SIGNUP_MODE=invite`) — the task brief requires the
 * UI to explain that rather than failing opaquely on a submit. `useProviders().signupMode` is
 * read straight from `GET /v1/auth/methods`, so the explanation and the invite-code field appear
 * or disappear the moment the server's configuration does, with no rebuild.
 */
import { useState, type FormEvent, type JSX } from "react";
import { ApiError, signup } from "../api/session";
import { Text } from "../design/Text";
import { ProviderButtons } from "./ProviderButtons";
import { useProviders } from "./useProviders";
import styles from "./forms.module.css";

export interface SignUpFormProps {
  readonly next?: string;
}

const SIGNUP_MODE_COPY: Record<"open" | "invite" | "closed", string> = {
  open: "Anyone can create a Hardwood account.",
  invite:
    "Hardwood is invite-only right now. Enter the invite code someone sent you, or leave it " +
    "blank if you already know your email is on the list.",
  closed: "New sign-ups are not open right now.",
};

export function SignUpForm({ next }: SignUpFormProps): JSX.Element {
  const { signupMode } = useProviders();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isDone, setIsDone] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await signup({
        email,
        password,
        displayName: displayName.trim() || null,
        inviteCode: inviteCode.trim() || null,
      });
      setIsDone(true);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isDone) {
    return (
      <Text as="p" style="tableCell">
        Check your email for a link to verify {email}. You can sign in before you click it —
        Hardwood does not require a verified email to use the product.
      </Text>
    );
  }

  const isClosed = signupMode === "closed";

  return (
    <div className={styles.form}>
      {signupMode && (
        <Text as="p" style="caption" color="secondary">
          {SIGNUP_MODE_COPY[signupMode]}
        </Text>
      )}
      {!isClosed && (
        <form className={styles.form} onSubmit={(event) => void handleSubmit(event)}>
          <label className={styles.field}>
            <Text style="tableHeader" as="span">
              Email
            </Text>
            <input
              className={styles.input}
              type="email"
              name="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <Text style="tableHeader" as="span">
              Password
            </Text>
            <input
              className={styles.input}
              type="password"
              name="password"
              autoComplete="new-password"
              required
              minLength={10}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <label className={styles.field}>
            <Text style="tableHeader" as="span">
              Name (optional)
            </Text>
            <input
              className={styles.input}
              type="text"
              name="name"
              autoComplete="name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
          </label>
          {signupMode === "invite" && (
            <label className={styles.field}>
              <Text style="tableHeader" as="span">
                Invite code (optional if your email is already on the list)
              </Text>
              <input
                className={styles.input}
                type="text"
                name="inviteCode"
                value={inviteCode}
                onChange={(event) => setInviteCode(event.target.value)}
              />
            </label>
          )}
          {error && (
            <p role="alert">
              <Text style="caption" className={styles.errorText}>
                {error}
              </Text>
            </p>
          )}
          <button className={styles.submit} type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Creating your account…" : "Create account"}
          </button>
        </form>
      )}
      <div className={styles.divider}>
        <Text style="caption" color="tertiary">
          or
        </Text>
      </div>
      <ProviderButtons next={next} />
    </div>
  );
}
