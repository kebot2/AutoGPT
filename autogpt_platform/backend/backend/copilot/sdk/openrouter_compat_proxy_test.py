"""Tests for the OpenRouter compatibility proxy.

The proxy strips two known forbidden patterns from requests so newer
``claude-agent-sdk`` / Claude Code CLI versions can talk to OpenRouter
through the unchanged transport. These tests cover both:

* the pure stripping helpers (deterministic, no I/O), and
* the end-to-end proxy behaviour against a fake upstream server, so we
  catch hop-by-hop header bugs and streaming regressions.

See ``openrouter_compat_proxy.py`` for the rationale and the upstream
issues being worked around.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp
import pytest
from aiohttp import web

from backend.copilot.sdk.openrouter_compat_proxy import (
    _FORBIDDEN_BETA_TOKENS,
    _HOP_BY_HOP_HEADERS,
    OpenRouterCompatProxy,
    clean_request_body_bytes,
    clean_request_headers,
    strip_forbidden_anthropic_beta_header,
    strip_forbidden_betas_from_body,
    strip_tool_reference_blocks,
)

# ---------------------------------------------------------------------------
# strip_tool_reference_blocks
# ---------------------------------------------------------------------------


class TestStripToolReferenceBlocks:
    """The CLI's built-in ToolSearch tool emits ``tool_reference``
    content blocks in ``tool_result.content``. OpenRouter's stricter
    Zod validation rejects them. We drop them entirely — they're
    metadata about which tools were searched, not real model-visible
    content."""

    def test_removes_tool_reference_block_at_top_level(self):
        block = {"type": "tool_reference", "tool_name": "find_block"}
        assert strip_tool_reference_blocks(block) is None

    def test_removes_tool_reference_block_from_list(self):
        blocks = [
            {"type": "text", "text": "hello"},
            {"type": "tool_reference", "tool_name": "find_block"},
            {"type": "text", "text": "world"},
        ]
        assert strip_tool_reference_blocks(blocks) == [
            {"type": "text", "text": "hello"},
            {"type": "text", "text": "world"},
        ]

    def test_strips_nested_tool_reference_inside_tool_result(self):
        # The exact shape PR #12294 root-caused: tool_result.content
        # contains the tool_reference block.
        request = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": [
                                {"type": "text", "text": "result text"},
                                {
                                    "type": "tool_reference",
                                    "tool_name": "mcp__copilot__find_block",
                                },
                            ],
                        }
                    ],
                }
            ]
        }
        cleaned = strip_tool_reference_blocks(request)
        tool_result_content = cleaned["messages"][0]["content"][0]["content"]
        assert tool_result_content == [{"type": "text", "text": "result text"}]

    def test_preserves_unrelated_payloads(self):
        payload = {
            "model": "claude-opus-4.6",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.7,
        }
        assert strip_tool_reference_blocks(payload) == payload

    def test_handles_empty_and_primitive_inputs(self):
        assert strip_tool_reference_blocks({}) == {}
        assert strip_tool_reference_blocks([]) == []
        assert strip_tool_reference_blocks("plain string") == "plain string"
        assert strip_tool_reference_blocks(42) == 42
        assert strip_tool_reference_blocks(None) is None

    def test_removes_dict_valued_tool_reference_child_entirely(self):
        # Regression guard: when a tool_reference dict is assigned to
        # a key rather than listed, the helper used to rewrite it to
        # `null` (leaving the parent key with a None value). That is
        # still schema-invalid upstream — remove the key entirely.
        payload = {
            "wrapper": {"type": "tool_reference", "tool_name": "find_block"},
            "keep": "value",
        }
        cleaned = strip_tool_reference_blocks(payload)
        assert "wrapper" not in cleaned
        assert cleaned["keep"] == "value"

    def test_preserves_genuine_none_values_on_non_dict_children(self):
        payload = {"explicit_null": None, "text": "ok"}
        cleaned = strip_tool_reference_blocks(payload)
        assert cleaned == {"explicit_null": None, "text": "ok"}


# ---------------------------------------------------------------------------
# strip_forbidden_betas_from_body
# ---------------------------------------------------------------------------


class TestStripForbiddenBetasFromBody:
    """OpenRouter rejects ``context-management-2025-06-27`` in the
    request body's ``betas`` array."""

    def test_removes_forbidden_token_keeps_others(self):
        body = {
            "model": "claude-opus-4.6",
            "betas": [
                "context-management-2025-06-27",
                "fine-grained-tool-streaming-2025",
            ],
        }
        cleaned = strip_forbidden_betas_from_body(body)
        assert cleaned["betas"] == ["fine-grained-tool-streaming-2025"]

    def test_removes_betas_field_entirely_when_only_forbidden(self):
        body = {"model": "x", "betas": ["context-management-2025-06-27"]}
        cleaned = strip_forbidden_betas_from_body(body)
        assert "betas" not in cleaned

    def test_no_op_when_no_betas_field(self):
        body = {"model": "x"}
        assert strip_forbidden_betas_from_body(body) == {"model": "x"}

    def test_no_op_on_non_dict(self):
        assert strip_forbidden_betas_from_body([1, 2, 3]) == [1, 2, 3]
        assert strip_forbidden_betas_from_body("plain") == "plain"

    def test_all_forbidden_tokens_constants_are_recognized(self):
        for forbidden in _FORBIDDEN_BETA_TOKENS:
            body = {"betas": [forbidden, "other"]}
            cleaned = strip_forbidden_betas_from_body(body)
            assert forbidden not in cleaned["betas"]


