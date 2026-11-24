import os
import re
import sys

import pymysql
from fastmcp import FastMCP

# from mcp.types import Resource

"""
测试用环境变量
export set  MYSQL_HOST=127.0.0.1
export set  MYSQL_PORT=3306
export set  MYSQL_USERNAME=root
export set  MYSQL_PASSWORD='<your-mysql-password>'
export set  MYSQL_DB=coingeek

## MCP的配置

{
  "args": [
    "-u",
    "/home/amdmz/motogpt/project/coingeekweb/gpt/mcp/mcp_server_mysql.py"
  ],
  "command": "/home/amdmz/miniconda3/envs/coingeek_py311/bin/python",
  "env": {
    "MYSQL_DB": "coingeek",
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PASSWORD": "Moto@Zolo123",
    "MYSQL_PORT": "3306",
    "MYSQL_USERNAME": "root"
  },
  "type": "stdio"
}

"""


def _ao_cfg(section: str, key: str, default):
    """Fall back to the main AgentGeeX config (agx_config.json) when env vars are missing.

    * 2026-11-04 why this was added: after turning this service into a PM2 resident
      service, credentials needed a home. Historically OpenClaw injected them via
      mcp.servers.env, but the service is no longer started by OpenClaw.
      We do not put passwords on the PM2 command line / config file (that would leak
      them into the process list and logs); instead we reuse the existing AgentGeeX
      config. Priority: env var > agx_config.json > default value.
    """
    try:
        # * sys.path fix: when started as `python agx_mcp/xxx.py`, sys.path[0] is agx_mcp/,
        #   so the project-root config_loader cannot be found (and PM2 starts it exactly that way).
        #   Here we explicitly add the parent directory (project root / ~/.agx) to the search path.
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


