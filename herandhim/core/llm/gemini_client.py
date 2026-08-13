"""
Google Gemini provider — uses the new ``google.genai`` SDK (genai.Client).

Adapts Gemini's Content/Part model to the OpenAI-compatible response shape
that ``Agent`` consumes via :mod:`herandhim.core.llm.response`.

Migrated from the deprecated ``google.generativeai`` package (last release
0.8.6, EOL).  The new SDK accepts standard JSON Schema directly for tool
parameters, so we no longer need to hand-sanitize fields like ``default``,
``examples``, ``additionalProperties`` — Gemini's server-side validation
in the new API is much more lenient.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types as _gtypes

from .base import LLMProvider
from .response import MockChoice, MockFunction, MockMessage, MockResponse, MockToolCall


class GeminiProvider(LLMProvider):
    supports_images = True

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self._client = genai.Client(api_key=api_key)
        self.model_name = model_name

    # ── Public API ──────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = "auto",
    ) -> Any:
        contents, system_instruction = self._build_contents(messages)

        gemini_tools: list[_gtypes.Tool] | None = None
        if tools:
            decls: list[_gtypes.FunctionDeclaration] = []
            for t in tools:
                if t.get("type") != "function":
                    continue
                fn = t["function"]
                decls.append(_gtypes.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description"),
                    parameters_json_schema=fn.get("parameters"),
                ))
            if decls:
                gemini_tools = [_gtypes.Tool(function_declarations=decls)]

        # Gemini requires the conversation to start with a 'user' turn.
        if contents and contents[0].role == "model":
            contents.insert(0, _gtypes.Content(
                role="user", parts=[_gtypes.Part(text="Hi")],
            ))

        config = _gtypes.GenerateContentConfig(
            system_instruction=system_instruction or None,
            tools=gemini_tools,
            http_options=_gtypes.HttpOptions(timeout=300_000),  # ms
        )

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        return self._response_to_mock(response)

    # ── Message → Gemini Content[] ──────────────────────────────────────

    def _build_contents(
        self,
        messages: List[Dict[str, Any]],
    ) -> tuple[list[_gtypes.Content], str | None]:
        contents: list[_gtypes.Content] = []
        system_instruction: str | None = None

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "system":
                # Stable + volatile system messages all concatenate into one
                # system_instruction. The Anthropic-flavoured VOLATILE_PREFIX
                # marker (if present) is stripped — Gemini has no equivalent
                # cache breakpoint concept.
                text = content or ""
                if isinstance(text, str) and text.startswith("[[VOLATILE]]"):
                    text = text[len("[[VOLATILE]]"):]
                system_instruction = (
                    text if system_instruction is None
                    else system_instruction + "\n" + text
                )

            elif role == "user":
                if isinstance(content, list):
                    parts = self._convert_user_parts(content)
                else:
                    parts = [_gtypes.Part(text=str(content or ""))]
                contents.append(_gtypes.Content(role="user", parts=parts))

            elif role == "assistant":
                parts: list[_gtypes.Part] = []
                if content:
                    parts.append(_gtypes.Part(text=str(content)))
                if msg.get("tool_calls"):
                    parts.extend(self._tool_calls_to_parts(msg["tool_calls"]))
                if parts:
                    contents.append(_gtypes.Content(role="model", parts=parts))

            elif role == "tool":
                func_name = self._find_tool_name(messages, msg.get("tool_call_id", ""))
                try:
                    resp_dict = json.loads(msg["content"])
                except (json.JSONDecodeError, TypeError):
                    resp_dict = {"result": msg["content"]}
                contents.append(_gtypes.Content(
                    role="user",
                    parts=[_gtypes.Part(function_response=_gtypes.FunctionResponse(
                        name=func_name, response=resp_dict,
                    ))],
                ))

        return contents, system_instruction

    @staticmethod
    def _tool_calls_to_parts(tool_calls_data: list) -> list[_gtypes.Part]:
        parts: list[_gtypes.Part] = []
        for tc in tool_calls_data:
            func = tc["function"] if isinstance(tc, dict) else tc.function
            name = func["name"] if isinstance(func, dict) else func.name
            raw_args = func["arguments"] if isinstance(func, dict) else func.arguments
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                args = {}
            parts.append(_gtypes.Part(function_call=_gtypes.FunctionCall(
                name=name, args=args,
            )))
        return parts

    @staticmethod
    def _convert_user_parts(parts: list[dict]) -> list[_gtypes.Part]:
        """Convert OpenAI-style content array to Gemini parts.

        Each entry is either ``{"type": "text", "text": "..."}`` or
        ``{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}``.
        """
        import base64 as _b64
        import re as _re

        out: list[_gtypes.Part] = []
        for p in parts:
            t = p.get("type")
            if t == "text":
                out.append(_gtypes.Part(text=p.get("text", "")))
            elif t == "image_url":
                url = p["image_url"]["url"]
                m = _re.match(r"data:(image/\w+);base64,(.+)", url, _re.DOTALL)
                if m:
                    out.append(_gtypes.Part(inline_data=_gtypes.Blob(
                        mime_type=m.group(1),
                        data=_b64.b64decode(m.group(2)),
                    )))
                else:
                    try:
                        import urllib.request
                        resp = urllib.request.urlopen(url, timeout=15)
                        data = resp.read()
                        ct = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
                        out.append(_gtypes.Part(inline_data=_gtypes.Blob(
                            mime_type=ct, data=data,
                        )))
                    except Exception:
                        out.append(_gtypes.Part(text=f"[image: {url}]"))
            else:
                out.append(_gtypes.Part(text=str(p)))
        return out or [_gtypes.Part(text="")]

    # ── Response → OpenAI-compat MockResponse ───────────────────────────

    @staticmethod
    def _response_to_mock(response: _gtypes.GenerateContentResponse) -> MockResponse:
        candidates = getattr(response, "candidates", None) or []
        if not candidates or not getattr(candidates[0], "content", None):
            return MockResponse(choices=[MockChoice(message=MockMessage(
                content="Error: empty response from Gemini", tool_calls=None,
            ))])

        parts = getattr(candidates[0].content, "parts", None) or []
        content_text: str | None = None
        tool_calls: list[MockToolCall] = []

        for part in parts:
            if getattr(part, "text", None):
                content_text = (content_text or "") + part.text
            fc = getattr(part, "function_call", None)
            if fc and fc.name:
                tool_calls.append(MockToolCall(
                    id=f"call_{uuid.uuid4().hex[:8]}",
                    function=MockFunction(
                        name=fc.name,
                        arguments=json.dumps(dict(fc.args or {})),
                    ),
                ))

        return MockResponse(choices=[MockChoice(message=MockMessage(
            content=content_text,
            tool_calls=tool_calls or None,
        ))])

    @staticmethod
    def _find_tool_name(messages: list[dict], tool_call_id: str) -> str:
        """Walk back through history to recover the function name for a tool_call_id."""
        for prev in reversed(messages):
            for tc in prev.get("tool_calls") or []:
                tc_id = tc["id"] if isinstance(tc, dict) else tc.id
                if tc_id == tool_call_id:
                    func = tc["function"] if isinstance(tc, dict) else tc.function
                    return func["name"] if isinstance(func, dict) else func.name
        return "unknown_tool"
