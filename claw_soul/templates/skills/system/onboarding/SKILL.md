---
name: onboarding
description: "First-time setup wizard that collects user name, agent personality, focus area, and language preference, then writes soul.md and persona.md. Use when: agent starts for the first time with no soul/persona configured, or user says reconfigure, setup, change my agent. NOT for: incremental edits to persona or soul (use change_persona or change_soul instead)."
metadata:
  emoji: "👋"
---

# Onboarding Skill

First-time setup wizard that guides users through configuring their agent identity.

## When to Use

✅ **USE this skill when:**
- Agent starts with empty/default soul.md and persona.md
- User says "reconfigure", "setup", "change my agent", "first-time setup"
- User wants a guided flow to set name, personality, focus, and language in one go

## When NOT to Use

❌ **DON'T use this skill when:**
- User wants to tweak one aspect of persona (e.g., "be more formal") → use change_persona
- User wants to change soul only (e.g., "call me Alex") → use change_soul
- Agent already has configured soul and persona and user just wants small edits

## Usage/Commands

**Onboarding flow** — Ask these questions **one at a time** in a friendly, conversational tone:

1. **Name**: "What should I call you?"
2. **Personality**: "What kind of personality would you like me to have? (e.g. professional & concise, friendly & casual, humorous, formal, encouraging)"
3. **Focus area**: "What area would you like me to focus on? (e.g. software development, finance, research, daily assistant, creative writing)"
4. **Language preference**: "What language do you prefer I respond in? (English, Chinese, etc.)"

**Write soul.md:**
```bash
python {skill_path}/write_identity.py --type soul \
  --user-name "NAME" \
  --personality "PERSONALITY" \
  --focus "FOCUS" \
  --language "LANGUAGE"
```

**Write persona.md:**
```bash
python {skill_path}/write_identity.py --type persona \
  --user-name "NAME" \
  --personality "PERSONALITY" \
  --focus "FOCUS" \
  --language "LANGUAGE"
```

After writing, tell the user: "Setup complete! Your preferences have been saved. Use `/clear` to start a fresh conversation with your new identity, or just keep chatting."

## Notes

- Uses bundled `write_identity.py` to generate soul.md and persona.md
- Files are written to `context/soul/SOUL.md` and `context/persona/persona.md`
