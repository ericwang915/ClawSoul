---
name: spotify
description: "Control Spotify playback — play, pause, skip, search, and queue tracks. Use when: user asks to play music, search for songs, control playback, or manage Spotify queue. NOT for: downloading music, non-Spotify services (Apple Music, YouTube Music), or audio file playback."
dependencies: requests
metadata:
  emoji: "🎵"
---

# Spotify

Control Spotify playback via the Spotify Web API.

## When to Use

✅ **USE this skill when:**

- "Play some jazz music"
- "Skip this song"
- "What's currently playing?"
- "Search for songs by The Beatles"
- "Pause the music"
- "Add this song to my queue"

## When NOT to Use

❌ **DON'T use this skill when:**

- Downloading or saving music → Spotify API doesn't support downloads
- Apple Music or YouTube Music → different APIs/tools
- Playing local audio files → use system media player
- Music recognition → use specialised tools (Shazam, etc.)

## Setup

1. Create a Spotify app at https://developer.spotify.com/dashboard
2. Set redirect URI to `http://localhost:8888/callback`
3. Get your Client ID and Client Secret
4. Configure in `claw_soul.json`:

```json
"skills": {
  "spotify": {
    "clientId": "your-client-id",
    "clientSecret": "your-client-secret"
  }
}
```

5. Run the auth flow once to get a refresh token:

```bash
python {skill_path}/spotify_ctl.py auth
```

### Alternative: spotify-player CLI

If you have `spotify_player` installed (Rust-based TUI):

```bash
brew install spotify_player  # macOS
```

Then use it directly:
```bash
spotify_player playback play
spotify_player playback pause
spotify_player search "query"
```

## Commands

### Current playback

```bash
python {skill_path}/spotify_ctl.py now-playing
```

### Search

```bash
python {skill_path}/spotify_ctl.py search "The Beatles" --type track
python {skill_path}/spotify_ctl.py search "Chill Vibes" --type playlist
```

### Playback control

```bash
python {skill_path}/spotify_ctl.py play
python {skill_path}/spotify_ctl.py pause
python {skill_path}/spotify_ctl.py next
python {skill_path}/spotify_ctl.py previous
```

### Play a specific track or playlist

```bash
python {skill_path}/spotify_ctl.py play --uri spotify:track:4iV5W9uYEdYUVa79Axb7Rh
python {skill_path}/spotify_ctl.py play --uri spotify:playlist:37i9dQZF1DXcBWIGoYBM5M
```

### Queue a track

```bash
python {skill_path}/spotify_ctl.py queue spotify:track:4iV5W9uYEdYUVa79Axb7Rh
```

### Volume

```bash
python {skill_path}/spotify_ctl.py volume 50
```

## Notes

- Requires Spotify Premium for playback control
- An active Spotify device must be running (desktop app, mobile, or web player)
- The auth token expires and auto-refreshes via the refresh token
- Search results return Spotify URIs which can be used for playback

## Resources

| File | Description |
|------|-------------|
| `spotify_ctl.py` | Spotify Web API controller |
