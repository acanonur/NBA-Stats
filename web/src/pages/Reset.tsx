import type { JSX } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ResetForm } from "../auth/ResetForm";
import { Text } from "../design/Text";

export default function Reset(): JSX.Element {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token");

  if (!token) {
    return (
      <Text as="p" style="tableCell">
        This link is missing its reset token. Request a new one from the{" "}
        <a href="/forgot">forgot password</a> page.
      </Text>
    );
  }

  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Set a new password
      </Text>
      <ResetForm token={token} onSuccess={() => void navigate("/sign-in?reset=1", { replace: true })} />
    </div>
  );
}
