---
name: system_random
description: "Generate random numbers, UUIDs, passwords, or pick random items from a list. Use when: user needs a random integer/float, UUID, password, or random choice from options. NOT for: cryptographic randomness, shuffling large datasets, or seeded/reproducible randomness."
metadata:
  emoji: "🎲"
---

# System Random Skill

Generate random numbers, UUIDs, passwords, and random picks.

## When to Use

✅ **USE this skill when:**
- "Pick a random number between 1 and 100"
- "Generate a UUID"
- "Create a random password"
- "Pick 2 random items from [apple, banana, cherry]"
- "Random float between 0 and 1"
- User needs any form of randomness

## When NOT to Use

❌ **DON'T use this skill when:**
- Cryptographic-grade randomness → use `secrets` or crypto libraries
- Shuffling large datasets → use shuffle/sample in code
- Reproducible seeded randomness → use random with seed in code

## Usage/Commands

```bash
# Random integer in range
python {skill_path}/random_util.py --int 1 100

# Random float in range
python {skill_path}/random_util.py --float 0.0 1.0

# UUID
python {skill_path}/random_util.py --uuid

# Random password (default 16 chars)
python {skill_path}/random_util.py --password 20

# Pick N random items from a comma-separated list
python {skill_path}/random_util.py --choice "apple,banana,cherry,date" --count 2
```

## Notes

- Uses bundled `random_util.py` CLI for all random generation
- Password length is configurable via the integer argument
