"""Unit tests for the public-share sanitizer.

The sanitizer is the security boundary for public chat sharing — every
test here is checking that data which must NOT cross the public link
genuinely does not.
"""

from datetime import datetime, timezone

from backend.copilot.model import ChatMessage as ChatMessageDomain
from backend.copilot.sharing.models import _redact_secret_keys, sanitize_chat_message


def _msg(**overrides) -> ChatMessageDomain:
    defaults = dict(
        id="m1",
        role="assistant",
        content=None,
        sequence=0,
        created_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return ChatMessageDomain(**defaults)


class TestRedactSecretKeys:
    def test_redacts_api_key_at_top_level(self):
        result = _redact_secret_keys({"api_key": "sk-xyz", "model": "claude"})
        assert result == {"api_key": "[redacted]", "model": "claude"}

    def test_redacts_nested_secrets_in_arguments(self):
        result = _redact_secret_keys(
            {
                "name": "run_agent",
                "arguments": {
                    "auth_token": "eyJ...",
                    "input": {"foo": "bar"},
                },
            }
        )
        assert result["arguments"]["auth_token"] == "[redacted]"
        assert result["arguments"]["input"] == {"foo": "bar"}

    def test_passes_through_non_secret_strings(self):
        result = _redact_secret_keys({"prompt": "hello"})
        assert result == {"prompt": "hello"}

    def test_matches_case_insensitively(self):
        result = _redact_secret_keys({"API_KEY": "x", "Cookie": "y"})
        assert result == {"API_KEY": "[redacted]", "Cookie": "[redacted]"}

    def test_redacts_password_variants(self):
        result = _redact_secret_keys({"password": "p", "passwd": "p2"})
        assert result == {"password": "[redacted]", "passwd": "[redacted]"}

    def test_walks_lists(self):
        result = _redact_secret_keys(
            [{"secret": "x"}, {"name": "y"}],
        )
        assert result == [{"secret": "[redacted]"}, {"name": "y"}]

    def test_leaves_non_string_secret_values_intact(self):
        # Non-string values aren't redacted — only string leaves at
        # secret-shaped keys get the [redacted] sentinel.
        result = _redact_secret_keys({"token": 42})
        assert result == {"token": 42}

    def test_does_not_mutate_input(self):
        original = {"api_key": "sk-1"}
        _redact_secret_keys(original)
        assert original == {"api_key": "sk-1"}


class TestSanitizeChatMessage:
    def test_drops_refusal_and_metadata(self):
        msg = _msg(
            role="assistant",
            content="reply",
            refusal="I cannot do that",
            metadata={"file_ids": ["secret-file"], "model": "claude"},
        )
        sanitized = sanitize_chat_message(msg)
        # SharedChatMessage has no refusal/metadata fields, so they
        # cannot be exposed via the public payload.
        dumped = sanitized.model_dump()
        assert "refusal" not in dumped
        assert "metadata" not in dumped

    def test_redacts_tool_call_arguments(self):
        msg = _msg(
            role="assistant",
            tool_calls=[
                {
                    "id": "call_1",
                    "function": {
                        "name": "fetch",
                        "arguments": {
                            "url": "https://example.com",
                            "api_key": "sk-xyz",
                        },
                    },
                }
            ],
        )
        sanitized = sanitize_chat_message(msg)
        assert sanitized.tool_calls is not None
        args = sanitized.tool_calls[0]["function"]["arguments"]
        assert args["api_key"] == "[redacted]"
        assert args["url"] == "https://example.com"

    def test_strips_injected_context_from_user_messages(self):
        # Real injected contexts use a ``\n\n`` separator between the
        # closing tag and the user's actual text — the regex in
        # ``strip_injected_context_for_display`` requires that to anchor
        # the leading-block match.  This mirrors the production format.
        msg = _msg(
            role="user",
            content="<memory_context>secret</memory_context>\n\nhello",
        )
        sanitized = sanitize_chat_message(msg)
        assert sanitized.content is not None
        assert "secret" not in sanitized.content
        assert "hello" in sanitized.content

    def test_assistant_content_passes_through_unchanged(self):
        # The stripper is intentionally user-only — assistant content
        # may legitimately reference these tags in narrative form.
        msg = _msg(role="assistant", content="see <memory_context> docs")
        sanitized = sanitize_chat_message(msg)
        assert sanitized.content == "see <memory_context> docs"

    def test_no_tool_calls_yields_none(self):
        msg = _msg(role="assistant", content="hi")
        sanitized = sanitize_chat_message(msg)
        assert sanitized.tool_calls is None
