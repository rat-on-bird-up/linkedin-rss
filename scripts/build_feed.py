#!/usr/bin/env python3
"""
Build one RSS feed per source from Apify actor output.

Each source is a JSON file in sources/. The filename stem is the slug, which is
also the output filename and therefore the published URL: sources/foo.json
becomes docs/foo.xml. Renaming a source file moves its feed URL and orphans
every subscriber, so the stem is the one field that must never change.

Runs weekly, and on any push that touches sources/. Each run MERGES fresh posts
into the existing feed rather than replacing it, so a feed accumulates history
even though one run only sees a short window. The file on disk is the archive:
Apify discards unnamed datasets after seven days.

Sources are independent. One failing source leaves every other feed untouched
and its own previous feed exactly as it was.

Standard library only, so CI needs no pip install step.
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime

SOURCES_DIR = "sources"
DOCS_DIR = "docs"
STATUS_PATH = os.path.join(DOCS_DIR, "status.json")
INDEX_PATH = os.path.join(DOCS_DIR, "index.html")

DEFAULT_TIMEOUT = 300  # the sync endpoint blocks until the actor run finishes
DEFAULT_MAX_ITEMS = 60
DEFAULT_MAX_CHARGE_USD = 0.50  # per source per run, enforced by Apify itself

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9._-]+~[A-Za-z0-9._-]+$")
TOKEN_RE = re.compile(r"apify_api_[A-Za-z0-9]+")

# Codepoints outside these ranges are illegal in XML 1.0. html.escape does not
# remove them, so a feed containing one is written successfully and then fails
# to parse on the next run, which is how an archive gets silently discarded.
_XML_ALWAYS_OK = frozenset((0x09, 0x0A, 0x0D))


def strip_illegal_xml(text):
    """Drop characters that XML 1.0 cannot represent at all."""
    return "".join(
        ch
        for ch in text
        if ord(ch) in _XML_ALWAYS_OK
        or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
        or 0x10000 <= ord(ch) <= 0x10FFFF
    )

# Tracking parameters that vary between scrapes of the same post. Left in, they
# make the guid unstable and every subscriber re-imports the whole archive.
VOLATILE_PARAMS = ("rcm", "trk", "trkInfo", "originalSubdomain", "refId")

# Candidate field paths, tried in order. A source can override any of these in
# its "keys" object. Dotted segments walk into nested objects; a numeric segment
# indexes a list.
BUILTIN_KEYS = {
    "id": ["urn", "full_urn", "postUrn", "post_urn", "id", "postId", "activityUrn"],
    "title": [],  # empty means: derive the title from the body text
    "text": ["text", "postText", "content", "post_text", "description"],
    "url": ["url", "postUrl", "post_url", "link", "postLink"],
    "date": [
        "postedAtISO",
        "posted_at.date",
        "posted_at",
        "postedAt",
        "publishedAt",
        "date",
        "time",
    ],
    # Media. '*' fans out over a list and name[key=value] keeps a value only
    # when that field matches, so an image post's media.url (a copy of its
    # first image) is never mistaken for a video. Unlike the fields above,
    # these collect every URL the first matching path yields.
    "images": [
        "media[type=image].images.*.url",
        "reshared_post.media[type=image].images.*.url",
    ],
    "video": ["media[type=video].url", "reshared_post.media[type=video].url"],
    "avatar": ["author.profile_picture"],
    # A repost's own text is often empty; the post it quotes lives here.
    "quote": ["reshared_post.text"],
}

# LinkedIn's media host answers Python's default User-Agent with a 403, and any
# ordinary one with a 200. Say who we are rather than pretend to be a browser.
MEDIA_USER_AGENT = (
    "Mozilla/5.0 (compatible; linkedin-rss feed builder; "
    "+https://github.com/rat-on-bird-up/linkedin-rss)"
)
MAX_IMAGE_BYTES = 15 * 1024 * 1024
# GitHub refuses files over 100 MB and warns from 50 MB. A longer video keeps
# its link to the original post instead.
MAX_VIDEO_BYTES = 50 * 1024 * 1024
MAX_IMAGES_PER_POST = 10
MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}

NS_MEDIA = "http://search.yahoo.com/mrss/"
NS_CONTENT = "http://purl.org/rss/1.0/modules/content/"
NS_ATOM = "http://www.w3.org/2005/Atom"


class SourceError(Exception):
    """A failure confined to one source. Its feed is left untouched."""


class ConfigError(Exception):
    """A malformed source file. Nothing is fetched and nothing is written."""


# --- Config ------------------------------------------------------------------


def _require(cond, path, message):
    if not cond:
        raise ConfigError(f"{path}: {message}")


def load_source(path):
    """Read and validate one source file. Raises ConfigError, never partially."""
    slug = os.path.splitext(os.path.basename(path))[0]
    _require(
        SLUG_RE.match(slug),
        path,
        "filename must be lowercase letters, digits and hyphens, starting with "
        "a letter or digit (it becomes the feed URL)",
    )

    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path}: not valid JSON ({error})") from None

    _require(isinstance(raw, dict), path, "must contain a JSON object")
    _require(raw.get("version") == 1, path, 'needs "version": 1')

    for field in ("title", "actor", "input"):
        _require(field in raw, path, f'missing required field "{field}"')
    _require(isinstance(raw["title"], str) and raw["title"].strip(), path,
             '"title" must be a non-empty string')
    _require(ACTOR_RE.match(str(raw["actor"])), path,
             '"actor" must look like owner~actor-name')
    _require(isinstance(raw["input"], dict) and raw["input"], path,
             '"input" must be a non-empty object')

    limit_field = raw.get("limit_field", "limit")
    _require(isinstance(limit_field, str), path, '"limit_field" must be a string')
    cap = raw["input"].get(limit_field)
    _require(
        isinstance(cap, int) and not isinstance(cap, bool) and 1 <= cap <= 100,
        path,
        f'input["{limit_field}"] must be an integer from 1 to 100 — it caps how '
        "many results the actor returns, and you are billed per result",
    )

    max_items = raw.get("max_items", DEFAULT_MAX_ITEMS)
    _require(isinstance(max_items, int) and max_items >= 1, path,
             '"max_items" must be an integer of at least 1')

    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    _require(isinstance(timeout, int) and 10 <= timeout <= 600, path,
             '"timeout" must be an integer from 10 to 600 seconds')

    charge = raw.get("max_charge_usd", DEFAULT_MAX_CHARGE_USD)
    _require(isinstance(charge, (int, float)) and 0 < charge <= 5, path,
             '"max_charge_usd" must be a number greater than 0 and at most 5')

    keys = dict(BUILTIN_KEYS)
    override = raw.get("keys", {})
    _require(isinstance(override, dict), path, '"keys" must be an object')
    for field, paths in override.items():
        _require(field in BUILTIN_KEYS, path,
                 f'unknown key "{field}" (expected one of {sorted(BUILTIN_KEYS)})')
        _require(isinstance(paths, list) and all(isinstance(p, str) for p in paths),
                 path, f'keys["{field}"] must be a list of strings')
        keys[field] = paths  # replace, so a wrong built-in guess can be removed

    link = raw.get("link", "")
    _require(isinstance(link, str), path, '"link" must be a string')
    if link:
        _require(urllib.parse.urlparse(link).scheme in ("http", "https"), path,
                 '"link" must be an http or https URL')
        # Normalise here, once. normalise() stores this string on items that
        # have no URL of their own, and read_existing() normalises whatever it
        # reads back. If the two differ, every archived fallback entry changes
        # identity on the next run and merge() collapses the archive.
        link = normalise_url(link)

    enabled = raw.get("enabled", True)
    _require(isinstance(enabled, bool), path,
             '"enabled" must be true or false, not a string')

    media = raw.get("media", True)
    _require(isinstance(media, bool), path,
             '"media" must be true or false, not a string')

    backfill = raw.get("backfill_pages", False)
    _require(isinstance(backfill, bool), path,
             '"backfill_pages" must be true or false, not a string')

    for field in raw:
        if field.startswith("_"):
            continue
        if field not in {"version", "title", "link", "description", "actor", "input",
                         "limit_field", "max_items", "timeout", "max_charge_usd",
                         "enabled", "keys", "media", "backfill_pages"}:
            print(f"  warning: {path}: ignoring unrecognised field {field!r}")

    return {
        "slug": slug,
        "path": path,
        "title": raw["title"].strip(),
        "link": link,
        "description": raw.get("description") or f"Rebuilt automatically from {raw['actor']}.",
        "actor": raw["actor"],
        "input": raw["input"],
        "max_items": max_items,
        "timeout": timeout,
        "max_charge_usd": float(charge),
        "cap": cap,
        "enabled": enabled,
        "media": media,
        "backfill_pages": backfill,
        "keys": keys,
    }


def load_all_sources():
    if not os.path.isdir(SOURCES_DIR):
        raise ConfigError(f"{SOURCES_DIR}/ does not exist — there is nothing to build")
    paths = sorted(
        os.path.join(SOURCES_DIR, name)
        for name in os.listdir(SOURCES_DIR)
        if name.endswith(".json")
    )
    if not paths:
        raise ConfigError(f"no *.json files in {SOURCES_DIR}/")
    sources, broken = [], []
    for path in paths:
        slug = os.path.splitext(os.path.basename(path))[0]
        try:
            sources.append(load_source(path))
        except ConfigError as error:
            # One unreadable file must not stop every other feed from updating.
            broken.append({"slug": slug, "path": path, "error": str(error)})
        except OSError as error:
            broken.append({"slug": slug, "path": path, "error": f"cannot read: {error}"})
    return sources, broken


def feed_path(slug):
    """Resolve a slug to its output path, refusing anything outside docs/."""
    root = os.path.realpath(DOCS_DIR)
    target = os.path.realpath(os.path.join(DOCS_DIR, f"{slug}.xml"))
    if os.path.commonpath([root, target]) != root:
        raise ConfigError(f"{slug}: resolves outside {DOCS_DIR}/")
    return os.path.join(DOCS_DIR, f"{slug}.xml")


# --- Field extraction --------------------------------------------------------


_FILTER_RE = re.compile(r"^([^\[\]]*)\[([^=\[\]]+)=([^\[\]]*)\]$")


def dig_all(item, path):
    """Walk a dotted path and return every value it reaches.

    Numeric segments index lists, '*' fans out over every element of a list,
    and name[key=value] keeps the value only when its key field equals value.
    """
    current = [item]
    for segment in path.split("."):
        condition = None
        match = _FILTER_RE.match(segment)
        if match:
            segment, condition = match.group(1), (match.group(2), match.group(3))
        reached = []
        for node in current:
            if segment == "*":
                if isinstance(node, list):
                    reached.extend(v for v in node if v is not None)
                continue
            if isinstance(node, dict):
                value = node.get(segment)
            elif isinstance(node, list) and segment.isdigit():
                index = int(segment)
                value = node[index] if index < len(node) else None
            else:
                value = None
            if value is None:
                continue
            if condition and not (
                isinstance(value, dict) and str(value.get(condition[0])) == condition[1]
            ):
                continue
            reached.append(value)
        current = reached
        if not current:
            return []
    return current


def dig(item, path):
    """Walk a dotted path. Numeric segments index lists. None if absent."""
    found = dig_all(item, path)
    return found[0] if found else None


def collect_urls(item, paths):
    """Every http(s) URL reached by the first path that reaches any."""
    for path in paths:
        urls = []
        for value in dig_all(item, path):
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                if value not in urls:
                    urls.append(value)
        if urls:
            return urls
    return []


def pick(item, paths):
    """First non-empty value among `paths`. Never stringifies a dict."""
    for path in paths:
        value = dig(item, path)
        if isinstance(value, dict):
            continue  # a dict here means the path is wrong, not that it matched
        if isinstance(value, list):
            parts = [str(v) for v in value if isinstance(v, (str, int, float))]
            value = " ".join(parts) if parts else None
        if value not in (None, "", [], {}):
            return value
    return None


def parse_date(raw):
    """Parse a date, or return None. Never guesses 'now' — that reorders feeds."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            seconds = raw / 1000 if abs(raw) > 1e11 else raw
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip().replace("Z", "+00:00")
    if not text:
        return None
    for attempt in (
        lambda: datetime.fromisoformat(text),
        lambda: parsedate_to_datetime(str(raw)),
        lambda: datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc),
    ):
        try:
            parsed = attempt()
        except Exception:
            continue
        if parsed is None:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def normalise_url(url):
    """Drop tracking parameters so a post's URL is the same on every scrape."""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return str(url)
    if not parts.scheme:
        return str(url)
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key not in VOLATILE_PARAMS
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


