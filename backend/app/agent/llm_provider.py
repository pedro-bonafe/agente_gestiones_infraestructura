"""
LLM Provider abstraction.

Supported providers:
  - huggingface : Qwen/Qwen2.5-72B-Instruct via HuggingFace Inference API (default)
  - openai      : OpenAI gpt-4o-mini (or any OpenAI model)
  - gemini      : Google Gemini via its OpenAI-compatible endpoint
  - claude      : Anthropic Claude via the anthropic SDK

All providers expose two operations:
  1. structured_parse(system, user, schema) -> dict
     Used by NLU to extract a typed ParsedQuery.

  2. chat_with_tools(messages, tools, system) -> LLMChatResponse
     Used by the ReAct executor loop.

Internally the executor keeps messages in OpenAI format.
AnthropicProvider converts them on every call.
"""

from __future__ import annotations

import asyncio
import json
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import structlog

from app.config import settings

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────
# Response model
# ─────────────────────────────────────────────

@dataclass
class ToolCallResult:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMChatResponse:
    """Normalised response from any provider's chat_with_tools call."""
    content: str | None
    tool_calls: list[ToolCallResult]
    finish_reason: str
    # Must be in OpenAI format so the executor can append it to messages.
    assistant_message: dict = field(default_factory=dict)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _normalize(text: str) -> str:
    nfd = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(without_accents.upper().split())


def _schema_to_prompt(schema: dict) -> str:
    """Render a JSON schema as plain-text instructions (for providers that don't support strict mode)."""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [
        "Devolvé ÚNICAMENTE un objeto JSON válido con exactamente estos campos:",
        "",
    ]
    for field_name, spec in props.items():
        raw_type = spec.get("type", "any")
        ftype = " | ".join(raw_type) if isinstance(raw_type, list) else raw_type
        enum_vals = spec.get("enum")
        req = " [requerido]" if field_name in required else " [opcional, puede ser null]"
        if enum_vals:
            lines.append(f'  "{field_name}": uno de {enum_vals}{req}')
        else:
            lines.append(f'  "{field_name}": {ftype}{req}')
    lines += ["", "No incluyas texto adicional fuera del JSON."]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────

class LLMProvider(ABC):

    @abstractmethod
    def structured_parse_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> dict:
        """Synchronous structured parse — wrapped in asyncio.to_thread by callers."""

    @abstractmethod
    def chat_with_tools_sync(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMChatResponse:
        """Synchronous chat with function calling — wrapped in asyncio.to_thread by callers."""

    async def structured_parse(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> dict:
        return await asyncio.to_thread(
            self.structured_parse_sync, system_prompt, user_prompt, json_schema
        )

    async def chat_with_tools(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMChatResponse:
        return await asyncio.to_thread(
            self.chat_with_tools_sync, system_prompt, messages, tools
        )


# ─────────────────────────────────────────────
# OpenAI-compatible providers
# (OpenAI, HuggingFace, Gemini all use the openai SDK with different base_url)
# ─────────────────────────────────────────────

class _OpenAICompatProvider(LLMProvider):
    """
    Shared implementation for any OpenAI-compatible REST endpoint.
    Subclasses set _base_url, _api_key, _model and _strict_schema flag.
    """

    _base_url: str | None = None   # None = default OpenAI endpoint
    _api_key: str = ""
    _model: str = "gpt-4o-mini"
    _strict_schema: bool = True    # Only OpenAI reliably supports strict json_schema

    def _client(self):
        from openai import OpenAI
        kwargs: dict = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return OpenAI(**kwargs)

    def structured_parse_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> dict:
        from openai import AuthenticationError, APIConnectionError, RateLimitError

        client = self._client()

        # Build messages — for non-strict providers, embed schema in system prompt
        if self._strict_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": json_schema,
                    "strict": True,
                },
            }
            system = system_prompt
        else:
            response_format = {"type": "json_object"}
            system = system_prompt + "\n\n" + _schema_to_prompt(json_schema)

        try:
            response = client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=response_format,
            )
        except (AuthenticationError, APIConnectionError, RateLimitError) as exc:
            raise RuntimeError(f"LLM provider error: {exc}") from exc

        content = (response.choices[0].message.content or "{}").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Provider returned invalid JSON: {content[:200]}") from exc

    def chat_with_tools_sync(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMChatResponse:
        from openai import AuthenticationError, APIConnectionError, RateLimitError

        client = self._client()
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        try:
            kwargs: dict = {
                "model": self._model,
                "temperature": 0.2,
                "messages": full_messages,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = client.chat.completions.create(**kwargs)
        except (AuthenticationError, APIConnectionError, RateLimitError) as exc:
            raise RuntimeError(f"LLM provider error: {exc}") from exc

        choice = response.choices[0]
        raw_tool_calls = choice.message.tool_calls or []

        tool_calls = [
            ToolCallResult(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments or "{}"),
            )
            for tc in raw_tool_calls
        ]

        # Build OpenAI-format assistant message for history
        assistant_message: dict = {"role": "assistant", "content": choice.message.content}
        if raw_tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in raw_tool_calls
            ]

        return LLMChatResponse(
            content=choice.message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            assistant_message=assistant_message,
        )


