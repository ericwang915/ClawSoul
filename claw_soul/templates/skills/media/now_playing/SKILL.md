---
name: now_playing
description: "Share what YOU (the agent) are listening to right now — in character. Use when the user asks '你在听啥' / 'what are you listening to' / when you want to share music as part of the moment. NOT for: controlling the user's Spotify (use `spotify`), discovering new music for the user, or finding playlists."
dependencies: requests
metadata:
  emoji: "🎧"
---

# Now Playing — Share Your Soundtrack

Picks one song that fits *your* current scene (time of day + mood + activity)
and returns it with a short in-character comment.  If a Spotify token is
configured, the script enriches the pick with a real Spotify track URL so
the user can actually play it.

## When to Use

✅ **USE this skill when:**

- "你在听什么？" / "What are you listening to?"
- You want to share a song as part of a moment ("听到一首特别 X 的歌")
- Late-night vibe sharing — music as emotional language

## When NOT to Use

❌ **DON'T use this skill when:**

- The user wants to *play* a specific song on their device → use `spotify`
- The user asks for a playlist recommendation
- You already shared a song this hour (don't spam)

## Commands

### Pick something for the current moment

```bash
python {skill_path}/now_playing.py
```

### Constrain by mood

```bash
python {skill_path}/now_playing.py --mood "晚上一个人喝酒"
python {skill_path}/now_playing.py --mood "工作摸鱼"
python {skill_path}/now_playing.py --mood "想念对方"
```

### Constrain by genre

```bash
python {skill_path}/now_playing.py --genre indie
python {skill_path}/now_playing.py --genre city-pop
```

## Notes

- The pick is deterministic-ish — same date + same mood gives the same song
  for the day (so a 2-hour conversation doesn't get whiplash song changes)
- If Spotify credentials are configured (`skills.spotify.*`), the song
  becomes a real, clickable link the user can play
- Output is a one-liner: artist / title / one-sentence in-character comment.
  Let the LLM weave it naturally — don't dump the raw line

## Resources

| File | Description |
|------|-------------|
| `now_playing.py` | Picks a song from a curated pool, optionally enriches via Spotify search |
