"""
database.py — Dual-backend Database Layer (MySQL / SQLite)
==============================================================
Production runs on MySQL (connection pool). Local dev, tests and demos
run on SQLite with zero infrastructure. The backend is chosen at startup
(see `init_db`) and every query goes through the same async helpers, so
callers never care which one is active.

All blocking driver calls are offloaded with `asyncio.to_thread()` so
the FastAPI event loop is never blocked.

Portability rules for callers:
  • Always use `%s` placeholders (translated to `?` for SQLite).
  • Avoid dialect-specific SQL functions (NOW(), DATE_SUB, ...). Store
    timestamps as ISO strings or epoch floats instead.
  • Where a dialect branch is unavoidable (e.g. upserts) use `dialect()`.
"""

import re
import asyncio
import logging
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Optional

from app.config import (
    DB_BACKEND, SQLITE_PATH,
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
)

logger = logging.getLogger(__name__)

_backend: Optional[str] = None   # "mysql" | "sqlite"
_pool = None                     # MySQL connection pool
_sqlite_lock = threading.Lock()  # serialise SQLite writers across worker threads


def dialect() -> str:
    """Active backend name, or 'none' before init_db() ran."""
    return _backend or "none"


# ═══════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════

_MYSQL_TABLES = {
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255),
            picture VARCHAR(512),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_email (email)
        )
    """,
    "token_ledger": """
        CREATE TABLE IF NOT EXISTS token_ledger (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_key VARCHAR(255) NOT NULL,
            tokens INT NOT NULL,
            consumed_ts DOUBLE NOT NULL,
            INDEX idx_user_key (user_key),
            INDEX idx_consumed_ts (consumed_ts)
        )
    """,
    "llm_metrics": """
        CREATE TABLE IF NOT EXISTS llm_metrics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(64) NOT NULL,
            endpoint VARCHAR(255) NOT NULL,
            model VARCHAR(255) NOT NULL,
            prompt_tokens INT NOT NULL DEFAULT 0,
            completion_tokens INT NOT NULL DEFAULT 0,
            total_tokens INT NOT NULL DEFAULT 0,
            latency_ms FLOAT NOT NULL DEFAULT 0,
            cost_usd FLOAT NOT NULL DEFAULT 0,
            status VARCHAR(64) NOT NULL DEFAULT 'success',
            user_key VARCHAR(255),
            INDEX idx_llm_user (user_key)
        )
    """,
    "tts_metrics": """
        CREATE TABLE IF NOT EXISTS tts_metrics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(64) NOT NULL,
            voice VARCHAR(255) NOT NULL,
            char_count INT NOT NULL DEFAULT 0,
            duration_sec FLOAT,
            status VARCHAR(64) NOT NULL DEFAULT 'success',
            user_key VARCHAR(255),
            INDEX idx_tts_user (user_key)
        )
    """,
    "cycle_metrics": """
        CREATE TABLE IF NOT EXISTS cycle_metrics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(64) NOT NULL,
            cycle_type VARCHAR(255) NOT NULL,
            total_latency_ms FLOAT NOT NULL DEFAULT 0,
            tasks_json LONGTEXT NOT NULL,
            user_key VARCHAR(255),
            INDEX idx_cycle_user (user_key)
        )
    """,
    "coach_runs": """
        CREATE TABLE IF NOT EXISTS coach_runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_key VARCHAR(255) NOT NULL,
            created_at VARCHAR(64) NOT NULL,
            source VARCHAR(16) NOT NULL DEFAULT 'text',
            context VARCHAR(1024),
            overall_score FLOAT,
            scores_json TEXT,
            metrics_json TEXT,
            actions_json TEXT,
            text_excerpt VARCHAR(512),
            INDEX idx_coach_user (user_key)
        )
    """,
}

_SQLITE_TABLES = {
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            name TEXT,
            picture TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "token_ledger": """
        CREATE TABLE IF NOT EXISTS token_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_key TEXT NOT NULL,
            tokens INTEGER NOT NULL,
            consumed_ts REAL NOT NULL
        )
    """,
    "llm_metrics": """
        CREATE TABLE IF NOT EXISTS llm_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms REAL NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'success',
            user_key TEXT
        )
    """,
    "tts_metrics": """
        CREATE TABLE IF NOT EXISTS tts_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            voice TEXT NOT NULL,
            char_count INTEGER NOT NULL DEFAULT 0,
            duration_sec REAL,
            status TEXT NOT NULL DEFAULT 'success',
            user_key TEXT
        )
    """,
    "cycle_metrics": """
        CREATE TABLE IF NOT EXISTS cycle_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            cycle_type TEXT NOT NULL,
            total_latency_ms REAL NOT NULL DEFAULT 0,
            tasks_json TEXT NOT NULL,
            user_key TEXT
        )
    """,
    "coach_runs": """
        CREATE TABLE IF NOT EXISTS coach_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'text',
            context TEXT,
            overall_score REAL,
            scores_json TEXT,
            metrics_json TEXT,
            actions_json TEXT,
            text_excerpt TEXT
        )
    """,
}

_SQLITE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_token_ledger_user ON token_ledger (user_key)",
    "CREATE INDEX IF NOT EXISTS idx_token_ledger_ts ON token_ledger (consumed_ts)",
    "CREATE INDEX IF NOT EXISTS idx_llm_user ON llm_metrics (user_key)",
    "CREATE INDEX IF NOT EXISTS idx_tts_user ON tts_metrics (user_key)",
    "CREATE INDEX IF NOT EXISTS idx_cycle_user ON cycle_metrics (user_key)",
    "CREATE INDEX IF NOT EXISTS idx_coach_user ON coach_runs (user_key)",
]

# Columns added after v3.0 — `CREATE TABLE IF NOT EXISTS` leaves existing
# tables alone, so these are applied with ALTER TABLE on databases that
# predate them. (name → (mysql type, sqlite type))
_COLUMN_MIGRATIONS = {
    "llm_metrics": {"user_key": ("VARCHAR(255)", "TEXT")},
    "tts_metrics": {"user_key": ("VARCHAR(255)", "TEXT")},
    "cycle_metrics": {"user_key": ("VARCHAR(255)", "TEXT")},
}


# ═══════════════════════════════════════════════════════════════
# Initialisation
# ═══════════════════════════════════════════════════════════════

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _mysql_reachable(timeout: Optional[float] = None) -> bool:
    """
    Is anything listening where MySQL should be?

    A refused local connection makes the MySQL driver retry for ~4 s on Windows (each
    refused attempt alone takes ~2 s), and `auto` mode paid that on every start and every
    dev reload. A plain TCP probe, trying every address of the host at once, settles it
    quickly: a loopback server answers in milliseconds, so a short timeout cannot give a
    false "down"; a remote one gets longer.
    """
    timeout = timeout if timeout is not None else (0.25 if MYSQL_HOST in _LOOPBACK else 1.5)
    try:
        addresses = socket.getaddrinfo(MYSQL_HOST, MYSQL_PORT, type=socket.SOCK_STREAM)
    except OSError:
        return False

    def attempt(address) -> bool:
        family, kind, proto, _, target = address
        try:
            with socket.socket(family, kind, proto) as probe:
                probe.settimeout(timeout)
                probe.connect(target)
            return True
        except OSError:
            return False

    with ThreadPoolExecutor(max_workers=len(addresses)) as pool:
        return any(pool.map(attempt, addresses))


def init_db() -> str:
    """Pick a backend, create the schema, and return the backend name."""
    global _backend

    if DB_BACKEND == "auto" and not _mysql_reachable():
        logger.info("MySQL not reachable at %s:%s — using SQLite at %s", MYSQL_HOST, MYSQL_PORT, SQLITE_PATH)
    elif DB_BACKEND in ("auto", "mysql"):
        try:
            _init_mysql()
            _backend = "mysql"
            _create_tables()
            logger.info("Database backend: MySQL (%s:%s/%s)", MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE)
            return _backend
        except Exception as err:
            if DB_BACKEND == "mysql":
                raise
            logger.warning("MySQL unavailable (%s) — falling back to SQLite at %s", err, SQLITE_PATH)

    _init_sqlite()
    _backend = "sqlite"
    _create_tables()
    logger.info("Database backend: SQLite (%s)", SQLITE_PATH)
    return _backend


def _init_mysql():
    global _pool
    import mysql.connector
    from mysql.connector.pooling import MySQLConnectionPool

    if not re.match(r"^[a-zA-Z0-9_]+$", MYSQL_DATABASE):
        raise ValueError(
            f"Invalid database name: {MYSQL_DATABASE!r}. Only alphanumeric characters and underscores are allowed."
        )

    conn = mysql.connector.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD,
        connection_timeout=4,
    )
    cursor = conn.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    )
    conn.commit()
    cursor.close()
    conn.close()

    _pool = MySQLConnectionPool(
        pool_name="cadence_pool",
        pool_size=5,
        pool_reset_session=True,
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
    )


def _init_sqlite():
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_PATH))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()
    finally:
        conn.close()


def _existing_columns(cursor, table: str) -> set:
    if _backend == "sqlite":
        cursor.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cursor.fetchall()}
    cursor.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row[0] for row in cursor.fetchall()}


def _migrate_columns(cursor):
    """Add columns introduced after a table was first created (idempotent)."""
    for table, columns in _COLUMN_MIGRATIONS.items():
        try:
            existing = _existing_columns(cursor, table)
        except Exception as err:
            logger.warning("Could not inspect %s for migration: %s", table, err)
            continue
        for column, (mysql_type, sqlite_type) in columns.items():
            if column in existing:
                continue
            col_type = mysql_type if _backend == "mysql" else sqlite_type
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
                logger.info("Migrated %s: added column %s", table, column)
            except Exception as err:
                logger.warning("Migration %s.%s failed: %s", table, column, err)


def _create_tables():
    tables = _MYSQL_TABLES if _backend == "mysql" else _SQLITE_TABLES
    with get_connection() as conn:
        cursor = conn.cursor()
        for name, ddl in tables.items():
            try:
                cursor.execute(ddl)
            except Exception as err:
                logger.error("Failed creating table %s: %s", name, err)
        _migrate_columns(cursor)
        if _backend == "sqlite":
            for ddl in _SQLITE_INDEXES:
                cursor.execute(ddl)
        conn.commit()
        cursor.close()


# ═══════════════════════════════════════════════════════════════
# Connections
# ═══════════════════════════════════════════════════════════════

@contextmanager
def get_connection():
    """Synchronous connection context manager (commits on success, rolls back on error)."""
    if _backend is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    if _backend == "mysql":
        conn = _pool.get_connection()
    else:
        conn = sqlite3.connect(str(SQLITE_PATH), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _adapt(query: str) -> str:
    """Translate `%s` placeholders to `?` for SQLite."""
    if _backend == "sqlite":
        return query.replace("%s", "?")
    return query


def _rows_to_dicts(rows, dictionary: bool):
    if _backend == "sqlite" and rows is not None:
        if dictionary:
            return [dict(r) for r in rows]
        return [tuple(r) for r in rows]
    return rows


# ═══════════════════════════════════════════════════════════════
# Sync primitives (run inside worker threads)
# ═══════════════════════════════════════════════════════════════

def _sync_execute(query: str, params: tuple = ()) -> None:
    """Execute a write query (INSERT/UPDATE/DELETE) synchronously."""
    if _backend == "sqlite":
        with _sqlite_lock, get_connection() as conn:
            conn.execute(_adapt(query), params)
        return
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        cursor.close()


def _sync_fetchone(query: str, params: tuple = (), dictionary: bool = False):
    """Execute a query and return one row synchronously."""
    with get_connection() as conn:
        if _backend == "sqlite":
            row = conn.execute(_adapt(query), params).fetchone()
            if row is None:
                return None
            return dict(row) if dictionary else tuple(row)
        cursor = conn.cursor(dictionary=dictionary)
        cursor.execute(query, params)
        row = cursor.fetchone()
        cursor.close()
        return row


def _sync_fetchall(query: str, params: tuple = (), dictionary: bool = False):
    """Execute a query and return all rows synchronously."""
    with get_connection() as conn:
        if _backend == "sqlite":
            rows = conn.execute(_adapt(query), params).fetchall()
            return _rows_to_dicts(rows, dictionary)
        cursor = conn.cursor(dictionary=dictionary)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows


# ═══════════════════════════════════════════════════════════════
# Async wrappers — never block the event loop
# ═══════════════════════════════════════════════════════════════

async def async_execute(query: str, params: tuple = ()) -> None:
    await asyncio.to_thread(_sync_execute, query, params)


async def async_fetchone(query: str, params: tuple = (), dictionary: bool = False):
    return await asyncio.to_thread(_sync_fetchone, query, params, dictionary)


async def async_fetchall(query: str, params: tuple = (), dictionary: bool = False):
    return await asyncio.to_thread(_sync_fetchall, query, params, dictionary)
