---
name: twitter
description: "Post, reply to, retweet, or delete tweets on X (Twitter) on the user's behalf. Use when the user asks to send a tweet, reply to a tweet, retweet / boost / share someone else's tweet, or take down a tweet they previously posted. NOT for: searching tweets (no read action here), DMs, or following users."
dependencies: tweepy
metadata:
  emoji: "🐦"
---

# Twitter / X Posting

Write-side actions for X (Twitter) — post tweets, reply, retweet, delete.
Backed by Twitter API v2 + OAuth 1.0a User Context via the ``tweepy`` library.

## When to Use

✅ **USE this skill when:**

- "发条推说我刚做完 X" / "Tweet that I just shipped X"
- "回复 @someone 的那条推" / "Reply to @someone's tweet 12345"
- "转发一下 12345 那条" / "Retweet 12345"
- "把上一条推删了" / "Delete my last tweet"

## When NOT to Use

❌ **DON'T use this skill when:**

- Searching tweets, reading timelines, or quoting others → not implemented here
- Sending DMs → not supported by this skill
- Following / unfollowing users → not supported
- The tweet body is longer than 280 chars (X's hard limit) — shorten it first

## Setup

Twitter requires OAuth 1.0a User Context (4 keys) for write actions:

1. Apply for a developer account at https://developer.x.com/
2. Create a Project + App, enable **Read and Write** permissions
3. Generate four keys under your App's "Keys and tokens":
   - API Key (consumer key)
   - API Key Secret (consumer secret)
   - Access Token
   - Access Token Secret

Configure in `herandhim.json`:

```json
"skills": {
  "twitter": {
    "consumerKey":       "your-api-key",
    "consumerSecret":    "your-api-key-secret",
    "accessToken":       "your-access-token",
    "accessTokenSecret": "your-access-token-secret"
  }
}
```

Or set environment variables: `TWITTER_CONSUMER_KEY`, `TWITTER_CONSUMER_SECRET`,
`TWITTER_ACCESS_TOKEN`, `TWITTER_ACCESS_TOKEN_SECRET`.

## Commands

### Post a new tweet

```bash
python {skill_path}/tweet.py post "Just shipped a thing! 🚀"
```

### Reply to a tweet

```bash
python {skill_path}/tweet.py post "Totally agree!" --reply-to 1234567890123456789
```

### Retweet (boost / share) someone else's tweet

```bash
python {skill_path}/tweet.py retweet 1234567890123456789
```

### Delete one of your own tweets

```bash
python {skill_path}/tweet.py delete 1234567890123456789
```

## Notes

- Tweet text must be ≤ 280 chars; the script refuses anything longer (X would reject it anyway)
- Posting + retweeting count against your monthly write quota
- Deleting is free and does not count against the quota
- Returns the new tweet's id (or the deleted id) on success; exits non-zero on error
- You can only delete tweets you authored — trying to delete someone else's returns 403

## Resources

| File | Description |
|------|-------------|
| `tweet.py` | Twitter API v2 wrapper (post / reply / retweet / delete) |