MYSQL_HOST = os.getenv("MYSQL_HOST") or _ao_cfg("mysql", "host", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT") or _ao_cfg("mysql", "port", 3306))
MYSQL_USERNAME = os.getenv("MYSQL_USERNAME") or _ao_cfg("mysql", "user", "ROOT")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD") or _ao_cfg("mysql", "password", "")
MYSQL_DB = os.getenv("MYSQL_DB") or _ao_cfg("mysql", "database", "coingeek")


# your database configuration
DB_CONFIG = {
    "host": MYSQL_HOST,
    "port": MYSQL_PORT,
    "user": MYSQL_USERNAME,
    "password": MYSQL_PASSWORD,
    "database": MYSQL_DB,
    "cursorclass": pymysql.cursors.DictCursor,
}

mcp = FastMCP("MySQL-Reflection-Server")


@mcp.tool()
def check_services():
    """
    Check whether the MySQL MCP service is usable.
    Runs a lightweight query (SELECT 1) to verify the DB connection and privileges.
    If this method returns without error and reports success, the service works.
    """
    connection = None
    try:
        # 1. try to establish a connection
        connection = pymysql.connect(**DB_CONFIG)

        # 2. run a minimal query to verify liveness
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

            # 3. fetch the version for a more deterministic response
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()

        return {
            "status": "healthy",
            "service_build": "MCP-BUILD-20261104-0746-T1",
            "message": "MySQL MCP 服务连接正常",
            "database": DB_CONFIG["database"],
            "server_version": version["VERSION()"],
            "endpoint": f"{DB_CONFIG['host']}:{DB_CONFIG['port']}",
        }
    except Exception as e:
        # catch the error explicitly and report it, so Claude knows what broke (network, password, or missing DB)
        return {
            "status": "unhealthy",
            "message": f"服务不可用，错误原因: {str(e)}",
            "config_preview": {"host": DB_CONFIG["host"], "port": DB_CONFIG["port"], "user": DB_CONFIG["user"]},
        }
    finally:
        if connection:
            connection.close()


@mcp.tool()
def get_table_full_schema(database_name: str, table_name: str):
    """
    Return the full structural definition of a table, including column comments.

    This is the primary reference for generating Vue form labels and validation rules.

    Args:
        database_name (str): database name, e.g. 'coingeek'.
        table_name (str): table name, e.g. 'stock_adj_factor'.

    Returns:
        list|str: a list of per-column dicts with Field, Type, Null, Key, Default, Extra, Comment.
    """

    if not re.match(r"^[a-zA-Z0-9_]+$", database_name) or not re.match(r"^[a-zA-Z0-9_]+$", table_name):
        return "错误：非法的数据库名或表名格式。"

    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            # SHOW FULL COLUMNS is the key to getting comments
            sql = f"SHOW FULL COLUMNS FROM `{database_name}`.`{table_name}`"
            cursor.execute(sql)
            result = cursor.fetchall()

            # parse the rows into an easy-to-read dict
            full_schema = []
            for row in result:
                full_schema.append(
                    {
                        "field": row[0],  # column name
                        "type": row[1],  # column type
                        "nullable": row[3],  # nullable or not (YES/NO)
                        "key": row[4],  # key type (PRI/MUL/UNI)
                        "default": row[5],  # default value
                        "extra": row[6],  # extra attributes (e.g. auto_increment)
                        "comment": row[8],  # column comment (business meaning)
                    }
                )
            return full_schema
    except Exception as e:
        return f"读取完整表结构失败: {str(e)}"
    finally:
        connection.close()


@mcp.tool()
def query_database(sql: str):
    """
    Execute a SQL query.

    Args:
        sql (str): the SQL to run. Read-only operations only (SELECT, SHOW, DESCRIBE).

    Returns:
        list|str: the result rows, or an error message.
    """
    # 1. trim leading/trailing whitespace and newlines
    clean_sql = sql.strip().upper()

    # 2. define the read-only allowlist (the statement must start with one of these)
    readonly_whitelist = ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN")

    # 3. define the dangerous blocklist (must not appear anywhere in the SQL)
    # stops multi-statement injection through a semicolon, e.g. SELECT * FROM users; DROP TABLE...
    danger_blacklist = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "TRUNCATE",
        "ALTER",
        "CREATE",
        "REPLACE",
        "GRANT",
        "REVOKE",
    ]

    # check: must start with an allowlisted keyword
    if not clean_sql.startswith(readonly_whitelist):
        return "拒绝执行：该工具仅支持只读查询（SELECT/SHOW/DESC）。"

    # check: blocklisted keywords must not appear (prevents nested or multi-statement injection)
    # regex on word boundaries avoids false positives (e.g. a table name containing the word 'create')
    for word in danger_blacklist:
        pattern = rf"\b{word}\b"
        if re.search(pattern, clean_sql):
            return f"拒绝执行：检测到非法操作关键字 '{word}'。"

    # 4. execution logic
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    except Exception as e:
        return f"读取资源失败: 错误信息 {str(e)}"
    finally:
        connection.close()


@mcp.tool()
def list_tables(db_name: str):
    """
    List every table in the database together with its comment.
    Args:
        db_name: the database to inspect
    """
    return query_database(
        f"SELECT table_name, table_comment FROM information_schema.tables WHERE table_schema = '{db_name}'"
    )


@mcp.tool()
def list_databases():
    """List every database on the current server and what it is used for."""
    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            # fetch all databases
            cursor.execute("SHOW DATABASES")
            databases = cursor.fetchall()

            # MySQL 5.7's information_schema.SCHEMATA has no SCHEMA_COMMENT column
            # so only the database name is returned; the business purpose must be inferred from the name
            result = []
            for db in databases:
                db_name = db["Database"]
                # infer the business purpose from the database name
                description = "无描述"
                if db_name == "information_schema":
                    description = "MySQL系统数据库，存储元数据信息"
                elif db_name == "mysql":
                    description = "MySQL系统数据库，存储用户权限等信息"
                elif db_name == "performance_schema":
                    description = "MySQL性能监控数据库"
                elif db_name == "sys":
                    description = "MySQL系统数据库，提供性能监控视图"
                elif "coingeek" in db_name.lower():
                    description = "证劵加密货币相关业务数据库"

                result.append({"数据库名称": db_name, "业务用途": description})

            return result
    except Exception as e:
        return f"读取资源失败: 错误信息 {str(e)}"
    finally:
        connection.close()


