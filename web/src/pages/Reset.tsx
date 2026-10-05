/**
 * `/reset` — set a new password from a mailed link.
 *
 * The token arrives in the URL **fragment** (`/reset#token=…`), not the query string, because a
 * `GET` on this route does not consume it: a query parameter left a live, single-use, one-hour
 * account-takeover credential in uvicorn's access log (and in any reverse proxy's) until the
 * person finished the flow. A fragment is never sent to the server at all. See
 * `routes_auth.py::_link_for`.
 *
 * The query string is still read as a fallback, for a link minted before this changed — and
 * such a link is stripped out of the address bar immediately, so it does not survive into a
 * bookmark, a shared URL or the next `Referer`.
 */
import { useEffect, useState, type JSX } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ResetForm } from "../auth/ResetForm";
import { Text } from "../design/Text";

function tokenFromHash(hash: string): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ""));
  return params.get("token");
}

export default function Reset(): JSX.Element {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryToken = params.get("token");
  // Read once, on mount: the fragment is cleared immediately afterwards, so re-reading it on a
  // later render would find nothing and unmount the form mid-flow.
  const [token] = useState<string | null>(
    () => tokenFromHash(typeof window === "undefined" ? "" : window.location.hash) ?? queryToken,
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!window.location.hash && !queryToken) return;
    // Take the credential out of the address bar without adding a history entry.
    window.history.replaceState(null, "", window.location.pathname);
  }, [queryToken]);

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
