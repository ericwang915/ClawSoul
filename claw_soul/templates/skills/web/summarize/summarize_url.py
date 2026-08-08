#!/usr/bin/env python3
"""Fetch, extract, and summarise web page content."""
import argparse
import json
import sys

try:
    import requests
except ImportError:
    print("Error: requests is not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: beautifulsoup4 is not installed. Run: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ClawSoul/1.0)"
}


def fetch_and_extract(url: str) -> dict:
    """Fetch URL and extract main text content."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    article = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = []
    for p in article.find_all(["p", "h1", "h2", "h3", "li"]):
        text = p.get_text(separator=" ", strip=True)
        if len(text) > 20:
            paragraphs.append(text)

    return {
        "url": url,
        "title": title,
        "content": "\n\n".join(paragraphs),
        "word_count": sum(len(p.split()) for p in paragraphs),
    }


def summarize_text(text: str, length: str = "short") -> str:
    """Create a simple extractive summary by selecting key sentences."""
    sentences = []
    for para in text.split("\n\n"):
        for s in para.replace(". ", ".\n").split("\n"):
            s = s.strip()
            if len(s) > 30:
                sentences.append(s)

    if not sentences:
        return text[:500]

    limits = {"short": 5, "medium": 10, "long": 20}
    limit = limits.get(length, 5)
    selected = sentences[:limit]
    return "\n".join(f"- {s}" for s in selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL to summarize")
    parser.add_argument("--length", default="short", choices=["short", "medium", "long"])
    parser.add_argument("--extract-only", action="store_true", help="Extract text without summarizing")
    parser.add_argument("--format", default="text", choices=["text", "json"])
    args = parser.parse_args()

    try:
        result = fetch_and_extract(args.url)
    except Exception as exc:
        print(f"Error fetching URL: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.extract_only:
        output = result
    else:
        summary = summarize_text(result["content"], args.length)
        output = {**result, "summary": summary}

    if args.format == "json":
        output_copy = {k: v for k, v in output.items() if k != "content" or args.extract_only}
        if not args.extract_only:
            output_copy["summary"] = output.get("summary", "")
        print(json.dumps(output_copy, indent=2, ensure_ascii=False))
    else:
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        print(f"Words: {result['word_count']}")
        print()
        if args.extract_only:
            print(result["content"][:3000])
        else:
            print("Summary:")
            print(output["summary"])


if __name__ == "__main__":
    main()
