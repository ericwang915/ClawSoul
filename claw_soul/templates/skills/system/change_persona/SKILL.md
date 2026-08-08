---
name: change_persona
description: "Modify the agent's personality and role (persona.md). Use when: user wants to change agent personality, communication style, focus area, or specialization. NOT for: changing user name or core identity (use change_soul), or first-time setup (use onboarding)."
metadata:
  emoji: "🎭"
---

# Change Persona Skill

Modify the agent's personality and role file at `context/persona/persona.md`.

## When to Use

✅ **USE this skill when:**
- "Be more formal"
- "Be funnier" / "Be more casual"
- "Focus on finance now"
- "Change your specialization to research"
- User wants to adjust communication style or personality traits
- User asks to change the agent's expertise area

## When NOT to Use

❌ **DON'T use this skill when:**
- First-time setup with no soul/persona → use onboarding
- Changing user's name or core identity (soul) → use change_soul
- Changing API keys or config → use change_setting

## Usage/Commands

1. Ask the user what they want to change
2. Read the current persona file:
   ```
   read_file("context/persona/persona.md")
   ```
3. Modify the relevant section and write it back:
   ```
   write_file("context/persona/persona.md", "...updated content...")
   ```
4. Tell the user: "Persona updated. Use `/clear` to apply the changes in a fresh conversation, or they will take effect on next restart."

## Notes

- Uses built-in `read_file` and `write_file` tools (no bundled script)
- Preserve the overall structure of persona.md
- Only change the specific section the user asked about
- If the file doesn't exist yet, create it with a reasonable template
