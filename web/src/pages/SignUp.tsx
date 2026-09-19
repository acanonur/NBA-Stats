import type { JSX } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { SignUpForm } from "../auth/SignUpForm";
import { Text } from "../design/Text";

export default function SignUp(): JSX.Element {
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/";

  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Create your account
      </Text>
      <SignUpForm next={next} />
      <Text as="p" style="caption" color="secondary">
        Already have an account? <Link to="/sign-in">Sign in</Link>.
      </Text>
    </div>
  );
}