def make_title(text):
    """Derive a title from the opening line, for sources whose posts have none."""
    flat = " ".join((text or "").split())
    if not flat:
        return "(post without text)"
    if len(flat) <= 90:
        return flat
    cut = flat[:90]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


def xml_text(value):
    """Escape for XML and drop characters XML 1.0 cannot represent."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return html.escape(strip_illegal_xml(text))


# --- Fetch -------------------------------------------------------------------


_LIVE_TOKEN = ""


def redact(text):
    """Scrub the token from anything that might reach a committed file."""
    out = TOKEN_RE.sub("apify_api_***", str(text))
    # urllib puts the offending header VALUE in its exception message, and a
    # token that does not match TOKEN_RE would otherwise land in the public
    # status.json verbatim.
    if _LIVE_TOKEN:
        out = out.replace(_LIVE_TOKEN, "***")
    return out


def fetch(source, token):
    """Run the actor and return its dataset items. Raises SourceError."""
    query = urllib.parse.urlencode(
        {
            "maxItems": source["cap"],
            "maxTotalChargeUsd": source["max_charge_usd"],
        }
    )
    url = (
        "https://api.apify.com/v2/acts/"
        f"{urllib.parse.quote(source['actor'], safe='~')}"
        f"/run-sync-get-dataset-items?{query}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(source["input"]).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # In the header, not the query string: query strings end up in error
            # messages, and this run writes errors to a public status file.
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=source["timeout"]) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = redact(error.read().decode("utf-8", "replace")[:400])
        raise SourceError(f"Apify returned HTTP {error.code}: {body}") from None
    except urllib.error.URLError as error:
        raise SourceError(f"could not reach Apify: {redact(error.reason)}") from None
    except json.JSONDecodeError as error:
        raise SourceError(f"Apify returned something that is not JSON: {error}") from None

    if isinstance(payload, dict):
        if "items" not in payload:
            message = payload.get("error") or payload.get("message") or sorted(payload)[:10]
            raise SourceError(f"unexpected response object from Apify: {redact(message)}")
        payload = payload["items"]
    if not isinstance(payload, list):
        raise SourceError(f"expected a list from Apify, got {type(payload).__name__}")
    return payload


def unwrap(payload):
    """Accept both shapes: one item per post, or a page wrapped in one item."""
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


def normalise(raw_items, source, run_started):
    """Turn actor output into the small shape a feed entry needs."""
    keys = source["keys"]
    posts = []
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        text = pick(raw, keys["text"]) or ""
        link = pick(raw, keys["url"])
        guid = pick(raw, keys["id"])
        if not (text or link or guid):
            # No text, no link, no id: an envelope or an unexpected shape rather
            # than a post. Dropping it keeps junk out. If everything drops, the
            # source fails loudly instead of publishing rubbish.
            continue
        link = normalise_url(link) if link else (source["link"] or "")
        if guid:
            guid = str(guid)
            # Normalise a URL-shaped id too, so it matches what read_existing
            # produces when the same value comes back out of the feed.
            if guid.startswith("http"):
                guid = normalise_url(guid)
        elif link and link != source["link"]:
            guid = link
        else:
            # No id and no URL of its own. Falling back to the shared source
            # link would give every such item the same guid, and merge() would
            # keep exactly one of them. Hash the content instead: stable across
            # runs for the same post, distinct between different posts.
            digest = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
            guid = f"{source['slug']}:{digest}"
        date = parse_date(pick(raw, keys["date"]))
        if date is None:
            # Stamp undated items in the order the actor returned them, which is
            # newest first. Calling now() per item would make each one *later*
            # than the last and publish the source in reverse.
            date = run_started - timedelta(seconds=index)
        video = collect_urls(raw, keys["video"])
        avatar = collect_urls(raw, keys["avatar"])
        posts.append(
            {
                "guid": guid,
                "title": str(pick(raw, keys["title"]) or make_title(text)),
                "description": str(text),
                "link": link,
                # The post on the platform. link may later move to a page we
                # host, and identity keeps following this.
                "origin": link,
                "date": date,
                "images": collect_urls(raw, keys["images"])[:MAX_IMAGES_PER_POST],
                "video": video[0] if video else "",
                "avatar": avatar[0] if avatar else "",
                "quote": str(pick(raw, keys["quote"]) or ""),
            }
        )
    return posts


# --- Feed read / write -------------------------------------------------------


def read_existing(path):
    """Load the entries already published. Raises rather than losing history."""
    if not os.path.exists(path):
        return []
    try:
        channel = ET.parse(path).getroot().find("channel")
    except ET.ParseError as error:
        # Returning [] here would replace the archive with this run's short
        # window, silently and permanently. Fail instead: the file is the only
        # copy, and nothing else can rebuild it.
        raise SourceError(
            f"existing feed {path} is unparseable ({error}). Refusing to "
            "overwrite it. Fix or delete the file to rebuild from scratch."
        ) from None
    if channel is None:
        raise SourceError(f"existing feed {path} has no <channel>")

    existing = []
    for item in channel.findall("item"):
        def text_of(tag):
            node = item.find(tag)
            return (node.text or "") if node is not None else ""

        link = normalise_url(text_of("link")) if text_of("link") else ""
        guid = text_of("guid") or link
        date = parse_date(text_of("pubDate"))
        via = next(
            (node.get("href", "") for node in item.findall(f"{{{NS_ATOM}}}link")
             if node.get("rel") == "via"),
            "",
        )
        thumb = item.find(f"{{{NS_MEDIA}}}thumbnail")
        existing.append(
            {
                "guid": normalise_url(guid) if guid.startswith("http") else guid,
                "title": text_of("title"),
                "description": text_of("description"),
                "link": link,
                "origin": normalise_url(via) if via else link,
                "date": date or datetime(1970, 1, 1, tzinfo=timezone.utc),
                "html": text_of(f"{{{NS_CONTENT}}}encoded"),
                "thumb": thumb.get("url", "") if thumb is not None else "",
            }
        )
    return existing


def identity(post, fallback_link=""):
    """What makes two entries the same post.

    Prefer the link, because it survives a change to which field the guid is
    derived from. Keying on guid alone means editing keys.id duplicates the
    entire archive: every post reappears under its new guid alongside its old
    one. Posts with no link of their own fall back to the source link, which is
    shared, so those key on guid instead.

    The link that counts is the original post's. Once a post gets a page of
    its own here, <link> points at that page and the original is kept as the
    item's via link, so identity does not move when the page appears.
    """
    link = post.get("origin") or post.get("link") or ""
    if link and link != fallback_link:
        return ("link", link)
    return ("guid", post["guid"])


def merge(existing, fresh, max_items, fallback_link=""):
    """Existing entries win on collision. Never rewrite what is already out."""
    existing_ids = [identity(post, fallback_link) for post in existing]
    if len(set(existing_ids)) != len(existing_ids):
        # Two archived entries now resolve to the same identity, so building a
        # dict from them would silently drop one. That only happens when the
        # identity rule has shifted under an existing archive, and the archive
        # is the only copy, so refuse rather than rewrite it.
        lost = len(existing_ids) - len(set(existing_ids))
        raise SourceError(
            f"{lost} of {len(existing_ids)} archived entries collapse to the "
            "same identity. Refusing to rewrite the feed."
        )

    # Fetched items can legitimately repeat: a repost of the author's own post
    # comes back under the original URL, and pagination can overlap. That is
    # not an archive-integrity problem, so keep the first occurrence and warn
    # rather than abandon the week. The archive guard above stays strict.
    seen = set()
    deduped = []
    for post in fresh:
        key = identity(post, fallback_link)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(post)
    dropped = len(fresh) - len(deduped)
    if dropped:
        print(
            f"  warning: {dropped} of {len(fresh)} fetched items shared an "
            "identity with another fetched item and were dropped (likely a "
            "repost or pagination overlap)"
        )
    fresh = deduped
    fresh_ids = [identity(post, fallback_link) for post in fresh]

    merged = dict(zip(fresh_ids, fresh))
    merged.update(zip(existing_ids, existing))
    ordered = sorted(merged.values(), key=lambda post: post["date"], reverse=True)
    kept = ordered[:max_items]

    kept_ids = {identity(post, fallback_link) for post in kept}
    added = len(kept_ids - set(existing_ids))
    evicted = len(set(existing_ids) - kept_ids)

    if len(kept) < min(len(existing_ids), max_items):
        raise SourceError(
            f"merge would shrink the feed from {len(existing)} to {len(kept)}"
        )
    return kept, added, evicted


def write_feed(source, path, posts, self_url, image_url=""):
    """Write atomically, and only after proving the result re-parses."""
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<rss version="2.0" xmlns:atom="{NS_ATOM}" xmlns:media="{NS_MEDIA}" '
        f'xmlns:content="{NS_CONTENT}">',
        "  <channel>",
        f"    <title>{xml_text(source['title'])}</title>",
        f"    <link>{xml_text(source['link'] or self_url)}</link>",
        f"    <description>{xml_text(source['description'])}</description>",
        f'    <atom:link href="{xml_text(self_url)}" rel="self" type="application/rss+xml"/>',
        # Always bumped, so a quiet week still produces a commit. That commit is
        # the heartbeat proving the job ran, and it resets GitHub's 60-day
        # inactivity clock that disables scheduled workflows.
        f"    <lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>",
    ]
    if image_url:
        # Readwise Reader shows this beside every entry that has no thumbnail
        # of its own, ahead of the generic logo on the post's own page.
        lines += [
            "    <image>",
            f"      <url>{xml_text(image_url)}</url>",
            f"      <title>{xml_text(source['title'])}</title>",
            f"      <link>{xml_text(source['link'] or self_url)}</link>",
            "    </image>",
        ]
    for post in posts:
        lines += [
            "    <item>",
            f"      <title>{xml_text(post['title'])}</title>",
            f"      <link>{xml_text(post['link'])}</link>",
            f"      <guid isPermaLink=\"false\">{xml_text(post['guid'])}</guid>",
            f"      <pubDate>{format_datetime(post['date'])}</pubDate>",
            f"      <description>{xml_text(post['description'])}</description>",
        ]
        if post.get("html"):
            lines.append(f"      <content:encoded>{xml_text(post['html'])}</content:encoded>")
        if post.get("thumb"):
            # Reader takes an entry's thumbnail from media:thumbnail and
            # ignores images inside the body; it hotlinks the URL rather than
            # copying it, so this must be one that never expires.
            lines.append(f'      <media:thumbnail url="{xml_text(post["thumb"])}"/>')
        origin = post.get("origin") or ""
        if origin and origin != post["link"]:
            lines.append(f'      <atom:link rel="via" href="{xml_text(origin)}"/>')
        lines.append("    </item>")
    lines += ["  </channel>", "</rss>", ""]

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines))
            handle.flush()
            os.fsync(handle.fileno())
        ET.parse(temporary)  # refuse to publish something we cannot read back
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


# --- Media and post pages ----------------------------------------------------
#
# Each new post gets a page of its own under docs/<slug>/p/, with its images
# and video copied into docs/<slug>/m/, and the feed entry links to that page.
# Found by testing against Readwise Reader on 25 Sep 2026:
#
# - Reader shows the page an entry links to, not the feed's own text. Linked to
#   LinkedIn, it gets whatever LinkedIn serves that day: images mostly, video
#   only sometimes. Linked to a page here, it shows exactly this page, video
#   included.
# - The media URLs the actor returns are signed and expire: images in about
#   three weeks, video in seven days. Reader hotlinks rather than copying, so
#   only a copy here stays visible.


def media_dirs(slug):
    """docs/<slug>/m and docs/<slug>/p, refusing anything outside docs/."""
    root = os.path.realpath(DOCS_DIR)
    base = os.path.realpath(os.path.join(DOCS_DIR, slug))
    if os.path.commonpath([root, base]) != root or base == root:
        raise ConfigError(f"{slug}: media directory resolves outside {DOCS_DIR}/")
    return os.path.join(DOCS_DIR, slug, "m"), os.path.join(DOCS_DIR, slug, "p")


def file_id(guid):
    """A short, filename-safe id for a post: its trailing number, or a hash."""
    match = re.search(r"(\d{6,})$", str(guid))
    if match:
        return match.group(1)
    return hashlib.sha1(str(guid).encode("utf-8", "replace")).hexdigest()[:16]


def _describe(url):
    """Host and path only. Signed query strings stay out of public logs."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.netloc}{parts.path[:60]}"


