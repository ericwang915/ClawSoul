---
name: horoscope
description: "Today's horoscope / divination reading, picked according to YOUR (the agent's) cultural context — Chinese 黄历 + 生肖, Western zodiac, Japanese 占い, or Indian rashifal. Use when: user asks '今天运势如何' / 'what's my horoscope' / morning messaging when the agent's culture is one that pays attention to daily fortune. NOT for: actual astrology consultations, life-decision predictions, or anything the user expects to be 'real'."
metadata:
  emoji: "🔮"
---

# Horoscope — Culture-Aware Daily Reading

Returns a short daily reading framed for the agent's cultural context.  The
voice and structure differ by culture:

| Culture (`--culture`) | Reading style |
|---|---|
| `cn` (default for CN-coded personas) | 黄历宜/忌 + 幸运色 / 幸运数字 + 生肖小贴士 |
| `en` | Western zodiac sun-sign one-paragraph |
| `jp` | 今日の星座占い (rank 1-12 + lucky item / color) |
| `in` | Rashifal sun-sign one-paragraph, with planetary tint |

## When to Use

✅ **USE this skill when:**

- "今天运势怎么样？" / "What's my horoscope today?"
- As a soft hook in a morning proactive ("看了下今天黄历...")
- The conversation drifts into superstition / what-the-stars-say territory

## When NOT to Use

❌ **DON'T use this skill when:**

- The user wants a *serious* astrology / birth-chart reading
- Important decisions hang on the output — disclaim if needed
- The agent's culture doesn't care about this stuff (set `culture` accordingly)

## Commands

### Today's reading for the agent's default culture

```bash
python {skill_path}/horoscope.py
```

### Override the culture for one call

```bash
python {skill_path}/horoscope.py --culture cn
python {skill_path}/horoscope.py --culture en --sign aries
python {skill_path}/horoscope.py --culture jp --sign 牡羊座
python {skill_path}/horoscope.py --culture in --sign mesha
```

### For the user (so you can read them theirs)

```bash
python {skill_path}/horoscope.py --culture en --sign libra
```

## Resolving the agent's culture

Resolution order:

1. `--culture` CLI flag (when supplied by the agent)
2. `agent.culture` in `claw_soul.json`
3. `memory["agent_culture"]` (the agent can set this via `remember`)
4. Default → `cn`

The matching sign (if not supplied):

- `cn`: today's 生肖 cycles (or year-animal from agent's "age" field if known)
- `en` / `in` / `jp`: agent's persona may have its own birth-date in
  ``persona.md`` / ``profile.md`` — fall back to the date sign for today

## Notes

- Output is deterministic per (date, culture, sign) — same hour same line
- Reading is *flavor*, not prophecy. Persona should treat it accordingly.
- 中国黄历 entries are hand-curated; not algorithmically computed from real astronomy

## Resources

| File | Description |
|------|-------------|
| `horoscope.py` | Culture-routed reading; small per-culture pool |
