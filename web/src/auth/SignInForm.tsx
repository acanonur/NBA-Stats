import { useState, type FormEvent, type JSX } from "react";
import { ApiError } from "../api/session";
import { Text } from "../design/Text";
import { useAuth } from "./AuthProvider";
import { ProviderButtons } from "./ProviderButtons";
import styles from "./forms.module.css";

export interface SignInFormProps {
  readonly onSuccess: () => void;
  /** `?next=` from the URL — passed through to the provider buttons too, so an OAuth sign-in
   * returns to the same place a password sign-in would. */
  readonly next?: string;
}

export function SignInForm({ onSuccess, next }: SignInFormProps): JSX.Element {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      onSuccess();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className={styles.form}>
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
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <p role="alert">
            <Text style="caption" className={styles.errorText}>
              {error}
            </Text>
          </p>
        )}
        <button className={styles.submit} type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <div className={styles.divider}>
        <Text style="caption" color="tertiary">
          or
        </Text>
      </div>
      <ProviderButtons next={next} />
    </div>
  );
}
