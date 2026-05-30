"""
Subscription plans — Free / Pro / Ultra.

Single source of truth for tier limits + pricing, used by:
  - quota.py            (enforcing message + photo caps)
  - web /api/plans      (rendering the pricing page)

Limits (decided 2026-05):
  Free   — 300 messages / month, 10 photos / month
  Pro    — unlimited messages (fair-use 500/day), 100 photos / month
  Ultra  — unlimited messages (fair-use 500/day), 300 photos / month

"Unlimited" carries a per-day fair-use ceiling so a single heavy/abusive
user can't run the shared LLM bill away (see the cost model).

Launch pricing is a limited-time discount off the regular price.
"""

from __future__ import annotations

# A high but finite ceiling that reads as "unlimited" to users while
# bounding worst-case cost.
_FAIR_USE_DAILY = 500
_ENTERPRISE_DAILY = 100_000

PLANS: dict[str, dict] = {
    "free": {
        "label": "Free",
        "tagline": "Start talking — no card needed.",
        "messages": {"period": "month", "cap": 300},
        "photos_per_month": 10,
        "price": 0.0,
        "price_regular": 0.0,
        "proactive": False,
        "features": [
            "300 messages a month",
            "10 photos a month",
            "Remembers everything you share",
        ],
    },
    "pro": {
        "label": "Pro",
        "tagline": "For an everyday companion.",
        "messages": {"period": "day", "cap": _FAIR_USE_DAILY},
        "photos_per_month": 100,
        "price": 9.9,
        "price_regular": 19.99,
        "proactive": True,
        "features": [
            "Unlimited messages",
            "100 photos a month",
            "Proactive check-ins & daily life",
            "Priority responses",
        ],
    },
    "ultra": {
        "label": "Ultra",
        "tagline": "The fullest, closest experience.",
        "messages": {"period": "day", "cap": _FAIR_USE_DAILY},
        "photos_per_month": 300,
        "price": 19.9,
        "price_regular": 39.99,
        "proactive": True,
        "features": [
            "Unlimited messages",
            "300 photos a month",
            "Proactive check-ins & daily life",
            "Everything in Pro",
        ],
    },
    "enterprise": {
        "label": "Enterprise",
        "tagline": "",
        "messages": {"period": "day", "cap": _ENTERPRISE_DAILY},
        "photos_per_month": 100_000,
        "price": None,
        "price_regular": None,
        "proactive": True,
        "features": [],
    },
}

# Legacy tier names → current plan keys.  Machines provisioned before the
# Free/Pro/Ultra split used "paid"; treat those as Pro.
_ALIASES = {"paid": "pro", "premium": "pro", "": "free"}


def normalize_tier(tier: str | None) -> str:
    t = (tier or "free").strip().lower()
    t = _ALIASES.get(t, t)
    return t if t in PLANS else "free"


def plan(tier: str | None) -> dict:
    return PLANS[normalize_tier(tier)]


def message_cap(tier: str | None) -> tuple[str, int]:
    """Return (period, cap) for messages — period is 'day' or 'month'."""
    m = plan(tier)["messages"]
    return m["period"], int(m["cap"])


def photo_cap(tier: str | None) -> int:
    return int(plan(tier)["photos_per_month"])


def proactive_enabled(tier: str | None) -> bool:
    return bool(plan(tier).get("proactive"))


# Order shown on the pricing page.
PUBLIC_ORDER = ["free", "pro", "ultra"]


def public_plans() -> list[dict]:
    """Plans for the pricing page (excludes enterprise)."""
    out = []
    for key in PUBLIC_ORDER:
        p = PLANS[key]
        out.append({
            "key": key,
            "label": p["label"],
            "tagline": p["tagline"],
            "price": p["price"],
            "price_regular": p["price_regular"],
            "features": p["features"],
        })
    return out
