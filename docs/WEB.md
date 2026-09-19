# Hardwood Web — running it, configuring it, and what it is safe to expose

Hardwood Web is the same service, with a browser on the front. `uvicorn` serves the built
React bundle at `/` and the existing API at `/v1`, from **one process on one origin**. There
is no second server, no CORS, and no API token in browser storage — the browser holds an
opaque session cookie and nothing else.

That single-origin choice is what makes the rest of this document short. It is also why
"deploy the frontend" is not a step: `web/dist` is committed, and the backend you already run
serves it.

Everything here was checked against `scripts/web.sh`, `backend/.env.example`,
`backend/nbastats/accounts/config.py` and the route modules as they stand, not against the
design document. Where the two disagree, this file is the one that matches the code, and
§12 says so explicitly.

> **Read [LEGAL.md](LEGAL.md) §2b before this gets a DNS name.** Accounts change the
> compliance argument this project rests on, and they add obligations to your own users that
> the repository never had. That is not a formality to skim past; it is the reason §9 of this
> document exists.

---

## 1. What you get

| | |
| --- | --- |
| Served at | `http://127.0.0.1:8000/` by default — SPA at `/`, API unchanged at `/v1` |
| Sign-in | Email + password always; Google when configured; Apple when configured **and** you have https |
| Sign-up | `invite` by default — an account is not created by anyone who finds the port |
| Storage | The same SQLite (or Postgres) database the stats live in, on its own set of tables |
| Widgets | All sixteen kinds, the same dashboards as iOS, resolved through the same `/v1/dashboard/resolve` |
| Design system | Generated from `ios/NBAStats/DesignSystem/Theme.swift`; the two clients cannot drift apart by hand |

Nothing the browser can ask for reaches stats.nba.com. Every read is served from Hardwood's
own store; only the scheduled ingest worker talks upstream. That is an architectural rule,
enforced by where the network client is imported, not a habit.

---

## 2. Run it

```bash
cd /path/to/NBA-Stats
./scripts/web.sh setup      # venv, pip install -e "backend[serve,web]", backend/.env, npm ci, npm run build
./scripts/web.sh dev        # uvicorn on 127.0.0.1:8000
```

`setup` is safe to re-run. It creates `backend/.venv` if absent, copies `backend/.env.example`
to `backend/.env` **only if `.env` does not already exist**, and builds `web/dist`. `dev`
refuses to start without the virtualenv, and warns — but still starts — if `web/dist/index.html`
is missing, because `/v1` is perfectly useful on its own.

Signup mode is `invite`, so mint yourself one:

```bash
cd backend && .venv/bin/python3 -m nbastats.accounts.admin invite --note "me"
```

It prints a single-use code on stdout. Paste it into the sign-up form.

Two more subcommands of `web.sh`:

* `build` — rebuild `web/dist` only, for iterating against a `dev` already running in another
  terminal. Reload the page when it finishes.
* `doctor` — prints Node, npm, Python, the virtualenv, whether `web/dist/index.html` exists,
  the full `AuthSettings` (secrets masked), every startup refusal and warning, and why each
  provider is on or off. This is the first thing to run when something is wrong, and the
  first thing to paste when asking someone else.

`PORT=9000 ./scripts/web.sh dev` moves the port. If you do that, `HARDWOOD_PUBLIC_BASE_URL`
has to move with it — see §3.

### Developing a component in isolation

`npm run dev` in `web/` starts Vite's own server and proxies `/v1` back to `127.0.0.1:8000`,
so the browser still sees one origin and the session cookie still travels. It is for
iterating on a component; `./scripts/web.sh dev` is what actually runs the product. See
[`web/README.md`](../web/README.md).

### Demo mode

`web.sh dev` exports `HARDWOOD_DEMO_MODE=1` unless you already set it, so a machine with no
ingested data still has a full synthetic league to click through. A value you export yourself
wins. Real data is [RUNBOOK.md](RUNBOOK.md) §2.

---

## 3. Configuration

Every web setting is read by `AuthSettings.from_env()` in
`backend/nbastats/accounts/config.py`. `backend/.env.example` is the annotated copy; this is
the summary.

