# Readwise Reader and media in RSS: what we tested, 25 Sep 2026

Question: why did Brad Haft's LinkedIn feed arrive in Reader as text beside a
LinkedIn logo, and how do we get his images and video in, headless?

Method: six throwaway feeds (`docs/lab/lab-a.xml` to `lab-f.xml`), each
carrying the same three real posts (text only, one image, one video) and each
marking them up differently. All six were subscribed in Reader, and Reader's list, reading view
and API (`image_url`) were checked for each entry.

## The variants

| Feed | Markup | List thumbnail: text / image / video |
|---|---|---|
| A | HTML in `<description>` only | avatar / avatar / avatar |
| B | A + `media:thumbnail` + `media:content` | avatar / post image / video still |
| C | A + `<enclosure>` (image, or the mp4 for video) | avatar / post image / avatar |
| D | `content:encoded` HTML + plain description + `media:thumbnail` | avatar / post image / video still |
| E | A, with the avatar as the first `<img>` of text posts | avatar / avatar / avatar |
| F | links to a self-hosted page per post + `media:thumbnail` | avatar / post image / video still |

"avatar" in A, C and E is the channel `<image>`, which every lab feed set to
Brad's profile picture.

## Findings

1. **Reader takes an entry's thumbnail from `media:thumbnail`.** Images inside
   the body are ignored for this (A, E). A video `enclosure` gives no thumbnail
   (C).
2. **Without one, it uses the channel `<image>`, and without that the linked
   page's `og:image`.** The LinkedIn logo on the live feed was the `og:image` of
   LinkedIn's public post page (`static.licdn.com/aero-v1/...`).
3. **Reader hotlinks the thumbnail.** The API's `image_url` is exactly the URL
   in the feed, so that URL has to stay valid for as long as the entry exists.
4. **The reading view is the linked page, not the feed's HTML.** Every entry
   linked to LinkedIn opened as Reader's parse of LinkedIn's public post page
   (links carried LinkedIn's `trk=public_post` parameters). The feed's own text
   only fills the Summary panel.
5. **So the live feed already showed images when opened**, served by LinkedIn
   with `e=2147483647` (no practical expiry). The list thumbnail was the only
   thing missing for images.
6. **Video through LinkedIn's page is unreliable.** Lab A's video entry,
   imported today, played LinkedIn's video. The same post in the live feed,
   imported 13 Sep, shows the text and no video.
7. **A self-hosted page renders exactly (F).** Images and a `<video>` with an
   mp4 source both came through, and the video played inside Reader (checked:
   `currentTime` advancing, no media error). Reader strips the `poster`
   attribute, so the player shows the first frame until played.
8. **Reader imports only the newest five entries when a feed is first
   subscribed.** Anything older in the file never arrives.
9. **Reader picked up new entries about 30 minutes after they were published**
   on the live feed, and within seconds on a fresh subscription.

## Facts about the source data

- The actor's media URLs are signed: images expire about three weeks after the
  run, video seven days after. Copies are the only way to keep them.
- `media.licdn.com` returns 403 to Python's default `Python-urllib` User-Agent
  and 200 to any ordinary one, including an honest one naming this repo.
- An image post also carries `media.url` (a copy of its first image). Only
  `media.type` tells image from video.
- A bare repost has empty text of its own; the quoted post's text and media are
  under `reshared_post`.
- The first frame of a video is often black. ffmpeg's `thumbnail` filter picks
  a representative frame instead.
- LinkedIn's video is a plain progressive mp4 (720p, 4.2 MB for 53 s), served
  by Pages as `video/mp4` with range requests, so it streams.

## What we built from this

Option F, in `scripts/build_feed.py`: each new post gets a page and copies of
its media under `docs/<slug>/`, the entry links to the page and carries
`media:thumbnail`, and the channel `<image>` is the profile picture. See the
README section "Images, video and post pages".

## Loose ends noticed on the way

- Reader's Manage feeds "Documents" column counts unseen entries only. It read
  0 for Brad's feed while 16 entries sat under Seen.
- Between 09:10 and 12:00 UTC on 25 Sep, two subscriptions disappeared from
  Reader, "Stewart Lee | The Guardian" and "SmarterEveryDay". Not caused by this
  work; cause unknown.
