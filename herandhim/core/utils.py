"""
Shared utilities for herandhim.
"""

from __future__ import annotations


def parse_frontmatter(content: str) -> tuple[dict, str]:
    """
    Parse YAML-style frontmatter delimited by '---' from *content*.

    Returns (metadata_dict, body_string).
    If no frontmatter is found, returns ({}, content).

    Supports:
      - Simple ``key: value`` pairs
      - YAML block scalars (``>``, ``|``) with indented continuation lines
      - Bare multi-line values (indented continuation lines without ``>`` / ``|``)
    """
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    metadata: dict[str, str] = {}
    current_key: str | None = None
    current_lines: list[str] = []
    block_mode: str | None = None  # ">" (folded) or "|" (literal)

    def _flush() -> None:
        if current_key is not None and current_lines:
            text = " ".join(current_lines) if block_mode == ">" else "\n".join(current_lines)
            metadata[current_key] = text.strip()

    for line in parts[1].strip().splitlines():
        stripped = line.strip()

        # Continuation line (starts with whitespace and we have a current key)
        if line and line[0] in (" ", "\t") and current_key is not None:
            current_lines.append(stripped)
            continue

        # New key: value pair
        if ":" in stripped:
            _flush()
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            value = value.strip()

            if value in (">", "|"):
                block_mode = value
                current_lines = []
            elif value:
                block_mode = None
                current_lines = [value]
            else:
                block_mode = None
                current_lines = []

    _flush()

    return metadata, parts[2].strip()
