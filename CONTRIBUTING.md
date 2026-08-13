# Contributing to HerAndHim

Thank you for your interest in contributing to HerAndHim! We welcome contributions of all kinds — bug fixes, new features, documentation improvements, and more.

## Getting Started

```bash
git clone https://github.com/ericwang915/HerAndHim.git
cd HerAndHim
python -m venv .venv && source .venv/bin/activate
pip install -e .
pytest tests/ -v
```

## How to Contribute

### Reporting Bugs
- Search [existing issues](https://github.com/ericwang915/HerAndHim/issues) first.
- If not found, open a new issue with a clear title, description, and reproduction steps.

### Pull Requests
1. Fork the repo and create your branch from `main`.
2. If you've added code, add tests.
3. Ensure the test suite passes: `pytest tests/ -v`
4. Make sure your code follows the style guide.
5. Submit the pull request!

## Code Style

- Follow PEP 8 standards.
- Use [Ruff](https://github.com/astral-sh/ruff) for linting: `ruff check herandhim/`
- Use meaningful variable names.
- Avoid redundant comments — code should be self-documenting.

## Adding New Skills

1. Create a directory under `herandhim/templates/skills/<category>/<skill_name>/`.
2. Add a `SKILL.md` with clear instructions for the agent.
3. Add any supporting scripts.
4. Submit a PR.

## Adding New LLM Providers

1. Create a new file in `herandhim/core/llm/`.
2. Implement the `LLMProvider` interface from `base.py`.
3. Add the provider option to `main.py:_build_provider()`.
4. Add tests and documentation.

## Adding New Channels

1. Create a new file in `herandhim/channels/`.
2. Use `SessionManager` for agent lifecycle management and concurrency control.
3. Follow the `telegram_bot.py` pattern (use `sm.acquire(sid)` for async handlers).
4. Wire the channel into `server.py`, `main.py`, and `onboard.py`.

Thanks for helping make HerAndHim better!
