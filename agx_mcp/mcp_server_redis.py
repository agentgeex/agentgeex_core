import os
import sys

from fastmcp import FastMCP
from redis import ConnectionPool, Redis

"""
测试用环境变量
export set  REDIS_HOST=127.0.0.1
export set  REDIS_PORT=6379
export set  REDIS_DB=0
export set  REDIS_PASSWORD='<your-redis-password>'
"""

"""
MCP的配置


{
  "args": [
    "-u",
    "/home/amdmz/motogpt/project/coingeekweb/gpt/mcp/mcp_server_redis.py"
  ],
  "command": "/home/amdmz/miniconda3/envs/coingeek_py311/bin/python",
  "env": {
    "REDIS_DB": "0",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PASSWORD": "motozolo@4$%6&",
    "REDIS_PORT": "6379"
  },
  "type": "stdio"
}
"""

# --- configuration and initialization ---

mcp = FastMCP("Local-Redis-Manager")

def _ao_cfg(section: str, key: str, default):
    """Fall back to the main AgentGeeX config (agx_config.json) when env vars are missing.

    * 2026-11-04: once this service became a PM2 resident process OpenClaw no longer
      injects env, and passwords stay off the PM2 command line (to avoid leaking them
      into the process list / logs); we reuse the AgentGeeX config instead.
      Priority: env var > agx_config.json > default value.
    """
    try:
        # * sys.path fix: when started as `python agx_mcp/xxx.py`, sys.path[0] is agx_mcp/,
        #   so the project-root config_loader cannot be found (and PM2 starts it exactly that way).
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        from config_loader import config as _c
        sec = getattr(_c, section, None)
        if sec is None:
            return default
        val = sec.get(key, None)
        return default if val in (None, "") else val
    except Exception:  # noqa: BLE001
        return default


REDIS_HOST = os.getenv("REDIS_HOST") or _ao_cfg("redis", "host", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT") or _ao_cfg("redis", "port", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD") or _ao_cfg("redis", "password", None)
REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")

# Note: ConnectionPool no longer pins a db, so a pooled connection can switch via SELECT
pool = ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    decode_responses=True,
    max_connections=5,  # bumped up slightly to support concurrent switching
    health_check_interval=10,
    socket_keepalive=True,  # enable system-level TCP keepalive
)
# redis_client = Redis(connection_pool=pool)


def get_db_client(db: int) -> Redis:
    # """internal helper: return a client instance bound to the given database"""
    # # a redis-py client is lightweight and shares one connection pool
    # client = Redis(connection_pool=pool)
    # client.execute_command("SELECT", db)
    # return client

    """Return a client instance bound to the given database.
    redis-py sends SELECT db automatically when a connection is checked out of the pool.
    """
    # coerce db to int for robustness
    return Redis(connection_pool=pool, db=int(db))


# --- MCP tool definitions ---


@mcp.tool()
def check_services(db_index: int = 0):
    """
    Check whether the Redis MCP service is usable.
    Runs PING to verify connectivity and returns the server's runtime information.
    If the status is 'healthy', the service works.
    """
    try:
        # 1. verify connectivity (PING)
        redis_client = get_db_client(db_index)
        if redis_client.ping():
            # 2. pull some server info to enrich the response
            info = redis_client.info(section="server")
            return {
                "status": "healthy",
                "service_build": "MCP-BUILD-20261104-0746-T1",
                "message": "Redis MCP 服务连接正常",
                "redis_version": info.get("redis_version"),
                "endpoint": f"{REDIS_HOST}:{REDIS_PORT}",
                "db_index": db_index,
            }
        else:
            return {"status": "unhealthy", "message": "Redis 未响应 PING 命令"}
    except Exception as e:
        return {
            "status": "unhealthy",
            "message": f"Redis 服务不可用，错误原因: {str(e)}",
            "config_preview": {"host": REDIS_HOST, "port": REDIS_PORT, "db": db_index},
        }


@mcp.tool()
def query_redis(command: str, argument1: str = "", argument2: str = "", argument3: str = "", db_index: int = 0) -> str:
    """
    Generic read-only Redis query tool, supporting cross-database queries.

    Args:
        command (str): Redis command (e.g. GET, HGETALL, LRANGE, TYPE).
        argument1 (str): first argument of the command (e.g. the key name).
        argument2 (str): second argument (optional, e.g. a hash field or the list start).
        argument3 (str): third argument (optional, e.g. the list end).
        db_index (int): database index (0-15).
    """
    readonly_whitelist = {
        "GET",
        "MGET",
        "GETRANGE",
        "STRLEN",
        "HGET",
        "HGETALL",
        "HKEYS",
        "HLEN",
        "LINDEX",
        "LLEN",
        "LRANGE",
        "SMEMBERS",
        "SCARD",
        "SISMEMBER",
        "ZRANGE",
        "ZCARD",
        "KEYS",
        "SCAN",
        "TYPE",
        "TTL",
        "EXISTS",
        "INFO",
        "DBSIZE",
    }

    cmd_upper = command.strip().upper()
    if cmd_upper not in readonly_whitelist:
        return f"拒绝执行：命令 '{command}' 具有写风险或不在白名单内。"

    try:
        # drop empty-string arguments
        params = [arg for arg in [argument1, argument2, argument3] if arg != ""]

        client = get_db_client(db_index)
        raw_result = client.execute_command(cmd_upper, *params)

        if raw_result is None:
            return "(nil)"
        if isinstance(raw_result, list):
            if not raw_result:
                return "(empty list or set)"
            return "\n".join([f"{i + 1}) {str(item)}" for i, item in enumerate(raw_result)])
        return str(raw_result)
    except Exception as e:
        return f"执行出错 (DB:{db_index}): {str(e)}"


