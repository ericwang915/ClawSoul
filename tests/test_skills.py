"""
Tests for the three-tier progressive skill loading system.

Level 1: Metadata discovery (YAML frontmatter)
Level 2: Full instructions (SKILL.md body)
Level 3: Resource discovery (bundled files)
"""

import os
import tempfile

import pytest

from claw_soul.core.skill_loader import (
    SkillMetadata,
    SkillRegistry,
    load_skill_by_name,
    search_skills,
    list_skills_in_category,
)


@pytest.fixture
def skills_dir(tmp_path):
    """Create a temporary skill directory with categorised and flat layouts."""

    # ── Categorised layout: math/calculator ───────────────────────────
    cat_dir = tmp_path / "math"
    cat_dir.mkdir()
    (cat_dir / "CATEGORY.md").write_text(
        "---\nname: math\ndescription: Math skills\n---\n"
    )

    calc_dir = cat_dir / "calculator"
    calc_dir.mkdir()
    (calc_dir / "SKILL.md").write_text(
        "---\n"
        "name: calculator\n"
        "description: Performs arithmetic. Use when the user asks to calculate.\n"
        "---\n"
        "# Calculator\n\n"
        "## Instructions\n"
        "Run `python {skill_path}/calc.py \"expr\"`\n\n"
        "## Resources\n"
        "- `calc.py` — script\n"
    )
    (calc_dir / "calc.py").write_text("print(eval(input()))\n")

    primer_dir = cat_dir / "primer"
    primer_dir.mkdir()
    (primer_dir / "SKILL.md").write_text(
        "---\n"
        "name: math_primer\n"
        "description: Checks if a number is prime.\n"
        "---\n"
        "# Prime Checker\n\nRun `python {skill_path}/prime.py <n>`\n"
    )
    (primer_dir / "prime.py").write_text("pass\n")

    # ── Flat layout: greeter ─────────────────────────────────────────
    greeter_dir = tmp_path / "greeter"
    greeter_dir.mkdir()
    (greeter_dir / "SKILL.md").write_text(
        "---\n"
        "name: greeter\n"
        "description: Greets the user. Use when user says hello.\n"
        "---\n"
        "# Greeter\n\nJust say hello!\n"
    )
    (greeter_dir / "hello.py").write_text("print('hello')\n")
    (greeter_dir / "REFERENCE.md").write_text("# Greeting reference\n")

    return str(tmp_path)


# ── Level 1: Metadata discovery ──────────────────────────────────────────────


class TestLevel1Discovery:
    def test_discovers_categorised_skills(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        metas = registry.discover()
        names = {m.name for m in metas}
        assert "calculator" in names
        assert "math_primer" in names

    def test_discovers_flat_skills(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        metas = registry.discover()
        names = {m.name for m in metas}
        assert "greeter" in names

    def test_category_assigned_correctly(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        metas = {m.name: m for m in registry.discover()}
        assert metas["calculator"].category == "math"
        assert metas["math_primer"].category == "math"
        assert metas["greeter"].category == ""

    def test_descriptions_loaded(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        metas = {m.name: m for m in registry.discover()}
        assert "arithmetic" in metas["calculator"].description.lower()
        assert "prime" in metas["math_primer"].description.lower()

    def test_discovery_is_cached(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        first = registry.discover()
        second = registry.discover()
        assert first is second

    def test_empty_directory(self, tmp_path):
        registry = SkillRegistry([str(tmp_path)])
        assert registry.discover() == []

    def test_missing_directory(self):
        registry = SkillRegistry(["/nonexistent/path"])
        assert registry.discover() == []

    def test_no_duplicate_names(self, skills_dir, tmp_path):
        # Create a second dir with a skill that has the same name
        dup_dir = tmp_path / "dup"
        dup_dir.mkdir()
        calc2 = dup_dir / "calc2"
        calc2.mkdir()
        (calc2 / "SKILL.md").write_text(
            "---\nname: calculator\ndescription: Duplicate.\n---\nBody\n"
        )
        registry = SkillRegistry([skills_dir, str(dup_dir)])
        names = [m.name for m in registry.discover()]
        assert names.count("calculator") == 1

    def test_skill_name_defaults_to_dir_name(self, tmp_path):
        skill_dir = tmp_path / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\ndescription: A skill without a name field.\n---\nBody\n"
        )
        registry = SkillRegistry([str(tmp_path)])
        metas = registry.discover()
        assert metas[0].name == "my_skill"


# ── Level 1: Catalog builder ─────────────────────────────────────────────────


class TestCatalogBuilder:
    def test_catalog_includes_all_skills(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        catalog = registry.build_catalog()
        assert "calculator" in catalog
        assert "math_primer" in catalog
        assert "greeter" in catalog

    def test_catalog_groups_by_category(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        catalog = registry.build_catalog()
        assert "[math]" in catalog

    def test_catalog_empty_shows_message(self, tmp_path):
        registry = SkillRegistry([str(tmp_path)])
        catalog = registry.build_catalog()
        assert "no skills" in catalog.lower()


# ── Level 2: Full instruction loading ────────────────────────────────────────


class TestLevel2Loading:
    def test_load_skill_returns_instructions(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        skill = registry.load_skill("calculator")
        assert skill is not None
        assert "Instructions" in skill.instructions

    def test_skill_path_placeholder_replaced(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        skill = registry.load_skill("calculator")
        assert "{skill_path}" not in skill.instructions
        assert skills_dir in skill.instructions

    def test_load_nonexistent_returns_none(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        assert registry.load_skill("nonexistent") is None

    def test_skill_metadata_accessible(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        skill = registry.load_skill("calculator")
        assert skill.name == "calculator"
        assert skill.description

    def test_load_skill_by_name_convenience(self, skills_dir):
        skill = load_skill_by_name("calculator", skills_dirs=[skills_dir])
        assert skill is not None
        assert skill.name == "calculator"


# ── Level 3: Resource discovery ──────────────────────────────────────────────


class TestLevel3Resources:
    def test_list_resources_excludes_skill_md(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        resources = registry.list_resources("calculator")
        assert "calc.py" in resources
        assert "SKILL.md" not in resources

    def test_list_resources_includes_all_files(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        resources = registry.list_resources("greeter")
        assert "hello.py" in resources
        assert "REFERENCE.md" in resources

    def test_list_resources_nonexistent_skill(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        assert registry.list_resources("nonexistent") == []

    def test_get_resource_path(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        path = registry.get_resource_path("calculator", "calc.py")
        assert path is not None
        assert os.path.isfile(path)

    def test_get_resource_path_missing_file(self, skills_dir):
        registry = SkillRegistry([skills_dir])
        assert registry.get_resource_path("calculator", "missing.py") is None


# ── Backward compatibility helpers ───────────────────────────────────────────


class TestBackwardCompat:
    def test_search_skills(self, skills_dir):
        results = search_skills("prime", skills_dirs=[skills_dir])
        assert len(results) >= 1
        assert results[0]["name"] == "math_primer"

    def test_search_skills_no_match(self, skills_dir):
        results = search_skills("zzzznotfound", skills_dirs=[skills_dir])
        assert results == []

    def test_list_skills_in_category(self, skills_dir):
        results = list_skills_in_category("math", skills_dirs=[skills_dir])
        names = {r["name"] for r in results}
        assert "calculator" in names
        assert "math_primer" in names
        assert "greeter" not in names
