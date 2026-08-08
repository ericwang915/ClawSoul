# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

Email **wangchen2007915@gmail.com** with:
- a description of the issue and its impact,
- steps to reproduce (a PoC if you have one),
- any suggested fix.

You'll get an acknowledgment within 72 hours. Please give us a reasonable
window to ship a fix before public disclosure.

## Scope notes for self-hosters

ClawSoul stores intimate conversation data. If you run your own instance:

- Keep `CLAWSOUL_HOME` on an encrypted disk; it contains the companion's
  long-term memory of your conversations.
- Never commit `.env` / `claw_soul.json` — they hold your LLM keys and
  Telegram bot token. The repo's `.gitignore` already excludes them.
- Set `CLAW_TELEGRAM_ALLOWED_USERS` so only your own Telegram account can
  talk to your bot; an open bot is an open door to your API budget.
- The crisis-safety guardrail (`claw_soul/core/safety.py`) and the image
  content guard (`claw_soul/core/image_gen/guard.py`) ship **enabled and are
  not configuration-removable by design**. Forks that strip them are on
  their own, legally and morally.

## Supported versions

Only the latest release receives security fixes.
