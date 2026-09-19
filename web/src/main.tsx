/**
 * Bootstrap entry point.
 *
 * NOT part of Package B's owned file set (WEB_DESIGN.md §9 gives `main.tsx` / `App.tsx` /
 * `routes.tsx` to WP4, "Shell, auth UI and dashboard chrome") — but `index.html` loads this
 * path unconditionally, and WP0's own exit criterion is a `npm run build` that actually
 * produces `web/dist` (WEB_DESIGN.md §9, WP0 "Delivers"). A `package.json` nobody has ever
 * pointed at a real module is not a build. This file and `App.tsx` are therefore a minimal,
 * intentionally inert placeholder: they import the generated tokens stylesheet and the
 * hand-authored base stylesheet (so a broken `@import` or a stray inline style fails the
 * build now, not when WP4 first runs it) and render one static screen with no state, no
 * router and no network call. WP4 replaces both files wholesale; nothing here is meant to
 * survive that.
 */
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./generated/tokens.css";
import "./design/theme.css";

const container = document.getElementById("root");
if (container === null) {
  throw new Error("#root is missing from index.html");
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
