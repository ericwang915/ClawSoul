---
name: news
description: "Search and summarize news on any topic via web_search or DuckDuckGo. Use when: user asks about recent news, current events, headlines, or wants a news briefing on any subject. NOT for: historical news archives, paywalled articles, or real-time breaking news alerts."
dependencies: duckduckgo-search
metadata:
  emoji: "📰"
---

# News Skill

Search for recent news on any topic using web_search (Tavily) or the bundled DuckDuckGo script.

## When to Use

✅ **USE this skill when:**
- "What's happening in the tech industry today?"
- "Latest AI news"
- "News about the 2026 World Cup"
- "Give me a news briefing on climate"
- "Headlines about [topic]"
- User wants recent or current events on any subject

## When NOT to Use

❌ **DON'T use this skill when:**
- Historical news archives → use specialized archive/search tools
- Paywalled articles → web_search may surface snippets only
- Real-time breaking news alerts → use dedicated news APIs or feeds

## Usage/Commands

### Option A — web_search tool (preferred when Tavily is configured)

```
web_search(query="latest news about <topic>", topic="news", max_results=10)
```

### Option B — Bundled script (works without Tavily)

```bash
python {skill_path}/search_news.py "topic" [--max 10]
```

### Examples

- "What's happening in the tech industry today?" → web_search or `search_news.py "tech industry"`
- "Give me the latest AI news" → web_search or `search_news.py "AI artificial intelligence"`

## Notes

- Tavily (web_search) is preferred when configured in herandhim.json
- search_news.py uses DuckDuckGo and requires no API key
