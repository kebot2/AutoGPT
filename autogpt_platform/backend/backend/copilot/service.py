"""CoPilot service — shared helpers used by both SDK and baseline paths.

This module contains:
- System prompt building (Langfuse + static fallback, cache-optimised)
- User context injection (prepends <user_context> to first user message)
- Session title generation
- Session assignment
- Shared config and client instances
"""

import asyncio
import logging
import re
from typing import Any

from langfuse import get_client
from langfuse.openai import (
    AsyncOpenAI as LangfuseAsyncOpenAI,  # pyright: ignore[reportPrivateImportUsage]
)

from backend.data.db_accessors import chat_db, understanding_db
from backend.data.understanding import (
    BusinessUnderstanding,
    format_understanding_for_prompt,
)
from backend.util.exceptions import NotAuthorizedError, NotFoundError
from backend.util.settings import AppEnvironment, Settings

from .config import ChatConfig
from .model import (
    ChatMessage,
    ChatSessionInfo,
    get_chat_session,
    update_session_title,
    upsert_chat_session,
)

logger = logging.getLogger(__name__)

config = ChatConfig()
settings = Settings()

_client: LangfuseAsyncOpenAI | None = None
_langfuse = None


def _get_openai_client() -> LangfuseAsyncOpenAI:
    global _client
    if _client is None:
        _client = LangfuseAsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
    return _client


def _get_langfuse():
    global _langfuse
    if _langfuse is None:
        _langfuse = get_client()
    return _langfuse


# Shared constant for the XML tag name used to wrap per-user context when
# injecting it into the first user message. Referenced by both the cacheable
# system prompt (so the LLM knows to parse it) and inject_user_context()
# (which writes the tag). Keeping both in sync prevents drift.
USER_CONTEXT_TAG = "user_context"

# Static system prompt for token caching — identical for all users.
# User-specific context is injected into the first user message instead,
# so the system prompt never changes and can be cached across all sessions.
#
# NOTE: This constant is part of the module's public API — it is imported by
# sdk/service.py, baseline/service.py, dry_run_loop_test.py, and
# prompt_cache_test.py. The leading underscore is retained for backwards
# compatibility; CACHEABLE_SYSTEM_PROMPT is exported as the public alias.
_CACHEABLE_SYSTEM_PROMPT = f"""You are an AI automation assistant helping users build and run automations.

Your goal is to help users automate tasks by:
- Understanding their needs and business context
- Building and running working automations
- Delivering tangible value through action, not just explanation

Be concise, proactive, and action-oriented. Bias toward showing working solutions over lengthy explanations.

When the user provides a <{USER_CONTEXT_TAG}> block in their message, use it to personalise your responses.
For users you are meeting for the first time with no context provided, greet them warmly and introduce them to the AutoGPT platform."""

# Public alias for the cacheable system prompt constant. New callers should
# prefer this name; the underscored original remains for existing imports.
CACHEABLE_SYSTEM_PROMPT = _CACHEABLE_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# user_context prefix helpers
# ---------------------------------------------------------------------------
#
# These two helpers are the *single source of truth* for the on-the-wire format
# of the injected `<user_context>` block. `inject_user_context()` writes via
# `format_user_context_prefix()`; the chat-history GET endpoint reads via
# `strip_user_context_prefix()`. Keeping both behind a shared format prevents
# silent drift between the writer and the reader.

# Matches a `<user_context>...</user_context>` block at the very start of a
# message followed by exactly the `\n\n` separator that the formatter writes.
# `re.DOTALL` lets `.*?` span newlines; the leading `^` keeps embedded literal
# blocks later in the message untouched.
_USER_CONTEXT_PREFIX_RE = re.compile(
    rf"^<{USER_CONTEXT_TAG}>.*?</{USER_CONTEXT_TAG}>\n\n", re.DOTALL
)

# Matches *any* occurrence of a `<user_context>...</user_context>` block,
# anywhere in the string. Used to defensively strip user-supplied tags from
# untrusted input before re-injecting the trusted prefix.
_USER_CONTEXT_ANYWHERE_RE = re.compile(
    rf"<{USER_CONTEXT_TAG}>.*?</{USER_CONTEXT_TAG}>\s*", re.DOTALL
)


