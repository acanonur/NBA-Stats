# Licensing, terms of use, and what you may actually ship

**Not legal advice.** This is a working summary of the constraints identified in the source
research, written so that nobody has to rediscover them later.

---

## The short version

| You want to… | Verdict |
| --- | --- |
| Run Hardwood privately for yourself, on your own machine, with data from stats.nba.com | **Fine.** This is the configuration in this repository. |
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

* Default deployment is private and single-user; the API binds to localhost and ships with no
  hosting configuration.
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

1. **Publishing is the trigger, and this feature raises the stakes of it.** Everything in §3
   above depends on the deployment staying private and single-user. A fantasy toolkit is the part
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

Publishing the API, putting the app in a store, monetizing in any form, adding odds or fantasy
scoring as a product feature, or redistributing the ingested database.

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
