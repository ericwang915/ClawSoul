---
name: change_soul
description: "Modify the agent's core identity (soul.md). Use when: user wants to change their name, core values, language preference, or fundamental agent behavior. NOT for: changing personality/style (use change_persona), first-time setup (use onboarding), or API/config (use change_setting)."
metadata:
  emoji: "💫"
---

# Change Soul Skill

Modify the agent's core identity file at `context/soul/SOUL.md`.

## When to Use

✅ **USE this skill when:**
- "Change my name to ..." / "Call me ..."
- "Change language to Chinese"
- User wants to modify core values or ethical boundaries
- User asks to update fundamental agent behavior
- User wants to change how the agent addresses them or core identity settings

## When NOT to Use

❌ **DON'T use this skill when:**
- First-time setup with no soul/persona → use onboarding
- Changing personality, tone, or specialization → use change_persona
- Changing API keys or config → use change_setting

## Usage/Commands

1. Ask the user what they want to change
2. Read the current soul file:
   ```
   read_file("context/soul/SOUL.md")
   ```
3. Modify the relevant section and write it back:
   ```
   write_file("context/soul/SOUL.md", "...updated content...")
   ```
4. Tell the user: "Soul updated. Use `/clear` to apply the changes in a fresh conversation, or they will take effect on next restart."

## Notes

- Uses built-in `read_file` and `write_file` tools (no bundled script)
- Preserve the overall structure of SOUL.md
- Only change the specific section the user asked about
- Keep core ethical boundaries intact — never remove safety guidelines