def download(url, stem, kind, limit, opener=None):
    """Save url as stem + extension. Returns the path written, or None.

    kind is 'image' or 'video'; anything served as another type is refused,
    as is anything over limit bytes. Never raises: a missing picture must not
    cost the feed its week.
    """
    if not url.startswith("https://"):
        print(f"  media: skipped non-https {kind} {_describe(url)}")
        return None
    opener = opener or urllib.request.urlopen
    request = urllib.request.Request(url, headers={"User-Agent": MEDIA_USER_AGENT})
    temporary = stem + ".tmp"
    try:
        with opener(request, timeout=120) as response:
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            extension = MEDIA_TYPES.get(content_type)
            if not extension or not content_type.startswith(kind + "/"):
                print(f"  media: skipped {kind} served as {content_type or 'nothing'}: {_describe(url)}")
                return None
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                print(f"  media: skipped {kind} over {limit:,} bytes: {_describe(url)}")
                return None
            os.makedirs(os.path.dirname(os.path.abspath(stem)), exist_ok=True)
            size = 0
            with open(temporary, "wb") as handle:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(f"over {limit:,} bytes")
                    handle.write(chunk)
        if size == 0:
            raise ValueError("empty response")
        final = stem + extension
        os.replace(temporary, final)
        return final
    except Exception as error:  # noqa: BLE001 - media is best effort
        print(f"  media: could not fetch {kind} {_describe(url)}: {redact(error)}")
        return None
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def run_ffmpeg(arguments):
    """Run ffmpeg if it is installed. True on success, False otherwise."""
    binary = shutil.which("ffmpeg")
    if not binary:
        return False
    try:
        subprocess.run([binary, "-y", "-loglevel", "error", *arguments],
                       check=True, timeout=180, capture_output=True)
        return True
    except (subprocess.SubprocessError, OSError) as error:
        print(f"  media: ffmpeg failed: {error}")
        return False


