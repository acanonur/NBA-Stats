import { useState, type JSX } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { SignInForm } from "../auth/SignInForm";
import { safeNext } from "../auth/safeNext";
import { Text } from "../design/Text";

export default function SignIn(): JSX.Element {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [navigationError, setNavigationError] = useState<string | null>(null);
  // Validated before it can reach `navigate` — see `safeNext`'s docstring for the failure this
  // prevents (authenticated, and stranded on the sign-in form with no message).
  const next = safeNext(params.get("next"));

  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Sign in
      </Text>
      <SignInForm
        next={next}
        onSuccess={() => {
          void Promise.resolve(navigate(next, { replace: true })).catch(() => {
            // Belt and braces: `safeNext` should have made this unreachable, but a rejected
            // navigation must never be an unhandled rejection and a blank-looking page.
            setNavigationError("Signed in, but could not open that page. Go to the front page.");
          });
        }}
      />
      {navigationError && (
        <p role="alert">
          <Text style="caption" color="negative">
            {navigationError} <Link to="/">Front page</Link>
          </Text>
        </p>
      )}
      <Text as="p" style="caption" color="secondary">
        No account? <Link to="/sign-up">Sign up</Link>. Forgot your password?{" "}
        <Link to="/forgot">Reset it</Link>.
      </Text>
    </div>
  );
}
