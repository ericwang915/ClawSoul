---
name: tavily_search
description: >
  Advanced web search using the Tavily API via built-in web_search tool. Use when:
  the user asks to search the web, look up current events, find real-time info,
  research a topic, check facts, or retrieve data from the internet. NOT for:
  scraping specific page content (use web_scraper), calling APIs (use http_request).
dependencies: tavily-python
metadata:
  emoji: "🔍"
---
# Tavily Web Search

## When to Use

- [ ] Search the web for information
- [ ] Look up current events or news
- [ ] Research a topic with real-time results
- [ ] Check facts or verify claims
- [ ] Find data, articles, or summaries from the internet

## When NOT to Use

- [ ] Scraping a specific URL's content — use `web_scraper`
- [ ] Calling a known API endpoint — use `http_request`
- [ ] GitHub or code-specific lookups — use `github` or search

## Setup

1. Get a free API key at [app.tavily.com](https://app.tavily.com/home)
2. Add to `claw_soul.json`:
   ```json
   "tavily": { "apiKey": "tvly-..." }
   ```
3. Install SDK: `pip install tavily-python`

## Usage/Commands

Use the built-in `web_search` tool — no script required.

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `query` | string | Search query (be specific) | **required** |
| `search_depth` | string | `"basic"` (fast) or `"advanced"` (thorough) | `"basic"` |
| `topic` | string | `"general"`, `"news"`, or `"finance"` | `"general"` |
| `max_results` | integer | Results count (1–20) | 5 |
| `time_range` | string | `"day"`, `"week"`, `"month"`, `"year"` | none |

**Examples:**

```
web_search(query="Python 3.13 new features")
web_search(query="AI industry news", topic="news", time_range="week", max_results=10)
web_search(query="NVIDIA stock analysis 2026", topic="finance", search_depth="advanced")
```

## Notes

- Be specific in queries (e.g., "Python asyncio best practices 2026" vs "Python async")
- Use `topic="news"` for current events, `topic="finance"` for market data
- Use `time_range` when freshness matters
- Always cite source URLs in your response