class OpenAIProvider(_OpenAICompatProvider):
    _strict_schema = True

    @property
    def _api_key(self) -> str:  # type: ignore[override]
        return settings.openai_api_key

    @property
    def _model(self) -> str:  # type: ignore[override]
        return settings.openai_model


class HuggingFaceProvider(_OpenAICompatProvider):
    """Qwen (or any model) via HuggingFace Inference Router (OpenAI-compatible)."""

    _base_url = "https://router.huggingface.co/v1/"
    _strict_schema = False  # HF models don't support strict json_schema

    @property
    def _api_key(self) -> str:  # type: ignore[override]
        return settings.huggingface_api_key

    @property
    def _model(self) -> str:  # type: ignore[override]
        return settings.huggingface_model


class GeminiProvider(_OpenAICompatProvider):
    """Gemini via Google's OpenAI-compatible endpoint."""

    _base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    _strict_schema = False  # Use json_object mode

    @property
    def _api_key(self) -> str:  # type: ignore[override]
        return settings.gemini_api_key

    @property
    def _model(self) -> str:  # type: ignore[override]
        return settings.gemini_model


# ─────────────────────────────────────────────
# Anthropic / Claude provider
# ─────────────────────────────────────────────

def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI tool descriptors to Anthropic format."""
    result = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _openai_messages_to_anthropic(messages: list[dict]) -> list[dict]:
    """
    Convert a message list in OpenAI format to Anthropic format.

    OpenAI → Anthropic differences:
    - assistant tool_calls  → assistant content with type=tool_use blocks
    - role=tool messages    → role=user with type=tool_result content
    """
    result: list[dict] = []
    for msg in messages:
        role = msg.get("role")

        if role in ("user", "assistant") and not msg.get("tool_calls") and msg.get("content"):
            result.append({"role": role, "content": msg["content"]})

        elif role == "assistant" and msg.get("tool_calls"):
            content_blocks = []
            if msg.get("content"):
                content_blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg["tool_calls"]:
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"].get("arguments") or "{}"),
                })
            result.append({"role": "assistant", "content": content_blocks})

        elif role == "tool":
            # Tool results become user messages in Anthropic format
            result.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id", ""),
                        "content": str(msg.get("content", "")),
                    }
                ],
            })
    return result


class AnthropicProvider(LLMProvider):
    """Claude via the official Anthropic SDK."""

    def _client(self):
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed. Add it to requirements.txt.") from exc
        return Anthropic(api_key=settings.anthropic_api_key)

    def structured_parse_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict,
    ) -> dict:
        client = self._client()

        # Use tool_use with forced tool selection — Claude's structured output equivalent
        tool = {
            "name": "structured_output",
            "description": "Extract structured data from the user query.",
            "input_schema": json_schema,
        }

        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "structured_output"},
        )

        for block in response.content:
            if hasattr(block, "type") and block.type == "tool_use":
                return block.input  # type: ignore[return-value]

        raise RuntimeError("Claude did not return a tool_use block for structured parse.")

    def chat_with_tools_sync(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMChatResponse:
        client = self._client()
        anthropic_messages = _openai_messages_to_anthropic(messages)
        anthropic_tools = _openai_tools_to_anthropic(tools)

        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=2048,
            temperature=0.2,
            system=system_prompt,
            messages=anthropic_messages,
            tools=anthropic_tools,
        )

        text_content: str | None = None
        tool_calls: list[ToolCallResult] = []

        for block in response.content:
            if hasattr(block, "type"):
                if block.type == "text":
                    text_content = block.text
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCallResult(
                            id=block.id,
                            name=block.name,
                            arguments=block.input,  # type: ignore[arg-type]
                        )
                    )

        # Build OpenAI-format assistant message so executor can store it uniformly
        if tool_calls:
            assistant_message = {
                "role": "assistant",
                "content": text_content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in tool_calls
                ],
            }
        else:
            assistant_message = {"role": "assistant", "content": text_content}

        finish = "tool_calls" if tool_calls else "stop"
        return LLMChatResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish,
            assistant_message=assistant_message,
        )


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

_PROVIDER_CACHE: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Return a singleton provider based on settings.llm_provider."""
    global _PROVIDER_CACHE
    if _PROVIDER_CACHE is not None:
        return _PROVIDER_CACHE

    name = settings.llm_provider.lower().strip()
    logger.info("Initializing LLM provider", provider=name)

    if name == "openai":
        if not settings.has_openai:
            raise RuntimeError("OPENAI_API_KEY not configured.")
        _PROVIDER_CACHE = OpenAIProvider()

    elif name == "huggingface":
        if not settings.huggingface_api_key:
            raise RuntimeError("HUGGINGFACE_API_KEY not configured.")
        _PROVIDER_CACHE = HuggingFaceProvider()

    elif name == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured.")
        _PROVIDER_CACHE = GeminiProvider()

    elif name == "claude":
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured.")
        _PROVIDER_CACHE = AnthropicProvider()

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER='{name}'. "
            "Valid options: openai | huggingface | gemini | claude"
        )

    return _PROVIDER_CACHE


def reset_provider_cache() -> None:
    """Force re-initialization on next call (useful for tests)."""
    global _PROVIDER_CACHE
    _PROVIDER_CACHE = None