def _sanitize_user_context_field(value: str) -> str:
    """Escape any characters that would let user-controlled text break out of
    the `<user_context>` block.

    The injection format wraps free-text fields in literal XML tags. If a
    user-controlled field contains the literal string `</user_context>` (or
    even just `<` / `>`), it can terminate the trusted block prematurely and
    smuggle instructions into the LLM's view as if they were out-of-band
    content. We replace `<` / `>` with their HTML entities so the LLM still
    reads the original characters but the parser-visible XML structure stays
    intact.
    """
    return value.replace("<", "&lt;").replace(">", "&gt;")


def format_user_context_prefix(formatted_understanding: str) -> str:
    """Wrap a pre-formatted understanding string in a `<user_context>` block.

    The input must already have been sanitised (callers should pipe
    `format_understanding_for_prompt()` output through
    `_sanitize_user_context_field()`). The output is the exact byte sequence
    `inject_user_context()` prepends to the first user message and the same
    sequence `strip_user_context_prefix()` is built to remove.
    """
    return f"<{USER_CONTEXT_TAG}>\n{formatted_understanding}\n</{USER_CONTEXT_TAG}>\n\n"


def strip_user_context_prefix(content: str) -> str:
    """Remove a leading `<user_context>...</user_context>\\n\\n` block, if any.

    Only the prefix at the very start of the message is stripped; embedded
    `<user_context>` strings later in the message are intentionally preserved.
    """
    return _USER_CONTEXT_PREFIX_RE.sub("", content)


# ---------------------------------------------------------------------------
# Shared helpers (used by SDK service and baseline)
# ---------------------------------------------------------------------------


def _is_langfuse_configured() -> bool:
    """Check if Langfuse credentials are configured."""
    return bool(
        settings.secrets.langfuse_public_key and settings.secrets.langfuse_secret_key
    )


async def _fetch_langfuse_prompt() -> str | None:
    """Fetch the static system prompt from Langfuse.

    Returns the compiled prompt string, or None if Langfuse is unconfigured
    or the fetch fails. Passes an empty users_information placeholder so the
    prompt text is identical across all users (enabling cross-session caching).
    """
    if not _is_langfuse_configured():
        return None
    try:
        label = (
            None if settings.config.app_env == AppEnvironment.PRODUCTION else "latest"
        )
        prompt = await asyncio.to_thread(
            _get_langfuse().get_prompt,
            config.langfuse_prompt_name,
            label=label,
            cache_ttl_seconds=config.langfuse_prompt_cache_ttl,
        )
        return prompt.compile(users_information="")
    except Exception as e:
        logger.warning(f"Failed to fetch prompt from Langfuse, using default: {e}")
        return None


async def _build_system_prompt(
    user_id: str | None,
) -> tuple[str, BusinessUnderstanding | None]:
    """Build a fully static system prompt suitable for LLM token caching.

    User-specific context is NOT embedded here. Callers must inject the
    returned understanding into the first user message via inject_user_context()
    so the system prompt stays identical across all users and sessions,
    enabling cross-session cache hits.

    Returns:
        Tuple of (static_prompt, understanding_object_or_None)
    """
    understanding: BusinessUnderstanding | None = None
    if user_id:
        try:
            understanding = await understanding_db().get_business_understanding(user_id)
        except Exception as e:
            logger.warning(f"Failed to fetch business understanding: {e}")

    prompt = await _fetch_langfuse_prompt() or _CACHEABLE_SYSTEM_PROMPT
    return prompt, understanding


