import type { JSX } from "react";
import { ForgotForm } from "../auth/ForgotForm";
import { Text } from "../design/Text";

export default function Forgot(): JSX.Element {
  return (
    <div style={{ maxWidth: 360, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Reset your password
      </Text>
      <ForgotForm />
    </div>
  );
}