def make_poster(video_path, poster_path):
    """A still for a video. The first frame is often black, so let ffmpeg's
    thumbnail filter pick a representative one from the opening seconds."""
    ok = run_ffmpeg(["-i", video_path, "-vf", "thumbnail=300,scale='min(1280,iw)':-2",
                     "-frames:v", "1", "-q:v", "3", poster_path])
    return ok and os.path.exists(poster_path) and os.path.getsize(poster_path) > 0


def refresh_avatar(url, media_dir, opener=None):
    """Keep docs/<slug>/m/avatar.jpg current. Returns its path, or ''."""
    target = os.path.join(media_dir, "avatar.jpg")
    if url:
        fetched = download(url, os.path.join(media_dir, "avatar-new"), "image",
                           MAX_IMAGE_BYTES, opener)
        if fetched:
            # Profile pictures arrive at 800px and ~800 KB; an icon needs 400.
            if not run_ffmpeg(["-i", fetched, "-vf", "scale='min(400,iw)':-2",
                               "-q:v", "3", target]):
                os.replace(fetched, target)
            if os.path.exists(fetched):
                os.remove(fetched)
    return target if os.path.exists(target) else ""


def paragraphs(text):
    return "".join(
        f"<p>{html.escape(strip_illegal_xml(line.strip()))}</p>"
        for line in str(text).replace("\r", "").split("\n")
        if line.strip()
    )


