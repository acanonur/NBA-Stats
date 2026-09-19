/**
 * What a mistyped URL and an unexpected render error both land on.
 *
 * The server serves `index.html` for every path it does not own, so `/signin`, `/login`,
 * `/dashboard` and `/tonite` all reach the SPA — and `routes.tsx` matched none of them and
 * declared no `errorElement`, so React Router rendered its own developer-facing default:
 * "Unexpected Application Error! 404 Not Found 💿 Hey developer 👋 You can provide a way
 * better UX than this…", on a bare white page with no header, no footer and no link home.
 * That is in the production bundle of every deployment, and the reader it is addressed to is
 * not the reader who sees it.
 */
import type { JSX } from "react";
import { Link, isRouteErrorResponse, useRouteError } from "react-router-dom";
import { Text } from "../design/Text";

function Body({
  title,
  detail,
}: {
  readonly title: string;
  readonly detail: string;
}): JSX.Element {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--hw-space-sm)", maxWidth: 520 }}>
      <Text as="p" style="sectionTitle">
        {title}
      </Text>
      <Text as="p" style="tableCell" color="secondary">
        {detail}
      </Text>
      <Text as="p" style="tableCell">
        <Link to="/">Back to Hardwood</Link>
      </Text>
    </div>
  );
}

/** The `{ path: "*" }` child: a page that genuinely does not exist. */
export default function NotFound(): JSX.Element {
  return (
    <Body
      title="That page does not exist"
      detail="Check the address, or start again from the front page. Sign in is at /sign-in."
    />
  );
}

/** The root route's `errorElement`. It renders *inside* `RootLayout`'s `<Outlet>`, so the
 * header, the nav and the NBA attribution footer all survive whatever threw. */
export function RouteError(): JSX.Element {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFound />;
  return (
    <Body
      title="Something went wrong on this page"
      detail="The rest of Hardwood is still working. Reload to try this page again, or go back to the front page."
    />
  );
}
