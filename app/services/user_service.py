"""
user_service.py — User Management (Async)
============================================
Handles user creation on login. All operations are async to
avoid blocking the event loop. Upserts differ per SQL dialect, so
this is one of the few places that checks `dialect()`.
"""

import logging
from app.services.database import async_execute, async_fetchone, dialect

logger = logging.getLogger(__name__)

_SELECT_USER = "SELECT id, email, name, picture, created_at, updated_at FROM users WHERE email = %s"


async def upsert_user(email: str, name: str = "", picture: str = "") -> dict:
    """Insert or update user on Google login."""
    try:
        if dialect() == "mysql":
            await async_execute(
                """
                INSERT INTO users (email, name, picture)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                name = VALUES(name), picture = VALUES(picture)
                """,
                (email, name, picture),
            )
        else:
            await async_execute(
                """
                INSERT INTO users (email, name, picture)
                VALUES (%s, %s, %s)
                ON CONFLICT(email) DO UPDATE SET
                name = excluded.name, picture = excluded.picture,
                updated_at = CURRENT_TIMESTAMP
                """,
                (email, name, picture),
            )

        user = await async_fetchone(_SELECT_USER, (email,), dictionary=True)
        return dict(user) if user else {}
    except Exception as e:
        logger.error(f"Failed to upsert user {email}: {e}")
        return {}
