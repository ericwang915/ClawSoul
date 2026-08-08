---
name: translator
description: >
  Translate text between 100+ languages with automatic source detection.
  Use when: the user asks to translate text, detect a language, or work with
  multilingual content. NOT for: translating code/documentation in bulk,
  summarization, or rewriting text in the same language.
dependencies: deep-translator
metadata:
  emoji: "🔤"
---
# Translator

## When to Use

- [ ] Translate text to or from any supported language
- [ ] Detect the source language of text
- [ ] Work with multilingual content (e.g., "how do you say X in Y?")
- [ ] Translate phrases, sentences, or paragraphs

## When NOT to Use

- [ ] Bulk translation of code or large docs — consider dedicated tools
- [ ] Summarization or paraphrasing
- [ ] Rewriting text in the same language
- [ ] When the user explicitly wants a different translation service

## Setup

Install dependency: `pip install deep-translator`

## Usage/Commands

```bash
python {skill_path}/translate.py "text to translate" --to TARGET_LANG [options]
```

| Option | Description |
|--------|-------------|
| `--to LANG` | Target language (required): code or name, e.g. `zh-CN`, `french`, `ja` |
| `--from LANG` | Source language (default: `auto`) |
| `--format json` | Output as JSON |

**Common codes:** `en`, `zh-CN`, `zh-TW`, `ja`, `ko`, `fr`, `de`, `es`, `pt`, `ru`, `ar`, `hi`, `it`

Full names also work: `chinese (simplified)`, `japanese`, `french`, etc.

## Notes

- Source language is auto-detected when `--from` is omitted
- Supports 100+ languages via `deep-translator`
- For programmatic use, `--format json` returns structured output
