# Licensing, terms of use, and what you may actually ship

**Not legal advice.** This is a working summary of the constraints identified in the source
research, written so that nobody has to rediscover them later.

---

## The short version

| You want to… | Verdict |
| --- | --- |
| Run Hardwood privately for yourself, on your own machine, with data from stats.nba.com | **Fine.** This is the *default* configuration — and, since the web release, a configuration you can leave. See §2b. |
| Run the web app for yourself or your household, invite-only, on a machine or LAN you control | **Fine**, on the same footing. Keep `HARDWOOD_SIGNUP_MODE=invite`. |
| Put the web app on a public DNS name, free, no ads, accounts and all | **Unresolved, and the licensing conversation comes first.** This is the step that ends the private/non-commercial predicate §2 rests on. See §2b. |
| Share the repository, its code, its schema and its formulas | **Fine.** Code and formulas are yours; the data is not redistributed. |
| Put Hardwood on the App Store, free, with scraped NBA.com data | **Offside.** NBA.com's terms restrict statistics to private, non-commercial purposes. |
| Add ads, a subscription, or an IAP | **Offside** on scraped data. License a feed first. |
| Build on Basketball-Reference scraped data, at any scale | **Prohibited** by their data-use policy. |
| Ship a real-time play-by-play depiction, a fantasy game, or anything gambling-adjacent | **Explicitly excluded** by NBA.com's terms. |
| Ship commercially on a licensed feed (Sportradar / SportsDataIO / paid API-NBA / balldontlie) | **Fine**, subject to that provider's contract. |

---

## 1. Facts, compilations and contracts

Raw statistics are facts, and in the United States facts are not copyrightable
(*Feist Publications v. Rural Telephone Service*, 1991). That is the part people remember.

Three things survive anyway, and they are what actually bind you:

1. **The compilation.** A particular selection and arrangement of facts can carry a thin
   copyright, even when the underlying facts do not.
2. **The presentation.** Tables, charts, logos, team marks and player likenesses are separate
   rights entirely. Hardwood ships no team logos and no player photographs for this reason; it
   renders monogram badges instead.
3. **The terms of use.** Accessing a site under its terms forms a contract. This is the binding
   constraint in practice, and it is the one below.

---

## 2. NBA.com Terms of Use

The operative restrictions on NBA statistics:

* Usable **only** for "legitimate news reporting or private, non-commercial purposes".
* Must carry **prominent NBA.com attribution**.
* May **not** be used in connection with any sponsorship or commercial identification.
* May **not** be used for gambling, a fantasy game, or a real-time play-by-play depiction.
* May **not** be used to build a **database product**.

### How this repository complies

* **The default deployment is private.** The API binds to loopback, `HARDWOOD_PUBLIC_BASE_URL`
  defaults to `http://127.0.0.1:8000`, web sign-up defaults to `invite`, and nothing here ships
  a hosting configuration. Since the web release this is a property of how you have deployed
  Hardwood rather than of what has been written, and it can be left by editing two environment
  variables — which is what §2b is about. Every bullet below depends on it.
* `/v1/meta` returns, and the iOS Settings screen displays,
  *"Stats via NBA.com. Not endorsed by or affiliated with the NBA."*
* No sponsorship, advertising, payment or betting surface exists anywhere in the codebase.
* No live play-by-play depiction: Hardwood ingests a game **after it is final**, which is a
  design choice with a legal dimension as well as an engineering one.
* No logos, no player photography, no team marks. `headshotUrl` is nullable and unpopulated.
* The fantasy toolkit analyses a manager's own roster decisions; it does not operate a game.
  See §2a below, which is the one line in this document that needed drawing rather than quoting.

## 2a. The fantasy line, and where this repository sits relative to it

The terms exclude using NBA statistics "for a fantasy game". That phrase has to be read against
what it is plainly aimed at — **operating** one: hosting leagues, scoring contests, taking
entries, running the thing people play. `backend/nbastats/fantasy.py` and its two widgets do none
of that. They value players against each other and tell one person what a draft pick or a trade
would do to their own roster, in a league run by ESPN or Yahoo or a group chat.

That is analysis of statistics for a private, non-commercial purpose, which is the use the same
paragraph permits. The distinction is real, but it is a distinction, not an exemption, and three
things follow that are worth writing down rather than discovering later:

1. **Publishing is the trigger, and this feature raises the stakes of it.** Everything in §2
   above depends on the deployment staying private. A fantasy toolkit is the part
   of this app most likely to make someone want to share it, and it is also the part whose
   compliance argument is thinnest. Publishing the API, shipping the app, or putting the board in
   front of anyone who is not the person who built it needs the licensing conversation first —
   not a re-reading of this section.
2. **It must never become a contest.** No entries, no scoring of leagues, no head-to-head results,
   no money, no prizes, no odds. `tests/test_fantasy.py` greps the module for the market
   vocabulary, and that test is the mechanism, not the intention.