| Variable | Default | What it decides |
| --- | --- | --- |
| `HARDWOOD_PUBLIC_BASE_URL` | `http://127.0.0.1:8000` | The **only** source of scheme/host/port for OAuth redirect URIs, cookie `Secure`, and the CSRF `Origin` check. Never derived from the request |
| `HARDWOOD_WEB` | `1` | Set false to run the API alone with no SPA mounted |
| `HARDWOOD_WEB_DIST` | `<repo>/web/dist` | Where the built bundle lives |
| `HARDWOOD_SIGNUP_MODE` | `invite` | `open` \| `invite` \| `closed` |
| `HARDWOOD_INVITE_CODE` | — | One well-known code. Mainly the required backstop for `open` on a non-loopback URL |
| `HARDWOOD_SESSION_DAYS` | `30` | Sliding idle window |
| `HARDWOOD_SESSION_ABSOLUTE_DAYS` | `90` | Hard cap, never extended |
| `HARDWOOD_SCRYPT_N` | `14` | scrypt cost exponent — about 60 ms and 16 MiB per login |
| `HARDWOOD_COOKIE_SECURE` | derived | Leave unset; it follows the base URL's scheme |
| `HARDWOOD_ALLOW_INSECURE_COOKIES` | `0` | The LAN escape hatch. See §9 |
| `HARDWOOD_DEV_LINKS` | `0` | Put the password-reset link in the API response — and only when the caller is *also* loopback |
| `HARDWOOD_MAILER` | `log` | `log` \| `file` \| `smtp` |
| `HARDWOOD_SMTP_URL` / `HARDWOOD_MAIL_FROM` | — | `smtps://` or `smtp+starttls://` only |
| `HARDWOOD_GOOGLE_CLIENT_ID` / `_SECRET` | — | Both, or the Google button does not render |
| `HARDWOOD_APPLE_SERVICES_ID` / `_TEAM_ID` / `_KEY_ID` / `_KEY_FILE` | — | All four, plus https |
| `HARDWOOD_TRUSTED_PROXY_CIDRS` / `_HOPS` | empty / `0` | Who may set `X-Forwarded-For`. The default trusts nobody |
| `HARDWOOD_CSP_IMG_SRC` | `https://cdn.nba.com` | Extra `img-src` origins beyond `'self'` |

### Where `backend/.env` is read

`create_app()` calls `accounts.config.load_env_file()` before it reads a single setting, so
the running server and `./scripts/web.sh doctor` load the same file through the same function
and cannot disagree about what is configured. `python3 -m nbastats.accounts.admin` loads it
too, which is why minting an invite works on a machine where nothing has been exported.

Two things worth knowing:

* **An exported variable always wins.** The loader never overwrites something already in the
  environment, so `HARDWOOD_SIGNUP_MODE=open ./scripts/web.sh dev` overrides the file for that
  run without editing it.
* **`HARDWOOD_ENV_FILE` moves the file.** Point it somewhere outside the repository if you
  would rather your secrets did not live next to your git history. Set it to a path that does
  not exist and Hardwood starts with no file at all, which is what the tests do.

This used to be the most surprising thing in this document: the file was read by `doctor` and
by nothing else, so `doctor` would report Google as configured while the site showed no Google
button. That is fixed; if you see those two disagree now, it is a bug worth reporting.

### Refusals and warnings

Five configurations make `create_app()` raise `RuntimeError` before a single request is
served, each naming the variable to change:

1. A non-loopback `http://` base URL without `HARDWOOD_ALLOW_INSECURE_COOKIES=1` — session
   cookies would travel in clear text.
2. `HARDWOOD_SIGNUP_MODE=open` on a non-loopback base URL with no `HARDWOOD_INVITE_CODE`.
3. An Apple `.p8` key file that is group- or world-readable.
4. `HARDWOOD_SMTP_URL` on the cleartext `smtp://` scheme.
5. `HARDWOOD_MAILER=log` or `file` on an **https** base URL — a site reachable over the public
   internet whose password-reset links go to a log file, where anyone who can read the log can
   take any account.

Four more are warnings, surfaced at `GET /v1/health` as `authWarnings` and never fatal: an
https base URL with no trusted-proxy CIDRs; Apple configured without https; a Google client
ID with no secret; and the log or file mailer on any non-loopback base URL, which is the
softer version of refusal 5 — fine for a household LAN, not fine facing the internet. A warning always describes a feature that will be quietly less useful than
you intended — most often a button that does not appear.

