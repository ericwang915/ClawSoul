"""
Skill discovery and loading for HerAndHim.

Three-tier progressive disclosure
----------------------------------
Inspired by Claude's Agent Skills architecture, skills are loaded on demand
in three tiers to minimise context-window usage:

  Level 1 — Metadata  (always loaded at startup, ~100 tokens per skill)
      YAML frontmatter from every SKILL.md (``name`` + ``description``).
      Injected into the system prompt as a "skill catalog" so the LLM
      knows what capabilities exist and when to activate them.

  Level 2 — Core instructions  (loaded when the LLM triggers ``use_skill``)
      The body of SKILL.md: workflows, rules, step-by-step guidance.

  Level 3 — Extended resources  (loaded as needed via ``read_file`` / ``run_command``)
      Scripts, schemas, reference docs, templates, CSV data — anything
      bundled in the skill folder that SKILL.md references.

SKILL.md format  (Claude-compatible)
--------------------------------------
    ---
    name: calculator
    description: >
      Performs basic arithmetic.  Use when the user asks to calculate
      math expressions, additions, multiplications, etc.
    ---
    # Calculator

    ## Instructions
    Run `python {skill_path}/calc.py "expression"` ...

    ## Resources
    - `calc.py` — arithmetic script

Directory layout (both flat and categorised)
----------------------------------------------
    <skills_dir>/
        <skill_name>/              — flat (Claude-style)
            SKILL.md
            *.py / *.sh

    <skills_dir>/
        <category>/                — categorised (HerAndHim-style)
            CATEGORY.md            — optional category description
            <skill_name>/
                SKILL.md
                *.py / *.sh
"""

from __future__ import annotations

import logging
import os

from .utils import parse_frontmatter

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────────

class CategoryMetadata:
    """Parsed CATEGORY.md frontmatter."""

    __slots__ = ("name", "description", "emoji")

    def __init__(self, name: str, description: str, emoji: str = "") -> None:
        self.name = name
        self.description = description
        self.emoji = emoji


