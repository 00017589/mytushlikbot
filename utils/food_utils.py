import logging
from database import get_collection

logger = logging.getLogger(__name__)

async def get_food_choices_for_date(date_str: str, is_test: bool = False) -> list:
    """
    Get all food choices for a specific date.
    Args:
        date_str: Date string in YYYY-MM-DD format
        is_test: Whether to use test collection
    Returns:
        List of dicts containing food choices and user info
    """
    try:
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        cursor = collection.find({"date": date_str})
        choices = []
        async for doc in cursor:
            choices.append({
                "telegram_id": doc["telegram_id"],
                "user_name": doc["user_name"],
                "food_name": doc["food_choice"],
                "date": doc["date"]
            })
        return choices
    except Exception as e:
        logger.error(f"Error getting food choices for date {date_str}: {str(e)}")
        return [] 