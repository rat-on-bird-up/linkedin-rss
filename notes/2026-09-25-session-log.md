# 25 Sep 2026: what was done, found and decided

One working session. It started with "Brad's feed doesn't seem to load in
Reader" and ended with media pages for every post, a new feed URL, and a
verified cost model for collecting LinkedIn posts at scale. Detail lives in
the two topic notes:

- `notes/reader-media-findings.md`: how Readwise Reader treats feeds,
  thumbnails, pages and URLs.
- `notes/linkedin-post-data-sources.md`: vendors, pricing models, legal
  risk, and the HarvestAPI billing trial.

## 1. "The feed isn't loading": diagnosis

- **The pipeline was healthy.** Every weekly build since 5 Sep had
  succeeded. The one failure was 30 Aug, the fetched-duplicate bug fixed on
  5 Sep, which delayed late-August posts by a week.
- **Brad simply didn't post** between 9 and 21 Sep. His posts of 21–23 Sep
  came after the 20 Sep build. A manual build fetched all three, and Reader
  picked them up about 30 minutes after they were published.
- **LinkedIn's own activity page is incomplete.** It skipped about 30 of his
  posts from July to early September that the feed had, and those posts still
  exist. Comparing against that page makes the feed look wrong when it isn't.
- **Reader made it look empty.** Every post that arrived was already marked
  seen, so the Unseen view read "All caught up". The Manage feeds "Documents"
  column counts unseen entries only.

## 2. Other feeds, and whether Reader had them

At the time:

- Brad's feed, from this repo.
- `instagram-natgeo`: a 22 Aug test, created and deleted the same day.
- Three native or filtered feeds handed over by Claude sessions:
  - Stewart Lee on The Nerve, via a siftrss filter;
  - Stewart Lee's Guardian author feed;
  - acollierastro's YouTube feed.

All were in Reader. Guil later removed the Guardian and SmarterEveryDay
subscriptions himself. Neither was built by this repo.

## 3. RSS.app, tried and dropped

A fresh account's trial was cancelled to the free plan on purpose. The free
plan refuses social-media sources ("Your plan doesn't support Social Media
feeds"), so it can't build a LinkedIn feed. Apify stays.

## 4. Images, video and thumbnails: the Reader lab

Six throwaway feeds (lab A–F) showed:

- Reader shows the linked page, not the feed's HTML.
- It takes each entry's thumbnail from `media:thumbnail`.
- It hotlinks rather than copying.
- Linked to LinkedIn, it shows video only sometimes.

The design that passed every check, F: each post gets a page on Pages, with
copies of its media, and the entry links to that page. That's now in
`build_feed.py` (commit "Give each new post a page of its own, with its
images and video"), tested end to end in GitHub Actions. The lab feeds and
the test source were deleted afterwards.

## 5. The logo that wouldn't change, and the new feed URL

- **The problem:** Reader keeps the first icon and thumbnail it saw for a
  feed URL and for each post URL, even across unsubscribing and
  resubscribing.
- **The fix:** Brad's feed moved from `feed.xml` to `linkedin-bradhaft.xml`.
  - The archive was carried over.
  - The one-off `backfill_pages` flag gave all 49 posts pages. The first build
    fetched 50 posts, so posts back to 1 Jul have their media.
  - Reader was re-subscribed to the new URL and put back in the LinkedIn
    folder. The feed icon is his photo; each post shows its own picture.
  - `feed.xml` was retired.

## 6. Collecting posts at scale

The scenario: about 5,000 people and company pages, into a table for
analysis.

- **Doing it ourselves isn't viable.** Logged-out LinkedIn blocks profile
  and activity pages. The current actor turns out to be a front for someone
  else's logged-in backend.
- **Chosen:** HarvestAPI's actor on Apify. A Friday run over the last 7 days,
  a cap of 3 posts per target, reposts and quote posts included.
- **Billing verified by trial:** $0.002 per post plus $0.001 per target with
  nothing new. There's no hidden charge for targets under the cap, a batch
  costs the sum of its parts, and company pages bill the same.
- **Budget:** at most $24 per run for 4,000 targets. About $35–85 a month
  all in, depending on how often the list posts.
- **Fallback vendor:** Bright Data.

## Spend on 25 Sep

Apify, all on the free plan's $5 monthly credit:

| Item | Cost |
|---|---|
| 5 builds of Brad's feed (one fetching 50 posts) | about $0.75 |
| Temporary media test source | $0.125 |
| HarvestAPI trial | $0.56 |
| Company-page check | $0.012 |

The cycle ended the day at $1.82 used of $5.

## Still open

- Wire HarvestAPI's actor into the feeds if it replaces apimaestro. The
  field mapping is known; see linkedin-post-data-sources.md, section 8.
- Recipes for LinkedIn company pages and for Instagram, TikTok and X still
  need their media key chains mapped, each from one real build.
- The `url-to-feed` skill should say: run one build before subscribing in
  Reader, because Reader keeps the first icon and thumbnails it sees.
