"""Tests for agent generation guide — verifies clarification section."""

import importlib
from pathlib import Path

from backend.copilot import prompting


class TestGetSdkSupplementStaticPlaceholder:
    """get_sdk_supplement must return a static string so the system prompt is
    identical for all users and sessions, enabling cross-user prompt-cache hits.
    """

    def setup_method(self):
        # Reset the module-level singleton before each test so tests are isolated.
        importlib.reload(prompting)

    def test_local_mode_uses_placeholder_not_uuid(self):
        result = prompting.get_sdk_supplement(use_e2b=False)
        assert "/tmp/copilot-<session-id>" in result

    def test_local_mode_is_idempotent(self):
        first = prompting.get_sdk_supplement(use_e2b=False)
        second = prompting.get_sdk_supplement(use_e2b=False)
        assert first == second, "Supplement must be identical across calls"

    def test_e2b_mode_uses_home_user(self):
        result = prompting.get_sdk_supplement(use_e2b=True)
        assert "/home/user" in result

    def test_e2b_mode_has_no_session_placeholder(self):
        result = prompting.get_sdk_supplement(use_e2b=True)
        assert "<session-id>" not in result


class TestAgentGenerationGuideContainsClarifySection:
    """The agent generation guide must include the clarification section."""

    def test_guide_includes_clarify_section(self):
        guide_path = Path(__file__).parent / "sdk" / "agent_generation_guide.md"
        content = guide_path.read_text(encoding="utf-8")
        assert "Before or During Building" in content

    def test_guide_mentions_find_block_for_clarification(self):
        guide_path = Path(__file__).parent / "sdk" / "agent_generation_guide.md"
        content = guide_path.read_text(encoding="utf-8")
        clarify_section = content.split("Before or During Building")[1].split(
            "### Workflow"
        )[0]
        assert "find_block" in clarify_section

    def test_guide_mentions_ask_question_tool(self):
        guide_path = Path(__file__).parent / "sdk" / "agent_generation_guide.md"
        content = guide_path.read_text(encoding="utf-8")
        clarify_section = content.split("Before or During Building")[1].split(
            "### Workflow"
        )[0]
        assert "ask_question" in clarify_section


class TestBaselineWebSearchSupplement:
    """The fast-mode web-search supplement must point at block IDs that
    actually exist and name each block's required input fields, so the
    Kimi / baseline model can call them via ``run_block`` without a
    ``find_block`` round-trip.  Pinning the block IDs against the live
    registry means a block rename / delete breaks this test rather than
    shipping a dead UUID to the model."""

    def test_perplexity_block_id_matches_registered_block(self):
        from backend.blocks.perplexity import PerplexityBlock

        assert PerplexityBlock().id == prompting.PERPLEXITY_BLOCK_ID

    def test_send_web_request_block_id_matches_registered_block(self):
        from backend.blocks.http import SendWebRequestBlock

        assert SendWebRequestBlock().id == prompting.SEND_WEB_REQUEST_BLOCK_ID

    def test_supplement_surfaces_both_block_ids(self):
        text = prompting.get_baseline_web_search_supplement()
        assert prompting.PERPLEXITY_BLOCK_ID in text
        assert prompting.SEND_WEB_REQUEST_BLOCK_ID in text

    def test_supplement_names_required_inputs(self):
        text = prompting.get_baseline_web_search_supplement()
        # Perplexity required input.
        assert '"prompt"' in text
        # SendWebRequest required input.
        assert '"url"' in text
        # Default Perplexity model is named explicitly so Kimi doesn't
        # guess (``sonar-xl`` etc. 404 on the Perplexity API).
        assert '"sonar"' in text

    def test_supplement_flags_credentials_dependency(self):
        text = prompting.get_baseline_web_search_supplement()
        assert "credentials" in text.lower()
        assert "connect_integration" in text