---

## 4. Sign in with Google — free, about fifteen minutes

1. <https://console.cloud.google.com> → create a project.
2. **APIs & Services → OAuth consent screen**: External; app name; your address as support and
   developer contact. Scopes: **`openid` and `email` only**. Hardwood requests exactly
   `openid email` (`accounts/providers/google.py`) and never asks for `profile`. Leave the app
   in *Testing* and add yourself under *Test users* — Testing allows up to 100 users and needs
   no verification.
3. **Credentials → Create credentials → OAuth client ID → Web application.** The authorised
   redirect URI must be exactly:

   ```
   {HARDWOOD_PUBLIC_BASE_URL}/v1/auth/google/callback
   ```

   which for the default configuration is `http://127.0.0.1:8000/v1/auth/google/callback`.
   Google compares the string — the port counts, the absence of a trailing slash counts, and
   `localhost` is not the same string as `127.0.0.1`. Whatever you register, set
   `HARDWOOD_PUBLIC_BASE_URL` to match it, because Hardwood builds the redirect URI from that
   variable and nothing else.
4. Put the pair in `backend/.env` (and see §3 about exporting them):

   ```
   HARDWOOD_GOOGLE_CLIENT_ID=….apps.googleusercontent.com
   HARDWOOD_GOOGLE_CLIENT_SECRET=…
   HARDWOOD_PUBLIC_BASE_URL=http://127.0.0.1:8000
   ```
5. Restart. The button appears — `provider_status()` is computed fresh on every call, so there
   is no cache to bust and no rebuild to do.

The flow is authorization code with PKCE (S256) and a `nonce`, `prompt=select_account`, and
the id token's signature verified against Google's JWKS. The one-shot transaction — state
hash, nonce, code verifier, where to return to — lives in `oauth_transactions`, keyed by a
hashed handle in a ten-minute `hw_oauth` cookie, and is consumed on the first callback so a
replay finds nothing.

**Cost: nothing.** Google does not charge for OAuth.

---

## 5. Sign in with Apple — $99/year, and it cannot work on localhost

Be clear-eyed about the prerequisites: a paid Apple Developer Program membership, a domain you
control, and https. Without all three, skip it. The site is complete without Apple, and
`provider_status()` hides the button rather than rendering a dead one.

1. **Apple Developer Program**, $99/year. <https://developer.apple.com/programs/>
2. **Certificates, Identifiers & Profiles → Identifiers → Services IDs → +.** Description
   "Hardwood Web", identifier e.g. `com.hardwood.web`. This is *not* the iOS app's bundle id.
3. Enable *Sign in with Apple* on that Services ID → **Configure**:
   * *Domains*: your real domain.
   * *Return URLs*: exactly `https://your.domain/v1/auth/apple/callback`. Apple refuses
     `localhost` and refuses `http`.
   * Put Apple's `apple-developer-domain-association.txt` at
     `web/dist/.well-known/apple-developer-domain-association.txt`. The SPA fallback serves a
     real file under `/.well-known/*` rather than returning `index.html`, specifically so this
     verification works.
4. **Keys → + → Sign in with Apple**, download `AuthKey_XXXXXXXXXX.p8`. **You can download it
   once.** Store it outside the repository and `chmod 600` it — a key readable by group or
   other is a startup refusal, not a warning.
5. Configure:

   ```
   HARDWOOD_PUBLIC_BASE_URL=https://your.domain
   HARDWOOD_APPLE_SERVICES_ID=com.hardwood.web
   HARDWOOD_APPLE_TEAM_ID=ABCDE12345
   HARDWOOD_APPLE_KEY_ID=XXXXXXXXXX
   HARDWOOD_APPLE_KEY_FILE=/secure/path/AuthKey_XXXXXXXXXX.p8
   ```
6. `python3 -m nbastats.accounts.admin check-apple` mints a client secret, posts a deliberately
   bad code to Apple's token endpoint, and prints Apple's own status and body verbatim. That
   response body is the only useful way anybody has ever debugged `invalid_client`.

