"""数据库操作模块 - 提供 SQLite 数据库的单例访问"""
import sqlite3
import logging
from pathlib import Path
from threading import Lock
from typing import Any, Optional

from config import DB_PATH, DATA_DIR

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库单例"""
    _instance: Optional["Database"] = None
    _lock = Lock()

    def __new__(cls) -> "Database":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._conn: Optional[sqlite3.Connection] = None
        return cls._instance

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(DB_PATH)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """初始化数据库表结构"""
        schema_path = Path(DATA_DIR) / "schema.sql"
        if not schema_path.exists():
            logger.error("schema.sql 不存在: %s", schema_path)
            raise FileNotFoundError(f"schema.sql not found: {schema_path}")
        with open(schema_path, encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.commit()
        logger.info("数据库初始化完成: %s", DB_PATH)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def dict_fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        row = self.fetchone(sql, params)
        return dict(row) if row else None

    def dict_fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self.fetchall(sql, params)]
