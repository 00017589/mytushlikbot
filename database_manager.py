import logging
import asyncio
from typing import Any, Optional, Dict, List
from datetime import datetime
import pytz
from db import db

logger = logging.getLogger(__name__)
TASHKENT_TZ = pytz.timezone("Asia/Tashkent")

class DatabaseManager:
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._cache = {}
        self._cache_timeout = 300  # 5 minutes

    async def _execute_with_retry(self, operation: str, func: callable, *args, **kwargs) -> Any:
        """Execute database operation with retry mechanism"""
        retry_count = 0
        last_error = None

        while retry_count < self.max_retries:
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"Database operation {operation} successful")
                return result
            except Exception as e:
                retry_count += 1
                last_error = e
                logger.warning(f"Database operation {operation} failed (attempt {retry_count}): {str(e)}")
                if retry_count < self.max_retries:
                    await asyncio.sleep(self.retry_delay)

        logger.error(f"Database operation {operation} failed after {self.max_retries} attempts: {str(last_error)}")
        raise last_error

    async def get_user(self, user_id: str) -> Optional[Dict]:
        """Get user with retries and caching"""
        cache_key = f"user_{user_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        user = await self._execute_with_retry(
            "get_user",
            lambda: db.get_user(user_id)
        )
        if user:
            self._cache[cache_key] = user
        return user

    async def update_user(self, user_id: str, update_data: dict) -> bool:
        """Update user with validation and retries"""
        try:
            # Validate required fields
            required_fields = ["name", "phone", "balance", "daily_price"]
            for field in required_fields:
                if field not in update_data:
                    raise ValueError(f"Missing required field: {field}")

            # Validate data types
            if not isinstance(update_data["balance"], (int, float)):
                raise ValueError("Balance must be a number")
            if not isinstance(update_data["daily_price"], (int, float)):
                raise ValueError("Daily price must be a number")

            result = await self._execute_with_retry(
                "update_user",
                lambda: db.update_user(user_id, update_data)
            )

            # Clear cache
            cache_key = f"user_{user_id}"
            if cache_key in self._cache:
                del self._cache[cache_key]

            return bool(result)
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {str(e)}")
            raise

    async def get_daily_attendance(self, date: str) -> Optional[Dict]:
        """Get daily attendance with retries and caching"""
        cache_key = f"attendance_{date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        attendance = await self._execute_with_retry(
            "get_daily_attendance",
            lambda: db.get_daily_attendance(date)
        )
        if attendance:
            self._cache[cache_key] = attendance
        return attendance

    async def update_daily_attendance(self, date: str, update_data: dict) -> bool:
        """Update daily attendance with validation and retries"""
        try:
            # Validate data structure
            if not isinstance(update_data.get("confirmed", []), list):
                raise ValueError("Confirmed must be a list")
            if not isinstance(update_data.get("declined", []), list):
                raise ValueError("Declined must be a list")
            if not isinstance(update_data.get("menu", {}), dict):
                raise ValueError("Menu must be a dictionary")

            result = await self._execute_with_retry(
                "update_daily_attendance",
                lambda: db.update_daily_attendance(date, update_data)
            )

            # Clear cache
            cache_key = f"attendance_{date}"
            if cache_key in self._cache:
                del self._cache[cache_key]

            return bool(result)
        except Exception as e:
            logger.error(f"Error updating daily attendance for {date}: {str(e)}")
            raise

    async def create_backup(self) -> bool:
        """Create backup with retries"""
        return await self._execute_with_retry(
            "create_backup",
            lambda: db.create_backup()
        )

    async def restore_from_backup(self, backup_dir: str, timestamp: str) -> bool:
        """Restore from backup with retries"""
        return await self._execute_with_retry(
            "restore_from_backup",
            lambda: db.restore_from_backup(backup_dir, timestamp)
        )

    def clear_cache(self):
        """Clear the entire cache"""
        self._cache.clear()

# Create a global instance
db_manager = DatabaseManager() 