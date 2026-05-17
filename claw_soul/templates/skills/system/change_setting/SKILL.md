---
name: change_setting
description: "Modify claw_soul.json configuration at runtime. Use when: user wants to set API keys, tokens, change LLM provider, adjust web port, or update any config value. NOT for: editing code, changing soul/persona files, or modifying skill files."
metadata:
  emoji: "⚙️"
---

# Change Setting Skill

Modify `claw_soul.json` configuration at runtime.

## When to Use

✅ **USE this skill when:**
- "Set my API key to ..."
- "Change LLM provider to Claude"
- "Update my Tavily/Telegram/GitHub token"
- "Change the web port to 8080"
- "Configure email credentials"
- User wants to change any runtime setting without editing files manually

## When NOT to Use

❌ **DON'T use this skill when:**
- Changing agent personality or identity → use change_persona or change_soul
- Editing Python code or skill files → use file edit tools
- Adding/removing skills → use skill management or manual file edits

## Usage/Commands

**Show current config:**
```bash
python {skill_path}/update_config.py --show
```

**Update a value** (dot-notation for key path):
```bash
python {skill_path}/update_config.py --set "llm.deepseek.apiKey" "sk-xxx"
```

**Examples:**
```bash
# Change LLM provider
python {skill_path}/update_config.py --set "llm.provider" "claude"

# Set Tavily API key
python {skill_path}/update_config.py --set "tavily.apiKey" "tvly-xxx"

# Set Telegram token
python {skill_path}/update_config.py --set "channels.telegram.token" "123:ABC"

# Set GitHub token
python {skill_path}/update_config.py --set "skills.github.token" "ghp_xxx"

# Change web port
python {skill_path}/update_config.py --set "web.port" "8080"

# Set email credentials
python {skill_path}/update_config.py --set "skills.email.senderEmail" "me@gmail.com"
python {skill_path}/update_config.py --set "skills.email.senderPassword" "app-password"
```

After updating, tell the user the change was saved and whether a restart is needed to take effect.

## Notes

- Uses bundled `update_config.py` to read and update claw_soul.json
- API keys, passwords, and tokens are masked when displaying config
- Only `claw_soul.json` is modified — no other files