# ---------------------------------------------------------------------------
# strip_forbidden_anthropic_beta_header
# ---------------------------------------------------------------------------


class TestStripForbiddenAnthropicBetaHeader:
    def test_removes_forbidden_token_keeps_others(self):
        value = "fine-grained-tool-streaming-2025, context-management-2025-06-27, other-beta"
        result = strip_forbidden_anthropic_beta_header(value)
        assert result == "fine-grained-tool-streaming-2025, other-beta"

    def test_returns_none_when_only_forbidden_token_present(self):
        assert (
            strip_forbidden_anthropic_beta_header("context-management-2025-06-27")
            is None
        )

    def test_passes_through_clean_header(self):
        assert strip_forbidden_anthropic_beta_header("foo, bar") == "foo, bar"

    def test_handles_empty_and_none_input(self):
        assert strip_forbidden_anthropic_beta_header("") == ""
        assert strip_forbidden_anthropic_beta_header(None) is None

    def test_handles_extra_whitespace(self):
        value = "  context-management-2025-06-27  ,  fine-grained  "
        result = strip_forbidden_anthropic_beta_header(value)
        assert result == "fine-grained"


# ---------------------------------------------------------------------------
# clean_request_body_bytes — combined body-level cleanup
# ---------------------------------------------------------------------------


class TestCleanRequestBodyBytes:
    def test_strips_both_patterns_in_one_pass(self):
        body = {
            "model": "claude-opus-4.6",
            "betas": ["context-management-2025-06-27"],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": [
                                {"type": "tool_reference", "tool_name": "find"},
                                {"type": "text", "text": "ok"},
                            ],
                        }
                    ],
                }
            ],
        }
        cleaned_bytes = clean_request_body_bytes(json.dumps(body).encode("utf-8"))
        cleaned = json.loads(cleaned_bytes.decode("utf-8"))
        assert "betas" not in cleaned  # only forbidden token, dropped
        tool_result_content = cleaned["messages"][0]["content"][0]["content"]
        assert tool_result_content == [{"type": "text", "text": "ok"}]

    def test_passes_through_non_json_body(self):
        garbage = b"\xff\xfe not json at all"
        assert clean_request_body_bytes(garbage) == garbage

    def test_passes_through_empty_body(self):
        assert clean_request_body_bytes(b"") == b""


# ---------------------------------------------------------------------------
# clean_request_headers — hop-by-hop + anthropic-beta cleanup
# ---------------------------------------------------------------------------