# --- 1. define a plain function (NO decorator!) ---
# --- core resource: read on demand (the only decorator you got working before) ---


@mcp.resource("mysql://{table_name}/data")
def table_data_resource(table_name: str) -> str:
    """
    [Dynamic data sampling resource]

    Purpose: fetch the first 50 real rows of a given database table.

    Parameters:
    - {table_name}: must be replaced with the actual MySQL table name (e.g. t_chip_distribution or t_stock_l4).

    When to use:
    When you are about to write SQL but are unsure about a column's value distribution,
    precision (e.g. the range of profit_ratio) or date format, read this resource first.
    """
    # 1. basic validation: guard against SQL injection (read-only, but an architect keeps the habit)
    # simple filter for non-identifier characters
    clean_table_name = "".join(filter(lambda x: x.isalnum() or x == "_", table_name))

    connection = pymysql.connect(**DB_CONFIG)
    try:
        with connection.cursor() as cursor:
            # 2. run the query: only the first 50 rows, to avoid a token explosion
            sql = f"SELECT * FROM `{clean_table_name}` LIMIT 50"
            cursor.execute(sql)

            # 3. get the column names (cursor.description carries the metadata)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()

            if not rows:
                return f"表 `{clean_table_name}` 目前是空的，或者不存在。"

            # 4. format the output as a Markdown table (AI understands Markdown best)
            # build the header row
            header = "| " + " | ".join(columns) + " |"
            separator = "| " + " | ".join(["---"] * len(columns)) + " |"

            # build the data rows
            data_rows = []
            for row in rows:
                # handle None values, converting them to strings
                formatted_row = [str(val) if val is not None else "NULL" for val in row.values()]
                data_rows.append("| " + " | ".join(formatted_row) + " |")

            # 5. assemble the final document
            result = [
                f"### 数据样本: {clean_table_name} (Top 50 rows)",
                f"提示：以下是数据库表 `{clean_table_name}` 的真实采样数据。",
                "",
                header,
                separator,
            ] + data_rows

            return "\n".join(result)

    except Exception as e:
        return f"读取资源失败: 错误信息 {str(e)}"
    finally:
        connection.close()


# --- 3. before startup, manually wire the logic in ---
if __name__ == "__main__":
    # key hack: attach the resource list provider by hand, working around the decorator bug
    # mcp.list_resources(list_my_databases_logic)

    # * 2026-11-04: support shared-service mode (default remains stdio, behaviour fully unchanged)
    #   stdio  one process per client (historical behaviour)
    #   http   Streamable HTTP + stateless, one process serving every client
    #   Context: 79 agents / N concurrent sessions used to start N sets of processes (a uv wrapper plus a python each),
    #            so memory and DB connections grew linearly with the session count — a resident service decouples that.
    _transport = "stdio"
    _host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
    _port = int(os.environ.get("MCP_HTTP_PORT", "31892"))
    _args = sys.argv[1:]
    _take = lambda flag: next((a.split("=", 1)[1].strip() for a in _args
                              if a.startswith(flag + "=")), "")
    _transport = _take("--transport") or _transport
    _host = _take("--host") or _host
    _port = int(_take("--port") or _port)

    if _transport in ("http", "streamable-http"):
        print(f"[mysql-mcp] Streamable HTTP (stateless) on http://{_host}:{_port}/mcp",
              flush=True)
        mcp.run(transport="http", host=_host, port=_port, stateless_http=True)
    elif _transport == "sse":
        print(f"[mysql-mcp] SSE (legacy) on http://{_host}:{_port}/sse", flush=True)
        mcp.run(transport="sse", host=_host, port=_port)
    else:
        mcp.run()
