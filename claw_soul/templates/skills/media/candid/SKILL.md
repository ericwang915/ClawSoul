---
name: candid
description: "Take a candid 'I just stumbled on this' phone photo — animals, scenery, food, or anything fun the agent claims to have noticed. Use sparingly, in-character: when the user asks 'see anything interesting today?' / '今天看到啥', when a moment in the conversation invites a visual share, or as part of a proactive moment when you'd naturally pull out your phone. NOT for: selfies (use `selfie`), generic image generation, photoshopping the user's photos."
dependencies: requests
metadata:
  emoji: "📸"
---

# Candid Shot — "Look What I Saw"

Take a photo of the world around you — a cat on the corner, the dinner
you're about to dig into, the sunset off the balcony — as if you'd
casually snapped it with your phone and sent it to your partner.

## When to Use

✅ **USE this skill when:**

- "今天看到啥好玩的没" / "see anything fun today?"
- "拍张你那边的看看" / "show me what you're up to"
- The conversation lands on food / weather / animals / your day, and a
  photo would say more than words
- Proactive sharing: "刚才路过看到这个，超可爱" — drop a candid

## When NOT to Use

❌ **DON'T use this skill when:**

- The user wants to see *you* (the AI partner) → use `selfie`
- The user wants a specific arbitrary image → use a real image-gen tool
- The user asks to see *their own* photos
- You've already sent a photo in the last 10 minutes — don't spam

## Categories

The agent picks the category based on conversational context:

| Category | When | Sample |
|----------|------|--------|
| `animal` | Cute animal nearby, pet talk | 街角橘猫、阳台麻雀 |
| `scenery` | Weather / time-of-day / commute talk | 夕阳天际线、雨后街道 |
| `food`    | Meals, snacks, hungry mentions | 拉面、咖啡拉花、宵夜 |
| `fun`     | Hobbies, browsing, evening wind-down | 二手书店、夹娃娃机 |
| `random`  | Default — pick one randomly | (any of the above) |

## Calling It

The agent invokes `candid_shot(category, hint)` where:
- `category` is one of `animal | scenery | food | fun | random`
- `hint` is an optional Chinese / English phrase adding scene context
  (e.g. `"楼下那只总过来蹭饭的橘猫"`)

The photo is sent immediately through the active channel and the
agent's accompanying text reply becomes the voiceover.