@mcp.tool()
def set_cache(key: str, value: str, expire_seconds: int | None = None, db_index: int = 0) -> str:
    """
    Store a string value in Redis.

    Args:

        key: key name; use a business prefix such as 'a320:status'
        value: the content to store
        expire_seconds: optional TTL in seconds

    """
    try:
        redis_client = get_db_client(db_index)
        redis_client.set(key, value, ex=expire_seconds)
        return f"成功存储键 '{key}' 到 DB:{db_index}"
    except Exception as e:
        return f"存储失败: {str(e)}"


@mcp.tool()
def get_cache(key: str, db_index: int = 0) -> str:
    """
    Read the value of a given key from Redis.

    Args:
        key: key name; use a business prefix such as
    """
    try:
        redis_client = get_db_client(db_index)
        val = redis_client.get(key)
        return val if val is not None else f"键 '{key}' 不存在"
    except Exception as e:
        return f" 读取失败: {str(e)}"


@mcp.tool()
def list_keys(pattern: str = "abc", db_index: int = 0) -> list[str]:
    """
    Search the Redis key space for keys matching a pattern.
    For example use 'a320:*' to find every A320-related state key.

    Args:
        pattern: the match pattern
        db_index: the Redis database index to query
    """
    try:
        redis_client = get_db_client(db_index)
        return redis_client.keys(pattern)
    except Exception as e:
        return [f"搜索失败: {str(e)}"]


@mcp.tool()
def log_event(list_name: str, message: str, keep_last: int = 100, db_index: int = 0) -> str:
    """
    Push an event message onto a Redis list, commonly used to record an Agent's activity trail.
    The list is trimmed automatically, keeping only the most recent N entries.

    Args:

        list_name: list name
        message: the message to store
        keep_last: how many recent entries to keep
        db_index: the Redis database index to query

    """
    try:
        redis_client = get_db_client(db_index)
        redis_client.lpush(list_name, message)
        redis_client.ltrim(list_name, 0, keep_last - 1)
        return f" 已记录到队列 '{list_name}'，当前保留最近 {keep_last} 条"
    except Exception as e:
        return f" 记录失败: {str(e)}"


# @mcp.tool()
# def task_save(task_key: str, task_node: str, task_finlish_date: datetime, task_finlish_flag: int = 0) -> str:
#     """record the information produced while a task completes

#     Args:
#         task_key (str): task id
#         task_node (str): task node name
#         task_finlish_date (datetime): task completion time, e.g. 2026-03-01 15:58:58
#         task_finlish_flag (int, optional): completion flag, 0=not done 1=done

#     Returns:
#         str: _description_
#     """
#     try:
#         # message = {
#         #     'task_node': task_node,
#         #     'task_finlish_date': task_finlish_date,
#         #     'task_finlish_flag': task_finlish_flag
#         # }

#         message_str = f"{task_node}##{task_finlish_date}##{task_finlish_flag}"

#         redis_client.lpush(task_key, message_str)

#         return f" completion info for node recorded into '{task_key}'"
#     except Exception as e:
#         return f" record failed: {str(e)}"


@mcp.tool()
def task_save(task_key: str, task_node_message: str, db_index: int = 0):
    """Record, per node, the information produced while a task (development flow) executes.
    Args:
        task_key (str): task id.
        task_node_message (str): completion status of the task node.
        db_index (int): the Redis database index to query

    Returns:
        str: _description_
    """
    try:
        # message = {
        #     'task_node': task_node,
        #     'task_finlish_date': task_finlish_date,
        #     'task_finlish_flag': task_finlish_flag
        # }

        # message_str = f"{task_node}##{task_finlish_date}##{task_finlish_flag}"
        redis_client = get_db_client(db_index)
        redis_client.lpush(task_key, task_node_message)

        return f" 任务节点完成信息已经记录到 '{task_key}'中"
    except Exception as e:
        return f" 记录失败: {str(e)}"


if __name__ == "__main__":
    # * 2026-11-04: support shared-service mode (default remains stdio, behaviour fully unchanged)
    #   stdio  one process per client (historical behaviour)
    #   http   Streamable HTTP + stateless, one process serving every client
    _transport = "stdio"
    _host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    _port = int(os.environ.get("MCP_HTTP_PORT", "31893"))
    _args = sys.argv[1:]
    _take = lambda flag: next((a.split("=", 1)[1].strip() for a in _args
                              if a.startswith(flag + "=")), "")
    _transport = _take("--transport") or _transport
    _host = _take("--host") or _host
    _port = int(_take("--port") or _port)

    if _transport in ("http", "streamable-http"):
        print(f"[redis-mcp] Streamable HTTP (stateless) on http://{_host}:{_port}/mcp",
              flush=True)
        mcp.run(transport="http", host=_host, port=_port, stateless_http=True)
    elif _transport == "sse":
        print(f"[redis-mcp] SSE (legacy) on http://{_host}:{_port}/sse", flush=True)
        mcp.run(transport="sse", host=_host, port=_port)
    else:
        mcp.run()
