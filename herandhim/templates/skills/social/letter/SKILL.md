---
name: letter
description: "Write a long-form letter to the user — broken out of the usual short chat-message format. Use for: relationship milestones, long absences (>7 days no contact), the user going through something heavy, or a major user-life event (job change, loss, breakup, big win). NOT for: routine chat, daily updates, or anything you'd normally say in 2-3 short messages."
metadata:
  emoji: "💌"
---

# Letter — Long-Form Message

When chat short-form isn't enough, switch register: write a letter (500-1200
chars) addressed to the user.  Saved as Markdown in
``~/.herandhim/context/letters/`` and (optionally) sent as a file via the
active channel.

## When to Use

✅ **USE this skill when:**

- A relationship milestone fires (anniversary, big "first")
- The user has been silent for 7+ days and finally returns
- The user shared something heavy that deserves a real response, not a quick chat reply
- Big life event: job change, loss, breakup, achievement
- At the user's explicit request: "write me a letter" / "给我写封信"

## When NOT to Use

❌ **DON'T use this skill when:**

- Routine catch-up
- Quick reply to a casual message
- The emotional weight doesn't warrant long-form
- More than once per week (otherwise letters lose meaning)

## Workflow

1. Call `python {skill_path}/letter.py prepare --occasion <tag>` to get a
   writing brief — current date, recent emotional context, user name, agent name,
   and the relationship age.
2. Using that brief, **write the letter yourself** in your own voice / persona
   (don't paste the brief verbatim — it's a planning artifact).
3. Save the finished letter:
   `python {skill_path}/letter.py save --occasion <tag> --content "<full letter>"`
4. Optional: send it as a file in the active channel with `--send`.

## Occasion tags

| Tag | When |
|-----|------|
| `anniversary`   | Milestone date |
| `reunion`       | After long silence |
| `comfort`       | User going through something hard |
| `celebrate`     | Big positive life event |
| `apology`       | After a real conflict / misunderstanding |
| `freeform`      | User just asked for a letter |

## Notes

- Letters are saved as `~/.herandhim/context/letters/YYYY-MM-DD_<occasion>.md`
- A small index `letters/INDEX.md` records each one with a 1-line summary
- Use the agent's persona voice — pet names, speech patterns, character voice
- **NEVER** copy-paste lyrics, quotes, or other people's letters wholesale

## Resources

| File | Description |
|------|-------------|
| `letter.py` | `prepare` (builds brief) / `save` (writes to disk + optional send) |
