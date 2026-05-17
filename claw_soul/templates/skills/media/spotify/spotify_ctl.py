#!/usr/bin/env python3
"""Spotify Web API controller for ClawSoul."""
import argparse
import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
except ImportError:
    print("Error: requests is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

TOKEN_FILE = os.path.expanduser("~/.claw_soul/.spotify_token.json")
REDIRECT_URI = "http://localhost:8888/callback"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def _get_config() -> dict:
    for path in [os.path.expanduser("~/.claw_soul/claw_soul.json"), "claw_soul.json"]:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    cfg = json.load(f)
                return cfg.get("skills", {}).get("spotify", {})
            except (json.JSONDecodeError, OSError):
                continue
    return {}


def _get_token() -> str:
    """Get a valid access token, refreshing if needed."""
    if os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        if data.get("access_token"):
            return data["access_token"]

    cfg = _get_config()
    if not cfg.get("clientId"):
        print("Error: Spotify not configured. Set skills.spotify.clientId/clientSecret", file=sys.stderr)
        sys.exit(1)

    if os.path.isfile(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        if data.get("refresh_token"):
            resp = requests.post("https://accounts.spotify.com/api/token", data={
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
                "client_id": cfg["clientId"],
                "client_secret": cfg.get("clientSecret", ""),
            }, timeout=10)
            if resp.ok:
                new_data = resp.json()
                data["access_token"] = new_data["access_token"]
                with open(TOKEN_FILE, "w") as f:
                    json.dump(data, f)
                return data["access_token"]

    print("No valid token. Run: python spotify_ctl.py auth", file=sys.stderr)
    sys.exit(1)


def _api(method: str, path: str, token: str, **kwargs) -> dict | None:
    url = f"https://api.spotify.com/v1/{path}"
    resp = getattr(requests, method)(url, headers={"Authorization": f"Bearer {token}"}, timeout=10, **kwargs)
    if resp.status_code == 204:
        return None
    resp.raise_for_status()
    return resp.json()


def cmd_auth(args):
    cfg = _get_config()
    if not cfg.get("clientId"):
        print("Set skills.spotify.clientId and clientSecret in claw_soul.json first")
        return

    auth_url = "https://accounts.spotify.com/authorize?" + urlencode({
        "response_type": "code",
        "client_id": cfg["clientId"],
        "scope": SCOPES,
        "redirect_uri": REDIRECT_URI,
    })
    print("Opening browser for Spotify auth...")
    webbrowser.open(auth_url)

    code = [None]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            code[0] = qs.get("code", [None])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK - you can close this window")

        def log_message(self, *a):
            pass

    server = HTTPServer(("localhost", 8888), Handler)
    server.handle_request()

    if code[0]:
        resp = requests.post("https://accounts.spotify.com/api/token", data={
            "grant_type": "authorization_code",
            "code": code[0],
            "redirect_uri": REDIRECT_URI,
            "client_id": cfg["clientId"],
            "client_secret": cfg.get("clientSecret", ""),
        }, timeout=10)
        resp.raise_for_status()
        token_data = resp.json()
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump(token_data, f)
        print("Auth successful! Token saved.")
    else:
        print("Auth failed — no code received.")


def cmd_now_playing(args):
    token = _get_token()
    data = _api("get", "me/player/currently-playing", token)
    if not data or not data.get("item"):
        print("Nothing currently playing")
        return
    item = data["item"]
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    print(f"Now playing: {item['name']} by {artists}")
    print(f"Album: {item.get('album', {}).get('name', '')}")
    print(f"URI: {item.get('uri', '')}")


def cmd_search(args):
    token = _get_token()
    data = _api("get", "search", token, params={
        "q": args.query, "type": args.type, "limit": 5
    })
    key = args.type + "s"
    for item in data.get(key, {}).get("items", []):
        if args.type == "track":
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            print(f"  {item['name']} — {artists} [{item['uri']}]")
        else:
            print(f"  {item['name']} [{item['uri']}]")


def cmd_play(args):
    token = _get_token()
    body = {}
    if args.uri:
        if "track" in args.uri:
            body = {"uris": [args.uri]}
        else:
            body = {"context_uri": args.uri}
    requests.put("https://api.spotify.com/v1/me/player/play",
                 headers={"Authorization": f"Bearer {token}"},
                 json=body if body else None, timeout=10)
    print("Playing" + (f": {args.uri}" if args.uri else ""))


def cmd_pause(args):
    token = _get_token()
    _api("put", "me/player/pause", token)
    print("Paused")


def cmd_next(args):
    token = _get_token()
    _api("post", "me/player/next", token)
    print("Skipped to next track")


def cmd_previous(args):
    token = _get_token()
    _api("post", "me/player/previous", token)
    print("Back to previous track")


def cmd_queue(args):
    token = _get_token()
    _api("post", f"me/player/queue?uri={args.uri}", token)
    print(f"Queued: {args.uri}")


def cmd_volume(args):
    token = _get_token()
    _api("put", f"me/player/volume?volume_percent={args.level}", token)
    print(f"Volume set to {args.level}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="Authenticate with Spotify")
    sub.add_parser("now-playing", help="Show current track")

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--type", default="track", choices=["track", "album", "playlist", "artist"])

    p = sub.add_parser("play")
    p.add_argument("--uri", default=None)

    sub.add_parser("pause")
    sub.add_parser("next")
    sub.add_parser("previous")

    p = sub.add_parser("queue")
    p.add_argument("uri")

    p = sub.add_parser("volume")
    p.add_argument("level", type=int)

    args = parser.parse_args()
    handlers = {
        "auth": cmd_auth, "now-playing": cmd_now_playing, "search": cmd_search,
        "play": cmd_play, "pause": cmd_pause, "next": cmd_next,
        "previous": cmd_previous, "queue": cmd_queue, "volume": cmd_volume,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
