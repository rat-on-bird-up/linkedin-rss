---
name: url-to-feed
description: Turns a pasted link into an RSS feed URL. Use when Guilherme shares a link he wants to follow, says "make this a feed", "turn this into RSS" or "can I follow this", or asks about his feeds.
---

# URL ➔ RSS feed

Guilherme pastes a link. He gets back a feed URL to paste into Readwise Reader.
Everything else is your problem, not his.

claude.ai caps the description above at 200 characters, so the full trigger list
lives here instead. Also apply this skill when he says "feed this", "add this to
my reader", "RSS for this" or "subscribe me to this", when he pastes a profile
URL with little other context, and when he asks what feeds he has, wants one
paused or removed, or asks why a feed has gone quiet.

## The two-tier answer

Most platforms already publish RSS and he should just subscribe — free, instant,
nothing to build. A minority (LinkedIn, X, Instagram, TikTok) deliberately
withhold one, and only those justify the repo, which pays a scraper per post.

**Always check the free path first.** Routing a YouTube channel through Apify
costs real money for something YouTube hands out for nothing.

## What to do

The routing table is `recipes.json` at the root of
**`rat-on-bird-up/linkedin-rss`**. Read it through the GitHub connector and
follow its `decision_procedure` — it is the source of truth, not this file, and
it carries every actor, price, regex and trap.

The short version:

1. Read `recipes.json`.
2. Check `native_rss` first. On a hit, build the feed URL, fetch it to confirm
   it returns a feed and not an HTML page, hand it over, and **stop**. No file,
   no cost, live immediately.
3. Otherwise match `recipes`. Fill the recipe's `config_template`, derive the
   slug, and write `sources/<slug>.json` through the connector.
4. Tell him the feed URL and what each refresh costs.

Pushing the file is the whole job. The build fires on the push, and the feed is
live a couple of minutes later. Verified 22 Aug 2026, end to end.

## Rules that matter

**The filename is the URL, permanently.** `sources/acme.json` serves
`/linkedin-rss/acme.xml` forever. Renaming it orphans every subscriber. Check
the slug is free before writing — a 404 on `sources/<slug>.json` means it is.
Never overwrite a live source to fix a typo in a title; edit the title.

**Always write the result cap explicitly.** Billing is per post. Several actors
default to 100, and the Instagram one treats a missing cap as unlimited. 25 is
the house default, and `recipes.json` names the right field per actor — it
varies (`limit`, `maxItems`, `resultsLimit`, `resultsPerPage`).

**Never put a credential in a source file.** The repo is public. Any actor whose
input needs a cookie or session token is disqualified outright.

**Never invent an actor name.** A config naming an actor that does not exist
fails silently every week. If nothing matches, follow the `fallback` section and
report back before writing anything.

## Answering him

Lead with the feed URL. Then the cost per refresh, if any. Then anything that
will actually bite — a platform that reshares a lot will produce blank entries,
a free-plan cap, an actor with a shaky success rate. `recipes.json` carries these
per recipe in `user_notes`; pass on the ones that matter and skip the rest.

Say plainly which path he got:

- **Native feed** — free, instant, no repo involved.
- **Repo feed** — live after the next build, a few minutes, and it costs per
  refresh.

Subscribing is manual and always will be: Readwise Reader has no API for adding
a feed. Feed ➔ Manage feeds ➔ Add feed, paste, done.

## Other things he might ask

**What feeds do I have?** List `sources/` and read
`https://rat-on-bird-up.github.io/linkedin-rss/status.json`, which carries per
feed the last success, item count and last error.

**Pause one.** Set `"enabled": false` in its source file. The archive survives
and the feed keeps serving. Better than deleting.

**Remove one.** Delete `sources/<slug>.json`. The build skips a slug whose file
has gone and stays green. `docs/<slug>.xml` stays behind serving its last
contents until it is deleted too.

**A feed went quiet.** Check `status.json` for that slug's `error`. Actors break
when sites change their markup; one failed run is usually transient.

**Change the schedule.** Needs a laptop. The connector cannot write to
`.github/workflows/` — it returns `403 Resource not accessible by integration`.
Anything tunable was deliberately put in the source files for this reason.
