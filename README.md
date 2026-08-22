# Apify ➔ RSS

Turns any Apify actor into an RSS feed, served from GitHub Pages and read in
Readwise Reader. One feed per source, each independent of the others.

Live feeds are listed at
https://rat-on-bird-up.github.io/linkedin-rss/

The repo name is historical. This is not LinkedIn-specific and never was: a
source names an actor and an input, and LinkedIn is simply the first one that
got a config. `recipes.json` carries verified recipes for LinkedIn profiles,
LinkedIn company pages, X, Instagram and TikTok, plus the procedure for
qualifying a new actor for anything else.

It is also not the answer for most sites. YouTube, Reddit, Substack, Medium,
GitHub, Mastodon, Bluesky and every WordPress or Ghost blog publish RSS
themselves, free and instantly, and `recipes.json` lists those templates so the
free path gets checked first. This repo is for the handful of platforms that
deliberately withhold a feed.

## How it works

Each source is a JSON file in `sources/`. A weekly GitHub Action calls that
source's Apify actor, merges anything new into `docs/<slug>.xml`, and commits
the result. Pages serves those files, and Readwise polls them like any feed.

**The filename is the URL.** `sources/feed.json` builds `docs/feed.xml`, served
at `/linkedin-rss/feed.xml`. Renaming a source file moves its feed and orphans
every subscriber, so the filename is the one thing that must never change. Every
other field is presentation and safe to edit.

The merge step matters. Each run only sees a short window of recent activity, so
the script keeps everything already in the feed and adds to it. The XML files
are the archive. Apify itself discards unnamed datasets after seven days, so
nothing else can rebuild them.

Sources are independent. If one actor breaks, its feed keeps serving its last
good contents, every other feed still rebuilds, and the run shows a red cross.

## Adding a source

Drop a new JSON file into `sources/`. Pushing it triggers a build of just that
source, so this works from a phone through the GitHub connector with no local
checkout.

That connector can write anywhere in the repo except `.github/workflows/`, which
returns `403 Resource not accessible by integration`. So anything you might want
to change without a laptop has to live in a source file, not in the workflow.
Changing the schedule or the triggers needs a real checkout.

```json
{
  "version": 1,
  "title": "Some Company — LinkedIn posts",
  "link": "https://www.linkedin.com/company/some-company/",
  "actor": "apimaestro~linkedin-company-posts",
  "input": { "company_name": "some-company", "limit": 25 },
  "limit_field": "limit"
}
```

`title`, `actor` and `input` are required, plus whichever field in `input` caps
the result count. Everything else has a default.

## From a phone

Paste a link into Claude and ask for a feed. It reads `recipes.json`, works out
whether the platform already has a free feed, and if not writes the source file
through the GitHub connector. The push triggers the build and the feed is live
about a minute later: roughly 20s of Actions, then 20s of Pages.

`docs/.nojekyll` is what keeps that second number small. Without it Pages runs
Jekyll over a directory of generated XML that has no Jekyll in it, which took
almost six minutes per deploy and intermittently failed outright when two
pushes landed close together.

`skill/SKILL.md` is what makes Claude reach for this without being told how.
It is installed on the account as of 22 August 2026, so it already works on
the phone. To replace it after an edit, repackage `skill/SKILL.md` and upload it
at claude.ai ➔ Customize ➔ Skills ➔ Add ➔ Upload a skill. Skills need "Code
execution and file creation" switched on under Settings ➔ Capabilities, and the
GitHub connector has to be connected or the skill has nothing to write with.

Two constraints that are easy to trip over when editing the skill. Keep the
frontmatter `description` short — one support article caps it at 200
characters, the spec says 1,024, and short trigger-only descriptions are what
Anthropic recommends regardless — so the full trigger list lives in the body. And the zip must hold the skill folder at its
root with forward-slash paths — PowerShell's `Compress-Archive` writes Windows
backslashes, which extractors read as one oddly-named file, so repackage with
`zipfile` in Python rather than `Compress-Archive`.

The whole path was verified end to end on 22 August 2026: a write through the
REST contents API fires the push trigger, the build rebuilds only that source
and leaves the others untouched, a malformed source fails loudly without
spending anything, and deleting a source file leaves the run green.