Two things worth knowing. Apple posts its callback (`response_mode=form_post`), which is a
cross-site POST, so the `hw_oauth` cookie has to be `SameSite=None` — which requires `Secure`,
which requires https. That chain, not an arbitrary rule, is why Apple is hard-gated off on
http. And Hardwood mints Apple's client secret itself with a **five-minute** lifetime and
re-mints on demand, rather than pasting in a six-month JWT, so "sign-in broke half a year
after launch and nobody knows why" cannot happen here.

A `GET` to `/v1/auth/apple/callback` is answered with a `400` that says *"Set the Services
ID's response mode to form_post"*, because that is the only way to arrive there by `GET` and
naming the misconfiguration beats letting the request fall through to the SPA and render a
blank page.

---

## 6. Email — optional

With `HARDWOOD_MAILER=log` (the default) nothing is sent anywhere; the message body goes to
the uvicorn log. `file` additionally drops an `.eml` into `backend/.hardwood-mail/`. Both are
fine for a single-user deployment, where password reset is operator-run:

```bash
python3 -m nbastats.accounts.admin reset-password someone@example.com   # prints a one-hour, single-use link
```

For real mail:

```
HARDWOOD_MAILER=smtp
HARDWOOD_SMTP_URL=smtps://user:pass@smtp.example:465
HARDWOOD_MAIL_FROM=Hardwood <no-reply@example.com>
```

Plain `smtp://` is refused at startup. A cleartext channel that can reset any account on the
system is not a trade-off worth offering.

For local development, `HARDWOOD_DEV_LINKS=1` returns the reset link in the API response —
but only when the requesting client is itself on loopback. The base URL being loopback is not
enough, deliberately: the two can disagree behind a proxy, and the one that matters is who is
actually asking.

---

## 7. How sessions and CSRF actually work

### The session

A session is an opaque `{session_id}.{secret}` pair in a cookie. The database stores
`sha256(secret)` and never the secret, so a stolen copy of `hardwood.db` does not hand anyone
a live session. There is no JWT anywhere in the browser's path; revocation is a row update,
which is the whole reason for choosing server-side sessions over a signed token.

| | |
| --- | --- |
| Cookie | `hw_session`, or `__Host-hw_session` once cookies are `Secure` |
| Flags | `HttpOnly`, `Path=/`, `SameSite=Lax`, `Secure` when the base URL is https |
| Idle window | 30 days, slid forward on use — but written at most once every five minutes, so an open dashboard is not one write per request |
| Absolute cap | 90 days, never extended |
| Cleanup | Expired sessions, OAuth transactions and tokens are swept opportunistically on roughly 1 % of verifies, and by `admin.py purge` |

The `__Host-` prefix is not decoration. It requires `Secure`, forbids `Domain` and pins
`Path=/`, which is what stops a compromised sibling subdomain from writing a cookie your
origin will read. Hardwood therefore reads **only** the prefixed name when cookies are
`Secure`, and both names when they are not: accepting the bare name on https would throw away
the entire guarantee and let an injected cookie log a victim into an attacker's account.
Flipping a deployment from http to https costs everyone one sign-in. That is the correct
price.

### CSRF

A synchroniser token, checked against the session row. Two things are required on every
state-changing request:

1. **`X-Hardwood-CSRF`**, whose `sha256` must equal the session row's `csrf_hash`. The token
   is derived by HMAC from the session secret the caller already holds, so
   `GET /v1/auth/session` can hand it back without writing anything — a read stays a read.
   (It did not always: minting a fresh token on that `GET` made two honest tabs break each
   other, no attacker required.)
2. **Same-origin**, by `Origin` matched against `HARDWOOD_PUBLIC_BASE_URL`, or, when `Origin`
   is absent, `Sec-Fetch-Site: same-origin | none`. Both headers absent is a rejection: a
   browser new enough to enforce `SameSite` is new enough to send one of them.

There is deliberately **no CSRF cookie**. The usual double-submit pattern assumes a cookie an
attacker cannot write, and on `127.0.0.1` that assumption is false — cookies are scoped by
host, not by port, so any other process listening on another local port can write one. The
token is only ever in a JSON body and a request header.

Deleting your account additionally requires a *fresh* session: authenticated within the last
ten minutes, or you re-enter your password first.

### Everything else on the wire