class SkillMetadata:
    """Level 1 — lightweight metadata for a single skill."""

    __slots__ = ("name", "description", "path", "category", "emoji", "dependencies")

    def __init__(
        self,
        name: str,
        description: str,
        path: str,
        category: str = "",
        emoji: str = "",
        dependencies: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.path = path
        self.category = category
        self.emoji = emoji
        self.dependencies: list[str] = dependencies or []


class Skill:
    """Level 2 — a fully loaded skill including its instruction text."""

    def __init__(self, metadata: SkillMetadata, instructions: str) -> None:
        self.metadata = metadata
        self.instructions = instructions

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


# ── Registry ─────────────────────────────────────────────────────────────────

class SkillRegistry:
    """
    Scans skill directories and provides three-tier progressive loading.

    Supports both flat layouts (``skills/calculator/SKILL.md``) and
    categorised layouts (``skills/math/calculator/SKILL.md``).
    """

    def __init__(self, skills_dirs: list[str] | None = None) -> None:
        self.skills_dirs: list[str] = list(skills_dirs) if skills_dirs else []
        self._cache: list[SkillMetadata] | None = None
        self._categories: dict[str, CategoryMetadata] = {}

    def invalidate(self) -> None:
        """Clear the discovery cache so new skills are picked up on next call."""
        self._cache = None
        self._categories = {}

    @property
    def categories(self) -> dict[str, CategoryMetadata]:
        """Return discovered category metadata (call discover() first)."""
        if self._cache is None:
            self.discover()
        return self._categories

    # ── Level 1: Metadata discovery ──────────────────────────────────────

    def discover(self) -> list[SkillMetadata]:
        """
        Scan all configured directories and return metadata for every skill.

        Results are cached after the first call.  This is the **Level 1**
        operation — only YAML frontmatter is read (name + description).
        """
        if self._cache is not None:
            return self._cache

        skills: list[SkillMetadata] = []
        seen_names: set[str] = set()

        for s_dir in self.skills_dirs:
            if not os.path.isdir(s_dir):
                continue
            self._scan_dir(s_dir, skills, seen_names)

        self._cache = skills
        return skills

    def _scan_dir(
        self,
        base_dir: str,
        out: list[SkillMetadata],
        seen: set[str],
    ) -> None:
        """Recursively find SKILL.md files up to 2 levels deep."""
        for entry in sorted(os.listdir(base_dir)):
            if entry.startswith(("__", ".")):
                continue
            entry_path = os.path.join(base_dir, entry)
            if not os.path.isdir(entry_path):
                continue

            skill_md = os.path.join(entry_path, "SKILL.md")
            if os.path.isfile(skill_md):
                # Flat layout: skills/<skill>/SKILL.md
                meta = self._read_metadata(skill_md, entry_path, category="")
                if meta and meta.name not in seen:
                    out.append(meta)
                    seen.add(meta.name)
            else:
                # Categorised layout: skills/<category>/<skill>/SKILL.md
                category_name = entry
                cat_md = os.path.join(entry_path, "CATEGORY.md")
                if os.path.isfile(cat_md) and category_name not in self._categories:
                    cat_meta = self._read_category(cat_md, category_name)
                    if cat_meta:
                        self._categories[category_name] = cat_meta

                for sub_entry in sorted(os.listdir(entry_path)):
                    if sub_entry.startswith(("__", ".")):
                        continue
                    sub_path = os.path.join(entry_path, sub_entry)
                    sub_md = os.path.join(sub_path, "SKILL.md")
                    if os.path.isdir(sub_path) and os.path.isfile(sub_md):
                        meta = self._read_metadata(
                            sub_md, sub_path, category=category_name
                        )
                        if meta and meta.name not in seen:
                            out.append(meta)
                            seen.add(meta.name)

    @staticmethod
    def _read_category(cat_path: str, fallback_name: str) -> CategoryMetadata | None:
        try:
            with open(cat_path, "r", encoding="utf-8") as f:
                content = f.read()
            meta, _ = parse_frontmatter(content)
            return CategoryMetadata(
                name=meta.get("name", fallback_name),
                description=meta.get("description", ""),
                emoji=meta.get("emoji", "").strip("\"'"),
            )
        except OSError:
            return None

    @staticmethod
    def _parse_deps(raw: str) -> list[str]:
        """Parse a comma-separated or YAML-ish dependency string.

        Handles ``requests, beautifulsoup4`` and ``[requests, bs4]``.
        """
        raw = raw.strip().strip("[]")
        return [d.strip().strip("\"'") for d in raw.split(",") if d.strip()]

    @staticmethod
    def _parse_metadata_block(raw: str) -> dict[str, str]:
        """Parse an indented ``key: value`` block stored as a flat string."""
        result: dict[str, str] = {}
        for line in raw.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                result[k.strip()] = v.strip().strip("\"'")
        return result

    @staticmethod
    def _read_metadata(
        md_path: str,
        skill_dir: str,
        category: str,
    ) -> SkillMetadata | None:
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            meta, _ = parse_frontmatter(content)
            name = meta.get("name", os.path.basename(skill_dir))
            description = meta.get("description", "No description.")

            emoji = ""
            metadata_block = meta.get("metadata", "")
            if isinstance(metadata_block, str) and metadata_block:
                parsed = SkillRegistry._parse_metadata_block(metadata_block)
                emoji = parsed.get("emoji", "")
            elif isinstance(metadata_block, dict):
                emoji = metadata_block.get("emoji", "")

            deps: list[str] = []
            raw_deps = meta.get("dependencies", "")
            if raw_deps:
                deps = SkillRegistry._parse_deps(raw_deps)

            return SkillMetadata(
                name=name,
                description=description,
                path=os.path.abspath(skill_dir),
                category=category,
                emoji=emoji,
                dependencies=deps,
            )
        except OSError as exc:
            logger.warning("Could not read skill at '%s': %s", md_path, exc)
            return None

    # ── Level 2: Full instruction loading ────────────────────────────────

    def load_skill(self, name: str) -> Skill | None:
        """
        Load the full SKILL.md body for a skill by name (**Level 2**).

        The ``{skill_path}`` placeholder in the instruction text is
        replaced with the skill's absolute directory path.
        """
        for meta in self.discover():
            if meta.name != name:
                continue
            md_path = os.path.join(meta.path, "SKILL.md")
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    content = f.read()
                _, instructions = parse_frontmatter(content)
                instructions = instructions.replace("{skill_path}", meta.path)
                return Skill(metadata=meta, instructions=instructions)
            except OSError as exc:
                logger.error("Error loading skill '%s': %s", name, exc)
                return None
        return None

    # ── Level 3: Resource discovery ──────────────────────────────────────

    def list_resources(self, name: str) -> list[str]:
        """
        List bundled resource files for a skill (**Level 3** discovery).

        Returns relative filenames (e.g. ``["calc.py", "REFERENCE.md"]``)
        excluding SKILL.md itself.
        """
        for meta in self.discover():
            if meta.name != name:
                continue
            try:
                return sorted(
                    f
                    for f in os.listdir(meta.path)
                    if f != "SKILL.md"
                    and not f.startswith(("__", "."))
                    and os.path.isfile(os.path.join(meta.path, f))
                )
            except OSError:
                return []
        return []

    def get_resource_path(self, skill_name: str, resource: str) -> str | None:
        """Return the absolute path to a resource file inside a skill folder."""
        for meta in self.discover():
            if meta.name != skill_name:
                continue
            full = os.path.join(meta.path, resource)
            if os.path.isfile(full):
                return full
            return None
        return None

    # ── Catalog builder (for system prompt injection) ────────────────────

    def build_catalog(self) -> str:
        """Build a compact skill catalog for the system prompt.

        Uses a terse format to minimize token usage while preserving
        discoverability. Emojis are omitted in the prompt version.
        """
        skills = self.discover()
        if not skills:
            return "(no skills installed)"

        groups: dict[str, list[SkillMetadata]] = {}
        for s in skills:
            groups.setdefault(s.category or "general", []).append(s)

        lines: list[str] = []
        for cat in sorted(groups):
            cat_label = cat
            if cat != "general":
                cat_meta = self._categories.get(cat)
                if cat_meta and cat_meta.name:
                    cat_label = cat_meta.name
            lines.append(f"[{cat_label}]")
            names = [
                f"{s.name}: {s.description[:60]}"
                for s in groups[cat]
            ]
            lines.append(", ".join(names))

        return "\n".join(lines)


# ── Module-level convenience functions ───────────────────────────────────────

def load_skill_by_name(
    skill_name: str,
    skills_dirs: list[str] | None = None,
) -> Skill | None:
    """Load a skill by name (Level 2)."""
    return SkillRegistry(skills_dirs).load_skill(skill_name)


def search_skills(
    query: str,
    skills_dirs: list[str] | None = None,
) -> list[dict]:
    """Search skills by keyword match in name or description."""
    q = query.lower()
    return [
        {"name": s.name, "description": s.description, "category": s.category}
        for s in SkillRegistry(skills_dirs).discover()
        if q in s.name.lower() or q in s.description.lower()
    ]


def list_skills_in_category(
    category: str,
    skills_dirs: list[str] | None = None,
) -> list[dict]:
    """List skills in a specific category (backward compat)."""
    return [
        {
            "name": s.name,
            "description": s.description,
            "path_name": os.path.basename(s.path),
        }
        for s in SkillRegistry(skills_dirs).discover()
        if s.category == category
    ]
