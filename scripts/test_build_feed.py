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


def main():
    for test in (
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