`SecurityHeadersMiddleware` puts a real CSP on every response: `default-src 'self'`,
`script-src 'self'` with **no nonce and no `unsafe-inline`** (which is why
`build.modulePreload.polyfill` is off in `vite.config.ts` — one injected inline script would
break every page load in a way `npm run build` cannot catch), `form-action` limited to self
plus the two providers, `frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'`.
Plus `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Cross-Origin-Opener-Policy: same-origin`, a `Permissions-Policy` denying geolocation, camera
and microphone, and HSTS **only** when the base URL is https. Per-user responses carry
`Cache-Control: no-store` and `Vary: Cookie`.

Static assets are the mirror image: `/assets/*` is fingerprinted by Vite, so it gets
`max-age=31536000, immutable` for free, while `index.html` is `no-store` so a returning
visitor always fetches the current list of asset filenames rather than one a deploy deleted.

---

## 8. Threat model

What this design actually defends against, and what it does not. Read the second column as
the thing you are accepting, not as a to-do list.

| Threat | Control | Residual |
| --- | --- | --- |
| Session theft via XSS | `HttpOnly` cookie; CSP with no inline script; no token in `localStorage` | A CSP bypass is still game over. There is no second factor |
| CSRF | Synchroniser token in a header + `Origin`/`Sec-Fetch-Site`; `SameSite=Lax` | — |
| Cookie injection from a sibling host | `__Host-` prefix, read exclusively on https | On plain http the prefix is meaningless. §9 |
| Password brute force | scrypt N=2¹⁴; 30 logins/15 min per IP and 10 per account; after six failures a per-account backoff of 2ⁿ⁻⁵ s capped at 300 s, cleared by a correct password | The IP limiter is **in-process**: two workers double it and a restart clears it. Only the per-account backoff survives a restart |
| Account enumeration | Signup, forgot-password and resend all answer `202` regardless of whether the address exists | Timing is not equalised |
| Unwanted accounts | `invite` by default; `open` on a non-loopback URL is a startup refusal without a backstop code | — |
| Stolen database | Only hashes stored: scrypt for passwords, sha256 for session secrets, CSRF tokens and reset tokens | Email addresses, OAuth subjects, `/24` IP prefixes and user agents are stored in the clear. §10 |
| Replayed OAuth callback | One-shot `oauth_transactions` row, consumed on the first callback; ten-minute handle cookie cleared on every branch, including errors | — |
| Provider account takeover | Linking rules in `accounts/linking.py`; an unverified provider email never silently claims an existing account | — |
| Forged client IP | `X-Forwarded-For` honoured only from a configured CIDR, counted from the right | With the default (trust nobody) behind a proxy, every visitor shares one rate-limit bucket. The https-without-CIDRs warning exists for this |
| Clickjacking | `frame-ancestors 'none'` and `X-Frame-Options: DENY` | — |
| Upstream abuse | No browser request path can reach stats.nba.com | — |
| Forced login (an attacker plants *their* session cookie in your browser, so your work lands in their account) | `GET /v1/auth/verify` issues no cookie; the SPA pins the `userId` it started with and hard-signs-out if a later bootstrap disagrees, clearing the query cache | **Narrowed, not closed, on plain http.** The pin catches the swap on the next `/v1/auth/session`, so a write issued between the cookie swap and that check still lands in the attacker's account, and a tab that never re-bootstraps is never checked. There is no server-side fix: a network attacker who can set a cookie on an http origin can always overwrite it. https closes it, because `__Host-` becomes real. §9 |

Three things worth naming plainly: **there is no second factor**, **rate limiting lives in
process memory**, and **two of the controls above are only real over https** — the `__Host-`
cookie prefix and the forced-login defence both degrade on plain http, and no amount of
server-side code recovers them. All three are proportionate for an invite-only deployment on a
machine you own. None of them is proportionate for the open internet, which is the same
conclusion §9 reaches from the other direction.

---

## 9. Your laptop, your LAN, and a public DNS name are three different things

They are not points on a line. Each step changes who can reach the service and what a mistake
costs.

**On your own machine** (`http://127.0.0.1:8000`, the default). Nobody else can connect. The
cleartext cookie is unobservable because it never leaves the loopback interface, which is
exactly why `startup_refusals` makes an exception here: `Secure` cookies are silently dropped
on plain http, so enforcing them locally would turn "login does nothing" into the beginner's
first hour.

