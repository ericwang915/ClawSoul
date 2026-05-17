---
name: model_usage
description: "Track and display LLM token usage, costs, and model statistics. Use when: user asks about token usage, API costs, how many messages were sent, or model performance stats. NOT for: changing the model or provider (use change_setting), or viewing conversation content (use session_logs)."
metadata:
  emoji: "📈"
---

# Model Usage

Track and display LLM token usage, costs, and session statistics.

## When to Use

✅ **USE this skill when:**

- "How many tokens have I used?"
- "What's my API cost so far?"
- "Show me model usage stats"
- "How many messages in this session?"
- "Which model am I using?"

## When NOT to Use

❌ **DON'T use this skill when:**

- Changing the LLM model or provider → use `change_setting`
- Viewing conversation content → use `session_logs`
- Checking system status → check agent status directly

## Usage

### Current session stats

```bash
python {skill_path}/usage_stats.py
```

### Check detailed interaction log

The `history_detail.jsonl` file under `~/.claw_soul/context/logs/` contains
structured records of every agent interaction, including:

- Input messages
- Tool calls and results
- LLM responses
- Timestamps

```bash
python {skill_path}/usage_stats.py --log ~/.claw_soul/context/logs/history_detail.jsonl
```

### Quick stats via jq (if installed)

```bash
# Count total interactions
wc -l ~/.claw_soul/context/logs/history_detail.jsonl

# Recent entries
tail -5 ~/.claw_soul/context/logs/history_detail.jsonl | python -m json.tool
```

## Notes

- Token counts are estimates based on message length
- Cost calculation requires knowing the model's pricing (not tracked automatically)
- The `history_detail.jsonl` is append-only and grows over time
- Use `/status` command in chat for quick session info

## Resources

| File | Description |
|------|-------------|
| `usage_stats.py` | Parse and summarise usage from history logs |
