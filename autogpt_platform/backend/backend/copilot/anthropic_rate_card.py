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

# Cache-write surcharge multipliers (applied to the input rate) keyed
# by TTL.  Anthropic publishes only two TTLs: 5m (1.25× input) and 1h
# (2× input) — see prompt-caching docs.  The active TTL is the
# ``baseline_prompt_cache_ttl`` config value, threaded in by the caller
# rather than read here so this module stays config-free for testing.
_CACHE_WRITE_MULTIPLIER_BY_TTL: dict[str, float] = {
    "5m": 1.25,
    "1h": 2.0,
}
_CACHE_READ_MULTIPLIER = 0.1


def compute_anthropic_cost_usd(
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_ttl: str = "1h",
) -> float | None:
    """Return the USD cost for an Anthropic-direct chat completion.

    ``prompt_tokens`` is the OpenAI-compat top-level total — on
    Anthropic's compat endpoint it **includes** cached + cache-write
    tokens (matching the OpenAI spec, where the cached subset lives
    in ``prompt_tokens_details``).  We subtract those buckets out
    before computing the fresh-input cost so each token is billed
    exactly once at its correct rate.  If the upstream over-reports
    the breakdown (cached + write > total), the fresh-input bucket is
    clamped to zero to avoid flipping the sign.

    *cache_ttl* selects the cache-write surcharge multiplier — must
    match the ``cache_control: ttl`` set on the cached blocks (today
    that's ``baseline_prompt_cache_ttl`` config; 1h default).  Unknown
    TTLs fall back to 1h with no error since over-billing is preferable
    to mis-billing in the cache write column.

    Returns ``None`` for unknown models so the caller can decide
    between recording 0 (under-bills) or skipping the row (silent
    miss).  We pick None to surface the misconfiguration upstream.
    """
    input_rate = _INPUT_USD_PER_MTOK.get(model)
    output_rate = _OUTPUT_USD_PER_MTOK.get(model)
    if input_rate is None or output_rate is None:
        return None
    # Clamp each token bucket to ``>= 0`` per the codebase convention —
    # a malformed upstream that reports a negative count must not flip
    # the sign of the recorded cost (which would skew rate-limit and
    # billing accounting).
    prompt_tokens = max(0, prompt_tokens)
    completion_tokens = max(0, completion_tokens)
    cache_read_tokens = max(0, cache_read_tokens)
    cache_creation_tokens = max(0, cache_creation_tokens)
    fresh_input_tokens = max(
        0, prompt_tokens - cache_read_tokens - cache_creation_tokens
    )
    cache_write_multiplier = _CACHE_WRITE_MULTIPLIER_BY_TTL.get(cache_ttl, 2.0)
    fresh_input_cost = fresh_input_tokens * input_rate / 1_000_000
    output_cost = completion_tokens * output_rate / 1_000_000
    cache_read_cost = (
        cache_read_tokens * input_rate * _CACHE_READ_MULTIPLIER / 1_000_000
    )
    cache_write_cost = (
        cache_creation_tokens * input_rate * cache_write_multiplier / 1_000_000
    )
    return fresh_input_cost + output_cost + cache_read_cost + cache_write_cost
