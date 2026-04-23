"""Unit tests for the cluster-only Redis client in ``redis_client``.

Patches the redis-py constructors + ``ping()`` so no real Redis is needed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.cluster import RedisCluster

import backend.data.redis_client as redis_client


@pytest.fixture(autouse=True)
def _reset_module_caches() -> None:
    """Flush cached singletons between tests so each test sees a fresh connect."""
    redis_client.get_redis.cache_clear()  # type: ignore[attr-defined]
    try:
        redis_client.get_redis_async.cache_clear()  # type: ignore[attr-defined]
    except AttributeError:
        pass


def test_connect_builds_redis_cluster() -> None:
    with patch.object(redis_client, "RedisCluster", autospec=True) as mock_cluster:
        mock_cluster.return_value = MagicMock(spec=RedisCluster)
        client = redis_client.connect()

    mock_cluster.assert_called_once()
    kwargs = mock_cluster.call_args.kwargs
    assert kwargs["password"] == redis_client.PASSWORD
    assert kwargs["decode_responses"] is True
    assert kwargs["socket_timeout"] == redis_client.SOCKET_TIMEOUT
    assert kwargs["socket_connect_timeout"] == redis_client.SOCKET_CONNECT_TIMEOUT
    assert kwargs["socket_keepalive"] is True
    assert kwargs["health_check_interval"] == redis_client.HEALTH_CHECK_INTERVAL
    startup = kwargs["startup_nodes"]
    assert len(startup) == 1
    # ClusterNode resolves "localhost" → "127.0.0.1" internally; both are
    # valid representations of the configured host.
    assert startup[0].host in {redis_client.HOST, "127.0.0.1"}
    assert startup[0].port == redis_client.PORT
    client.ping.assert_called_once()


@pytest.mark.asyncio
async def test_connect_async_builds_async_redis_cluster() -> None:
    with patch.object(redis_client, "AsyncRedisCluster", autospec=True) as mock_cluster:
        fake = MagicMock(spec=AsyncRedisCluster)
        fake.ping = AsyncMock()
        mock_cluster.return_value = fake
        client = await redis_client.connect_async()

    mock_cluster.assert_called_once()
    kwargs = mock_cluster.call_args.kwargs
    assert kwargs["host"] == redis_client.HOST
    assert kwargs["port"] == redis_client.PORT
    assert kwargs["password"] == redis_client.PASSWORD
    assert kwargs["decode_responses"] is True
    client.ping.assert_awaited_once()
