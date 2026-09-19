/** `/legal/privacy` — required by the account system (WEB_DESIGN.md §11). Describes what an
 * account actually stores, matching `backend/nbastats/accounts/models.py` rather than a
 * boilerplate policy. */
import type { JSX } from "react";
import { Text } from "../../design/Text";

export default function Privacy(): JSX.Element {
  return (
    <div style={{ maxWidth: 640, display: "flex", flexDirection: "column", gap: "var(--hw-space-md)" }}>
      <Text as="p" style="sectionTitle">
        Privacy
      </Text>
      <Text as="p" style="tableCell">
        An account stores your email address, a scrypt hash of your password (never the password
        itself), your display name, your favourite player and team, your saved dashboards, and a
        record of your active sign-in sessions (device, approximate location prefix, and when
        each was last used).
      </Text>
      <Text as="p" style="tableCell">
        Signing in with Google or Apple stores only the identifier and email that provider gives
        Hardwood — never a password, and no separate tracking script runs on this site.
      </Text>
      <Text as="p" style="tableCell">
        You can download a copy of everything stored about your account, and every dashboard you
        have saved, from Settings at any time. Deleting your account disables it immediately and
        erases it permanently after 30 days.
      </Text>
    </div>
  );
}
