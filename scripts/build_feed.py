#!/usr/bin/env python3
"""
Build an RSS feed from a LinkedIn profile's public posts.

Runs weekly. Each run fetches the profile's recent posts from an Apify actor
and MERGES them into the existing feed rather than replacing it, so the feed
accumulates history even though each run only sees a short window.

Standard library only — no pip install step in CI.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime

# --- Configuration -----------------------------------------------------------

PROFILE_USERNAME = "bradhaft"
PROFILE_URL = f"https://www.linkedin.com/in/{PROFILE_USERNAME}/"

# Apify actor. Change ACTOR_ID if you pick a different one; the input keys
# below must then match that actor's own input schema.
ACTOR_ID = "apimaestro~linkedin-profile-posts"
ACTOR_INPUT = {
    "username": PROFILE_USERNAME,
    "page_number": 1,
    # The actor bills per result returned, and its own default is 100. A weekly
    # run only needs the handful of posts since the last one, so cap it well
    # above a busy week but far below the default.
    "limit": 25,
}

FEED_PATH = "docs/feed.xml"
FEED_TITLE = "Brad Haft — LinkedIn posts"
FEED_DESCRIPTION = "Public LinkedIn posts, rebuilt weekly as RSS."
MAX_ITEMS = 60  # how many entries the feed keeps
TIMEOUT = 300  # seconds — the sync endpoint blocks until the run finishes

# Candidate field names, tried in order. Actors differ; this keeps the script
# working across a few of them without edits.
KEYS_ID = ("urn", "postUrn", "post_urn", "id", "postId", "activityUrn")
KEYS_TEXT = ("text", "postText", "content", "post_text", "description")
KEYS_URL = ("url", "postUrl", "post_url", "link", "postLink")
KEYS_DATE = ("postedAtISO", "posted_at", "postedAt", "publishedAt", "date", "time")


# --- Helpers -----------------------------------------------------------------


def first_present(item, keys):
    """Return the first non-empty value among `keys`, searching one level deep."""
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("text") or value.get("date") or value.get("value")
        if value not in (None, "", [], {}):
            return value
    return None


def parse_date(raw):
    """Best-effort date parsing. Falls back to 'now' so an item is never dropped."""
    if raw is None:
        return datetime.now(timezone.utc)
    if isinstance(raw, (int, float)):  # epoch, seconds or milliseconds
        seconds = raw / 1000 if raw > 1e11 else raw
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(raw).strip().replace("Z", "+00:00")
    for attempt in (
        lambda: datetime.fromisoformat(text),
        lambda: parsedate_to_datetime(str(raw)),
        lambda: datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc),
    ):
        try:
            parsed = attempt()
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return datetime.now(timezone.utc)


def make_title(text):
    """LinkedIn posts have no titles, so derive one from the opening line."""
    flat = " ".join((text or "").split())
    if not flat:
        return "(post without text)"
    if len(flat) <= 90:
        return flat
    cut = flat[:90]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


# --- Fetch -------------------------------------------------------------------


def fetch_posts(token):
    url = (
        f"https://api.apify.com/v2/acts/{ACTOR_ID}"
        f"/run-sync-get-dataset-items?token={token}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(ACTOR_INPUT).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", "replace")[:500]
        sys.exit(f"Apify returned HTTP {error.code}: {body}")
    except urllib.error.URLError as error:
        sys.exit(f"Could not reach Apify: {error.reason}")

    if isinstance(payload, dict):
        payload = payload.get("items", [])
    if not isinstance(payload, list):
        sys.exit(f"Unexpected response shape from Apify: {type(payload).__name__}")
    return payload


def unwrap(payload):
    """Flatten the two shapes actors use.

    Some push one dataset item per post; others wrap a whole page in a single
    item such as {"data": {"posts": [...]}}. Accept either.
    """
    items = []
    for entry in payload:
        if isinstance(entry, dict):
            nested = entry.get("data")
            if isinstance(nested, dict) and isinstance(nested.get("posts"), list):
                items.extend(nested["posts"])
                continue
            if isinstance(entry.get("posts"), list):
                items.extend(entry["posts"])
                continue
        items.append(entry)
    return items


def normalise(raw_items):
    """Turn actor output into the small shape the feed needs."""
    posts = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        text = first_present(raw, KEYS_TEXT) or ""
        link = first_present(raw, KEYS_URL)
        guid = first_present(raw, KEYS_ID)
        if not (text or link or guid):
            # No text, no link, no id — an envelope or an unexpected shape
            # rather than a post. Dropping it keeps junk out of the feed, and
            # if everything drops main() bails instead of publishing rubbish.
            continue
        link = link or PROFILE_URL
        guid = guid or link
        posts.append(
            {
                "guid": str(guid),
                "title": make_title(text),
                "description": str(text),
                "link": str(link),
                "date": parse_date(first_present(raw, KEYS_DATE)),
            }
        )
    return posts


# --- Feed read / write -------------------------------------------------------


def read_existing(path):
    """Load the entries already in the feed so history survives each run."""
    if not os.path.exists(path):
        return []
    try:
        channel = ET.parse(path).getroot().find("channel")
    except ET.ParseError:
        print("Existing feed is unparseable — starting a fresh one.")
        return []
    if channel is None:
        return []

    existing = []
    for item in channel.findall("item"):
        def text_of(tag):
            node = item.find(tag)
            return node.text or "" if node is not None else ""

        existing.append(
            {
                "guid": text_of("guid") or text_of("link"),
                "title": text_of("title"),
                "description": text_of("description"),
                "link": text_of("link"),
                "date": parse_date(text_of("pubDate")),
            }
        )
    return existing


def merge(existing, fresh):
    """Existing entries win on collision — never rewrite what's already published."""
    by_guid = {post["guid"]: post for post in fresh}
    by_guid.update({post["guid"]: post for post in existing})
    merged = sorted(by_guid.values(), key=lambda post: post["date"], reverse=True)
    added = len(by_guid) - len(existing)
    return merged[:MAX_ITEMS], added


def write_feed(path, posts):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        f"    <title>{html.escape(FEED_TITLE)}</title>",
        f"    <link>{html.escape(PROFILE_URL)}</link>",
        f"    <description>{html.escape(FEED_DESCRIPTION)}</description>",
        f"    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]
    for post in posts:
        lines += [
            "    <item>",
            f"      <title>{html.escape(post['title'])}</title>",
            f"      <link>{html.escape(post['link'])}</link>",
            f"      <guid isPermaLink=\"false\">{html.escape(post['guid'])}</guid>",
            f"      <pubDate>{format_datetime(post['date'])}</pubDate>",
            f"      <description>{html.escape(post['description'])}</description>",
            "    </item>",
        ]
    lines += ["  </channel>", "</rss>", ""]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# --- Entry point -------------------------------------------------------------


def main():
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("APIFY_TOKEN is not set.")

    raw_items = unwrap(fetch_posts(token))
    print(f"Apify returned {len(raw_items)} raw item(s).")

    fresh = normalise(raw_items)
    if not fresh:
        # Bail out rather than overwrite a good feed with an empty one. A silent
        # empty run is the main way this setup rots without you noticing.
        sys.exit("No usable posts parsed — leaving the existing feed untouched.")

    existing = read_existing(FEED_PATH)
    merged, added = merge(existing, fresh)
    write_feed(FEED_PATH, merged)
    print(f"{added} new post(s); feed now holds {len(merged)}.")


if __name__ == "__main__":
    main()
