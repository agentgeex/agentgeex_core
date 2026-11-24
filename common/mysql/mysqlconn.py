import os
from timeit import default_timer

import pymysql
from dbutils.pooled_db import PooledDB

from config_loader import config


class UsingMysql:
    # Class variable: per-process connection pools {pid: connection_pool}
    _connpools = {}

    def __init__(self, commit=True, log_time=True, log_label="总用时"):
        self._log_time = log_time
        self._commit = commit
        self._log_label = log_label

        self.ifusrpool = str(config.mysql.get("pool_enabled", 0))
        self.pool_size = str(config.mysql.get("pool_size", 2))
        self.host = config.mysql.get("host", "")
        self.port = str(config.mysql.get("port", 3306))
        self.username = config.mysql.get("user", "")
        self.passwd = config.mysql.get("password", "")
        self.database = config.mysql.get("database", "")
        self.charset = config.mysql.get("charset", "utf8mb4")

        # 1. Per-process isolated initialization
        pid = os.getpid()
        if pid not in UsingMysql._connpools and self.ifusrpool == "1":
            print(f"进程 {pid} 正在创建独立的数据库连接池...")
            UsingMysql._connpools[pid] = PooledDB(
                pymysql,
                maxconnections=int(self.pool_size),
                mincached=2,
                maxcached=10,
                blocking=True,
                ping=1,
                host=self.host,
                port=int(self.port),
                user=self.username,
                password=self.passwd,
                db=self.database,
                charset=self.charset,  # Recommended default: utf8mb4
                connect_timeout=10,
                read_timeout=60,
            )

    def get_connection(self):
        pid = os.getpid()
        # print(f"current pid =>  {pid}")
        pool = UsingMysql._connpools.get(pid)
        if pool:
            return pool.connection()

        # Fallback: when pooling is off, use a plain connection
        return pymysql.connect(
            host=self.host,
            port=int(self.port),
            user=self.username,
            password=self.passwd,
            db=self.database,
            charset=self.charset,
            connect_timeout=10,
        )

    def __enter__(self):
        # Merged __enter__; makes sure the start variable is initialized
        if self._log_time:
            self._start = default_timer()

        self._conn = self.get_connection()
        self._cursor = self._conn.cursor(pymysql.cursors.DictCursor)
        # Note: with PooledDB you usually do not need to set autocommit=False manually,
        # because pool.connection() handles it via the commit argument; setting it explicitly is harmless
        return self

    @property
    def cursor(self):
        """Allows access via `um.cursor`."""
        return self._cursor

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:  # Roll back on exception
                self._conn.rollback()
                print(f"进程 {os.getpid()} 执行SQL出错: {exc_val}")
            else:
                if self._commit:
                    self._conn.commit()

            self._cursor.close()
            self._conn.close()  # close() here returns the connection to the pool; it does not really disconnect

            if self._log_time and hasattr(self, "_start"):
                _diff = default_timer() - self._start
                # print(f"-- {self._log_label} [PID:{os.getpid()}]: {_diff:.6f} s")

        except Exception as e:
            print(f"当前进程 {os.getpid()} MySQL 退出异常: {e}")