class TestCleanRequestHeaders:
    def test_drops_hop_by_hop_headers(self):
        headers = {
            "Host": "example.com",
            "Connection": "keep-alive",
            "Content-Length": "42",
            "Authorization": "Bearer xxx",
            "Content-Type": "application/json",
        }
        cleaned = clean_request_headers(headers)
        assert "Host" not in cleaned
        assert "Connection" not in cleaned
        assert "Content-Length" not in cleaned
        assert cleaned["Authorization"] == "Bearer xxx"
        assert cleaned["Content-Type"] == "application/json"

    def test_strips_forbidden_token_from_anthropic_beta_header(self):
        headers = {
            "anthropic-beta": "context-management-2025-06-27, other-beta",
            "Authorization": "Bearer x",
        }
        cleaned = clean_request_headers(headers)
        assert cleaned["anthropic-beta"] == "other-beta"

    def test_drops_anthropic_beta_header_when_only_forbidden(self):
        headers = {"anthropic-beta": "context-management-2025-06-27"}
        cleaned = clean_request_headers(headers)
        assert "anthropic-beta" not in cleaned

    def test_hop_by_hop_set_completeness(self):
        # Sanity check: if upstream removes hop-by-hop headers from
        # this set we want to know — keep the canonical RFC 7230 list.
        for required in (
            "connection",
            "transfer-encoding",
            "host",
            "trailer",
            "trailers",
        ):
            assert required in _HOP_BY_HOP_HEADERS

    def test_drops_headers_listed_in_connection_field(self):
        # Per RFC 7230 §6.1 intermediaries must also drop every
        # header name listed in the incoming Connection field value
        # (extension hop-by-hop headers signalled per-connection).
        headers = {
            "Connection": "X-Custom-Hop, Upgrade",
            "X-Custom-Hop": "secret-extension",
            "Authorization": "Bearer x",
            "X-Keep": "ok",
        }
        cleaned = clean_request_headers(headers)
        assert "X-Custom-Hop" not in cleaned
        # Upgrade is a static hop-by-hop header; Connection itself is
        # also dropped; the rest pass through.
        assert "Connection" not in cleaned
        assert cleaned["Authorization"] == "Bearer x"
        assert cleaned["X-Keep"] == "ok"

    def test_connection_token_matching_is_case_insensitive(self):
        headers = {
            "Connection": "x-hop-HEADER",
            "X-Hop-Header": "drop-me",
            "X-Keep": "ok",
        }
        cleaned = clean_request_headers(headers)
        assert "X-Hop-Header" not in cleaned
        assert cleaned["X-Keep"] == "ok"


# ---------------------------------------------------------------------------
# End-to-end: real proxy + fake upstream
# ---------------------------------------------------------------------------


class _FakeUpstream:
    """Tiny aiohttp app that records every request the proxy forwards
    so the test can assert on the cleaned payloads."""

    def __init__(self) -> None:
        self.captured: list[dict[str, Any]] = []
        self._runner: web.AppRunner | None = None
        self.port: int = 0

    async def start(self) -> str:
        async def handler(request: web.Request) -> web.StreamResponse:
            body = await request.text()
            self.captured.append(
                {
                    "method": request.method,
                    "path": request.path_qs,
                    "headers": {k: v for k, v in request.headers.items()},
                    "body": body,
                }
            )
            # Return a minimal JSON success response so the proxy has
            # something to stream back.
            return web.json_response({"ok": True, "echoed": body})

        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        server = site._server
        assert server is not None
        sockets = getattr(server, "sockets", None)
        assert sockets is not None
        self.port = sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None


@pytest.mark.asyncio
async def test_proxy_strips_tool_reference_block_end_to_end():
    upstream = _FakeUpstream()
    upstream_url = await upstream.start()
    proxy = OpenRouterCompatProxy(target_base_url=upstream_url)
    await proxy.start()
    try:
        body = {
            "model": "claude-opus-4.6",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {
                            "type": "tool_reference",
                            "tool_name": "mcp__copilot__find_block",
                        },
                    ],
                }
            ],
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json=body,
                headers={"Authorization": "Bearer test"},
            ) as resp:
                assert resp.status == 200
                await resp.read()
    finally:
        await proxy.stop()
        await upstream.stop()

    assert len(upstream.captured) == 1
    forwarded = json.loads(upstream.captured[0]["body"])
    # The tool_reference block must NOT be in the upstream-visible body.
    assert '"tool_reference"' not in upstream.captured[0]["body"]
    assert forwarded["messages"][0]["content"] == [{"type": "text", "text": "hi"}]


@pytest.mark.asyncio
async def test_proxy_strips_context_management_beta_header_end_to_end():
    upstream = _FakeUpstream()
    upstream_url = await upstream.start()
    proxy = OpenRouterCompatProxy(target_base_url=upstream_url)
    await proxy.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json={"model": "x", "messages": []},
                headers={
                    "Authorization": "Bearer test",
                    "anthropic-beta": "context-management-2025-06-27, other-beta",
                },
            ) as resp:
                assert resp.status == 200
                await resp.read()
    finally:
        await proxy.stop()
        await upstream.stop()

    forwarded_headers = upstream.captured[0]["headers"]
    # Header is rewritten to remove only the forbidden token, keeping the rest.
    assert any(
        k.lower() == "anthropic-beta" and v == "other-beta"
        for k, v in forwarded_headers.items()
    )


