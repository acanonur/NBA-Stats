"""Shared fetch plumbing for every source the injury, headline and EuroLeague work reads.

This package is the *only* place Hardwood's new sources touch the network. It holds no models
and no league knowledge: a polite HTTP client, a ``robots.txt`` checker, an RSS/Atom reader and
a place to write what came back. The NBA injury report (``nbastats.nba_intel``) and the
EuroLeague's live client (``nbastats.euroleague.ingest``) are both built on it, which is the
point. Politeness rules that live in two clients get implemented twice and drift; rules that
live here are implemented once and tested once.

Posture, as amended
-------------------
Hardwood runs privately on one person's Mac, for that person's own use, and says so in the
``User-Agent`` it sends. ``docs/LEGAL.md`` §2 states that posture for NBA.com data and §2d/§2e
extend it to the EuroLeague's data service, the NBA injury report and news headlines. None of
those terms could be read from the development environment, because every sports host is
denied there, and that is a limit of where the code was written rather than a reason to make
the person who owns the Mac fill in a form before the app shows data. So there is no terms
gate in this package: no review date, no terms URL, no "outcome". What stays is the behaviour
that makes the posture true in practice:

* a descriptive ``User-Agent`` naming Hardwood and stating personal use;
* at least one second between requests, at most two in flight, conditional requests wherever
  the server offers a validator, exponential backoff on 429 and 5xx, and a circuit breaker that
  stops asking a host that has said no (:mod:`nbastats.intel.http`);
* ``robots.txt`` read automatically before a feed is fetched, cached for 24 hours, with a
  disallow switching that feed off and the reason shown (:mod:`nbastats.intel.robots`);
* a hard size cap and a refusal of any XML that declares a document type or an entity, so a
  hostile feed cannot make the parser allocate without bound (:mod:`nbastats.intel.feeds`);
* title, link, date and source name from a feed and nothing else: article bodies are never
  fetched and a ``description`` is never read.

There is deliberately no ``terms.py`` here. The design once had one; the lead removed the gate.

Why nothing here imports a database
-----------------------------------
A fetcher that knew about a league's tables would be a fetcher whose behaviour depended on
which league was loaded, and the EuroLeague's store is sealed behind an import boundary the
isolation test enforces. Callers persist what they learn (a breaker's ``paused_until``, a
feed's ETag) in their own stores; this package hands them plain values to persist.

Modules
-------
``http``        the polite client, its retry and backoff rules and its circuit breaker
``robots``      ``urllib.robotparser`` wrapped with a 24 hour cache and conservative failure
``feeds``       RSS 2.0 and Atom, title/link/date/source only, and the fetch around it
``recordings``  the data directory, probe recordings and content-addressed raw payloads
"""
