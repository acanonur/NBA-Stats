# `web/` — the Hardwood single-page app

React 19 + TypeScript + Vite. It is not a separate product: it renders the same dashboards
from the same `contracts/`, through the same `/v1` API, wearing a design system generated from
the iOS app's `Theme.swift`. The two clients are meant to be recognisably one thing, and the
mechanism for that is code generation rather than discipline.

Operational documentation — running the server, configuring Google and Apple, sessions, CSRF,
backups, what is safe to expose — is [`docs/WEB.md`](../docs/WEB.md). This file is for working
in this directory.

---

## Running it

```bash
./scripts/web.sh setup     # from the repo root: venv, pip install, npm ci, npm run build
./scripts/web.sh dev       # uvicorn serves the built bundle at / and the API at /v1
```

**Single origin is the point**, so the normal loop is *build, then let the backend serve it* —
there is no long-running frontend server in the product. `web.sh build` rebuilds `dist/`
against a `dev` already running in another terminal; reload the page when it finishes.

For iterating on one component, Vite's own server is still there:

```bash
npm run dev        # :5173, proxying /v1 to 127.0.0.1:8000 (see vite.config.ts)
```

The proxy is not a convenience. `app.py`'s CORS block is `allow_origins=["*"]` with
`allow_credentials=False`, which does not carry the session cookie — a genuinely cross-origin
`fetch` would silently run signed out. Proxying keeps the browser's view single-origin before
the production build does.

```bash
npm test           # vitest, jsdom — 502 tests across 48 files
npm run typecheck  # tsc -b
npm run lint       # eslint + stylelint
npm run build      # tsc -b && vite build -> dist/
```

`dist/` is **committed on purpose**, so `uvicorn` alone serves a working site from a fresh
checkout. CI runs `npm run build` and then `git diff --exit-code -- web/dist`, so pushing
source without rebuilding fails there rather than shipping yesterday's JavaScript against
today's API.

---

## Layout

```
src/
├── main.tsx, App.tsx, routes.tsx   entry, providers, the route table
├── api/          client.ts (fetch + CSRF + error mapping), session, resolve, dashboards, sync, types
├── auth/         AuthProvider, RequireAuth, the four forms, provider buttons
├── dashboard/    the editor: layout store, flow layout, widget container, catalog sheet, config editors
├── design/       ~25 primitives + broadsheet/ and svg/ chart parts — the design system
├── generated/    tokens.ts · tokens.css · contracts.ts · registry.ts — DO NOT EDIT
├── pages/        routed screens, incl. legal/Terms and legal/Privacy
└── widgets/      one directory per widget kind, sixteen of them
```

### The generated four

Nothing in `src/generated/` is written by hand; each file's first line says so. The chain runs
from the Swift design system and the shared contracts:

```
ios/NBAStats/DesignSystem/Theme.swift ──gen_theme.py──▶ contracts/theme.json
                                                              │
                                              gen_web_tokens.py
                                                              ▼
                                        src/generated/tokens.ts + tokens.css

contracts/{metrics,widgets}.json ──gen_web_contracts.py──▶ src/generated/contracts.ts
                                                            src/generated/registry.ts
```

Re-run the whole chain with `./scripts/sync_contracts.sh` from the repo root, then
`python3 scripts/check_contracts.py` to confirm nothing drifted. Twelve checks run there; four
of them exist for this directory (the web artifacts regenerate byte-identically, the widget
kinds agree across Python, Swift and TypeScript, every kind has a fixture, and the parity
cases are wired into both test suites).

`gen_theme.py` parses Swift, strictly, and exits non-zero with a `file:line` on anything it
does not recognise. That is deliberate: a `Theme.swift` refactor blocking CI is a loud
maintenance cost, which is the trade made to avoid a silent colour drift between the two
clients.

`registry.ts` maps a widget kind to its component, and a kind's entry is `null` until
`src/widgets/<kind>/index.tsx` exists on disk. Adding a widget is therefore: create the
directory, re-run `sync_contracts.sh`. Nothing is registered by hand.

### The frozen interface

[`CONTRACT-FOR-AGENTS.md`](CONTRACT-FOR-AGENTS.md) records the exact exported signatures of
`generated/tokens.ts`, `generated/contracts.ts`, `api/types.ts` and `api/client.ts`. It exists
because five work packages were built in parallel against it. It is still the right place to
look for what a token or a payload type is, and still the right thing to update *before*
changing one of those four files rather than after.

---

## Conventions that are enforced, not suggested

**A null stat is an em dash, never a zero.** `MetricAvailability` travels with every value,
and `design/availability.ts` plus `StatValue` render `unavailable` as `—` with an explainer,
`estimated` with a dashed underline and an "est." badge. A local ESLint rule,
`eslint-local/rules/no-nullable-number-zero-fallback.js`, fails the build on
`value ?? 0` / `value || 0` for a nullable number, because that single idiom is how "a stat
that did not exist in 1962" silently becomes a zero on screen.

**No gambling vocabulary.** Not in code, not in copy, not in a comment. The forbidden list is
the one asserted in `backend/tests/test_fantasy.py::test_nothing_here_translates_to_a_market`;
read it there rather than trusting a paraphrase. The fantasy widgets value a manager's own
roster decisions and nothing else. This is a compliance boundary, not a style preference —
[`docs/LEGAL.md`](../docs/LEGAL.md) §2a — and it is worth knowing that the mechanical guard
does **not** currently cover this directory: `backend/tests/test_fantasy.py::
test_nothing_here_translates_to_a_market` greps `nbastats/fantasy.py` only. Until that grep is
extended over `web/src/**`, the rule here is enforced in review.

**No inline `<script>`, ever.** The server ships `script-src 'self'` with no nonce and no
`unsafe-inline`, which is what a single-origin static bundle buys. `build.modulePreload.polyfill`
is off in `vite.config.ts` for exactly this reason — Vite's polyfill injects an inline script
that `npm run build` cannot warn you about and a real browser refuses to run.

**CSS Modules, and tokens rather than values.** Colours, spacing, radii and type come from the
`--hw-*` custom properties in `generated/tokens.css`, which carries light, `prefers-color-scheme:
dark` and an explicit `[data-theme="dark"]` override. A literal hex in a component is a drift
from `Theme.swift` that no generator can catch, so `stylelint` bans literal colours and
un-tokenised shadows outright across `src/**/*.css`. `design/theme.css` is the one hand-written
stylesheet allowed raw values, and its header explains the two that cannot be expressed as a
`var()`.

**Writes go through `api/client.ts`.** It attaches `X-Hardwood-CSRF`, sends credentials, and
maps the API's error envelope to a typed failure. A bare `fetch` in a component will work
until the first non-GET and then fail as a CSRF rejection.