3. **The cleanest footing is a manager's own numbers.** `SeasonLine` is a plain value object so a
   spreadsheet's projections can be valued instead of NBA.com-derived season lines. Fed that way
   the feature does not rest on NBA statistics at all, which makes the question moot for the case
   it is most useful in.

### What would break compliance

Leaving the private configuration — a site people you did not invite can reach or sign up for —
putting the app in a store, monetizing in any form, adding contest scoring or any
gambling-adjacent feature, or redistributing the ingested database. A store submission is one
shape of publishing, not the definition of it; §2b draws the line where it actually falls.

## 2b. The web surface, and what "private" now has to mean

Hardwood Web is a browser client with user accounts, served by the same process at `/` with the
API unchanged at `/v1`. That does not by itself break anything in §2. A web app on
`127.0.0.1` that only you sign into is the same private, non-commercial use a native app on
your own phone is, and an invite-only site for a household is not obviously different from one
person with two devices.

What accounts changed is where the predicate lives. Before the web release, "private and
single-user" was a property of what had been *written*: there was no sign-in, no multi-user
anything, and no way to deploy one without writing it. Now it is a property of how you have
*deployed* it, and it can be left by editing two environment variables. The sentence this
document's compliance argument used to rest on is no longer a fact about the repository, so it
has stopped being allowed to do that work.

**The line, as precisely as it can be drawn here: §2 holds while the deployment is private —
you, or a household, on a machine or a network you control, with accounts you personally
issued. It stops holding the moment the site is reachable by people you did not invite.** A
public DNS name is the clearest way to cross it and the one to assume you have crossed if you
are unsure, but the test is who can reach the thing, not what the URL looks like.

What this repository carries as code rather than as intention:

* `HARDWOOD_SIGNUP_MODE` defaults to `invite`. `open` on a non-loopback base URL is a **startup
  refusal** unless a backstop invite code is also set, so an open public sign-up cannot happen
  by forgetting a setting.
* `HARDWOOD_PUBLIC_BASE_URL` defaults to `http://127.0.0.1:8000`, and the server refuses to
  start on a non-loopback `http` URL unless you explicitly accept cleartext session cookies.
  Leaving loopback is a decision you have to make out loud.
* The NBA attribution string is in the **footer of every page**, not a settings row. A surface
  more people can see raises what "prominent" has to mean, and a footer is the honest reading.
* **No request the browser can make reaches stats.nba.com.** Every user-facing read is served
  from Hardwood's own store; only the scheduled ingest worker talks upstream. That is now an
  architectural rule enforced by where the network client is imported, not inherited discipline.
* `/fantasy` carries the no-contest notice, and the fantasy tools stay what §2a describes:
  analysis of one manager's own roster, never the operation of a game.

What none of that resolves, and cannot: whether a free, ad-free, account-gated site counts as
"private, non-commercial", and how far "database product" reaches when what you are running is
a queryable store of NBA statistics behind a login. Those need a lawyer, not a repository. The
controls above exist so the question can still be asked later, rather than being answered
wrongly by accident in the meantime.

## 2c. Personal data Hardwood collects from its own users

Everything above this line concerns obligations to the people the *data* came from. Accounts
created a second, unrelated set: obligations to the people who sign in. The repository had none
of these before the web release, and neither this document nor
[DATA_SOURCES.md](DATA_SOURCES.md) addressed them.

### What is stored

| Data | Where | Why it exists |
| --- | --- | --- |
| Email address, and a normalised lookup copy | `users.email`, `users.email_lookup` | Sign-in identity, password reset, email-change confirmation |
| scrypt password hash | `users.password_hash` | Never the password itself |
| Display name; given and family name | `users` | Shown in the UI. The name fields are populated only when a provider sends them |
| Favourite player and team, theme, selected dashboard | `users` | Product preferences |
| Provider subject identifier, and the email at the time of linking | `user_identities` | The stable id Google or Apple uses for you — the thing that makes "sign in with Google" the same account next time |
| Saved dashboards | `user_dashboards.document_json` | The thing an account exists to keep |
| One row per signed-in browser | `auth_sessions` | A hash of the session secret, timestamps, the sign-in method, a **truncated IP prefix** and a **user-agent string** (first 255 characters) — so you can see and revoke your own sessions, and so rate limiting has something to key on |
| Single-use tokens | `auth_tokens` | Password reset and email verification: hashed, expiring |
| In-flight sign-ins | `oauth_transactions` | A ten-minute record of an OAuth round trip, deleted when consumed or expired |

The IP prefix is truncated to `/24` (IPv4) or `/48` (IPv6) *before* it is written. It is honest
minimisation and deliberately not pseudonymisation: a salted hash of a 32-bit address space is
reversible by brute force in seconds, so storing one and calling it anonymous would be a claim
that does not survive scrutiny. A truncated prefix cannot be reversed to the original address
at all.

