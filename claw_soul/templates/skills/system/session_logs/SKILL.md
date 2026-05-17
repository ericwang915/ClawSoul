---
name: session_logs
description: "Search and analyse your own conversation history from session files. Use when: user references older conversations, asks what was said before, or needs historical context from past sessions. NOT for: current session messages (already in context), non-session files, or external logs."
metadata:
  emoji: "📜"
---

# Session Logs

Search your complete conversation history stored in session Markdown files.

## When to Use

✅ **USE this skill when:**

- "What did we talk about yesterday?"
- "Find the conversation where I asked about X"
- "How many sessions have I had?"
- "What was the decision we made about Y?"
- User references a past conversation not in current context

## When NOT to Use

❌ **DON'T use this skill when:**

- Current session context → already in your message history
- Application error logs → use `read_file` on log files directly
- Non-ClawSoul logs → use `run_command` with grep/rg

## Session File Location

Session files are stored under `~/.claw_soul/context/sessions/`.

Each session is a Markdown file named `<session_id>.md` containing the full
conversation transcript.

## Commands

### List all sessions by date and size

```bash
ls -lhS ~/.claw_soul/context/sessions/*.md
```

### Search across ALL sessions for a keyword

```bash
python {skill_path}/search_sessions.py "keyword"
```

Or with grep:

```bash
grep -rl "keyword" ~/.claw_soul/context/sessions/*.md
```

### Read a specific session

```bash
cat ~/.claw_soul/context/sessions/<session_id>.md
```

### Count sessions

```bash
ls ~/.claw_soul/context/sessions/*.md | wc -l
```

## Notes

- Session files are append-only Markdown
- Large sessions can be several hundred KB — use `head`/`tail` for sampling
- Session IDs follow the pattern `<channel>_<id>` (e.g. `telegram_123456`, `web_default`)
- The `history_detail.jsonl` file contains structured tool-call logs

## Resources

| File | Description |
|------|-------------|
| `search_sessions.py` | Search across all session files by keyword |