def render_body(post, images, video, poster):
    """The post as an HTML fragment: the page's article and content:encoded."""
    parts = [paragraphs(post["description"])]
    if post.get("quote"):
        parts.append(f"<blockquote>{paragraphs(post['quote'])}</blockquote>")
    for url in images:
        parts.append(f'<figure><img src="{html.escape(url)}" alt=""></figure>')
    if video:
        poster_attr = f' poster="{html.escape(poster)}"' if poster else ""
        parts.append(
            f'<figure><video controls preload="metadata"{poster_attr}>'
            f'<source src="{html.escape(video)}" type="video/mp4"></video></figure>'
            f'<p><a href="{html.escape(video)}">▶ Play the video</a></p>'
        )
    origin = post.get("origin") or ""
    if origin.startswith(("https://", "http://")):
        parts.append(f'<p><a href="{html.escape(origin)}">View the original post</a></p>')
    return "".join(parts)


def render_page(source, post, body, page_url, thumb):
    title = html.escape(strip_illegal_xml(post["title"]))
    summary = html.escape(strip_illegal_xml(" ".join(post["description"].split())[:200]))
    image_meta = f'\n<meta property="og:image" content="{html.escape(thumb)}">' if thumb else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta property="og:type" content="article">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{summary}">{image_meta}
