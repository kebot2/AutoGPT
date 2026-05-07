"""Static Anthropic rate card for direct-mode cost computation.

Used by the baseline path when ``CHAT_USE_OPENROUTER=false`` — the
OpenAI-compat endpoint at api.anthropic.com does **not** return a
``usage.cost`` field (that is an OpenRouter extension), so we compute
USD from token counts × rates here.

Rates are USD per 1M tokens, sourced from
https://www.anthropic.com/pricing (Sonnet 4.6 and Opus 4.7 — the two
models the SDK / baseline configurations resolve to today).  Cache
write/read multipliers follow Anthropic's prompt-caching docs:
https://docs.claude.com/en/docs/build-with-claude/prompt-caching.
"""

from __future__ import annotations

# Per-million-token rates in USD.  Keep keyed on the **post-normalize**
# slug (no ``anthropic/`` prefix, dots → hyphens) since
# ``normalize_model_for_transport`` runs upstream.
_INPUT_USD_PER_MTOK: dict[str, float] = {
    "claude-sonnet-4-5": 3.0,
    "claude-sonnet-4-6": 3.0,
    "claude-opus-4-6": 15.0,
    "claude-opus-4-7": 15.0,
    "claude-haiku-4-5": 1.0,
}
_OUTPUT_USD_PER_MTOK: dict[str, float] = {
    "claude-sonnet-4-5": 15.0,
    "claude-sonnet-4-6": 15.0,
    "claude-opus-4-6": 75.0,
    "claude-opus-4-7": 75.0,
    "claude-haiku-4-5": 5.0,
}

# Cache-write surcharge multipliers (applied to the input rate).  We
# default to the 1h TTL multiplier because ``baseline_prompt_cache_ttl``
# defaults to ``"1h"`` and the system prompt + tools array (the only
# cached prefix) is identical across users in our workspace, so 1h
# cross-user reads amortise the higher write cost.
_CACHE_WRITE_MULTIPLIER = 2.0  # 1h TTL; 5m TTL would be 1.25
_CACHE_READ_MULTIPLIER = 0.1


def compute_anthropic_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float | None:
    """Return the USD cost for an Anthropic-direct chat completion.

    ``prompt_tokens`` is the OpenAI-compat top-level number, which on
    Anthropic's compat endpoint **excludes** cached and cache-write
    tokens (those land in the prompt-tokens-details breakdown).  So the
    formula sums them as separate buckets at their own rates rather
    than double-counting against ``prompt_tokens``.

    Returns ``None`` for unknown models so the caller can decide
    between recording 0 (under-bills) or skipping the row (silent
    miss).  We pick None to surface the misconfiguration upstream.
    """
    input_rate = _INPUT_USD_PER_MTOK.get(model)
    output_rate = _OUTPUT_USD_PER_MTOK.get(model)
    if input_rate is None or output_rate is None:
        return None
    fresh_input_cost = prompt_tokens * input_rate / 1_000_000
    output_cost = completion_tokens * output_rate / 1_000_000
    cache_read_cost = (
        cache_read_tokens * input_rate * _CACHE_READ_MULTIPLIER / 1_000_000
    )
    cache_write_cost = (
        cache_creation_tokens * input_rate * _CACHE_WRITE_MULTIPLIER / 1_000_000
    )
    return fresh_input_cost + output_cost + cache_read_cost + cache_write_cost