**On your home LAN** (`http://192.168.1.x:8000`). The server refuses to start until you set
`HARDWOOD_ALLOW_INSECURE_COOKIES=1`, and you should read that flag as what it is: every
session cookie now crosses your Wi-Fi in clear text, readable by anything else on the network,
including a guest phone and anything on it. `__Host-` protects nothing on http. Keep
`HARDWOOD_SIGNUP_MODE=invite`. This is defensible for a household; it is not a security
posture, it is an accepted risk with a known blast radius.

**On a public DNS name.** Everything changes at once, and the checklist is short but none of
it is optional:

* `HARDWOOD_PUBLIC_BASE_URL` must be your **https** URL. Get a real certificate; terminate TLS
  in front. Never set `HARDWOOD_ALLOW_INSECURE_COOKIES` here.
* Set `HARDWOOD_TRUSTED_PROXY_CIDRS` and `HARDWOOD_TRUSTED_PROXY_HOPS` to match your proxy, or
  every visitor on earth shares one rate-limit bucket keyed to the proxy's own address.
  `/v1/health.authWarnings` tells you when you have forgotten.
* Keep `HARDWOOD_SIGNUP_MODE=invite`.
* Put the in-process rate limiter somewhere real, or accept that a restart clears it and two
  workers double it.
* Set `HARDWOOD_API_KEY` if you want `/v1` closed to everything but the browser — a valid
  session cookie satisfies the gate, so the SPA keeps working.
* **Read [LEGAL.md](LEGAL.md) §2b and §2c.** A public name is precisely the line the
  compliance argument is drawn at, and it is the point at which you are processing other
  people's personal data. That is a conversation to have before the DNS record, not after.

The order matters. The technical checklist is the easy half.

---

## 10. Backups — WAL means three files

`PRAGMA journal_mode=WAL` is on for every SQLite engine this project creates (`nbastats/db.py`),
which buys concurrent readers alongside the ingest writer. It also means the database is not
one file:

```
backend/hardwood.db          the main database
backend/hardwood.db-wal      committed transactions not yet checkpointed into it
backend/hardwood.db-shm      the shared-memory index into that WAL
```

Copying only `hardwood.db` from a running service gives you a database missing every
transaction still in the WAL — which, for accounts, is the most recent sign-ups and the
dashboard someone just saved. Do not do it. Use SQLite's own online backup, which is
consistent without stopping the service:

```bash
sqlite3 backend/hardwood.db ".backup '/backups/hardwood-$(date +%F).db'"
```

The result is a single self-contained file. If you would rather copy files, stop the service
first and copy all three.

Accounts live in the same database file as the stats, on their own tables
(`users`, `user_identities`, `auth_sessions`, `auth_tokens`, `oauth_transactions`,
`user_dashboards`, `web_invites`) and their own SQLAlchemy metadata. The separation is
load-bearing rather than tidy: `HARDWOOD_DEMO_MODE` re-seeds the stats tables by deleting
every table on the stats metadata, and account rows must not be in that blast radius. There is
a test whose entire job is to prove a user survives a reseed.

A backup of the stats alone is replaceable — re-run the ingest. A backup of the account tables
is not: saved dashboards exist nowhere else.

---

## 11. Operating

```bash
python3 -m nbastats.accounts.admin <command>
```

| Command | What it does |
| --- | --- |
| `invite [--note X] [--days N]` | Mint a single-use code, print it |
| `create-user EMAIL [--password …] [--display-name …]` | Make an account directly |
| `list-users` | Who exists |
| `reset-password EMAIL` | Print a one-hour, single-use reset link — the no-mail-server path |
| `export-user EMAIL` | The account and its dashboards as JSON, to stdout |
| `delete-user EMAIL` | Hard delete, immediately, with its related rows |
| `purge [--older-than-days 30]` | Erase soft-deleted accounts and dashboards past the window, and sweep expired sessions, OAuth transactions and tokens |
| `check-apple` | Dry-run token exchange; prints Apple's own error body |
| `doctor` | Compare every account table against its model and report column drift |

The server already runs `purge` by itself — once at startup and every twenty-four hours after
— so a self-service deletion completes on its own and expired rows do not accumulate. The cron
entry below is belt and braces, and it earns its place for two reasons: the in-process job is
an `asyncio` task in one worker rather than a real scheduler, and it is skipped entirely when
the accounts feature is off. Run it on any deployment other people sign in to:

