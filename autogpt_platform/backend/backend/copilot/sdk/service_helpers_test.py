"""Unit tests for extracted service helpers.

Covers ``_is_prompt_too_long``, ``_reduce_context``, ``_iter_sdk_messages``,
``ReducedContext``, and the ``is_parallel_continuation`` logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

from .conftest import build_test_transcript as _build_transcript
from .service import (
    ReducedContext,
    _is_prompt_too_long,
    _is_tool_only_message,
    _iter_sdk_messages,
    _normalize_model_name,
    _reduce_context,
    _resolve_user_model_override,
)

# ---------------------------------------------------------------------------
# _is_prompt_too_long
# ---------------------------------------------------------------------------


class TestIsPromptTooLong:
    def test_direct_match(self) -> None:
        assert _is_prompt_too_long(Exception("prompt is too long")) is True

    def test_case_insensitive(self) -> None:
        assert _is_prompt_too_long(Exception("PROMPT IS TOO LONG")) is True

    def test_no_match(self) -> None:
        assert _is_prompt_too_long(Exception("network timeout")) is False

    def test_request_too_large(self) -> None:
        assert _is_prompt_too_long(Exception("request too large for model")) is True

    def test_context_length_exceeded(self) -> None:
        assert _is_prompt_too_long(Exception("context_length_exceeded")) is True

    def test_max_tokens_exceeded_not_matched(self) -> None:
        """'max_tokens_exceeded' is intentionally excluded (too broad)."""
        assert _is_prompt_too_long(Exception("max_tokens_exceeded")) is False

    def test_max_tokens_config_error_no_match(self) -> None:
        """'max_tokens must be at least 1' should NOT match."""
        assert _is_prompt_too_long(Exception("max_tokens must be at least 1")) is False

    def test_chained_cause(self) -> None:
        inner = Exception("prompt is too long")
        outer = RuntimeError("SDK error")
        outer.__cause__ = inner
        assert _is_prompt_too_long(outer) is True

    def test_chained_context(self) -> None:
        inner = Exception("request too large")
        outer = RuntimeError("wrapped")
        outer.__context__ = inner
        assert _is_prompt_too_long(outer) is True

    def test_deep_chain(self) -> None:
        bottom = Exception("maximum context length")
        middle = RuntimeError("middle")
        middle.__cause__ = bottom
        top = ValueError("top")
        top.__cause__ = middle
        assert _is_prompt_too_long(top) is True

    def test_chain_no_match(self) -> None:
        inner = Exception("rate limit exceeded")
        outer = RuntimeError("wrapped")
        outer.__cause__ = inner
        assert _is_prompt_too_long(outer) is False

    def test_cycle_detection(self) -> None:
        """Exception chain with a cycle should not infinite-loop."""
        a = Exception("error a")
        b = Exception("error b")
        a.__cause__ = b
        b.__cause__ = a  # cycle
        assert _is_prompt_too_long(a) is False

    def test_all_patterns(self) -> None:
        patterns = [
            "prompt is too long",
            "request too large",
            "maximum context length",
            "context_length_exceeded",
            "input tokens exceed",
            "input is too long",
            "content length exceeds",
        ]
        for pattern in patterns:
            assert _is_prompt_too_long(Exception(pattern)) is True, pattern


# ---------------------------------------------------------------------------
# _reduce_context
# ---------------------------------------------------------------------------


class TestReduceContext:
    @pytest.mark.asyncio
    async def test_first_retry_compaction_success(self) -> None:
        # After compaction the retry runs WITHOUT --resume because we cannot
        # inject the compacted content into the CLI's native session file format.
        # The compacted builder state is still set for future upload_transcript.
        transcript = _build_transcript([("user", "hi"), ("assistant", "hello")])
        compacted = _build_transcript([("user", "hi"), ("assistant", "[summary]")])

        with (
            patch(
                "backend.copilot.sdk.service.compact_transcript",
                new_callable=AsyncMock,
                return_value=compacted,
            ),
            patch(
                "backend.copilot.sdk.service.validate_transcript",
                return_value=True,
            ),
        ):
            ctx = await _reduce_context(
                transcript, False, "sess-123", "/tmp/cwd", "[test]"
            )

        assert isinstance(ctx, ReducedContext)
        assert ctx.use_resume is False
        assert ctx.resume_file is None
        assert ctx.transcript_lost is False
        assert ctx.tried_compaction is True

    @pytest.mark.asyncio
    async def test_compaction_fails_drops_transcript(self) -> None:
        transcript = _build_transcript([("user", "hi"), ("assistant", "hello")])

        with patch(
            "backend.copilot.sdk.service.compact_transcript",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ctx = await _reduce_context(
                transcript, False, "sess-123", "/tmp/cwd", "[test]"
            )

        assert ctx.use_resume is False
        assert ctx.resume_file is None
        assert ctx.transcript_lost is True
        assert ctx.tried_compaction is True

    @pytest.mark.asyncio
    async def test_already_tried_compaction_skips(self) -> None:
        transcript = _build_transcript([("user", "hi"), ("assistant", "hello")])

        ctx = await _reduce_context(transcript, True, "sess-123", "/tmp/cwd", "[test]")

        assert ctx.use_resume is False
        assert ctx.transcript_lost is True
        assert ctx.tried_compaction is True

    @pytest.mark.asyncio
    async def test_empty_transcript_drops(self) -> None:
        ctx = await _reduce_context("", False, "sess-123", "/tmp/cwd", "[test]")

        assert ctx.use_resume is False
        assert ctx.transcript_lost is True

    @pytest.mark.asyncio
    async def test_compaction_returns_same_content_drops(self) -> None:
        transcript = _build_transcript([("user", "hi"), ("assistant", "hello")])

        with patch(
            "backend.copilot.sdk.service.compact_transcript",
            new_callable=AsyncMock,
            return_value=transcript,  # same content
        ):
            ctx = await _reduce_context(
                transcript, False, "sess-123", "/tmp/cwd", "[test]"
            )

        assert ctx.transcript_lost is True

    @pytest.mark.asyncio
    async def test_compaction_invalid_transcript_drops(self) -> None:
        # When validate_transcript returns False for compacted content, drop transcript.
        transcript = _build_transcript([("user", "hi"), ("assistant", "hello")])
        compacted = _build_transcript([("user", "hi"), ("assistant", "[summary]")])

        with (
            patch(
                "backend.copilot.sdk.service.compact_transcript",
                new_callable=AsyncMock,
                return_value=compacted,
            ),
            patch(
                "backend.copilot.sdk.service.validate_transcript",
                return_value=False,
            ),
        ):
            ctx = await _reduce_context(
                transcript, False, "sess-123", "/tmp/cwd", "[test]"
            )

        assert ctx.transcript_lost is True


# ---------------------------------------------------------------------------
# _iter_sdk_messages
# ---------------------------------------------------------------------------


class TestIterSdkMessages:
    @pytest.mark.asyncio
    async def test_yields_messages(self) -> None:
        messages = ["msg1", "msg2", "msg3"]
        client = AsyncMock()

        async def _fake_receive() -> AsyncGenerator[str]:
            for m in messages:
                yield m

        client.receive_response = _fake_receive
        result = [msg async for msg in _iter_sdk_messages(client)]
        assert result == messages

    @pytest.mark.asyncio
    async def test_heartbeat_on_timeout(self) -> None:
        """Yields None when asyncio.wait times out."""
        client = AsyncMock()
        received: list = []

        async def _slow_receive() -> AsyncGenerator[str]:
            await asyncio.sleep(100)  # never completes
            yield "never"  # pragma: no cover — unreachable, yield makes this an async generator

        client.receive_response = _slow_receive

        with patch("backend.copilot.sdk.service._HEARTBEAT_INTERVAL", 0.01):
            count = 0
            async for msg in _iter_sdk_messages(client):
                received.append(msg)
                count += 1
                if count >= 3:
                    break

        assert all(m is None for m in received)

    @pytest.mark.asyncio
    async def test_exception_propagates(self) -> None:
        client = AsyncMock()

        async def _error_receive() -> AsyncGenerator[str]:
            raise RuntimeError("SDK crash")
            yield  # pragma: no cover — unreachable, yield makes this an async generator

        client.receive_response = _error_receive

        with pytest.raises(RuntimeError, match="SDK crash"):
            async for _ in _iter_sdk_messages(client):
                pass

    @pytest.mark.asyncio
    async def test_task_cleanup_on_break(self) -> None:
        """Pending task is cancelled when generator is closed."""
        client = AsyncMock()

        async def _slow_receive() -> AsyncGenerator[str]:
            yield "first"
            await asyncio.sleep(100)
            yield "second"

        client.receive_response = _slow_receive

        gen = _iter_sdk_messages(client)
        first = await gen.__anext__()
        assert first == "first"
        await gen.aclose()  # should cancel pending task cleanly


# ---------------------------------------------------------------------------
# is_parallel_continuation logic
# ---------------------------------------------------------------------------


class TestIsParallelContinuation:
    """Unit tests for the is_parallel_continuation expression in the streaming loop.

    Verifies the vacuous-truth guard (empty content must return False) and the
    boundary cases for mixed TextBlock+ToolUseBlock messages.
    """

    def _make_tool_block(self) -> MagicMock:
        block = MagicMock(spec=ToolUseBlock)
        return block

    def test_all_tool_use_blocks_is_parallel(self):
        """AssistantMessage with only ToolUseBlocks is a parallel continuation."""
        msg = MagicMock(spec=AssistantMessage)
        msg.content = [self._make_tool_block(), self._make_tool_block()]
        assert _is_tool_only_message(msg) is True

    def test_empty_content_is_not_parallel(self):
        """AssistantMessage with empty content must NOT be treated as parallel.

        Without the bool(sdk_msg.content) guard, all() on an empty iterable
        returns True via vacuous truth — this test ensures the guard is present.
        """
        msg = MagicMock(spec=AssistantMessage)
        msg.content = []
        assert _is_tool_only_message(msg) is False

    def test_mixed_text_and_tool_blocks_not_parallel(self):
        """AssistantMessage with text + tool blocks is NOT a parallel continuation."""
        msg = MagicMock(spec=AssistantMessage)
        text_block = MagicMock(spec=TextBlock)
        msg.content = [text_block, self._make_tool_block()]
        assert _is_tool_only_message(msg) is False

    def test_non_assistant_message_not_parallel(self):
        """Non-AssistantMessage types are never parallel continuations."""
        assert _is_tool_only_message("not a message") is False
        assert _is_tool_only_message(None) is False
        assert _is_tool_only_message(42) is False

    def test_single_tool_block_is_parallel(self):
        """Single ToolUseBlock AssistantMessage is a parallel continuation."""
        msg = MagicMock(spec=AssistantMessage)
        msg.content = [self._make_tool_block()]
        assert _is_tool_only_message(msg) is True


# ---------------------------------------------------------------------------
# _resolve_user_model_override
# ---------------------------------------------------------------------------


class TestResolveUserModelOverride:
    @pytest.mark.asyncio
    async def test_no_env_no_ld_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        """When no env override and LD returns None, result is None."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        with patch(
            "backend.copilot.sdk.service.get_feature_flag_value",
            new=AsyncMock(return_value=None),
        ):
            result = await _resolve_user_model_override("user-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_env_override_bypasses_ld(self, monkeypatch: pytest.MonkeyPatch):
        """FORCE_FLAG_COPILOT_MODEL short-circuits the LD call."""
        monkeypatch.setenv("FORCE_FLAG_COPILOT_MODEL", "anthropic/claude-opus-4-6")
        ld_mock = AsyncMock(return_value=None)
        with patch("backend.copilot.sdk.service.get_feature_flag_value", new=ld_mock):
            result = await _resolve_user_model_override("user-123")
        # LD should not be called
        ld_mock.assert_not_called()
        # Model name is normalized (OpenRouter prefix stripped)
        assert result == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_ld_returns_model_string(self, monkeypatch: pytest.MonkeyPatch):
        """When LD returns a model string, it is normalized and returned."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        with patch(
            "backend.copilot.sdk.service.get_feature_flag_value",
            new=AsyncMock(return_value="anthropic/claude-opus-4-6"),
        ):
            result = await _resolve_user_model_override("user-123")
        assert result == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_ld_returns_non_string_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When LD returns a non-string (e.g. True), result is None."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        with patch(
            "backend.copilot.sdk.service.get_feature_flag_value",
            new=AsyncMock(return_value=True),
        ):
            result = await _resolve_user_model_override("user-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_ld_returns_empty_string_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """When LD returns an empty string, result is None."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        with patch(
            "backend.copilot.sdk.service.get_feature_flag_value",
            new=AsyncMock(return_value=""),
        ):
            result = await _resolve_user_model_override("user-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_already_normalized_model_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A model name without the OpenRouter prefix is returned as-is."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        with patch(
            "backend.copilot.sdk.service.get_feature_flag_value",
            new=AsyncMock(return_value="claude-opus-4-6"),
        ):
            result = await _resolve_user_model_override("user-123")
        assert result == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_ld_call_uses_correct_flag_key(self, monkeypatch: pytest.MonkeyPatch):
        """get_feature_flag_value is called with Flag.COPILOT_MODEL and the user_id."""
        monkeypatch.delenv("FORCE_FLAG_COPILOT_MODEL", raising=False)
        monkeypatch.delenv("NEXT_PUBLIC_FORCE_FLAG_COPILOT_MODEL", raising=False)
        ld_mock = AsyncMock(return_value=None)
        with patch("backend.copilot.sdk.service.get_feature_flag_value", new=ld_mock):
            await _resolve_user_model_override("user-abc")
        ld_mock.assert_called_once_with("copilot-model", "user-abc", default=None)


# ---------------------------------------------------------------------------
# _normalize_model_name — used by per-request model override
# ---------------------------------------------------------------------------


class TestNormalizeModelName:
    """Unit tests for the model-name normalisation helper.

    The per-request model toggle calls _normalize_model_name with either
    ``"anthropic/claude-opus-4-6"`` (for 'advanced') or ``config.model`` (for
    'standard').  These tests verify the OpenRouter/provider-prefix stripping
    that keeps the value compatible with the Claude CLI.
    """

    def test_strips_anthropic_prefix(self):
        assert _normalize_model_name("anthropic/claude-opus-4-6") == "claude-opus-4-6"

    def test_strips_openai_prefix(self):
        assert _normalize_model_name("openai/gpt-4o") == "gpt-4o"

    def test_strips_google_prefix(self):
        assert _normalize_model_name("google/gemini-2.5-flash") == "gemini-2.5-flash"

    def test_already_normalized_unchanged(self):
        assert (
            _normalize_model_name("claude-sonnet-4-20250514")
            == "claude-sonnet-4-20250514"
        )

    def test_empty_string_unchanged(self):
        assert _normalize_model_name("") == ""

    def test_opus_model_roundtrip(self):
        """The exact string used for the 'opus' toggle strips correctly."""
        assert _normalize_model_name("anthropic/claude-opus-4-6") == "claude-opus-4-6"

    def test_sonnet_openrouter_model(self):
        """Sonnet model as stored in config (OpenRouter-prefixed) strips cleanly."""
        assert _normalize_model_name("anthropic/claude-sonnet-4") == "claude-sonnet-4"