@pytest.mark.asyncio
async def test_proxy_strips_betas_from_request_body_end_to_end():
    upstream = _FakeUpstream()
    upstream_url = await upstream.start()
    proxy = OpenRouterCompatProxy(target_base_url=upstream_url)
    await proxy.start()
    try:
        body = {
            "model": "x",
            "betas": [
                "context-management-2025-06-27",
                "fine-grained-tool-streaming-2025",
            ],
            "messages": [],
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json=body,
            ) as resp:
                assert resp.status == 200
                await resp.read()
    finally:
        await proxy.stop()
        await upstream.stop()

    forwarded = json.loads(upstream.captured[0]["body"])
    # Only the surviving beta should be present.
    assert forwarded["betas"] == ["fine-grained-tool-streaming-2025"]


@pytest.mark.asyncio
async def test_proxy_passes_through_clean_request_unchanged():
    """The proxy must be a no-op for requests that don't contain any of
    the forbidden patterns — no other rewriting allowed."""
    upstream = _FakeUpstream()
    upstream_url = await upstream.start()
    proxy = OpenRouterCompatProxy(target_base_url=upstream_url)
    await proxy.start()
    try:
        body = {
            "model": "claude-opus-4.6",
            "messages": [{"role": "user", "content": "hello"}],
            "temperature": 0.7,
        }
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json=body,
                headers={
                    "Authorization": "Bearer test",
                    "Content-Type": "application/json",
                },
            ) as resp:
                assert resp.status == 200
                await resp.read()
    finally:
        await proxy.stop()
        await upstream.stop()

    forwarded = json.loads(upstream.captured[0]["body"])
    assert forwarded == body


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_failure():
    """If the upstream is unreachable the proxy must return a clear
    502, not silently hang.

    Note: the outer ``client.post`` talks to the *proxy* on localhost,
    not to the dead upstream directly. The proxy is the thing under
    test, so it should always respond with a 502 — we must NOT
    swallow ``aiohttp.ClientError`` / ``asyncio.TimeoutError`` on the
    outer call, because that would mask a proxy crash and turn the
    assertion into a false positive. Let any such exception fail the
    test.
    """
    proxy = OpenRouterCompatProxy(
        target_base_url="http://127.0.0.1:1",  # nothing listening
    )
    await proxy.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json={"model": "x"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                assert resp.status == 502
                text = await resp.text()
                # Generic error message — no internal hostname leaked.
                assert "upstream error" in text
    finally:
        await proxy.stop()


@pytest.mark.asyncio
async def test_proxy_returns_502_on_upstream_timeout():
    """``aiohttp.ClientTimeout`` raises ``asyncio.TimeoutError`` (not
    ``aiohttp.ClientError``), which previously escaped the except
    block and surfaced as a 500.  This regression-guards the 502
    contract for hung upstreams."""

    class _HangingUpstream:
        """Upstream that accepts the request but never finishes the
        response body, forcing the proxy's client timeout to fire."""

        def __init__(self) -> None:
            self._runner: web.AppRunner | None = None
            self.port: int = 0

        async def start(self) -> str:
            async def handler(request: web.Request) -> web.StreamResponse:
                # Hold the response open longer than the proxy's
                # client timeout so aiohttp raises TimeoutError on
                # the proxy side.
                await asyncio.sleep(30)
                return web.Response(status=200)

            app = web.Application()
            app.router.add_route("*", "/{tail:.*}", handler)
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, "127.0.0.1", 0)
            await site.start()
            server = site._server
            assert server is not None
            sockets = getattr(server, "sockets", None)
            assert sockets is not None
            self.port = sockets[0].getsockname()[1]
            return f"http://127.0.0.1:{self.port}"

        async def stop(self) -> None:
            if self._runner is not None:
                await self._runner.cleanup()
                self._runner = None

    upstream = _HangingUpstream()
    upstream_url = await upstream.start()
    # Short proxy timeout so the test finishes quickly.
    proxy = OpenRouterCompatProxy(target_base_url=upstream_url, request_timeout=0.5)
    await proxy.start()
    try:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"{proxy.local_url}/v1/messages",
                json={"model": "x"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                assert resp.status == 502
                text = await resp.text()
                # Generic error message — no internal hostname leaked.
                assert "upstream error" in text
    finally:
        await proxy.stop()
        await upstream.stop()


@pytest.mark.asyncio
async def test_proxy_local_url_raises_before_start():
    proxy = OpenRouterCompatProxy(target_base_url="http://example.com")
    with pytest.raises(RuntimeError):
        _ = proxy.local_url
