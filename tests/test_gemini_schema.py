"""Verify Gemini tool-schema sanitizer strips fields that the Schema proto rejects."""

from __future__ import annotations

from claw_soul.core.llm.gemini_client import _sanitize_schema


def test_strips_default_at_property_level():
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "description": "count", "default": 5},
        },
        "required": ["n"],
    }
    out = _sanitize_schema(schema)
    assert "default" not in out["properties"]["n"]
    assert out["properties"]["n"] == {"type": "integer", "description": "count"}
    assert out["required"] == ["n"]


def test_strips_examples_and_format():
    schema = {
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email", "examples": ["a@b.com"]},
        },
    }
    out = _sanitize_schema(schema)
    assert out["properties"]["email"] == {"type": "string"}


def test_strips_additionalProperties_and_dollar_schema():
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "additionalProperties": False,
        "properties": {"x": {"type": "string"}},
    }
    out = _sanitize_schema(schema)
    assert "$schema" not in out
    assert "additionalProperties" not in out
    assert out["properties"]["x"] == {"type": "string"}


def test_preserves_nested_items_array():
    schema = {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string", "default": "x"},
            },
        },
    }
    out = _sanitize_schema(schema)
    assert out["properties"]["tags"]["items"] == {"type": "string"}


def test_preserves_enum():
    schema = {
        "type": "string",
        "enum": ["a", "b", "c"],
        "default": "a",
    }
    out = _sanitize_schema(schema)
    assert out == {"type": "string", "enum": ["a", "b", "c"]}


def test_none_passthrough():
    assert _sanitize_schema(None) is None
    assert _sanitize_schema("scalar") == "scalar"
    assert _sanitize_schema(42) == 42