There is no analytics, no advertising identifier, and no third-party script of any kind on the
site — the Content-Security-Policy is `script-src 'self'` with no exceptions, which is a
mechanism rather than a promise. The only outbound requests are to Google or Apple, during a
sign-in you started.

### How long it is kept

* Sessions expire after 30 days idle and 90 days absolute; expired rows are deleted, both
  opportunistically on use and by `admin.py purge`.
* OAuth transactions live ten minutes and are deleted on first use.
* Reset and verification tokens are deleted once they expire.
* Account data is kept until the account is deleted. `DELETE /v1/me` disables it and revokes
  every session immediately; the rows are erased permanently thirty days later. That erasure
  runs by itself — the server sweeps once at startup and every twenty-four hours after — so a
  deletion completes without anyone remembering to do anything. `admin.py purge` forces a pass
  early, and a cron entry ([WEB.md](WEB.md) §11) is worth adding anyway if the process stays
  up for months at a time, because the in-process job is a task in a single worker rather than
  a real scheduler.

### Access and erasure

* `GET /v1/me/export` returns the account record and every saved dashboard as JSON.
* `DELETE /v1/me` deletes the account, behind a typed confirmation and a re-authentication
  requirement.
* The operator equivalents, for someone who asks by email, are `admin.py export-user` and
  `admin.py delete-user`.

These are in the first release rather than on a roadmap, because the alternative is answering a
real request by hand against a SQLite file, and that is how a promise quietly becomes a lie.

### Where this needs a lawyer, and not this file

If the only account is yours, this section is housekeeping. The moment somebody else signs in,
you are handling another person's personal data, and which rules apply depends on where you are
and where they are. That is not a question this repository can answer, and naming statutes here
would be pretending to an authority this document does not have.

What can be said without guessing: you should be able to state what you collect, why, and for
how long — that is the table above; you should be able to produce a copy of it and delete it on
request — those are the endpoints above; and `/legal/privacy` is a page that has to stay true
as the schema changes, which means it is maintenance, not decoration. Any deployment beyond
your own household should get real advice **before** its first outside sign-up, not after.

---

## 3. Sports Reference / Basketball-Reference

Their data-use policy states you "should not create websites or tools based on data you scrape
from Sports Reference or any of our sites or use our data to train generative artificial
intelligence models without our permission." Custom data requests carry a $5,000 minimum. The
rate limit is 20 requests/minute (10 on FBref and Stathead), enforced with roughly an hour's
block.

**Hardwood contains no Basketball-Reference scraper and must not gain one.** The historical
advanced numbers come from a redistributed Kaggle compilation, and the PER / Win Shares / BPM /
VORP implementations in `backend/nbastats/metrics.py` are written from the published *formulas*
— which are methodology, not data — with Basketball-Reference credited as their origin.

If you add a scraper, you have taken the project out of compliance regardless of how polite the
crawl delay is.

---

## 4. Bulk dataset licenses

* **Wyatt Walsh's NBA Database** is CC BY-SA 4.0. Attribution and share-alike apply to the
  compilation. Note carefully: a CC license granted by a third-party compiler **does not**
  override the NBA's rights in the underlying data. It makes the compilation redistributable by
  its author's choice; it does not make commercial redistribution of NBA statistics lawful.
* The other Kaggle datasets are governed by Kaggle's terms plus whatever the uploader declared.
* Treat all of them as: fine for personal and research use, risky for commercial use.

Attribution for every dataset actually loaded is recorded per row in the `data_source` column
and per run in `ingest_log`, so provenance is always answerable.

---

## 5. Tracking data

Optical player tracking has been Sony Hawk-Eye since 2023-24 (SportVU before that, then Second
Spectrum). Raw player and ball coordinates are commercially licensed only and have never been
openly downloadable. Sportradar generates the derived tracking metrics exclusively.

Hardwood consumes only the **derived** tracking-era stats that NBA.com publishes, from 2013-14
onward, and treats them as optional.

---

## 6. If you decide to ship

Do these in order:

1. **Pick a feed and sign for it.** API-NBA ($15–35/mo) or balldontlie GOAT ($39.99/mo) for
   hobby scale; Sportradar or SportsDataIO for anything real. Check that the contract covers
   *app distribution*, not just internal use.
2. **Rip out the scraped path in production.** Write a new client module against
   `backend/nbastats/ingest/normalize.py`'s column maps and switch `runner.py` to it. Keep the
   `nba_api` path for local development only.
3. **Re-examine your historical backfill.** Licensed feeds often have shallower history than the
   Kaggle dumps. Either license the history too, or ship with the coverage your feed provides
   and say so in the app.
4. **Add the attribution your contract requires**, which will be more specific than the current
   NBA.com line.
5. **Re-read App Store Review Guideline 5.2** on third-party rights, and be ready to show your
   data license.

## 7. Trademarks

"NBA", team names, team marks and player names and likenesses belong to their owners. This
project is unaffiliated with and unendorsed by the NBA, its teams, or its players. The product
name "Hardwood" is a placeholder chosen to avoid implying any affiliation.
