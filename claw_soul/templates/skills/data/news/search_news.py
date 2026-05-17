#!/usr/bin/env python3
"""Search for recent news using DuckDuckGo (no API key required)."""

import argparse
import json
import sys

try:
    from duckduckgo_search import DDGS
except ImportError:
    print("Error: duckduckgo-search not installed.  Run: pip install duckduckgo-search", file=sys.stderr)
    sys.exit(1)


def search_news(query: str, max_results: int = 10) -> list[dict]:
    with DDGS() as ddgs:
        results = list(ddgs.news(query, max_results=max_results))
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "source": r.get("source", ""),
            "date": r.get("date", ""),
            "body": r.get("body", "")[:300],
        }
        for r in results
    ]


def main():
    parser = argparse.ArgumentParser(description="Search news on any topic.")
    parser.add_argument("query", help="News search query")
    parser.add_argument("--max", type=int, default=10, help="Max results (default: 10)")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    results = search_news(args.query, max_results=args.max)

    if args.format == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not results:
            print("No news found.")
            return
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['title']}")
            if r["source"]:
                print(f"   Source: {r['source']}  Date: {r['date']}")
            if r["url"]:
                print(f"   {r['url']}")
            if r["body"]:
                print(f"   {r['body']}")
            print()


if __name__ == "__main__":
    main()
