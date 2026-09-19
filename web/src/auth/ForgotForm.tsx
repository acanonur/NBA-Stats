import { useState, type FormEvent, type JSX } from "react";
import { ApiError, forgotPassword } from "../api/session";
import { Text } from "../design/Text";
import styles from "./forms.module.css";

export function ForgotForm(): JSX.Element {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [devLink, setDevLink] = useState<string | null>(null);
  const [isDone, setIsDone] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const result = await forgotPassword(email);
      setDevLink(result.devLink ?? null);
      setIsDone(true);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : "Something went wrong. Try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isDone) {
    return (
      <div className={styles.form}>
        <Text as="p" style="tableCell">
          If {email} has a Hardwood account with a password, we sent a link to reset it.
        </Text>
        {devLink && (
          <Text as="p" style="caption" color="secondary">
            Local dev link: <a href={devLink}>{devLink}</a>
          </Text>
        )}
      </div>
    );
  }

  return (
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
      {error && (
        <p role="alert">
          <Text style="caption" className={styles.errorText}>
            {error}
          </Text>
        </p>
      )}
      <button className={styles.submit} type="submit" disabled={isSubmitting}>
        {isSubmitting ? "Sending…" : "Send reset link"}
      </button>
    </form>
  );
}
