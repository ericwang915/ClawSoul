"""
ClawSoul "Tools" catalog.

Defines the user-facing tool/integration directory that powers the new
dashboard "Tools" tab. Each entry maps a presentable name to:
  * an internal skill (already in ``claw_soul/templates/skills/``), OR
  * a third-party integration that needs API key / OAuth setup.

Per-user status is computed from the Supabase ``user_settings.integrations``
JSONB blob — see ``supabase/migrations/002_integrations.sql``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


# Auth model
AuthType = Literal[
    "none",          # always-on, no credentials needed (weather, summarize, …)
    "shared",        # uses the server's shared key (selfie via Seedream)
    "api_key",       # user pastes their own API key in a modal
    "oauth",         # OAuth provider via Supabase
    "caldav",        # username + app-password (Apple Calendar)
]


@dataclass(frozen=True)
class Tool:
    name: str                          # slug, also key under integrations JSONB
    display_name: str
    description: str
    icon: str                          # emoji fallback (legacy)
    icon_key: str                      # key into the frontend ICON_SVG dict
    auth_type: AuthType
    category: str = "Standard"         # "Premium" | "Standard"
    accent: str = "#7c7589"            # icon brand color / tint

    # Provider-specific
    key_url: str | None = None         # link shown to user where to get the key
    oauth_provider: str | None = None  # "google" | "twitter" | "reddit"
    oauth_scopes: list[str] = field(default_factory=list)

    # Status
    coming_soon: bool = False
    server_env: str | None = None      # if set + present in os.environ, status = activated

    def status_for(self, user_integrations: dict) -> str:
        """``activated`` / ``not_connected`` / ``coming_soon``."""
        if self.coming_soon:
            return "coming_soon"
        if self.auth_type == "none":
            return "activated"
        if self.auth_type == "shared":
            return "activated" if self.server_env and os.environ.get(self.server_env) else "not_connected"
        entry = user_integrations.get(self.name, {}) if user_integrations else {}
        if self.auth_type == "api_key" and entry.get("api_key"):
            return "activated"
        if self.auth_type == "oauth" and entry.get("access_token"):
            return "activated"
        if self.auth_type == "caldav" and entry.get("username") and entry.get("password"):
            return "activated"
        return "not_connected"


# ── Catalog ─────────────────────────────────────────────────────────────────

CATALOG: list[Tool] = [
    # ── Always-on / shared ────────────────────────────────────────────────
    Tool(
        name="selfie",
        display_name="AI Selfies",
        description="Auto-generated selfies via Seedream — matches their mood + plan.",
        icon="📷", icon_key="selfie",
        auth_type="shared",
        category="Premium",
        accent="#ff6b9d",
        server_env="ARK_API_KEY",
    ),
    Tool(
        name="weather",
        display_name="Weather",
        description="Real-time weather + 7-day forecast via Open-Meteo. No setup.",
        icon="🌤️", icon_key="weather",
        auth_type="none",
        category="Standard",
        accent="#38bdf8",
    ),
    Tool(
        name="time",
        display_name="Time & Timezones",
        description="Current time, date, timezone conversion, Unix timestamps.",
        icon="⏰", icon_key="time",
        auth_type="none",
        category="Standard",
        accent="#a78bfa",
    ),
    Tool(
        name="translator",
        display_name="Translator",
        description="Cross-language translation via the LLM. No external API.",
        icon="🌐", icon_key="translator",
        auth_type="none",
        category="Standard",
        accent="#34d399",
    ),
    Tool(
        name="summarize",
        display_name="Article Summarizer",
        description="Summarize any URL, article, or PDF.",
        icon="📰", icon_key="summarize",
        auth_type="none",
        category="Standard",
        accent="#fbbf24",
    ),
    Tool(
        name="news",
        display_name="News Briefing",
        description="Latest headlines on any topic via web search.",
        icon="📡", icon_key="news",
        auth_type="none",
        category="Standard",
        accent="#fb923c",
    ),
    Tool(
        name="random",
        display_name="Random",
        description="Random numbers, UUIDs, passwords, or pick-from-list.",
        icon="🎲", icon_key="random",
        auth_type="none",
        category="Standard",
        accent="#94a3b8",
    ),

    # ── Always-on companion skills (shipped, no auth) ──────────────────────
    Tool(
        name="horoscope",
        display_name="Horoscope",
        description="Daily reading in your culture — Chinese 黄历, Western zodiac, 占い, rashifal.",
        icon="🔮", icon_key="horoscope",
        auth_type="none",
        category="Standard",
        accent="#a855f7",
    ),
    Tool(
        name="look_back",
        display_name="Photo Album Recap",
        description="Look through past selfies together — milestones, anniversaries, throwbacks.",
        icon="🖼️", icon_key="look_back",
        auth_type="none",
        category="Premium",
        accent="#ec4899",
    ),
    Tool(
        name="now_playing",
        display_name="Now Playing",
        description="They share what they're listening to right now — in character.",
        icon="🎧", icon_key="now_playing",
        auth_type="none",
        category="Standard",
        accent="#22d3ee",
    ),
    Tool(
        name="bucket_list",
        display_name="Couple Bucket List",
        description='"WE" aspirations — places, foods, milestones to chase together.',
        icon="✨", icon_key="bucket_list",
        auth_type="none",
        category="Standard",
        accent="#facc15",
    ),
    Tool(
        name="letter",
        display_name="Long-form Letter",
        description="A heartfelt long letter on milestones, hard moments, or big news.",
        icon="💌", icon_key="letter",
        auth_type="none",
        category="Premium",
        accent="#f472b6",
    ),
    Tool(
        name="mood_share",
        display_name="Mood Share",
        description="A passing emotional beat — lyric, quote, one-line poem they're feeling.",
        icon="💭", icon_key="mood_share",
        auth_type="none",
        category="Standard",
        accent="#c084fc",
    ),

    # ── User-provided API keys ─────────────────────────────────────────────
    Tool(
        name="tavily",
        display_name="Tavily Web Search",
        description="AI-grade web search with citations. 1000 free queries/month.",
        icon="🔎", icon_key="tavily",
        auth_type="api_key",
        category="Premium",
        accent="#22c55e",
        key_url="https://app.tavily.com/home",
    ),
    Tool(
        name="openai_images",
        display_name="DALL-E Image Gen",
        description="Generate images via OpenAI Images API (DALL-E 3 / GPT-image).",
        icon="🎨", icon_key="openai",
        auth_type="api_key",
        category="Premium",
        accent="#10a37f",   # OpenAI green
        key_url="https://platform.openai.com/api-keys",
    ),
    Tool(
        name="elevenlabs",
        display_name="ElevenLabs Voice",
        description="Text-to-speech voice replies. Custom voice + emotion.",
        icon="🎙️", icon_key="elevenlabs",
        auth_type="api_key",
        category="Premium",
        accent="#0f0f0f",   # ElevenLabs uses near-black brand
        key_url="https://elevenlabs.io/app/settings/api-keys",
    ),
    Tool(
        name="deepgram",
        display_name="Deepgram Speech",
        description="Voice input — transcribe what you say, multilingual.",
        icon="🎧", icon_key="deepgram",
        auth_type="api_key",
        category="Premium",
        accent="#13ef93",   # Deepgram brand green
        key_url="https://console.deepgram.com",
    ),

    # ── OAuth integrations (Coming Soon — Phase 2+) ────────────────────────
    Tool(
        name="gmail",
        display_name="Gmail",
        description="Read, search, and send email on your behalf.",
        icon="📧", icon_key="gmail",
        auth_type="oauth",
        category="Premium",
        accent="#ea4335",
        oauth_provider="google",
        oauth_scopes=["gmail.readonly", "gmail.send"],
        coming_soon=True,
    ),
    Tool(
        name="google_calendar",
        display_name="Google Calendar",
        description="Create events, check availability across calendars.",
        icon="📅", icon_key="google_calendar",
        auth_type="oauth",
        category="Premium",
        accent="#1a73e8",
        oauth_provider="google",
        oauth_scopes=["calendar"],
        coming_soon=True,
    ),
    Tool(
        name="twitter",
        display_name="X / Twitter",
        description="Search trending posts, your timeline, mentions.",
        icon="𝕏", icon_key="twitter",
        auth_type="oauth",
        category="Premium",
        accent="#000000",
        oauth_provider="twitter",
        oauth_scopes=["tweet.read", "users.read"],
        coming_soon=True,
    ),
    Tool(
        name="reddit",
        display_name="Reddit",
        description="Search discussions — what people are saying about a topic.",
        icon="🤖", icon_key="reddit",
        auth_type="oauth",
        category="Premium",
        accent="#ff4500",
        oauth_provider="reddit",
        oauth_scopes=["read"],
        coming_soon=True,
    ),
    Tool(
        name="apple_calendar",
        display_name="Apple Calendar",
        description="Sync iCloud / Apple calendar via CalDAV (username + app password).",
        icon="🍎", icon_key="apple",
        auth_type="caldav",
        category="Premium",
        accent="#000000",
        coming_soon=True,
    ),
    Tool(
        name="spotify",
        display_name="Spotify",
        description="Play, search, queue tracks. Voice control your music.",
        icon="🎵", icon_key="spotify",
        auth_type="oauth",
        category="Premium",
        accent="#1ed760",
        oauth_provider="spotify",
        oauth_scopes=["user-modify-playback-state", "user-read-playback-state"],
        coming_soon=True,
    ),
]


def serialize_tool(tool: Tool, user_integrations: dict) -> dict:
    """JSON payload for one tool card."""
    return {
        "name":         tool.name,
        "displayName":  tool.display_name,
        "description":  tool.description,
        "icon":         tool.icon,
        "iconKey":      tool.icon_key,
        "accent":       tool.accent,
        "category":     tool.category,
        "authType":     tool.auth_type,
        "keyUrl":       tool.key_url,
        "oauthProvider": tool.oauth_provider,
        "status":       tool.status_for(user_integrations),
    }


def find(name: str) -> Tool | None:
    return next((t for t in CATALOG if t.name == name), None)
