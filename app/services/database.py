import re
import asyncio
import mysql.connector
from mysql.connector import errorcode
from mysql.connector.pooling import MySQLConnectionPool
from contextlib import contextmanager
from app.config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
import logging

logger = logging.getLogger(__name__)

# Global connection pool
_pool = None

def init_db():
    global _pool
    # First, ensure the database exists
    try:
        # Validate database name to prevent SQL injection
        if not re.match(r"^[a-zA-Z0-9_]+$", MYSQL_DATABASE):
            raise ValueError(f"Invalid database name: {MYSQL_DATABASE!r}. Only alphanumeric characters and underscores are allowed.")

        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD
        )
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DATABASE}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        conn.commit()
        cursor.close()
        conn.close()
    except mysql.connector.Error as err:
        logger.error(f"Failed to create database: {err}")
        raise

    # Initialize connection pool
    try:
        _pool = MySQLConnectionPool(
            pool_name="cadence_pool",
            pool_size=5,
            pool_reset_session=True,
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE
        )
    except mysql.connector.Error as err:
        logger.error(f"Failed to create connection pool: {err}")
        raise

    # Create tables
    tables = {}
    tables['users'] = """
        CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            email VARCHAR(255) NOT NULL UNIQUE,
            name VARCHAR(255),
            picture VARCHAR(512),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_email (email)
        )
    """
    
    tables['token_usage'] = """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INT AUTO_INCREMENT PRIMARY KEY,
            user_key VARCHAR(255) NOT NULL,
            tokens INT NOT NULL,
            consumed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_user_key (user_key),
            INDEX idx_consumed_at (consumed_at)
        )
    """
    
    tables['llm_metrics'] = """
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
            status VARCHAR(64) NOT NULL DEFAULT 'success'
        )
    """
    
    tables['tts_metrics'] = """
        CREATE TABLE IF NOT EXISTS tts_metrics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(64) NOT NULL,
            voice VARCHAR(255) NOT NULL,
            char_count INT NOT NULL DEFAULT 0,
            duration_sec FLOAT,
            status VARCHAR(64) NOT NULL DEFAULT 'success'
        )
    """
    
    tables['cycle_metrics'] = """
        CREATE TABLE IF NOT EXISTS cycle_metrics (
            id INT AUTO_INCREMENT PRIMARY KEY,
            timestamp VARCHAR(64) NOT NULL,
            cycle_type VARCHAR(255) NOT NULL,
            total_latency_ms FLOAT NOT NULL DEFAULT 0,
            tasks_json LONGTEXT NOT NULL
        )
    """
    
    with get_connection() as conn:
        cursor = conn.cursor()
        for name, ddl in tables.items():
            try:
                cursor.execute(ddl)
            except mysql.connector.Error as err:
                logger.error(f"Failed creating table {name}: {err}")
        conn.commit()
        cursor.close()

@contextmanager
def get_connection():
    """Synchronous connection context manager (used for startup and sync code)."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_db() first.")
    
    conn = _pool.get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally: 
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Async Database Helpers
# ═══════════════════════════════════════════════════════════════
# These use asyncio.to_thread() to offload blocking MySQL calls
# to a thread pool, preventing event loop blocking in FastAPI.
# ═══════════════════════════════════════════════════════════════

def _sync_execute(query: str, params: tuple = ()) -> None:
    """Execute a write query (INSERT/UPDATE/DELETE) synchronously."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        cursor.close()


def _sync_fetchone(query: str, params: tuple = (), dictionary: bool = False):
    """Execute a query and return one row synchronously."""
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=dictionary)
        cursor.execute(query, params)
        row = cursor.fetchone()
        cursor.close()
        return row


def _sync_fetchall(query: str, params: tuple = (), dictionary: bool = False):
    """Execute a query and return all rows synchronously."""
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=dictionary)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows


async def async_execute(query: str, params: tuple = ()) -> None:
    """Async wrapper: execute a write query without blocking the event loop."""
    await asyncio.to_thread(_sync_execute, query, params)


async def async_fetchone(query: str, params: tuple = (), dictionary: bool = False):
    """Async wrapper: fetch one row without blocking the event loop."""
    return await asyncio.to_thread(_sync_fetchone, query, params, dictionary)


async def async_fetchall(query: str, params: tuple = (), dictionary: bool = False):
    """Async wrapper: fetch all rows without blocking the event loop."""
    return await asyncio.to_thread(_sync_fetchall, query, params, dictionary)

