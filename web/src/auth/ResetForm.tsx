import { useState, type FormEvent, type JSX } from "react";
import { ApiError, resetPassword } from "../api/session";
import { Text } from "../design/Text";
import styles from "./forms.module.css";

export interface ResetFormProps {
  readonly token: string;
  readonly onSuccess: () => void;
}

export function ResetForm({ token, onSuccess }: ResetFormProps): JSX.Element {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await resetPassword(token, password);
      onSuccess();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={(event) => void handleSubmit(event)}>
      <label className={styles.field}>
        <Text style="tableHeader" as="span">
          New password
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
      {error && (
        <p role="alert">
          <Text style="caption" className={styles.errorText}>
            {error}
          </Text>
        </p>
      )}
      <button className={styles.submit} type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Saving…" : "Set new password"}
      </button>
    </form>
  );
}
