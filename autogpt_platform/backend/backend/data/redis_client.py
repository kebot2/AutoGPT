import logging
import os

from dotenv import load_dotenv
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.cluster import RedisCluster as AsyncRedisCluster
from redis.cluster import ClusterNode, RedisCluster

from backend.util.cache import cached, thread_cached
from backend.util.retry import conn_retry

load_dotenv()

HOST = os.getenv("REDIS_HOST", "localhost")
PORT = int(os.getenv("REDIS_PORT", "6379"))
PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Default socket timeouts so a wedged Redis endpoint can't hang callers
# indefinitely — long-running code paths (cluster_lock refresh in particular)
# rely on these to fail-fast instead of blocking on no-response TCP. Override
# via env if a specific deployment needs a different budget.
#
# 30s matches the convention in ``backend.data.rabbitmq`` and leaves ~6x
# headroom over the largest ``xread(block=5000)`` wait in stream_registry.
# The connect timeout is shorter (5s) because initial connects should be
# fast; a slow connect usually means the endpoint is genuinely unreachable.
SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "30"))
SOCKET_CONNECT_TIMEOUT = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
# How often redis-py sends a PING on idle connections to detect half-open
# sockets; cheap and avoids waiting for the OS TCP keepalive (~2h default).
HEALTH_CHECK_INTERVAL = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))

logger = logging.getLogger(__name__)

# Aliases kept so call-sites don't care which class this is — the backend
# always talks to a Redis Cluster (1-shard locally, sharded in prod).
RedisClient = RedisCluster
AsyncRedisClient = AsyncRedisCluster


@conn_retry("Redis", "Acquiring connection")
def connect() -> RedisClient:
    c = RedisCluster(
        startup_nodes=[ClusterNode(HOST, PORT)],
        password=PASSWORD,
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_keepalive=True,
        health_check_interval=HEALTH_CHECK_INTERVAL,
    )
    c.ping()
    return c


@conn_retry("Redis", "Releasing connection")
def disconnect():
    get_redis().close()


@cached(ttl_seconds=3600)
def get_redis() -> RedisClient:
    return connect()


@conn_retry("AsyncRedis", "Acquiring connection")
async def connect_async() -> AsyncRedisClient:
    c = AsyncRedisCluster(
        host=HOST,
        port=PORT,
        password=PASSWORD,
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_keepalive=True,
        health_check_interval=HEALTH_CHECK_INTERVAL,
    )
    await c.ping()
    return c


@conn_retry("AsyncRedis", "Releasing connection")
async def disconnect_async():
    c = await get_redis_async()
    await c.close()


@thread_cached
async def get_redis_async() -> AsyncRedisClient:
    return await connect_async()


# Pub/sub uses a plain (Async)Redis connection to the seed node: async
# RedisCluster has no ``pubsub()``, and classic pub/sub is broadcast across
# the whole cluster so one-node connection suffices at any shard count.


@conn_retry("RedisPubSub", "Acquiring connection")
def connect_pubsub() -> Redis:
    c = Redis(
        host=HOST,
        port=PORT,
        password=PASSWORD,
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_keepalive=True,
        health_check_interval=HEALTH_CHECK_INTERVAL,
    )
    c.ping()
    return c


@conn_retry("AsyncRedisPubSub", "Acquiring connection")
async def connect_pubsub_async() -> AsyncRedis:
    c = AsyncRedis(
        host=HOST,
        port=PORT,
        password=PASSWORD,
        decode_responses=True,
        socket_timeout=SOCKET_TIMEOUT,
        socket_connect_timeout=SOCKET_CONNECT_TIMEOUT,
        socket_keepalive=True,
        health_check_interval=HEALTH_CHECK_INTERVAL,
    )
    await c.ping()
    return c


@cached(ttl_seconds=3600)
def get_redis_pubsub() -> Redis:
    """Return a plain ``Redis`` client dedicated to pub/sub.

    A subscribed connection blocks on ``listen()`` and cannot be interleaved
    with regular command traffic, so pub/sub gets its own connection separate
    from the cluster-aware client returned by :func:`get_redis`.
    """
    return connect_pubsub()


@thread_cached
async def get_redis_pubsub_async() -> AsyncRedis:
    """Async equivalent of :func:`get_redis_pubsub`.

    Separate connection for the same reason as the sync helper, and because
    the async ``RedisCluster`` client does not expose ``pubsub()`` at all.
    """
    return await connect_pubsub_async()
