"""Unit tests for the Anthropic rate card used in direct-mode cost computation."""

from .anthropic_rate_card import compute_anthropic_cost_usd


class TestComputeAnthropicCostUsd:
    def test_sonnet_basic(self):
        # 1M prompt × $3 + 1M completion × $15 = $18
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert cost == 18.0

    def test_opus_basic(self):
        # 1M prompt × $15 + 1M completion × $75 = $90
        cost = compute_anthropic_cost_usd(
            model="claude-opus-4-7",
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
        )
        assert cost == 90.0

    def test_cache_read_at_one_tenth_input_rate(self):
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_tokens=1_000_000,
        )
        # 1M tokens × $3 × 0.1 = $0.30
        assert cost == 0.3

    def test_cache_write_at_two_times_input_rate(self):
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=0,
            completion_tokens=0,
            cache_creation_tokens=1_000_000,
        )
        # Default cache_ttl="1h": 1M tokens × $3 × 2.0 = $6
        assert cost == 6.0

    def test_cache_write_5m_ttl_uses_smaller_multiplier(self):
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=0,
            completion_tokens=0,
            cache_creation_tokens=1_000_000,
            cache_ttl="5m",
        )
        # 5m TTL: 1M tokens × $3 × 1.25 = $3.75
        assert cost == 3.75

    def test_cache_write_unknown_ttl_falls_back_to_1h(self):
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=0,
            completion_tokens=0,
            cache_creation_tokens=1_000_000,
            cache_ttl="24h",
        )
        # Unknown TTL → 1h multiplier (over-bills rather than mis-bills).
        assert cost == 6.0

    def test_unknown_model_returns_none(self):
        # Caller decides between recording 0 or skipping; None signals
        # the misconfiguration upstream.
        assert (
            compute_anthropic_cost_usd(
                model="claude-future-7-5",
                prompt_tokens=1000,
                completion_tokens=1000,
            )
            is None
        )

    def test_zero_tokens_zero_cost(self):
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=0,
            completion_tokens=0,
        )
        assert cost == 0.0

    def test_negative_tokens_clamped_to_zero(self):
        # Malformed upstream returning negative counts must not flip
        # the sign of the recorded cost.
        cost = compute_anthropic_cost_usd(
            model="claude-sonnet-4-6",
            prompt_tokens=-100,
            completion_tokens=-50,
            cache_read_tokens=-10,
            cache_creation_tokens=-20,
        )
        assert cost == 0.0
