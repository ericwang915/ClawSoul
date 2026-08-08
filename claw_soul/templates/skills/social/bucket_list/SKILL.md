---
name: bucket_list
description: "Record and recall shared 'WE' aspirations — places to visit, foods to try, milestones to reach together. Distinct from `wishlist_add` (user's individual wants). Use bucket_list when the user expresses a long-term, couple-flavoured aspiration. The tools `bucket_add` / `bucket_mark_done` / `bucket_list` are always available — this skill is mainly documentation."
metadata:
  emoji: "✨"
---

# Couple Bucket List

A durable list of things *we* want to do together. Tracked as a relationship
artifact — different in tone and timescale from individual wishes.

## When to record an entry

The user says (or implies) any of:

- "Someday we should…" / "改天我们…" / "以后一起去…"
- "I wish we could…" / "好想跟你一起…"
- "We have to try…" / "下次一定要…"
- "One day…" / "以后吧…"

Phrase the entry in **second-person plural** — "we go to Hokkaido in winter",
not "the user wants to visit Hokkaido". This is the *relationship's* list.

## Categories

| Category | Examples |
|----------|----------|
| travel       | 去北海道看雪 / road trip the Pacific Coast Highway |
| food         | omakase at that place downtown / 一起做寿喜烧 |
| experience   | watch a meteor shower / hot-air balloon ride |
| milestone    | adopt a cat / move in together / "meet in person" |
| general      | anything else couple-flavoured |

## Tools (always available — call directly, no `use_skill` needed)

| Tool | Purpose |
|------|---------|
| `bucket_add(text, category, note?)` | Add a new shared aspiration |
| `bucket_mark_done(item_id, note?)` | Mark when the couple actually does it (feeds milestone celebration) |
| `bucket_list(category?)` | List currently pending entries |

## Notes

- Entries are durable — they are **not** pruned by age (unlike wishlist)
- Storage: `~/.claw_soul/context/bucket_list.json`
- When the user reports a bucket item completed, mark it done **and** consider
  generating a small celebration — this is a relationship milestone
