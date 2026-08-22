# LinkedIn posts ➔ RSS

A weekly job that turns one public LinkedIn profile's posts into an RSS feed, served from GitHub Pages and read in Readwise Reader.

Currently pointed at https://www.linkedin.com/in/bradhaft/

## How it works

Every Sunday, GitHub Actions calls an Apify actor that scrapes the profile's recent public posts, merges anything new into `docs/feed.xml`, and commits the result. GitHub Pages serves that file at a stable URL, which Readwise polls like any other feed.

The merge step matters. Each run only sees a short window of recent activity, so the script keeps everything already in the feed and adds to it. `docs/feed.xml` is therefore the archive — Apify itself discards unnamed datasets after seven days.

## Setup

1. **Apify account** — sign up at https://apify.com (free plan, no card). Under Settings ➔ Integrations, copy your API token.
2. **Repo secret** — in this repo, Settings ➔ Secrets and variables ➔ Actions ➔ New repository secret. Name it `APIFY_TOKEN` and paste the token.
3. **Confirm the actor input** — the actor is https://apify.com/apimaestro/linkedin-profile-posts and its input schema takes `username`, `page_number` and `limit`, which is what `ACTOR_INPUT` in `scripts/build_feed.py` sends. If you switch actors, check the field names against the new one's schema.
4. **Enable Pages** — Settings ➔ Pages ➔ Source: Deploy from a branch ➔ `main` / `/docs`.
5. **First run** — Actions tab ➔ Build LinkedIn feed ➔ Run workflow. This populates `docs/feed.xml`.
6. **Subscribe** — the feed lands at https://rat-on-bird-up.github.io/linkedin-rss/feed.xml. Add it in Readwise Reader via Feed ➔ Manage feeds ➔ Add feed.

The repo has to be public: GitHub Pages only serves from private repos on a paid plan, and Readwise fetches the feed anonymously either way.

## Cost

The actor bills per result returned, at $0.005 each, so the bill tracks how many posts you pull rather than how many times you run. `limit` is set to 25, which caps a run at about $0.13 and a month of weekly runs at roughly $0.55 — comfortably inside Apify's free $5 monthly allowance, which does not roll over and does not bill you past it. Runs are simply blocked if you ever exhaust it.

The actor's own default is 100 results per page. Leaving it there would cost about four times as much for posts the feed almost always already has.

## Things that will eventually break

- **The actor.** Third-party scrapers are maintained by individuals and LinkedIn changes its markup. If the feed goes quiet, run the workflow by hand and read the log.
- **Actor pricing.** This one already moved to pay-per-event in January 2026, so the 1 October 2026 retirement of the old rental model does not affect it. The author can still change the per-result price.
- **Silent staleness.** The script exits with an error rather than writing an empty feed, so a broken run shows as a red cross in the Actions tab instead of a feed that quietly stops updating. Worth glancing at occasionally.

## Tuning

- `MAX_ITEMS` — how many entries the feed retains (default 60).
- `limit` in `ACTOR_INPUT` — how many posts each run fetches, and therefore what it costs (default 25, actor maximum 100).
- The cron line in `.github/workflows/build-feed.yml` — `0 7 * * 0` is Sunday 07:00 UTC.
- `KEYS_*` in the script — candidate field names tried when parsing actor output. Add to these rather than rewriting the parser if you switch actors.