```cron
# 04:00 daily: complete deletions past their 30-day window and sweep expired rows
0 4 * * *  cd /srv/hardwood/backend && .venv/bin/python3 -m nbastats.accounts.admin purge >> /var/log/hardwood-purge.log 2>&1
```

`admin.py doctor` matters because there is no Alembic here and `create_all` never `ALTER`s.
New tables appear on restart; a new **column** on an existing table does not. `doctor` is how
you find out, and `users.profile_json` / `user_dashboards.document_json` are the TEXT escape
hatches that let most changes avoid the problem entirely.

Health, for a monitor:

```bash
curl -s localhost:8000/v1/health | python3 -m json.tool   # databaseReady, syncVersion, dataThrough, authReady, authWarnings
```

---

## 12. The first hour — three failures that look identical from the browser

All three present as "I opened the page and it doesn't work". They have nothing in common
otherwise. Run `./scripts/web.sh doctor` first, every time — it now reads the same `.env` the
server does, so what it reports is what the server sees.

There used to be a fourth, and it was the nasty one: `doctor` read `backend/.env` and the
server did not, so `doctor` confidently described a configuration that was not running. Both
go through `load_env_file()` now.

### 1. No Node, so there is no bundle

**Symptom:** `/` returns a 404 from the API instead of a page; `/v1/health` answers fine.
**Cause:** `web/dist/index.html` does not exist, so `mount_web` logged a warning and mounted
nothing. Either `npm ci`/`npm run build` never ran, or Node is not installed.
**Fix:** `./scripts/web.sh build`. If Node is missing, `web.sh` says so in a sentence rather
than a stack trace — install Node 22+ and re-run `setup`. `web/dist` is committed, so a clean
checkout has a working bundle. A *stale* committed bundle — yesterday's JavaScript against
today's API — is a different problem, and the guard for it is
`git diff --exit-code -- web/dist` in CI after a rebuild.

### 2. A provider secret that is wrong, rather than badly pasted

**Symptom:** the Google button appears, the round trip to Google works, and the callback fails
with `invalid_client`.
**Cause:** not usually whitespace. `AuthSettings` strips surrounding whitespace from every
value it reads, so a trailing newline on a pasted secret does not survive to the wire — a
correction to the design document, which warns about exactly that. In practice `invalid_client`
means the client ID and secret do not belong together, or belong to a different project.
The other common callback failure is a **redirect URI mismatch**, which Google reports as its
own distinct error: the registered string must equal `{HARDWOOD_PUBLIC_BASE_URL}/v1/auth/google/callback`
character for character, and `localhost` is not `127.0.0.1`.
**Fix:** re-copy both values from the Credentials page; check the registered URI against what
`doctor` prints for `public_base_url`.

### 3. A `.p8` that other people can read

**Symptom:** the server does not start at all. `uvicorn` exits immediately with a
`RuntimeError` naming `HARDWOOD_APPLE_KEY_FILE`.
**Cause:** the Apple signing key is group- or world-readable. This is a refusal, not a
warning, because an Apple signing key cannot be rotated in place if it leaks.
**Fix:** `chmod 600 /secure/path/AuthKey_XXXXXXXXXX.p8`.

### And the fifth, which is not a failure

**Symptom:** sign-up says "That invite code is not valid."
**Cause:** `HARDWOOD_SIGNUP_MODE` is `invite` and you have not minted one. Working as
designed.
**Fix:** `python3 -m nbastats.accounts.admin invite --note "me"`.

---

## 13. Legal

Accounts moved this project off the sentence its compliance argument used to rest on, and gave
it a category of obligation it never had — to its own users, not just to data providers.
Both are written up in [LEGAL.md](LEGAL.md): **§2b** for what the web surface changes about
the NBA.com terms, **§2c** for what Hardwood collects from the people who sign in, how long it
keeps it, and the two endpoints (`GET /v1/me/export`, `DELETE /v1/me`) that exist so the answer
to "give me my data" and "delete me" is a button rather than a favour.

Neither section is legal advice, and neither is a substitute for asking a lawyer before this
gets a public DNS name.
