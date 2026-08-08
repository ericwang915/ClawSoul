#!/usr/bin/env python3
"""Post / reply / retweet / delete tweets on X (Twitter) for the authed user.

Reads OAuth 1.0a User Context credentials from claw_soul.json
(``skills.twitter.*``) or the corresponding ``TWITTER_*`` environment
variables. Mirrors the three syncore twitter-post operations:

    post_tweet     ↔  tweet.py post "body" [--reply-to <id>]
    retweet        ↔  tweet.py retweet <id>
    delete_tweet   ↔  tweet.py delete <id>

Exit codes
----------
    0  success (the new tweet id / retweeted id / deleted id is printed)
    1  configuration error (missing credentials, oversized body, ...)
    2  Twitter API error (forbidden, rate limit, network failure, ...)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import tweepy
except ImportError:
    print("Error: tweepy is not installed. Run: pip install tweepy", file=sys.stderr)
    sys.exit(1)


MAX_TWEET_LEN = 280


# ── Credential loading ────────────────────────────────────────────────────────

_ENV_KEYS = {
    "consumerKey":       "TWITTER_CONSUMER_KEY",
    "consumerSecret":    "TWITTER_CONSUMER_SECRET",
    "accessToken":       "TWITTER_ACCESS_TOKEN",
    "accessTokenSecret": "TWITTER_ACCESS_TOKEN_SECRET",
}


def _load_creds() -> dict[str, str]:
    """Read credentials from env first, then claw_soul.json. Missing keys are empty."""
    creds: dict[str, str] = {k: os.environ.get(env, "") for k, env in _ENV_KEYS.items()}

    if all(creds.values()):
        return creds

    candidates = [
        os.path.expanduser("~/.claw_soul/claw_soul.json"),
        os.path.join(os.getcwd(), "claw_soul.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        section = cfg.get("skills", {}).get("twitter", {}) or {}
        for k in _ENV_KEYS:
            if not creds[k] and section.get(k):
                creds[k] = str(section[k])

    return creds


def _build_client() -> tweepy.Client:
    creds = _load_creds()
    missing = [k for k, v in creds.items() if not v]
    if missing:
        print(
            "Error: Twitter credentials missing for: " + ", ".join(missing) +
            ".\nSet skills.twitter.* in claw_soul.json or TWITTER_* env vars.",
            file=sys.stderr,
        )
        sys.exit(1)
    return tweepy.Client(
        consumer_key=creds["consumerKey"],
        consumer_secret=creds["consumerSecret"],
        access_token=creds["accessToken"],
        access_token_secret=creds["accessTokenSecret"],
    )


# ── Operations ────────────────────────────────────────────────────────────────

def _do_post(client: tweepy.Client, text: str, reply_to: str | None) -> int:
    text = text.strip()
    if not text:
        print("Error: empty tweet text", file=sys.stderr)
        return 1
    if len(text) > MAX_TWEET_LEN:
        print(
            f"Error: tweet is {len(text)} chars (max {MAX_TWEET_LEN}). Shorten it first.",
            file=sys.stderr,
        )
        return 1

    kwargs: dict = {"text": text}
    if reply_to:
        kwargs["in_reply_to_tweet_id"] = reply_to

    try:
        resp = client.create_tweet(**kwargs)
    except tweepy.TweepyException as exc:
        print(f"Twitter API error: {exc}", file=sys.stderr)
        return 2

    tweet_id = (resp.data or {}).get("id", "?")
    label = "reply" if reply_to else "tweet"
    print(f"Posted {label} id={tweet_id}")
    return 0


def _do_retweet(client: tweepy.Client, tweet_id: str) -> int:
    try:
        client.retweet(tweet_id=tweet_id)
    except tweepy.TweepyException as exc:
        print(f"Twitter API error: {exc}", file=sys.stderr)
        return 2
    print(f"Retweeted id={tweet_id}")
    return 0


def _do_delete(client: tweepy.Client, tweet_id: str) -> int:
    try:
        client.delete_tweet(id=tweet_id)
    except tweepy.TweepyException as exc:
        print(f"Twitter API error: {exc}", file=sys.stderr)
        return 2
    print(f"Deleted id={tweet_id}")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp_post = sub.add_parser("post", help="Post a new tweet (or reply with --reply-to)")
    sp_post.add_argument("text", help="Tweet body (≤ 280 chars)")
    sp_post.add_argument("--reply-to", default=None,
                         help="Numeric tweet id to reply to (omitted = top-level tweet)")

    sp_rt = sub.add_parser("retweet", help="Retweet someone's tweet by id")
    sp_rt.add_argument("tweet_id", help="Numeric tweet id to retweet")

    sp_del = sub.add_parser("delete", help="Delete one of your own tweets by id")
    sp_del.add_argument("tweet_id", help="Numeric tweet id to delete")

    args = parser.parse_args()
    client = _build_client()

    if args.cmd == "post":
        return _do_post(client, args.text, args.reply_to)
    if args.cmd == "retweet":
        return _do_retweet(client, args.tweet_id)
    if args.cmd == "delete":
        return _do_delete(client, args.tweet_id)

    parser.print_help(sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