<meta property="article:published_time" content="{post['date'].isoformat()}">
<link rel="canonical" href="{html.escape(page_url)}">
<style>
  body {{ font: 17px/1.6 system-ui, sans-serif; max-width: 40rem; margin: 2rem auto; padding: 0 1rem; }}
  img, video {{ max-width: 100%; height: auto; }}
  figure {{ margin: 1.5rem 0; }}
  blockquote {{ border-left: 3px solid #ccc; margin: 1rem 0; padding-left: 1rem; color: #555; }}
  .meta {{ color: #777; font-size: .9rem; }}
</style>
</head>
<body>
<article>
<h1>{title}</h1>
<p class="meta">{html.escape(source['title'])} · {post['date']:%d %b %Y}</p>
{body}
</article>
</body>
</html>
"""


def publish_post_page(source, post, site_url, avatar_url, opener=None):
    """Copy a post's media here, write its page, and point the entry at it.

    Whatever fails, the entry is still published: without media it keeps its
    text, and without a page it keeps its original link.
    """
    media_dir, page_dir = media_dirs(source["slug"])
    base = f"{site_url}{source['slug']}/"
    pid = file_id(post["guid"])
    counts = {"images": 0, "video": 0}

    images = []
    for number, url in enumerate(post.get("images") or [], 1):
        saved = download(url, os.path.join(media_dir, f"{pid}-{number}"), "image",
                         MAX_IMAGE_BYTES, opener)
        if saved:
            images.append(base + "m/" + os.path.basename(saved))
    counts["images"] = len(images)

    video = poster = ""
    if post.get("video"):
        saved = download(post["video"], os.path.join(media_dir, pid), "video",
                         MAX_VIDEO_BYTES, opener)
        if saved:
            video = base + "m/" + os.path.basename(saved)
            counts["video"] = 1
            poster_path = os.path.join(media_dir, f"{pid}-poster.jpg")
            if make_poster(saved, poster_path):
                poster = base + "m/" + os.path.basename(poster_path)

    thumb = (images[0] if images else "") or poster or avatar_url
    page_url = f"{base}p/{pid}.html"
    body = render_body(post, images, video, poster)
    try:
        os.makedirs(page_dir, exist_ok=True)
        page_path = os.path.join(page_dir, f"{pid}.html")
        with open(page_path + ".tmp", "w", encoding="utf-8", newline="\n") as handle:
            handle.write(render_page(source, post, body, page_url, thumb))
        os.replace(page_path + ".tmp", page_path)
    except OSError as error:
        print(f"  media: could not write the page for {pid}: {error}")
        post["thumb"] = thumb
        return counts

    post["origin"] = post.get("origin") or post["link"]
    post["link"] = page_url
    post["html"] = body
    post["thumb"] = thumb
    return counts


def prune_media(slug, posts, site_url):
    """Delete pages and media belonging to posts no longer in the feed."""
    media_dir, page_dir = media_dirs(slug)
    prefix = f"{site_url}{slug}/p/"
    in_use = {
        post["link"][len(prefix):-len(".html")]
        for post in posts
        if post["link"].startswith(prefix) and post["link"].endswith(".html")
    }
    removed = 0
    for directory in (media_dir, page_dir):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.startswith("avatar"):
                continue
            owner = re.split(r"[-.]", name, maxsplit=1)[0]
            if owner not in in_use:
                os.remove(os.path.join(directory, name))
                removed += 1
    return removed


# --- Site outputs ------------------------------------------------------------


def write_status(results, site_url, path=STATUS_PATH):
    previous = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                for entry in json.load(handle).get("sources", []):
                    previous[entry.get("slug")] = entry
        except (json.JSONDecodeError, OSError):
            previous = {}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_url": os.environ.get("RUN_URL", ""),
        "sources": [],
    }
    for result in results:
        was = previous.get(result["slug"], {})
        payload["sources"].append(
            {
                "slug": result["slug"],
                "title": result["title"],
                "feed": f"{site_url}{result['slug']}.xml",
                "status": result["status"],
                "items": result.get("items", was.get("items", 0)),
                "added_last_run": result.get("added", 0),
                "evicted_last_run": result.get("evicted", 0),
                "fetched_last_run": result.get("fetched", 0),
                "last_success": result.get("last_success") or was.get("last_success", ""),
                "last_attempt": datetime.now(timezone.utc).isoformat(),
                "error": redact(result.get("error", ""))[:300],
            }
        )
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    return payload


def write_index(status, path=INDEX_PATH):
    def safe_href(url):
        parts = urllib.parse.urlparse(url)
        return url if parts.scheme in ("http", "https") or not parts.scheme else "#"

    rows = []
    for entry in status["sources"]:
        state = entry["status"]
        badge = {"ok": "ok", "skipped": "paused", "failed": "failing"}.get(state, state)
        last = (entry.get("last_success") or "")[:10] or "never"
        rows.append(
            "      <tr>"
            f'<td><a href="{html.escape(safe_href(entry["feed"]), quote=True)}">'
            f'{html.escape(entry["title"])}</a></td>'
            f'<td class="num">{entry.get("items", 0)}</td>'
            f"<td>{html.escape(last)}</td>"
            f'<td class="{html.escape(badge)}">{html.escape(badge)}</td>'
            "</tr>"
        )

    generated = html.escape(status["generated_at"][:16].replace("T", " "))
    document = f"""<!doctype html>
<meta charset="utf-8">
<title>RSS feeds</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 46rem; margin: 3rem auto; padding: 0 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1.5rem 0; }}
  th, td {{ text-align: left; padding: .5rem .75rem; border-bottom: 1px solid #e4e4e7; }}
  th {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; color: #71717a; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .ok {{ color: #15803d; }}
  .failing {{ color: #b91c1c; font-weight: 600; }}
  .paused {{ color: #a1a1aa; }}
  footer {{ font-size: .85rem; color: #71717a; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #18181b; color: #e4e4e7; }}
    th, td {{ border-bottom-color: #3f3f46; }}
    a {{ color: #93c5fd; }}
    .ok {{ color: #4ade80; }}
    .failing {{ color: #f87171; }}
  }}
</style>
<h1>RSS feeds</h1>
<p>Feeds rebuilt from Apify actors, weekly and on every change. Subscribe to any
of them in a reader.</p>
<table>
  <thead><tr><th>Feed</th><th class="num">Items</th><th>Last update</th><th>State</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
  </tbody>
</table>
<footer>Generated {generated} UTC. Machine-readable status in
<a href="status.json">status.json</a>.</footer>
"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)


# --- Build -------------------------------------------------------------------


def build_one(source, token, run_started, site_url, opener=None):
    path = feed_path(source["slug"])
    self_url = f"{site_url}{source['slug']}.xml"

    existing = read_existing(path)  # before spending anything
    raw_items = unwrap(fetch(source, token))
    fresh = normalise(raw_items, source, run_started)
    if not fresh:
        raise SourceError(
            f"actor returned {len(raw_items)} item(s) but none parsed as posts "
            "— check the keys overrides against the actor's output"
        )

    merged, added, evicted = merge(
        existing, fresh, source["max_items"], source["link"]
    )

    avatar_url = ""
    media = {"pages": 0, "images": 0, "video": 0, "pruned": 0}
    if source["media"]:
        media_dir, _ = media_dirs(source["slug"])
        avatar = next((p.get("avatar") for p in fresh if p.get("avatar")), "")
        if refresh_avatar(avatar, media_dir, opener):
            avatar_url = f"{site_url}{source['slug']}/m/avatar.jpg"
        # Only entries not yet published get a page. Changing the link of one
        # already out would make every reader import it a second time, so
        # backfill_pages, which does exactly that, is for a feed nobody reads
        # yet: a new URL being seeded from an old archive.
        archived = {identity(p, source["link"]) for p in existing}
        fetched = {identity(p, source["link"]): p for p in fresh}
        page_prefix = f"{site_url}{source['slug']}/p/"
        for post in merged:
            key = identity(post, source["link"])
            if key in archived:
                if not source.get("backfill_pages") or post["link"].startswith(page_prefix):
                    continue
                # An archived entry keeps its own text and date; its media can
                # only come from this run's fetch, since the archive holds none.
                match = fetched.get(key, {})
                for field in ("images", "video", "avatar", "quote"):
                    post[field] = match.get(field) or post.get(field) or ("" if field != "images" else [])
            counts = publish_post_page(source, post, site_url, avatar_url, opener)
            media["pages"] += 1
            media["images"] += counts["images"]
            media["video"] += counts["video"]

    write_feed(source, path, merged, self_url, avatar_url)
    if source["media"]:
        media["pruned"] = prune_media(source["slug"], merged, site_url)
    return {
        "fetched": len(raw_items),
        "items": len(merged),
        "added": added,
        "evicted": evicted,
        "media": media,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="build only these slugs (others keep their existing feed)",
    )
    args = parser.parse_args()

    token = os.environ.get("APIFY_TOKEN")
    if not token:
        sys.exit("APIFY_TOKEN is not set.")
    global _LIVE_TOKEN
    _LIVE_TOKEN = token

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repository:
        owner, name = repository.split("/", 1)
        site_url = f"https://{owner}.github.io/{name}/"
    else:
        site_url = "/"

    try:
        sources, broken = load_all_sources()
    except ConfigError as error:
        # Nothing has been fetched and nothing written, so every feed is intact.
        sys.exit(f"config error: {error}")
    for bad in broken:
        print(f"{bad['slug']}: FAILED — {bad['error']}")

    run_started = datetime.now(timezone.utc)
    selected = None
    if args.only is not None:
        if not args.only:
            sys.exit("config error: --only needs at least one source name")
        selected = set(args.only)
        known = {s["slug"] for s in sources} | {b["slug"] for b in broken}
        unknown = selected - known
        if unknown:
            # A source deleted in the same push is named here but no longer
            # exists. Skipping it beats aborting and rebuilding nothing.
            print(f"note: --only names source(s) that no longer exist, skipping: "
                  f"{sorted(unknown)}")
            selected -= unknown

    results = [
        {"slug": b["slug"], "title": b["slug"], "link": "", "status": "failed",
         "error": b["error"]}
        for b in broken
    ]
    failures = len(broken)
    for source in sources:
        slug = source["slug"]
        if not source["enabled"]:
            print(f"{slug}: disabled, leaving its feed as it is")
            results.append({**source, "status": "skipped"})
            continue
        if selected is not None and slug not in selected:
            print(f"{slug}: unchanged, leaving its feed as it is")
            results.append({**source, "status": "skipped"})
            continue
        try:
            outcome = build_one(source, token, run_started, site_url)
        except SourceError as error:
            failures += 1
            print(f"{slug}: FAILED — {redact(error)}")
            results.append({**source, "status": "failed", "error": str(error)})
        except Exception as error:  # noqa: BLE001 - one source must not stop the rest
            failures += 1
            print(f"{slug}: FAILED — unexpected {type(error).__name__}: {redact(error)}")
            results.append({**source, "status": "failed", "error": f"{type(error).__name__}: {error}"})
        else:
            note = f", {outcome['evicted']} evicted" if outcome["evicted"] else ""
            print(
                f"{slug}: fetched {outcome['fetched']}, added {outcome['added']}, "
                f"feed holds {outcome['items']}{note}"
            )
            media = outcome.pop("media")
            if source["media"]:
                print(
                    f"{slug}: media: {media['pages']} new page(s), {media['images']} "
                    f"image(s), {media['video']} video(s), {media['pruned']} old file(s) removed"
                )
            results.append(
                {
                    **source,
                    "status": "ok",
                    "last_success": run_started.isoformat(),
                    **outcome,
                }
            )

    status = write_status(results, site_url)
    write_index(status)

    built = sum(1 for r in results if r["status"] == "ok")
    print(f"\n{built} built, {failures} failed, {len(results) - built - failures} skipped.")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Feeds\n\n{built} built, {failures} failed\n\n")
            for entry in status["sources"]:
                mark = {"ok": "OK", "failed": "FAIL", "skipped": "--"}[entry["status"]]
                handle.write(
                    f"- `{mark}` **{entry['title']}** ({entry['items']} items)"
                    + (f" — {entry['error']}" if entry["error"] else "")
                    + "\n"
                )

    # Non-zero so a broken source shows as a red cross, but only after every
    # healthy feed has been written and is ready to commit.
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
