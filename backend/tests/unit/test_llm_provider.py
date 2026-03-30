"""
Unit tests for the LLM provider abstraction layer.

Covers:
- Factory function (get_provider / reset_provider_cache)
- _openai_tools_to_anthropic conversion
- _openai_messages_to_anthropic conversion
- _schema_to_prompt helper
- Each provider class dispatches to the right model/base_url
- Structured parse and chat_with_tools for OpenAI-compat providers (mocked)
- AnthropicProvider structured_parse and chat_with_tools (mocked)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# Reset singleton between tests
from app.agent.llm_provider import (
    AnthropicProvider,
    GeminiProvider,
    HuggingFaceProvider,
    LLMChatResponse,
    OpenAIProvider,
    ToolCallResult,
    _openai_messages_to_anthropic,
    _openai_tools_to_anthropic,
    _schema_to_prompt,
    get_provider,
    reset_provider_cache,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_openai_tool_descriptor(name: str = "my_tool", description: str = "desc") -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"arg": {"type": "string"}},
                "required": ["arg"],
            },
        },
    }


# ─────────────────────────────────────────────
# Conversion helpers
# ─────────────────────────────────────────────

class TestOpenAIToolsToAnthropic:
    def test_basic_conversion(self):
        tools = [_make_openai_tool_descriptor("search", "search something")]
        result = _openai_tools_to_anthropic(tools)
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["description"] == "search something"
        assert "input_schema" in result[0]
        assert result[0]["input_schema"]["type"] == "object"

    def test_parameters_renamed_to_input_schema(self):
        tools = [_make_openai_tool_descriptor()]
        result = _openai_tools_to_anthropic(tools)
        assert "parameters" not in result[0]
        assert "input_schema" in result[0]

    def test_empty_list(self):
        assert _openai_tools_to_anthropic([]) == []

    def test_multiple_tools(self):
        tools = [
            _make_openai_tool_descriptor("tool_a"),
            _make_openai_tool_descriptor("tool_b"),
        ]
        result = _openai_tools_to_anthropic(tools)
        assert [r["name"] for r in result] == ["tool_a", "tool_b"]


class TestOpenAIMessagesToAnthropic:
    def test_simple_user_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = _openai_messages_to_anthropic(messages)
        assert result == [{"role": "user", "content": "hello"}]

    def test_simple_assistant_message(self):
        messages = [{"role": "assistant", "content": "hi there"}]
        result = _openai_messages_to_anthropic(messages)
        assert result == [{"role": "assistant", "content": "hi there"}]

    def test_tool_result_becomes_user_message(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": '{"total": 42}',
            }
        ]
        result = _openai_messages_to_anthropic(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "call_abc"
        assert content[0]["content"] == '{"total": 42}'

    def test_assistant_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {
                            "name": "get_data",
                            "arguments": '{"region": "norte"}',
                        },
                    }
                ],
            }
        ]
        result = _openai_messages_to_anthropic(messages)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        content = result[0]["content"]
        # Should have one tool_use block
        tool_use = next(b for b in content if b["type"] == "tool_use")
        assert tool_use["id"] == "call_xyz"
        assert tool_use["name"] == "get_data"
        assert tool_use["input"] == {"region": "norte"}

    def test_assistant_with_text_and_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "Voy a buscar eso.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "search", "arguments": "{}"},
                    }
                ],
            }
        ]
        result = _openai_messages_to_anthropic(messages)
        content = result[0]["content"]
        text_block = next(b for b in content if b["type"] == "text")
        assert text_block["text"] == "Voy a buscar eso."

    def test_full_conversation_round_trip(self):
        messages = [
            {"role": "user", "content": "Dame los datos"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "fetch", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"result": "ok"}'},
        ]
        result = _openai_messages_to_anthropic(messages)
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[2]["role"] == "user"  # tool result becomes user

    def test_skips_empty_content_messages(self):
        # assistant message with no content and no tool_calls should be skipped
        messages = [{"role": "assistant", "content": None}]
        result = _openai_messages_to_anthropic(messages)
        assert result == []


class TestSchemaToPrompt:
    def test_required_field_marked(self):
        schema = {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["a", "b"]},
                "score": {"type": "number"},
            },
            "required": ["intent"],
        }
        prompt = _schema_to_prompt(schema)
        assert "[requerido]" in prompt
        assert "[opcional, puede ser null]" in prompt
        assert '"intent"' in prompt
        assert '"score"' in prompt

    def test_enum_values_listed(self):
        schema = {
            "type": "object",
            "properties": {"color": {"type": "string", "enum": ["red", "green"]}},
            "required": [],
        }
        prompt = _schema_to_prompt(schema)
        assert "red" in prompt
        assert "green" in prompt

    def test_no_extra_text_instruction(self):
        schema = {"type": "object", "properties": {}, "required": []}
        prompt = _schema_to_prompt(schema)
        assert "No incluyas texto adicional fuera del JSON" in prompt


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────

class TestGetProvider:
    def setup_method(self):
        reset_provider_cache()

    def teardown_method(self):
        reset_provider_cache()

    def test_returns_openai_provider(self):
        with patch("app.config.settings.llm_provider", "openai"), \
             patch("app.config.settings.has_openai", True), \
             patch("app.config.settings.openai_api_key", "sk-test"):
            provider = get_provider()
        assert isinstance(provider, OpenAIProvider)

    def test_returns_huggingface_provider(self):
        with patch("app.config.settings.llm_provider", "huggingface"), \
             patch("app.config.settings.huggingface_api_key", "hf-test"):
            provider = get_provider()
        assert isinstance(provider, HuggingFaceProvider)

    def test_returns_gemini_provider(self):
        with patch("app.config.settings.llm_provider", "gemini"), \
             patch("app.config.settings.gemini_api_key", "AIza-test"):
            provider = get_provider()
        assert isinstance(provider, GeminiProvider)

    def test_returns_anthropic_provider(self):
        with patch("app.config.settings.llm_provider", "claude"), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"):
            provider = get_provider()
        assert isinstance(provider, AnthropicProvider)

    def test_singleton_cached(self):
        with patch("app.config.settings.llm_provider", "huggingface"), \
             patch("app.config.settings.huggingface_api_key", "hf-test"):
            p1 = get_provider()
            p2 = get_provider()
        assert p1 is p2

    def test_reset_allows_new_provider(self):
        with patch("app.config.settings.llm_provider", "huggingface"), \
             patch("app.config.settings.huggingface_api_key", "hf-test"):
            p1 = get_provider()
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "openai"), \
             patch("app.config.settings.has_openai", True), \
             patch("app.config.settings.openai_api_key", "sk-test"):
            p2 = get_provider()
        assert type(p1) is not type(p2)

    def test_unknown_provider_raises_value_error(self):
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "nonexistent"):
            with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
                get_provider()

    def test_missing_openai_key_raises(self):
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "openai"), \
             patch("app.config.settings.has_openai", False):
            with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
                get_provider()

    def test_missing_huggingface_key_raises(self):
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "huggingface"), \
             patch("app.config.settings.huggingface_api_key", ""):
            with pytest.raises(RuntimeError, match="HUGGINGFACE_API_KEY"):
                get_provider()

    def test_missing_gemini_key_raises(self):
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "gemini"), \
             patch("app.config.settings.gemini_api_key", ""):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                get_provider()

    def test_missing_anthropic_key_raises(self):
        reset_provider_cache()
        with patch("app.config.settings.llm_provider", "claude"), \
             patch("app.config.settings.anthropic_api_key", ""):
            with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
                get_provider()


# ─────────────────────────────────────────────
# OpenAI-compat provider: structured_parse
# ─────────────────────────────────────────────

class TestOpenAICompatStructuredParse:
    """Tests OpenAIProvider.structured_parse_sync using mocked openai.OpenAI client."""

    def _mock_completion(self, content: str) -> MagicMock:
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def test_returns_parsed_json(self):
        provider = OpenAIProvider()
        payload = {"intent": "territorial_listing", "confidence": 0.9}

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_completion(
            json.dumps(payload)
        )

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            result = provider.structured_parse_sync(
                system_prompt="parse this",
                user_prompt="cuáles gestiones hay",
                json_schema={"type": "object", "properties": {}},
            )

        assert result == payload

    def test_strict_schema_used_for_openai(self):
        provider = OpenAIProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_completion("{}")

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            provider.structured_parse_sync("sys", "user", {})

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"]["type"] == "json_schema"

    def test_json_object_used_for_huggingface(self):
        provider = HuggingFaceProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_completion("{}")

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.huggingface_api_key", "hf-test"), \
             patch("app.config.settings.huggingface_model", "Qwen/test"):
            provider.structured_parse_sync("sys", "user", {})

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["response_format"]["type"] == "json_object"

    def test_invalid_json_raises_runtime_error(self):
        provider = OpenAIProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_completion("not-json")

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            with pytest.raises(RuntimeError, match="invalid JSON"):
                provider.structured_parse_sync("sys", "user", {})


# ─────────────────────────────────────────────
# OpenAI-compat provider: chat_with_tools
# ─────────────────────────────────────────────

class TestOpenAICompatChatWithTools:
    def _mock_tool_call(self, tool_id: str, name: str, args: dict) -> MagicMock:
        tc = MagicMock()
        tc.id = tool_id
        tc.function.name = name
        tc.function.arguments = json.dumps(args)
        return tc

    def _mock_response(
        self,
        content: str | None = None,
        tool_calls: list | None = None,
        finish_reason: str = "stop",
    ) -> MagicMock:
        choice = MagicMock()
        choice.message.content = content
        choice.message.tool_calls = tool_calls or []
        choice.finish_reason = finish_reason
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def test_no_tool_calls_returns_text_response(self):
        provider = OpenAIProvider()
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = self._mock_response(
            content="Hay 42 gestiones.", finish_reason="stop"
        )

        messages = [{"role": "user", "content": "cuántas gestiones hay?"}]
        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            result = provider.chat_with_tools_sync("sys", messages, [])

        assert result.content == "Hay 42 gestiones."
        assert result.tool_calls == []
        assert result.finish_reason == "stop"

    def test_tool_calls_parsed_correctly(self):
        provider = OpenAIProvider()
        mock_client = MagicMock()
        tc = self._mock_tool_call("call_1", "get_totals", {"departamento": "Capital"})
        mock_client.chat.completions.create.return_value = self._mock_response(
            tool_calls=[tc], finish_reason="tool_calls"
        )

        messages = [{"role": "user", "content": "totales de Capital"}]
        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            result = provider.chat_with_tools_sync("sys", messages, [])

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_totals"
        assert result.tool_calls[0].arguments == {"departamento": "Capital"}
        assert result.tool_calls[0].id == "call_1"

    def test_assistant_message_includes_tool_calls_in_openai_format(self):
        provider = OpenAIProvider()
        mock_client = MagicMock()
        tc = self._mock_tool_call("call_2", "my_tool", {"x": 1})
        mock_client.chat.completions.create.return_value = self._mock_response(tool_calls=[tc])

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.openai_api_key", "sk-test"), \
             patch("app.config.settings.openai_model", "gpt-4o-mini"):
            result = provider.chat_with_tools_sync("sys", [], [])

        msg = result.assistant_message
        assert msg["role"] == "assistant"
        assert "tool_calls" in msg
        assert msg["tool_calls"][0]["id"] == "call_2"


# ─────────────────────────────────────────────
# AnthropicProvider (mocked SDK)
# ─────────────────────────────────────────────

class TestAnthropicProvider:
    def _make_text_block(self, text: str) -> MagicMock:
        block = MagicMock()
        block.type = "text"
        block.text = text
        return block

    def _make_tool_use_block(self, bid: str, name: str, inp: dict) -> MagicMock:
        block = MagicMock()
        block.type = "tool_use"
        block.id = bid
        block.name = name
        block.input = inp
        return block

    def _make_anthropic_response(self, content_blocks: list) -> MagicMock:
        resp = MagicMock()
        resp.content = content_blocks
        return resp

    def test_structured_parse_extracts_tool_use_block(self):
        provider = AnthropicProvider()
        tool_block = self._make_tool_use_block(
            "tu_1", "structured_output", {"intent": "urgent_alerts", "confidence": 0.95}
        )
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([tool_block])

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            result = provider.structured_parse_sync(
                "parse this", "dame las urgentes", {"type": "object", "properties": {}}
            )

        assert result["intent"] == "urgent_alerts"
        assert result["confidence"] == 0.95

    def test_structured_parse_raises_if_no_tool_use(self):
        provider = AnthropicProvider()
        text_block = self._make_text_block("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([text_block])

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            with pytest.raises(RuntimeError, match="tool_use block"):
                provider.structured_parse_sync("sys", "user", {})

    def test_chat_with_tools_text_only_response(self):
        provider = AnthropicProvider()
        text_block = self._make_text_block("Hay 10 gestiones urgentes.")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([text_block])

        messages = [{"role": "user", "content": "urgentes?"}]
        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            result = provider.chat_with_tools_sync("sys", messages, [])

        assert result.content == "Hay 10 gestiones urgentes."
        assert result.tool_calls == []
        assert result.finish_reason == "stop"

    def test_chat_with_tools_tool_call_response(self):
        provider = AnthropicProvider()
        tool_block = self._make_tool_use_block(
            "tu_abc", "get_urgent_alerts", {"departamento": "Colón"}
        )
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([tool_block])

        messages = [{"role": "user", "content": "alertas en Colón"}]
        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            result = provider.chat_with_tools_sync("sys", messages, [])

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "get_urgent_alerts"
        assert result.tool_calls[0].arguments == {"departamento": "Colón"}
        assert result.finish_reason == "tool_calls"

    def test_chat_with_tools_builds_openai_format_assistant_message(self):
        provider = AnthropicProvider()
        tool_block = self._make_tool_use_block("tu_x", "some_tool", {"a": "b"})
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([tool_block])

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            result = provider.chat_with_tools_sync("sys", [], [])

        msg = result.assistant_message
        assert msg["role"] == "assistant"
        assert "tool_calls" in msg
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tu_x"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "some_tool"
        # Arguments must be a JSON string (OpenAI format)
        assert json.loads(tc["function"]["arguments"]) == {"a": "b"}

    def test_messages_converted_to_anthropic_format_on_call(self):
        """Verify that OpenAI-format messages are converted before being sent to Anthropic."""
        provider = AnthropicProvider()
        text_block = self._make_text_block("ok")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._make_anthropic_response([text_block])

        messages = [
            {"role": "user", "content": "hola"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "fetch", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"result": "ok"}'},
        ]

        with patch.object(provider, "_client", return_value=mock_client), \
             patch("app.config.settings.anthropic_api_key", "sk-ant-test"), \
             patch("app.config.settings.anthropic_model", "claude-haiku-4-5-20251001"):
            provider.chat_with_tools_sync("sys", messages, [])

        call_kwargs = mock_client.messages.create.call_args[1]
        converted = call_kwargs["messages"]

        # First: user
        assert converted[0]["role"] == "user"
        # Second: assistant with tool_use block
        assert converted[1]["role"] == "assistant"
        tu = next(b for b in converted[1]["content"] if b["type"] == "tool_use")
        assert tu["name"] == "fetch"
        # Third: tool result → user message
        assert converted[2]["role"] == "user"
        tr = converted[2]["content"][0]
        assert tr["type"] == "tool_result"
        assert tr["tool_use_id"] == "c1"
