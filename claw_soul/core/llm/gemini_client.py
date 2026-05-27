"""
Google Gemini provider — adapts the Gemini API to the OpenAI-compatible
response format used by Agent.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

import google.generativeai as genai
from google.generativeai.types import content_types

from .base import LLMProvider
from .response import MockChoice, MockFunction, MockMessage, MockResponse, MockToolCall


# Fields Gemini's Schema proto accepts on function-tool parameters.
# Anything else (default, examples, additionalProperties, $schema,
# format, const, oneOf, anyOf, allOf, ...) gets stripped.
_GEMINI_SCHEMA_FIELDS = {
    "type", "description", "properties", "required",
    "items", "enum", "nullable", "title",
}


def _sanitize_schema(schema: Any) -> Any:
    """Recursively drop JSON-Schema fields that Gemini's Schema proto rejects.

    Property names inside a ``"properties"`` dict are user-defined keys
    (e.g. ``{"properties": {"email": {...}}}``) — they must NOT be filtered
    by the whitelist; only their *values* are nested schemas.
    """
    if isinstance(schema, dict):
        cleaned: dict = {}
        for k, v in schema.items():
            if k not in _GEMINI_SCHEMA_FIELDS:
                continue
            if k == "properties" and isinstance(v, dict):
                cleaned[k] = {pname: _sanitize_schema(pschema) for pname, pschema in v.items()}
            else:
                cleaned[k] = _sanitize_schema(v)
        return cleaned
    if isinstance(schema, list):
        return [_sanitize_schema(item) for item in schema]
    return schema


class GeminiProvider(LLMProvider):
    supports_images = True

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash-exp"):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
        self.model_name = model_name  # for diagnostics / routing logs

    def _convert_tool_calls_to_parts(self, tool_calls_data: list) -> list:
        parts = []
        for tc in tool_calls_data:
            func = tc["function"] if isinstance(tc, dict) else tc.function
            name = func["name"] if isinstance(func, dict) else func.name
            args = json.loads(func["arguments"] if isinstance(func, dict) else func.arguments)
            parts.append(content_types.FunctionCall(name=name, args=args))
        return parts

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = "auto",
    ) -> Any:
        gemini_history: list[dict] = []
        system_instruction: str | None = None

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "system":
                system_instruction = (
                    content if system_instruction is None
                    else system_instruction + "\n" + content
                )

            elif role == "user":
                if isinstance(content, list):
                    gemini_history.append({
                        "role": "user",
                        "parts": self._convert_user_parts(content),
                    })
                else:
                    gemini_history.append({"role": "user", "parts": [content]})

            elif role == "assistant":
                parts: list = []
                if content:
                    parts.append(content)
                if msg.get("tool_calls"):
                    parts.extend(self._convert_tool_calls_to_parts(msg["tool_calls"]))
                gemini_history.append({"role": "model", "parts": parts})

            elif role == "tool":
                func_name = self._find_tool_name(messages, msg["tool_call_id"])
                try:
                    resp_dict = json.loads(msg["content"])
                except (json.JSONDecodeError, TypeError):
                    resp_dict = {"result": msg["content"]}
                gemini_history.append({
                    "role": "user",
                    "parts": [content_types.FunctionResponse(name=func_name, response=resp_dict)],
                })

        # Convert tool schemas. Gemini's Schema proto is stricter than the
        # OpenAI/JSON-Schema dialect — it rejects `default`, `examples`,
        # `additionalProperties`, etc. with "Unknown field for Schema".
        gemini_tools = None
        if tools:
            declarations = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description"),
                    "parameters": _sanitize_schema(t["function"].get("parameters")),
                }
                for t in tools if t["type"] == "function"
            ]
            if declarations:
                gemini_tools = [declarations]

        # Inject system instruction into the first user message
        if gemini_history and gemini_history[0]["role"] == "model":
            gemini_history.insert(0, {"role": "user", "parts": ["Hi"]})
        if system_instruction and gemini_history:
            first_parts = gemini_history[0]["parts"]
            if isinstance(first_parts, list):
                first_parts.insert(0, f"System Instruction: {system_instruction}")

        response = self.model.generate_content(
            contents=gemini_history,
            tools=gemini_tools,
            request_options={"timeout": 300},
        )

        # Convert to OpenAI-compatible format
        try:
            _ = response.parts[0]
        except (IndexError, AttributeError, ValueError):
            return MockResponse(choices=[
                MockChoice(message=MockMessage(content="Error: empty response from Gemini", tool_calls=None))
            ])

        content_text: str | None = None
        tool_calls: list[MockToolCall] = []
        for part in response.parts:
            if part.text:
                content_text = (content_text or "") + part.text
            if part.function_call:
                tool_calls.append(MockToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    function=MockFunction(
                        name=part.function_call.name,
                        arguments=json.dumps(dict(part.function_call.args)),
                    ),
                ))

        return MockResponse(choices=[
            MockChoice(message=MockMessage(
                content=content_text,
                tool_calls=tool_calls or None,
            ))
        ])

    @staticmethod
    def _convert_user_parts(parts: list[dict]) -> list:
        """Convert OpenAI-style content array to Gemini parts."""
        import base64 as _b64
        import re as _re

        out: list = []
        for p in parts:
            if p.get("type") == "text":
                out.append(p["text"])
            elif p.get("type") == "image_url":
                url = p["image_url"]["url"]
                m = _re.match(
                    r"data:(image/\w+);base64,(.+)", url, _re.DOTALL
                )
                if m:
                    # Gemini's inline_data wants RAW bytes, not the base64
                    # text. Passing the b64 string makes Gemini decode it as
                    # if it were bytes — model sees noise and says "I can't
                    # see anything".
                    out.append({
                        "inline_data": {
                            "mime_type": m.group(1),
                            "data": _b64.b64decode(m.group(2)),
                        }
                    })
                else:
                    try:
                        import urllib.request

                        resp = urllib.request.urlopen(url, timeout=15)
                        data = resp.read()
                        ct = resp.headers.get(
                            "Content-Type", "image/jpeg"
                        ).split(";")[0]
                        out.append({
                            "inline_data": {
                                "mime_type": ct,
                                "data": data,  # raw bytes, not base64
                            }
                        })
                    except Exception:
                        out.append(f"[image: {url}]")
            else:
                out.append(str(p))
        return out or [""]

    @staticmethod
    def _find_tool_name(messages: list[dict], tool_call_id: str) -> str:
        """Walk backwards through messages to find the function name for a tool_call_id."""
        for prev in reversed(messages):
            for tc in prev.get("tool_calls") or []:
                tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                if tc_id == tool_call_id:
                    func = tc["function"] if isinstance(tc, dict) else tc.function
                    return func["name"] if isinstance(func, dict) else func.name
        return "unknown_tool"
