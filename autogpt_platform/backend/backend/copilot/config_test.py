"""Unit tests for ChatConfig."""

import pytest

from .config import ChatConfig

# Env vars that the ChatConfig validators read — must be cleared so they don't
# override the explicit constructor values we pass in each test.
_ENV_VARS_TO_CLEAR = (
    "CHAT_USE_E2B_SANDBOX",
    "CHAT_E2B_API_KEY",
    "E2B_API_KEY",
    "CHAT_USE_OPENROUTER",
    "CHAT_API_KEY",
    "OPEN_ROUTER_API_KEY",
    "OPENAI_API_KEY",
    "CHAT_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENAI_BASE_URL",
    "CHAT_CLAUDE_AGENT_CLI_PATH",
    "CLAUDE_AGENT_CLI_PATH",
    "CHAT_CLAUDE_AGENT_USE_COMPAT_PROXY",
    "CLAUDE_AGENT_USE_COMPAT_PROXY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(var, raising=False)


class TestOpenrouterActive:
    """Tests for the openrouter_active property."""

    def test_enabled_with_credentials_returns_true(self):
        cfg = ChatConfig(
            use_openrouter=True,
            api_key="or-key",
            base_url="https://openrouter.ai/api/v1",
        )
        assert cfg.openrouter_active is True

    def test_enabled_but_missing_api_key_returns_false(self):
        cfg = ChatConfig(
            use_openrouter=True,
            api_key=None,
            base_url="https://openrouter.ai/api/v1",
        )
        assert cfg.openrouter_active is False

    def test_disabled_returns_false_despite_credentials(self):
        cfg = ChatConfig(
            use_openrouter=False,
            api_key="or-key",
            base_url="https://openrouter.ai/api/v1",
        )
        assert cfg.openrouter_active is False

    def test_strips_v1_suffix_and_still_valid(self):
        cfg = ChatConfig(
            use_openrouter=True,
            api_key="or-key",
            base_url="https://openrouter.ai/api/v1",
        )
        assert cfg.openrouter_active is True

    def test_invalid_base_url_returns_false(self):
        cfg = ChatConfig(
            use_openrouter=True,
            api_key="or-key",
            base_url="not-a-url",
        )
        assert cfg.openrouter_active is False


class TestE2BActive:
    """Tests for the e2b_active property — single source of truth for E2B usage."""

    def test_both_enabled_and_key_present_returns_true(self):
        """e2b_active is True when use_e2b_sandbox=True and e2b_api_key is set."""
        cfg = ChatConfig(use_e2b_sandbox=True, e2b_api_key="test-key")
        assert cfg.e2b_active is True

    def test_enabled_but_missing_key_returns_false(self):
        """e2b_active is False when use_e2b_sandbox=True but e2b_api_key is absent."""
        cfg = ChatConfig(use_e2b_sandbox=True, e2b_api_key=None)
        assert cfg.e2b_active is False

    def test_disabled_returns_false(self):
        """e2b_active is False when use_e2b_sandbox=False regardless of key."""
        cfg = ChatConfig(use_e2b_sandbox=False, e2b_api_key="test-key")
        assert cfg.e2b_active is False


class TestClaudeAgentCliPathEnvFallback:
    """``claude_agent_cli_path`` accepts both the Pydantic-prefixed
    ``CHAT_CLAUDE_AGENT_CLI_PATH`` env var and the unprefixed
    ``CLAUDE_AGENT_CLI_PATH`` form (mirrors ``api_key`` / ``base_url``).
    """

    def test_prefixed_env_var_is_picked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAT_CLAUDE_AGENT_CLI_PATH", "/opt/claude-prefixed")
        cfg = ChatConfig()
        assert cfg.claude_agent_cli_path == "/opt/claude-prefixed"

    def test_unprefixed_env_var_is_picked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_AGENT_CLI_PATH", "/opt/claude-unprefixed")
        cfg = ChatConfig()
        assert cfg.claude_agent_cli_path == "/opt/claude-unprefixed"

    def test_prefixed_wins_over_unprefixed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAT_CLAUDE_AGENT_CLI_PATH", "/opt/claude-prefixed")
        monkeypatch.setenv("CLAUDE_AGENT_CLI_PATH", "/opt/claude-unprefixed")
        cfg = ChatConfig()
        assert cfg.claude_agent_cli_path == "/opt/claude-prefixed"

    def test_no_env_var_defaults_to_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = ChatConfig()
        assert cfg.claude_agent_cli_path is None


class TestClaudeAgentUseCompatProxyEnvFallback:
    """``claude_agent_use_compat_proxy`` accepts both the Pydantic-
    prefixed ``CHAT_CLAUDE_AGENT_USE_COMPAT_PROXY`` env var and the
    unprefixed ``CLAUDE_AGENT_USE_COMPAT_PROXY`` form.  Regression
    guard for the bool-default pitfall: the field has a non-None
    default (``False``), so Pydantic passes the default into the
    validator when no value is provided and a naive ``if v is None``
    check would never fire.
    """

    def test_prefixed_env_var_enables_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CHAT_CLAUDE_AGENT_USE_COMPAT_PROXY", "true")
        cfg = ChatConfig()
        assert cfg.claude_agent_use_compat_proxy is True

    def test_unprefixed_env_var_enables_proxy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_AGENT_USE_COMPAT_PROXY", "true")
        cfg = ChatConfig()
        assert cfg.claude_agent_use_compat_proxy is True

    def test_unprefixed_env_var_respects_falsy_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_AGENT_USE_COMPAT_PROXY", "false")
        cfg = ChatConfig()
        assert cfg.claude_agent_use_compat_proxy is False

    def test_prefixed_wins_over_unprefixed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When both are set, the Pydantic-prefixed var is authoritative
        so the validator doesn't silently clobber an explicit
        ``CHAT_...=false`` with an unprefixed ``=true``."""
        monkeypatch.setenv("CHAT_CLAUDE_AGENT_USE_COMPAT_PROXY", "false")
        monkeypatch.setenv("CLAUDE_AGENT_USE_COMPAT_PROXY", "true")
        cfg = ChatConfig()
        assert cfg.claude_agent_use_compat_proxy is False

    def test_no_env_var_uses_field_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = ChatConfig()
        # Default is False on this branch; the dev-preview branch
        # flips it to True but that's a separate PR.
        assert cfg.claude_agent_use_compat_proxy is False
