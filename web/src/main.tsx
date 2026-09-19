/**
 * Bootstrap entry point — WP4's territory (WEB_DESIGN.md §9 gives `main.tsx` / `App.tsx` /
 * `routes.tsx` to "Shell, auth UI and dashboard chrome"). Mounts `<App>` (the `QueryClient` +
 * router) into `#root`, after the generated tokens stylesheet and the hand-authored base
 * stylesheet WP0's placeholder already proved load cleanly.
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