| Field | Default | What it does |
|---|---|---|
| `link` | the feed's own URL | The site the feed points at, and the fallback link for a post with no URL |
| `description` | derived from `actor` | Channel description |
| `limit_field` | `limit` | Which `input` field caps results. Actors vary: `limit`, `maxItems`, `maxPosts`, `resultsLimit` |
| `max_items` | 60 | How many entries the archive keeps |
| `max_charge_usd` | 0.50 | Hard per-run spend ceiling, enforced by Apify |
| `timeout` | 300 | Seconds to wait for the actor |
| `enabled` | true | Set false to pause a source without deleting its archive |
| `keys` | built-in guesses | Per-field parser overrides, see below |
| `_anything` | | Ignored, so use it for comments |

Only ever raise `max_items`. Lowering it deletes entries from the only copy that
exists.

### When a new actor's output does not parse

`keys` maps the five fields a feed entry needs onto that actor's output. Each is
a list of dotted paths tried in order, and a numeric segment indexes a list.

```json
"keys": {
  "date": ["attributes.published_at", "created_at"],
  "title": ["headline"]
}
```

A chain you supply replaces the built-in guesses for that field rather than
adding to them, so you can remove a guess that is matching the wrong thing.
Leaving `title` empty makes the builder cut a title from the opening line of the
post, which is right for social posts and wrong for anything with a real
headline.

Items with no text, no link and no id are dropped. If every item drops, the
source fails rather than publishing an empty feed.

## Setup

1. **Apify account.** Sign up at https://apify.com. Under Settings ➔
   Integrations, copy your API token.
2. **Repo secret.** Settings ➔ Secrets and variables ➔ Actions ➔ New repository
   secret, named `APIFY_TOKEN`.
3. **Spend limit.** In the Apify console, set a hard account usage limit of $4.
   The per-run ceiling in each source file only guards one run at a time.
4. **Pages.** Settings ➔ Pages ➔ Deploy from a branch ➔ `main` / `/docs`. The
   repo has to be public: Pages only serves private repos on a paid plan, and
   Readwise fetches anonymously either way.
5. **Subscribe.** Add the feed URL in Readwise Reader via Feed ➔ Manage feeds ➔
   Add feed. There is no API for this, it is a UI-only action.

## Cost

Actors bill per result returned, typically $0.005 each. The bill tracks how many
posts you pull, not how many times you run.

At `limit: 25`, one source costs about $0.13 a run and $0.55 a month. Apify's
free allowance is $5 a month, shared across every actor, and does not roll over.
That is roughly seven sources at weekly cadence before the allowance binds.

Two guards sit under that. Each run sends `maxItems` and `maxTotalChargeUsd`, so
Apify caps the spend on its side even if a config is wrong. The account-level
usage limit from step 3 catches anything the repo does not control.

Pushing a config change rebuilds only the changed source. Scheduled and manual
runs rebuild everything.

## Checking on it

`status.json` next to the feeds records per-source state: last success, item
count, and the last error. The landing page renders it as a table. A failing
source shows there long before you would notice a feed going quiet.

The run summary in the Actions tab lists every source and what happened to it.

Every run rewrites `lastBuildDate`, so a quiet week still produces a commit.
That is deliberate. The commit is proof the job ran, and it resets GitHub's
60-day inactivity clock that otherwise disables scheduled workflows.

## Tests

`python scripts/test_build_feed.py` runs 41 cases, no network, a couple of
seconds. CI runs it before spending anything.

Every case corresponds to a fault that actually occurred here, most of them
silent: an archive replaced by one week of posts, a whole archive collapsed onto
a single entry, posts overwriting each other because they shared a guid. If you
change the merge or the parsing, run it.

## Things that will eventually break

- **The actors.** Third-party scrapers are maintained by individuals, and the
  sites change their markup. If a feed goes quiet, check `status.json`, then run
  the workflow by hand and read the log.
- **Actor pricing.** Authors can change their per-result price. The LinkedIn one
  moved to pay-per-event in January 2026, so the retirement of Apify's old
  rental model on 1 October 2026 does not affect it.
- **A corrupt archive.** If an XML file becomes unparseable the build refuses to
  touch it, rather than replacing your history with one week of posts. Fix or
  delete the file to rebuild.

## A note on what belongs here

`sources/*.json` is world-readable. Never put a credential in an actor input.
The only secret in this project is `APIFY_TOKEN`, and it lives in repo secrets.

Do not point this at anything that already publishes RSS. YouTube, Reddit,
Substack and most blogs already have feeds, and Reader takes those directly for
free. This is for platforms that deliberately withhold one.
