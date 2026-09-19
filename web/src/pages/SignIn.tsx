import type { JSX } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { SignInForm } from "../auth/SignInForm";
import { Text } from "../design/Text";

export default function SignIn(): JSX.Element {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const next = params.get("next") ?? "/";

  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Sign in
      </Text>
      <SignInForm next={next} onSuccess={() => void navigate(next, { replace: true })} />
      <Text as="p" style="caption" color="secondary">
        No account? <Link to="/sign-up">Sign up</Link>. Forgot your password?{" "}
        <Link to="/forgot">Reset it</Link>.
      </Text>
    </div>
  );
}
