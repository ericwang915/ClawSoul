#!/usr/bin/env python3
"""Translate text between languages using deep-translator."""

import argparse
import json
import sys

try:
    from deep_translator import GoogleTranslator
except ImportError:
    print("Error: deep-translator not installed.  Run: pip install deep-translator",
          file=sys.stderr)
    sys.exit(1)


def translate(text: str, target: str, source: str = "auto") -> dict:
    translator = GoogleTranslator(source=source, target=target)
    result = translator.translate(text)
    return {
        "source_lang": source,
        "target_lang": target,
        "original": text,
        "translated": result,
    }


def list_languages() -> dict:
    return GoogleTranslator().get_supported_languages(as_dict=True)


def main():
    parser = argparse.ArgumentParser(description="Translate text between languages.")
    parser.add_argument("text", nargs="?", help="Text to translate")
    parser.add_argument("--to", dest="target", default="en", help="Target language")
    parser.add_argument("--from", dest="source", default="auto", help="Source language (auto)")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--list-languages", action="store_true",
                        help="List all supported languages")
    args = parser.parse_args()

    if args.list_languages:
        langs = list_languages()
        if args.format == "json":
            print(json.dumps(langs, indent=2, ensure_ascii=False))
        else:
            for name, code in sorted(langs.items()):
                print(f"  {code:10s} {name}")
        return

    if not args.text:
        parser.error("Please provide text to translate.")

    try:
        result = translate(args.text, target=args.target, source=args.source)
    except Exception as exc:
        print(f"Translation error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result['translated']}")


if __name__ == "__main__":
    main()