async def inject_user_context(
    understanding: BusinessUnderstanding | None,
    message: str,
    session_id: str,
    session_messages: list[ChatMessage],
) -> str | None:
    """Prepend a <user_context> block to the first user message.

    Updates the in-memory session_messages list and persists the prefixed
    content to the DB so resumed sessions and page reloads retain
    personalisation.

    Untrusted input — both the user-supplied ``message`` and the user-owned
    fields inside ``understanding`` — are stripped/escaped before being placed
    inside the trusted ``<user_context>`` block. This prevents a user from
    spoofing their own (or another user's) personalisation context by
    supplying a literal `<user_context>...</user_context>` tag in the message
    body or in any of their understanding fields.

    Idempotent: if there is no understanding to inject, the original message
    is returned unchanged and no DB write is issued.

    Returns:
        The prefixed message string, or None if no user message was found.
    """
    # Defence-in-depth: scrub any user-supplied <user_context> blocks from the
    # incoming message before we re-wrap it. Without this, a user can either
    # (a) suppress the trusted injection by typing the tag themselves, since
    # the inject would otherwise see "already prefixed" and skip, or
    # (b) spoof a different personalization to bias the LLM. Strip the tag
    # everywhere it appears so the trusted prefix is always the only one.
    sanitized_message = _USER_CONTEXT_ANYWHERE_RE.sub("", message)

    if understanding is None:
        return None

    raw_ctx = format_understanding_for_prompt(understanding)
    user_ctx = _sanitize_user_context_field(raw_ctx)
    prefixed = format_user_context_prefix(user_ctx) + sanitized_message
    for session_msg in session_messages:
        if session_msg.role == "user":
            session_msg.content = prefixed
            if session_msg.sequence is not None:
                await chat_db().update_message_content_by_sequence(
                    session_id, session_msg.sequence, prefixed
                )
            else:
                logger.warning(
                    f"[inject_user_context] Cannot persist user context for session "
                    f"{session_id}: first user message has no sequence number"
                )
            return prefixed
    return None


async def _generate_session_title(
    message: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> str | None:
    """Generate a concise title for a chat session based on the first message.

    Args:
        message: The first user message in the session
        user_id: User ID for OpenRouter tracing (optional)
        session_id: Session ID for OpenRouter tracing (optional)

    Returns:
        A short title (3-6 words) or None if generation fails
    """
    try:
        # Build extra_body for OpenRouter tracing and PostHog analytics
        extra_body: dict[str, Any] = {}
        if user_id:
            extra_body["user"] = user_id[:128]  # OpenRouter limit
            extra_body["posthogDistinctId"] = user_id
        if session_id:
            extra_body["session_id"] = session_id[:128]  # OpenRouter limit
        extra_body["posthogProperties"] = {
            "environment": settings.config.app_env.value,
        }

        response = await _get_openai_client().chat.completions.create(
            model=config.title_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Generate a very short title (3-6 words) for a chat conversation "
                        "based on the user's first message. The title should capture the "
                        "main topic or intent. Return ONLY the title, no quotes or punctuation."
                    ),
                },
                {"role": "user", "content": message[:500]},  # Limit input length
            ],
            max_tokens=20,
            extra_body=extra_body,
        )
        title = response.choices[0].message.content
        if title:
            # Clean up the title
            title = title.strip().strip("\"'")
            # Limit length
            if len(title) > 50:
                title = title[:47] + "..."
            return title
        return None
    except Exception as e:
        logger.warning(f"Failed to generate session title: {e}")
        return None


async def _update_title_async(
    session_id: str, message: str, user_id: str | None = None
) -> None:
    """Generate and persist a session title in the background.

    Shared by both the SDK and baseline execution paths.
    """
    try:
        title = await _generate_session_title(message, user_id, session_id)
        if title and user_id:
            await update_session_title(session_id, user_id, title, only_if_empty=True)
            logger.debug("Generated title for session %s", session_id)
    except Exception as e:
        logger.warning("Failed to update session title for %s: %s", session_id, e)


async def assign_user_to_session(
    session_id: str,
    user_id: str,
) -> ChatSessionInfo:
    """
    Assign a user to a chat session.
    """
    session = await get_chat_session(session_id, None)
    if not session:
        raise NotFoundError(f"Session {session_id} not found")
    if session.user_id is not None and session.user_id != user_id:
        logger.warning(
            f"[SECURITY] Attempt to claim session {session_id} by user {user_id}, "
            f"but it already belongs to user {session.user_id}"
        )
        raise NotAuthorizedError(f"Not authorized to claim session {session_id}")
    session.user_id = user_id
    session = await upsert_chat_session(session)
    return session
