"""
user_service.py — User Management (Async)
============================================
Handles user creation and lookups using MySQL.
All operations are async to avoid blocking the event loop.
"""

from typing import Optional
import logging
from app.services.database import async_execute, async_fetchone

logger = logging.getLogger(__name__)


async def upsert_user(email: str, name: str = "", picture: str = "") -> dict:
    """Insert or update user on Google login."""
    try:
        # Insert if not exists, otherwise update name and picture
        await async_execute(
            """
            INSERT INTO users (email, name, picture) 
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            name = VALUES(name), picture = VALUES(picture)
            """,
            (email, name, picture),
        )
        
        # Fetch the updated/inserted user
        user = await async_fetchone(
            "SELECT id, email, name, picture, created_at, updated_at FROM users WHERE email = %s",
            (email,),
            dictionary=True,
        )
        return dict(user) if user else {}
    except Exception as e:
        logger.error(f"Failed to upsert user {email}: {e}")
        return {}


async def get_user_by_email(email: str) -> Optional[dict]:
    """Fetch user record by email."""
    try:
        user = await async_fetchone(
            "SELECT id, email, name, picture, created_at, updated_at FROM users WHERE email = %s",
            (email,),
            dictionary=True,
        )
        return dict(user) if user else None
    except Exception as e:
        logger.error(f"Failed to get user {email}: {e}")
        return None


async def user_exists(email: str) -> bool:
    """Quick existence check."""
    try:
        result = await async_fetchone(
            "SELECT 1 FROM users WHERE email = %s LIMIT 1",
            (email,),
        )
        return result is not None
    except Exception as e:
        logger.error(f"Failed to check if user exists {email}: {e}")
        return False
