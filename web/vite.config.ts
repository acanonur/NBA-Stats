/**
 * Vite build configuration.
 *
 * Two settings here are load-bearing for the security model in WEB_DESIGN.md §2.12, not
 * defaults left alone by accident:
 *
 * - `build.modulePreload.polyfill = false`. Vite's modulepreload polyfill injects an inline
 *   `<script>` into `index.html` unless this is off. `SecurityHeadersMiddleware` (backend,
 *   WP2) ships `script-src 'self'` with **no nonce and no `unsafe-inline`** — the whole point
 *   of a single-origin static bundle is that the CSP needs neither — so one inline script
 *   anywhere in the emitted HTML breaks every page load in a real browser, silently, in a way
 *   `npm run build` cannot catch. Every module script this app needs is a same-origin `<script
 *   type="module" src="...">`, which `script-src 'self'` already allows.
 * - `base: "/"` and a single entry at `index.html`. The app is served by
 *   `nbastats.api.routes_web.mount_web` from `<dist>/`, mounted at `/` behind `/v1` — never a
 *   subpath — so asset URLs must be root-relative, not relative to a nested route.
 *
 * `server.proxy` forwards `/v1` to the FastAPI dev server during `vite dev` so the app can be
 * developed against a real backend without CORS: `app.py`'s CORS block is
 * `allow_origins=["*"], allow_credentials=False` (WEB_DESIGN.md §0), which does not carry the
 * session cookie, so a cross-origin `fetch` in dev would silently run signed out. Proxying
 * keeps the browser's view single-origin even before the production build does.
 */
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/",
  plugins: [react()],
  build: {
    outDir: "dist",
    modulePreload: { polyfill: false },
    // The shipped bundle carries no source map. `web/dist` is served by the FastAPI process
    // at `/assets`, unauthenticated, with `max-age=31536000, immutable` — so `sourcemap: true`
    // published 2.8 MB of `sourcesContent` (131 files under `src/`, every page, every widget,
    // every docstring describing the CSRF header, the in-memory-token model and the
    // 401-redirect contract) to anyone who asked, and pinned it in caches for a year. No
    // credentials were in it; the annotated client half of the auth surface was.
    //
    // `"hidden"` is the setting to use if these ever go to an error tracker: it still writes
    // the map but omits the `sourceMappingURL` comment. `false` keeps it out of `dist/`
    // entirely, which is what a committed bundle wants.
    sourcemap: false,
  },
  server: {
    proxy: {
      "/v1": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    css: true,
  },
});
