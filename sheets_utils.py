import os
import json
import gspread
import logging
import asyncio
from functools import wraps
from datetime import datetime
import datetime as dt
import pytz
from google.oauth2.service_account import Credentials
from database import users_col

# Initialize logger
logger = logging.getLogger(__name__)

# Google Sheets setup
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_ID = '1pjMXd8NBkDq1NgT7-QwjY5NkYUs9R7EG-t9N4Zgl1_M'
WORKSHEET_NAME = 'Sheet1'

# Cache for worksheet to minimize API calls
_worksheet_cache = None
_cache_timestamp = None
_CACHE_EXPIRY_SECONDS = 5 * 60  # 5 minutes

def get_creds():
    """Load service account credentials from the environment."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON environment variable not set!")
    creds_dict = json.loads(creds_json)
    return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

def to_async(func):
    """Decorator to run blocking functions in a thread pool."""
    @wraps(func)
    async def run(*args, loop=None, executor=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))
    return run

@to_async
def get_worksheet(use_cache=True):
    """Get the worksheet object (by ID), with optional caching."""
    global _worksheet_cache, _cache_timestamp

    now = datetime.now()
    if use_cache and _worksheet_cache and _cache_timestamp:
        if (now - _cache_timestamp).total_seconds() < _CACHE_EXPIRY_SECONDS:
            return _worksheet_cache

    try:
        creds = get_creds()
        gc = gspread.authorize(creds)

        # Open by spreadsheet ID instead of title
        sh = gc.open_by_key(SHEET_ID)
        worksheet = sh.worksheet(WORKSHEET_NAME)

        # Update cache
        _worksheet_cache = worksheet
        _cache_timestamp = now
        return worksheet

    except Exception as e:
        logger.error(f"Unexpected error getting worksheet: {repr(e)} (type: {type(e)})")
        if hasattr(e, 'status_code'):
            logger.error(f"Response status: {e.status_code}, content: {getattr(e, 'content', '')}")
        return None

async def fetch_all_rows():
    """Fetch all rows from the worksheet as records."""
    worksheet = await get_worksheet()
    if not worksheet:
        return []
    try:
        return worksheet.get_all_records()
    except Exception as e:
        logger.error(f"Error fetching rows: {str(e)}")
        return []

async def clear_cache():
    """Force-clear the worksheet cache."""
    global _worksheet_cache, _cache_timestamp
    _worksheet_cache = None
    _cache_timestamp = None
    return True

async def update_user_balance_in_sheet(telegram_id: int, new_balance: float) -> bool:
    """Update a user's balance in Google Sheets."""
    try:
        worksheet = await get_worksheet(use_cache=False)
        if not worksheet:
            return False

        cell = worksheet.find(str(telegram_id))
        if not cell:
            logger.warning(f"User {telegram_id} not found in sheet")
            return False

        row = cell.row
        # Assuming column 3 (C) is for balance (1-indexed in Sheets)
        worksheet.update_cell(row, 3, new_balance)
        logger.info(f"Updated balance for user {telegram_id} to {new_balance} in sheet")
        return True

    except Exception as e:
        logger.error(f"Error updating balance in sheet: {str(e)}")
        return False

async def sync_balances_from_sheet(context=None) -> dict:
    """Sync all balances from Google Sheets to database."""
    try:
        worksheet = await get_worksheet(use_cache=False)
        if not worksheet:
            return {"success": False, "error": "Failed to get worksheet"}

        data = worksheet.get_all_records()
        updated = 0
        errors = 0
        skipped = 0

        

        for row in data:
            try:
                telegram_id = row.get('Telegram ID')
                if not telegram_id:
                    skipped += 1
                    continue

                try:
                    telegram_id = int(telegram_id)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid Telegram ID format: {telegram_id}")
                    errors += 1
                    continue

                balance_str = row.get('Balance')
                if balance_str is None:
                    balance = 0
                else:
                    try:
                        balance = float(str(balance_str).replace(' ', '').replace(',', ''))
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid balance format for user {telegram_id}: {balance_str}")
                        balance = 0

                result = await users_col.update_one(
                    {"telegram_id": telegram_id},
                    {"$set": {"balance": balance}}
                )
                if result.modified_count > 0:
                    updated += 1
                    logger.info(f"Updated balance for user {telegram_id} to {balance}")

            except Exception as e:
                errors += 1
                logger.error(f"Error updating user from sheet: {str(e)}")

        logger.info(f"Balance sync completed: {updated} updated, {errors} errors, {skipped} skipped")
        return {"success": True, "updated": updated, "errors": errors, "skipped": skipped}

    except Exception as e:
        logger.error(f"Error syncing from sheet: {str(e)}")
        return {"success": False, "error": str(e)}

async def find_user_in_sheet(telegram_id: int):
    """Find user in sheet and return their row data."""
    try:
        worksheet = await get_worksheet()
        if not worksheet:
            return None

        records = worksheet.get_all_records()
        for record in records:
            if str(record.get('Telegram ID')) == str(telegram_id):
                return record
        return None

    except Exception as e:
        logger.error(f"Error finding user in sheet: {str(e)}")
        return None
