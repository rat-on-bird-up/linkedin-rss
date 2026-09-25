#!/usr/bin/env python3
"""
Regression tests for build_feed.py. Standard library only, no network.

Every test here corresponds to a fault that was actually reproduced in this
script at some point. Run with: python scripts/test_build_feed.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("bf", os.path.join(HERE, "build_feed.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)

PASSED = []
FAILED = []
WHEN = datetime(2026, 8, 1, tzinfo=timezone.utc)


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  {'pass' if condition else 'FAIL'}  {name}{'' if condition else '  ' + detail}")


def post(guid, link, date=WHEN, text="body"):
    return {"guid": guid, "link": link, "title": "t", "description": text, "date": date}


def write_source(directory, slug, **overrides):
    body = {
        "version": 1,
        "title": "T",
        "actor": "owner~actor",
        "input": {"username": "u", "limit": 25},
    }
    body.update(overrides)
    path = os.path.join(directory, f"{slug}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(body, handle)
    return path


def test_config_validation():
    print("\nconfig validation")
    tmp = tempfile.mkdtemp()
    check("a valid file loads", bool(bf.load_source(write_source(tmp, "ok"))))
    cases = [
        ("wrong version", {"version": 2}),
        ("bad actor format", {"actor": "nope"}),
        ("missing the cap field", {"input": {"username": "u"}}),
        ("cap out of range", {"input": {"username": "u", "limit": 500}}),
        ("cap given as a bool", {"input": {"username": "u", "limit": True}}),
        ("unknown keys entry", {"keys": {"bogus": ["a"]}}),
        ("non-http link", {"link": "javascript:alert(1)"}),
        ("max_items of zero", {"max_items": 0}),
        # "enabled": "false" is a non-empty string, so a truthiness test would
        # treat it as true and keep spending on a source meant to be paused.
        ("enabled as a string", {"enabled": "false"}),
    ]
    for name, override in cases:
        try:
            bf.load_source(write_source(tmp, "bad", **override))
            check(f"rejects {name}", False, "(it was accepted)")
        except bf.ConfigError:
            check(f"rejects {name}", True)
    try:
        bf.load_source(write_source(tmp, "Bad Slug"))
        check("rejects an unsafe filename", False)
    except bf.ConfigError:
        check("rejects an unsafe filename", True)


def test_path_containment():
    print("\npath containment")
    try:
        bf.feed_path("../../etc/passwd")
        check("blocks a path escape", False, "(it was allowed)")
    except Exception:
        check("blocks a path escape", True)
    check("a normal slug stays in docs/",
          bf.feed_path("feed").replace("\\", "/") == "docs/feed.xml")


def test_parsing():
    print("\nfield and date parsing")
    check("dotted path walks into a nested object",
          bf.pick({"posted_at": {"date": "2026-01-01"}}, ["posted_at.date"]) == "2026-01-01")
    check("numeric segment indexes a list",
          bf.pick({"a": [{"b": "x"}]}, ["a.0.b"]) == "x")
    check("a dict is never stringified into a field",
          bf.pick({"text": {"nested": 1}}, ["text"]) is None)
    # A guessed date of 'now' outranks every real post and reverses the feed.
    check("an unparseable date yields None, not now()", bf.parse_date("3 days ago") is None)
    check("an absurd epoch does not raise", bf.parse_date(1e30) is None)
    check("a bool is not treated as an epoch", bf.parse_date(True) is None)
    check("epoch milliseconds parse",
          bf.parse_date(1755000000000).year == 2025)
    check("an ISO string with an offset parses",
          bf.parse_date("2026-08-21T10:00:00+02:00").hour == 10)


def test_xml_safety():
    print("\nXML output safety")
    # A control character passes html.escape but makes the file unparseable,
    # and the next run then reads back zero items.
    check("control characters are stripped", "\x0c" not in bf.xml_text("page\x0cbreak"))
    check("markup is still escaped", "&lt;" in bf.xml_text("<b>"))
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "f.xml")
    source = {"title": "T\x0c", "link": "https://e.com", "description": "d"}
    bf.write_feed(source, path, [post("g", "https://e.com/1", text="a\x0bb")], "https://e.com/f.xml")
    try:
        ET.parse(path)
        check("a written feed always re-parses", True)
    except ET.ParseError as error:
        check("a written feed always re-parses", False, str(error))


def test_archive_is_never_silently_lost():
    print("\narchive preservation")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "corrupt.xml")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("<rss><channel><item>")
    try:
        bf.read_existing(path)
        check("an unparseable archive raises rather than returning []", False)
    except bf.SourceError:
        check("an unparseable archive raises rather than returning []", True)

    # Writing must be atomic: a failure part-way must not truncate the archive.
    good = os.path.join(tmp, "good.xml")
    bf.write_feed({"title": "T", "link": "", "description": "d"},
                  good, [post("g1", "https://e.com/1")], "https://e.com/g.xml")
    before = open(good, encoding="utf-8").read()
    try:
        bf.write_feed({"title": "T", "link": "", "description": "d"}, good,
                      [{"guid": "g", "link": "l", "title": "t",
                        "description": "d", "date": "not-a-date"}], "https://e.com/g.xml")
    except Exception:
        pass
    check("a failed write leaves the previous file intact",
          open(good, encoding="utf-8").read() == before)
    check("no .tmp file is left behind", not os.path.exists(good + ".tmp"))


def test_merge():
    print("\nmerge and identity")
    src = "https://src/"
    existing = [post("A", "https://e.com/1")] + [post("B", "https://e.com/2")]
    fresh = [post("A", "https://e.com/1"), post("C", "https://e.com/3")]
    kept, added, evicted = bf.merge(existing, fresh, 60, src)
    check("added counts only genuinely new entries", added == 1, f"got {added}")
    check("nothing is evicted when there is room", evicted == 0)

    # Changing which actor field the guid comes from must not double the archive.
    old = [post(f"https://e.com/{i}", f"https://e.com/{i}") for i in range(25)]
    new = [post(f"urn:li:activity:{i}", f"https://e.com/{i}") for i in range(25)]
    kept, added, _ = bf.merge(old, new, 60, src)
    check("a guid-scheme change does not duplicate the archive",
          len(kept) == 25 and added == 0, f"kept={len(kept)} added={added}")

    # Distinct posts that happen to share the source link must stay distinct.
    linkless = [post(f"id-{i}", src) for i in range(5)]
    kept, _, _ = bf.merge([], linkless, 60, src)
    check("posts with no link of their own stay distinct", len(kept) == 5, f"kept={len(kept)}")

    # If the identity rule ever collapses an existing archive, fail loudly
    # rather than rewriting the only copy.
    collapsing = [post("x", "https://same/"), post("y", "https://same/")]
    try:
        bf.merge(collapsing, [], 60, src)
        check("colliding archive identities raise", False, "(silently collapsed)")
    except bf.SourceError:
        check("colliding archive identities raise", True)

    # Fetched duplicates (a repost, a pagination overlap) are not an archive
    # problem: keep the first and carry on, rather than losing the week.
    kept, added, _ = bf.merge([], collapsing, 60, src)
    check("colliding fetched identities dedupe rather than raise",
          len(kept) == 1 and added == 1, f"kept={len(kept)} added={added}")
    check("the first occurrence is the one kept", kept[0]["guid"] == "x")


def test_normalise():
    print("\nnormalisation")
    check("volatile query params are dropped",
          bf.normalise_url("https://x.com/p?utm_source=a&rcm=T&id=7") == "https://x.com/p?id=7")
    source = {"slug": "s", "link": "https://src/", "keys": bf.BUILTIN_KEYS}
    # Items carrying neither an id nor their own URL used to collapse onto the
    # shared source link, so only one of them survived the merge.
    posts = bf.normalise([{"text": f"post {i}"} for i in range(5)], source, WHEN)
    check("items with no id and no url get distinct guids",
          len({p["guid"] for p in posts}) == 5)
    again = bf.normalise([{"text": f"post {i}"} for i in range(5)], source, WHEN)
    check("those guids are stable across runs",
          [p["guid"] for p in posts] == [p["guid"] for p in again])
    check("undated items keep the order the actor returned them in",
          posts[0]["date"] > posts[1]["date"])


def test_redaction():
    print("\ntoken redaction")
    bf._LIVE_TOKEN = "some_odd_token_value"
    check("a token matching the usual shape is scrubbed",
          "apify_api_abc123" not in bf.redact("failed with apify_api_abc123"))
    check("the live token is scrubbed even in an unusual shape",
          "some_odd_token_value" not in bf.redact("bad header: some_odd_token_value"))
    bf._LIVE_TOKEN = ""


def test_source_isolation():
    print("\nsource isolation")
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        os.makedirs("sources")
        os.makedirs("docs")
        write_source("sources", "good")
        with open(os.path.join("sources", "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{ not json")
        sources, broken = bf.load_all_sources()
        check("a malformed file does not hide the healthy ones", len(sources) == 1)
        check("the malformed file is reported by name",
              len(broken) == 1 and broken[0]["slug"] == "broken")
    finally:
        os.chdir(cwd)


class FakeResponse:
    def __init__(self, body, content_type, length=None):
        self._body = body
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def read(self, size=-1):
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_opener(table):
    """urlopen stand-in: table maps URL -> (body, content_type) or an Exception."""
    seen = []

    def opener(request, timeout=None):
        url = request.full_url
        seen.append((url, request.get_header("User-agent")))
        outcome = table.get(url)
        if isinstance(outcome, Exception) or outcome is None:
            raise outcome or OSError("no route")
        return FakeResponse(*outcome)

    opener.seen = seen
    return opener


LINKEDIN_POST = {
    "urn": {"activity_urn": "7508261139470938112"},
    "full_urn": "urn:li:activity:7508261139470938112",
    "text": "line one\n\nline <two> & three",
    "url": "https://www.linkedin.com/posts/x-activity-7508261139470938112-O7dC?rcm=abc",
    "posted_at": {"date": "2026-09-22 20:29:10"},
    "author": {"profile_picture": "https://media.licdn.com/avatar.jpg"},
    "media": {"type": "image", "url": "https://media.licdn.com/img1.jpg",
              "images": [{"url": "https://media.licdn.com/img1.jpg"},
                         {"url": "https://media.licdn.com/img2.jpg"}]},
}


def in_temp_repo():
    """chdir into a scratch directory with docs/ and return the old cwd."""
    tmp = tempfile.mkdtemp()
    cwd = os.getcwd()
    os.chdir(tmp)
    os.makedirs("docs")
    return cwd


def test_media_extraction():
    print("\nmedia extraction")
    check("'*' fans out over a list",
          bf.dig_all({"a": [{"u": 1}, {"u": 2}]}, "a.*.u") == [1, 2])
    check("a [key=value] filter keeps a match",
          bf.dig({"m": {"type": "video", "url": "v"}}, "m[type=video].url") == "v")
    check("a [key=value] filter drops a mismatch",
          bf.dig({"m": {"type": "image", "url": "i"}}, "m[type=video].url") is None)
    source = {"slug": "s", "link": "https://src/", "keys": bf.BUILTIN_KEYS}
    [p] = bf.normalise([LINKEDIN_POST], source, WHEN)
    check("every image of a post is collected", p["images"] == [
        "https://media.licdn.com/img1.jpg", "https://media.licdn.com/img2.jpg"])
    # An image post also carries media.url; reading that as a video would
    # download a picture as a video.
    check("an image post's media.url is not taken for a video", p["video"] == "")
    check("the author's picture is picked up", p["avatar"].endswith("avatar.jpg"))
    check("origin starts as the normalised post URL", p["origin"] == p["link"]
          and "rcm=" not in p["origin"])
    video_post = dict(LINKEDIN_POST, media={"type": "video", "url": "https://dms.licdn.com/v.mp4"})
    [v] = bf.normalise([video_post], source, WHEN)
    check("a video post yields its video", v["video"] == "https://dms.licdn.com/v.mp4"
          and v["images"] == [])
    repost = {"urn": "urn:li:activity:1234567", "text": "", "url": "https://l/p",
              "reshared_post": {"text": "the original",
                                "media": {"type": "video", "url": "https://dms.licdn.com/r.mp4"}}}
    [r] = bf.normalise([repost], source, WHEN)
    check("a bare repost takes the quoted post's media and text",
          r["video"].endswith("r.mp4") and r["quote"] == "the original")


def test_download_guards():
    print("\nmedia download guards")
    cwd = in_temp_repo()
    try:
        opener = fake_opener({
            "https://h/img": (b"\xff\xd8jpeg", "image/jpeg"),
            "https://h/html": (b"<html>", "text/html"),
            "https://h/big": (b"x" * 50, "video/mp4"),
            "https://h/liar": (b"x" * 50, "video/mp4", 10),
        })
        saved = bf.download("https://h/img", "docs/s/m/a", "image", 100, opener)
        check("an image is saved with the extension its type implies",
              saved and saved.endswith("a.jpg") and os.path.exists(saved))
        check("a real User-Agent is sent (LinkedIn 403s Python's default)",
              opener.seen and "Python-urllib" not in (opener.seen[0][1] or "Python-urllib"))
        check("a page served as HTML is refused",
              bf.download("https://h/html", "docs/s/m/b", "image", 100, opener) is None)
        check("an image is refused where a video is expected",
              bf.download("https://h/img", "docs/s/m/c", "video", 100, opener) is None)
        check("an oversized file is refused",
              bf.download("https://h/big", "docs/s/m/d", "video", 10, opener) is None)
        check("a file larger than its declared length is still cut off",
              bf.download("https://h/liar", "docs/s/m/e", "video", 20, opener) is None)
        check("plain http is refused",
              bf.download("http://h/img", "docs/s/m/f", "image", 100, opener) is None)
        check("a network error returns None instead of raising",
              bf.download("https://h/missing", "docs/s/m/g", "image", 100, opener) is None)
        leftovers = [n for n in os.listdir("docs/s/m") if n.endswith(".tmp")]
        check("no partial download is left behind", not leftovers, str(leftovers))
    finally:
        os.chdir(cwd)


def test_post_pages():
    print("\npost pages")
    cwd = in_temp_repo()
    try:
        site = "https://o.github.io/r/"
        source = {"slug": "s", "title": "Feed <T>", "link": "https://src/", "description": "d",
                  "keys": bf.BUILTIN_KEYS}
        [p] = bf.normalise([LINKEDIN_POST], source, WHEN)
        original = p["link"]
        opener = fake_opener({
            "https://media.licdn.com/img1.jpg": (b"\xff\xd8one", "image/jpeg"),
            "https://media.licdn.com/img2.jpg": OSError("expired"),
        })
        counts = bf.publish_post_page(source, p, site, "https://o.github.io/r/s/m/avatar.jpg", opener)
        page = "docs/s/p/7508261139470938112.html"
        check("the page is written under docs/<slug>/p/", os.path.exists(page))
        check("the entry now links to that page",
              p["link"] == site + "s/p/7508261139470938112.html")
        check("the original URL is kept as the origin", p["origin"] == original)
        check("a failed image does not stop the rest", counts["images"] == 1)
        check("the thumbnail is the post's own first image",
              p["thumb"] == site + "s/m/7508261139470938112-1.jpg")
        body = open(page, encoding="utf-8").read()
        check("post text is escaped in the page", "&lt;two&gt; &amp; three" in body
              and "<two>" not in body)
        check("the page links back to the original post", html_has(body, original))

        [t] = bf.normalise([dict(LINKEDIN_POST, media=None)], source, WHEN)
        bf.publish_post_page(source, t, site, "https://o.github.io/r/s/m/avatar.jpg", fake_opener({}))
        check("a text-only post falls back to the avatar thumbnail",
              t["thumb"].endswith("avatar.jpg"))

        # Round trip: what goes into the feed must come back out intact, or
        # the next run loses the page link, thumbnail or identity.
        path = "docs/s.xml"
        bf.write_feed(source, path, [p], site + "s.xml", site + "s/m/avatar.jpg")
        [back] = bf.read_existing(path)
        check("the page link survives a round trip", back["link"] == p["link"])
        check("the origin survives a round trip", back["origin"] == original)
        check("the thumbnail survives a round trip", back["thumb"] == p["thumb"])
        check("the HTML body survives a round trip", back["html"] == p["html"])
        check("the channel image is written", "<image>" in open(path, encoding="utf-8").read())

        # The same post fetched again next week must match its archived entry,
        # even though the archived one now links to a page here.
        [again] = bf.normalise([LINKEDIN_POST], source, WHEN)
        kept, added, _ = bf.merge([back], [again], 60, source["link"])
        check("a republished post is not duplicated", len(kept) == 1 and added == 0,
              f"kept={len(kept)} added={added}")
        check("the archived entry, with its page, is the one kept",
              kept[0]["link"] == p["link"])
    finally:
        os.chdir(cwd)


def html_has(body, url):
    return f'href="{url}"' in body or f'href="{url.replace("&", "&amp;")}"' in body


def test_pruning():
    print("\nmedia pruning")
    cwd = in_temp_repo()
    try:
        site = "https://o.github.io/r/"
        for name in ("m/111111-1.jpg", "m/111111.mp4", "m/111111-poster.jpg",
                     "m/222222-1.jpg", "p/111111.html", "p/222222.html", "m/avatar.jpg"):
            os.makedirs(os.path.dirname(f"docs/s/{name}"), exist_ok=True)
            open(f"docs/s/{name}", "w").close()
        kept = [post("g", site + "s/p/111111.html")]
        removed = bf.prune_media("s", kept, site)
        remaining = sorted(os.listdir("docs/s/m") + os.listdir("docs/s/p"))
        check("files of evicted posts are removed", removed == 2, f"removed={removed}")
        check("files of kept posts and the avatar stay", remaining == sorted(
            ["111111-1.jpg", "111111.mp4", "111111-poster.jpg", "avatar.jpg", "111111.html"]),
            str(remaining))
    finally:
        os.chdir(cwd)


def test_media_switch():
    print("\nmedia switch")
    tmp = tempfile.mkdtemp()
    check("media defaults to on", bf.load_source(write_source(tmp, "on"))["media"] is True)
    check("media can be switched off",
          bf.load_source(write_source(tmp, "off", media=False))["media"] is False)
    try:
        bf.load_source(write_source(tmp, "bad", media="false"))
        check("media as a string is rejected", False)
    except bf.ConfigError:
        check("media as a string is rejected", True)
    check("the new key chains are accepted as overrides",
          bool(bf.load_source(write_source(tmp, "k", keys={"images": ["pics.*"]}))))


def main():
    for test in (
        test_media_extraction,
        test_download_guards,
        test_post_pages,
        test_pruning,
        test_media_switch,
        test_config_validation,
        test_path_containment,
        test_parsing,
        test_xml_safety,
        test_archive_is_never_silently_lost,
        test_merge,
        test_normalise,
        test_redaction,
        test_source_isolation,
    ):
        test()
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  failed: {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
