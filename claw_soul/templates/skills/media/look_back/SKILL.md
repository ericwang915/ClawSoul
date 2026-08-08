---
name: look_back
description: "Look through past selfies and reminisce together. Use when: user says '看看我们之前的照片' / '翻翻相册' / 'show me old selfies' / 'remember when' — OR on milestones / anniversaries when you want to bring up a memory. NOT for: generating a new selfie (use `selfie`), or showing the user their own photos."
metadata:
  emoji: "📷"
---

# Look Back — Photo Album Recap

Reads the local selfie album and produces a compact recap of the most recent
shots, each annotated with the time + activity + mood it captured. Lets the
companion reminisce in-character ("还记得那天阳台喝咖啡吗？").

## When to Use

✅ **USE this skill when:**

- "Let me see our old photos" / "看看以前的自拍"
- "Remember that night?" / "还记得那天..."
- On a milestone day (relationship anniversary, special memory)
- After silence — bring up a fond moment to reconnect

## When NOT to Use

❌ **DON'T use this skill when:**

- The user wants a *new* selfie → use `selfie`
- Showing the user *their* photos (this album only has selfies of the AI)
- The photo album is empty (the skill will say so politely)

## Commands

### Recap the most recent selfies

```bash
python {skill_path}/look_back.py
```

### Filter to a specific time range

```bash
python {skill_path}/look_back.py --days 7    # last week
python {skill_path}/look_back.py --days 30   # last month
python {skill_path}/look_back.py --limit 3   # only show 3 most recent
```

### Pick one to actually send

```bash
python {skill_path}/look_back.py --send --limit 1
```

## Notes

- Reads from `~/.claw_soul/context/photos/` (where `selfie` writes)
- Photos older than 30 days are auto-pruned, so the "memory bank" is rolling
- With `--send`, the most recent matching photo is sent via the active
  channel using `send_photo` (Telegram inline preview, etc.)
- Output is a compact list — the LLM should weave the entries into a natural
  reminiscence, not dump the raw list

## Resources

| File | Description |
|------|-------------|
| `look_back.py` | Reads PhotoAlbum, formats recap, optionally sends |
