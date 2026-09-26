# Getting LinkedIn post data: sources, costs and a verified billing model

Research and tests from 25 Sep 2026. The question behind it: what is the
cheapest dependable way to collect the public posts of many LinkedIn people
and company pages (thousands, weekly) into a table for analysis? The same
answers apply to the feeds this repo builds.

Prices are in US dollars, as the vendors quote them. "Verified" means read
from a vendor's own API, build or pricing page, or measured in a real run.
"Claimed" means the vendor or a third party says so and it was not tested.

## 1. What the current actor really is

`apimaestro~linkedin-profile-posts`, the actor behind this repo's LinkedIn
feeds, is a thin paid front end (verified from its build and our run logs):

- Its source is hidden (`isSourceCodeHidden: true`), and its only settings
  are two hidden environment variables, `API_KEY` and `API_URL`.
- A run makes one HTTP call, and that call returns up to 50 posts in about
  3 seconds.
- The pagination token it logs decodes to LinkedIn's own internal format: a
  post URN plus a timestamp.

So the scraping happens in someone else's backend, presumably logged-in
LinkedIn sessions behind proxies. "No account needed" means no account of
*ours*.

## 2. Doing it ourselves is not an option

- **Logged out, LinkedIn gives nothing to list from.** Profile and
  recent-activity pages answer HTTP 999 (LinkedIn's bot block) and the
  sign-in wall, even from a home connection. A single post's page does load
  logged out, but it links to none of the author's other posts. So a
  logged-out scraper can read posts but never discover new ones.
- **The official API does not cover it.** Reading other members' posts needs
  partner-only permissions.
- **What it would take.** Logged-in sessions against LinkedIn's internal API,
  and at any scale a pool of accounts plus proxies to avoid detection. That
  breaks the LinkedIn User Agreement, risks the account used, and is exactly
  the conduct LinkedIn sues over (section 5). Not pursued.

## 3. The pricing model matters more than the price

- **Per post, with a date filter:** the cost follows what people actually
  post. Checking more often costs almost nothing extra.
- **Per call:** you pay for every check, including the many that find
  nothing. Checking daily costs about 7× checking weekly.

Worked example: 5,000 people, 1 post per person per week on average, 3-post
cap. Data cost per month:

| Model | Weekly run | Daily runs |
|---|---|---|
| Per post + $0.001 per empty check (HarvestAPI on Apify, verified below) | ~$55 | ~$180 |
| Per call at $0.004 (HarvestAPI direct) | $87 | $608 |
| Per post without a date filter (apimaestro, re-fetches recent posts every check) | ~$540 at a 5-post cap | ~$2,300 |

## 4. Vendors reviewed

Three desk-research tracks, 25 Sep 2026. None of the vendors listed as
reviewed needs our LinkedIn login. Anything that does (PhantomBuster,
Unipile, Dripify, Expandi, Waalaxy, Linked API) was excluded outright.

| Vendor | How it's priced | 5,000 people weekly, per month | Verdict |
|---|---|---|---|
| **HarvestAPI on Apify** (`harvestapi~linkedin-profile-posts`) | $0.002 per post + $0.001 per profile with nothing (verified) | ~$35–100 | **Chosen.** Batch input, date filter, 37k users, 0.04% failed runs |
| HarvestAPI direct (harvestapi.io) | $0.004 per call, prepaid credit valid a year | ~$87 ($50 at the best top-up discount) | Good for small, irregular use |
| Bright Data | $1.50 per 1,000 posts; 5,000 free records a month | ~$16–65 if its date filter works (not confirmed) | **Fallback.** Strongest legal footing |
| RockApis on RapidAPI | per call; 50 posts, exact timestamps | ~$175 | Well documented; anonymous operator |
| Api G. on RapidAPI | per call | ~$74 | Posts endpoint undocumented; probably browser automation |
| apimaestro (current) | $0.005 per post, no date filter | ~$540 at a 5-post cap | Reliable, but 2.5× dearer per post |
| apimaestro batch variant | $0.005 per post, many profiles per run | as above | Useful as a reference |

Rejected, and why:

- **FreshData (RapidAPI):** 2 credits per call, 15 s latency, and error
  reports on its Discussions tab go unanswered.
- **EZ (RapidAPI):** runs out of scraping accounts ("No free account found")
  and returns week-old posts.
- **Netrows:** €99/month minimum, and no confirmed endpoint for one person's
  posts. Its "€0.005 per request" figure comes from its own comparison
  article.
- **Scrapin.io:** $30 paid trial, pay-as-you-go from $500.
- **EnrichLayer (Proxycurl's successor):** no posts feed.
- **ScrapingDog:** fetches a single post only.
- **LinkdAPI:** relative dates only ("3d"), 7 requests a minute.
- **SocialCrawl:** 3–5× HarvestAPI's price.
- **Other Apify actors** (supreme_coder, crustapi, atomus, datadoping,
  unseenuser, memo23): weak documentation, a 10-post minimum, a free-plan
  cap of 10 events, a missing date column, or Google-index lag.

RSS.app (tested 25 Sep): its free plan refuses social-media sources ("Your
plan doesn't support Social Media feeds"). LinkedIn needs a paid plan, from
about $8/month for 15 feeds with 60-minute refresh.

## 5. Legal and continuity risk

- **Proxycurl:** LinkedIn sued it in January 2025 (fake accounts). It shut
  down on 4 July 2025 and judgment followed.
  https://nubela.co/blog/goodbye-proxycurl/
- **ProAPIs:** LinkedIn sued it in October 2025 (millions of fake accounts,
  scraping behind the login). A consent judgment followed in February 2026:
  stop scraping and destroy the data.
  https://therecord.media/linkedin-sues-data-scraping-company
- **Bright Data** won Meta v. Bright Data (January 2024) on logged-out
  scraping of public data.
- **The pattern:** LinkedIn wins against vendors that use fake accounts or
  go behind the login. A full feed of someone's posts probably needs logged-in
  accounts somewhere, whatever "no cookies" means for us, so any vendor here
  could disappear at short notice.
- **Mitigation:**
  - keep every raw response;
  - map each vendor's output into our own rows in one place, so switching
    vendor means rewriting only that mapping;
  - prepay small amounts;
  - keep a second vendor wired up.
- **GDPR:** storing thousands of people's posts is our own processing of
  personal data. It needs a legitimate-interest note and a retention rule.

## 6. Apify plans (verified on apify.com/pricing)

- **No top-ups.** The Free plan blocks runs once its $5 monthly credit is
  used.
- **Paid plans include their fee as usage:** Starter's $19 counts towards
  what you spend, then pay-as-you-go.
- **No rollover:** unused credit expires every month, on every plan.
- **Starter adds:** 32 concurrent runs (Free: 5), chat support, and the Bronze
  store discount. HarvestAPI charges the same on Free and Bronze.
- **When to upgrade:** stay on Free until usage passes $5 a month, then take
  Starter.

## 7. HarvestAPI on Apify: the trial

Cost $0.56 on 25 Sep 2026. Ten person profiles were chosen so that each one
tests a single billing case. Every run was checked against the apimaestro
reference, whose exact timestamps gave the true answer.

Settings: `maxPosts: 3`, `postedLimitDate` exactly 7 days before the run,
`includeReposts` and `includeQuotePosts` on, comments and reactions off
(each is billed at $0.002). Each person ran alone, then all ten in one batch,
then the batch again with a cap of 10.

| Case | In the 7-day window (reference) | Returned | Charged |
|---|---|---|---|
| Never posted | 0 | 0 | 1 `no-result` |
| Last post two months ago | 0 | 0 | 1 `no-result` |
| Two posts nine days ago | 0 | 0 | 1 `no-result` |
| One own post | 1 | 1 | 1 `post` |
| One repost only | 1 | 1 | 1 `post` |
| Two own posts | 2 | 2 | 2 `post` |
| Exactly three | 3 | 3 | 3 `post` |
| Four own posts | 4 | 3 | 3 `post` |
| Ten or more own posts | 10+ | 3 | 3 `post` |
| Seven reposts and quotes | 7 | 3 | 3 `post` |
| All ten in one batch, cap 3 | – | 16 | 16 `post`, 3 `no-result`, 1 start |
| All ten in one batch, cap 10 | 28 | 28 | 28 `post`, 3 `no-result`, 1 start |

Billing findings:

- **The cost formula, exact:**
  > cost per run = $0.002 × posts returned + $0.001 × targets with nothing
  > in the window + $0.00005 per run (the start fee)
- **Posts returned:** min(posts in the window, cap) per target.
- **No trailing charge:** people under the cap pay only for their posts.
- **Batches:** a batch costs the sum of its single runs, minus their start
  fees.
- **Worst case:** targets × cap × $0.002. For 4,000 targets at cap 3, that's
  $24 per run.
- **Timing:** `chargedEventCounts` on a run reads 0 until shortly after it
  finishes, so read it again a minute later.

Data findings:

- **Recall:** 28 of 28 posts found against the reference timestamps.
  Posts 9 days old were correctly excluded.
- **Text:** complete in all 28 compared; none cut short.
- **Reposts are dated by the repost.** `postedAt` equals `repostedAt`, so
  "last 7 days" means what the person did this week. Each repost carries
  `repostedBy` and the original `author`.
- **The cap keeps the newest N posts,** but returns them unsorted.
- **Fields:**
  - `content`, `postedAt.date`/`timestamp`, `linkedinUrl`, `entityId`;
  - `postImages[]` (url, width, height);
  - `engagement` (likes, comments, shares, reactions by type);
  - `author` (with a `type` of profile or company);
  - `repostedBy`, `repostedAt`.
- **Other media, verified from the same 34 items:**
  - `postVideo` (videoUrl, a 720p mp4, plus thumbnailUrl) on 7 posts;
  - `document` (title, page count, cover images, a transcribed PDF link);
  - `article` (link, title, image);
  - `contentAttributes[]`: every tagged person or company, with its offset in the text.
- **A repost's original post** sits in `repost`: author, text, media, date and engagement.

Company pages: `targetUrls` also takes `linkedin.com/company/...` URLs. Two
company pages returned 6 posts, charged as 6 `post` events plus the start
fee: the same formula. Company pages repost staff a lot, so they hit the cap
more often. Not yet seen: a company page with nothing in the window
(presumably one `no-result`).

## 8. What this means for this repo

- HarvestAPI's actor could replace apimaestro for the feeds: 2.5× cheaper
  per post, with a real date filter, and the same "no cookies" basis. A source
  would need its own `keys` mapping. The trial has most of it:
  - text: `content`
  - date: `postedAt.date`
  - url: `linkedinUrl`
  - id: `entityId`
  - images: `postImages.*.url`

  - video: `postVideo.videoUrl`, thumbnail `postVideo.thumbnailUrl`
  - avatar: `author.avatar.url`

  Not wired up yet.
- apimaestro stays, because it is proven with this repo's feeds, and it is
  the reference for checking any other actor.
