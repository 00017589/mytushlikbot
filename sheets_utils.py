import gspread
import logging
from google.oauth2.service_account import Credentials
from functools import wraps
import asyncio

# Initialize logger
logger = logging.getLogger(__name__)

# Google Sheets setup
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_NAME = 'tushlik'
WORKSHEET_NAME = 'Sheet1'
CREDENTIALS_FILE = 'credentials.json'

def to_async(func):
    @wraps(func)
    async def run(*args, loop=None, executor=None, **kwargs):
        if loop is None:
            loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))
    return run

@to_async
def get_worksheet():
    """Get the worksheet object. Now async-compatible."""
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        gc = gspread.authorize(creds)
        sh = gc.open(SHEET_NAME)
        worksheet = sh.worksheet(WORKSHEET_NAME)
        return worksheet
    except Exception as e:
        logger.error(f"Error getting worksheet: {str(e)}")
        return None

@to_async
def fetch_all_rows():
    """Fetch all rows from the worksheet. Now async-compatible."""
    try:
        worksheet = get_worksheet()
        return worksheet.get_all_records()
    except Exception as e:
        logger.error(f"Error fetching rows: {str(e)}")
        return []

async def update_user_balance_in_sheet(telegram_id: int, new_balance: float) -> bool:
    """Update a user's balance in Google Sheets."""
    try:
        worksheet = await get_worksheet()
        if not worksheet:
            return False

        # Find and update the user's row
        cell = worksheet.find(str(telegram_id))
        if cell:
            row = cell.row
            worksheet.update_cell(row, 4, new_balance)  # Column D is balance
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating balance in sheet: {str(e)}")
        return False

async def sync_balances_from_sheet(context=None) -> dict:
    """Sync all balances from Google Sheets to database."""
    try:
        worksheet = await get_worksheet()
        if not worksheet:
            return {"success": False, "error": "Failed to get worksheet"}

        data = worksheet.get_all_records()
        updated = 0
        errors = 0

        for row in data:
            try:
                # Skip rows with missing or invalid Telegram ID
                telegram_id = row.get('Telegram ID')
                if not telegram_id:
                    continue
                
                try:
                    telegram_id = int(telegram_id)
                except (ValueError, TypeError):
                    continue

                # Handle balance with proper error checking
                balance_str = row.get('Balance')
                if balance_str is None:
                    balance = 0
                else:
                    try:
                        # Remove any spaces and commas
                        balance_str = str(balance_str).replace(' ', '').replace(',', '')
                        balance = float(balance_str)
                    except (ValueError, TypeError):
                        balance = 0
                
                # Update in database
                from database import users_col
                result = await users_col.update_one(
                    {"telegram_id": telegram_id},
                    {"$set": {"balance": balance}}
                )
                
                if result.modified_count > 0:
                    updated += 1
                else:
                    errors += 1
            except Exception as e:
                errors += 1
                logger.error(f"Error updating user from sheet: {str(e)}")

        return {
            "success": True,
            "updated": updated,
            "errors": errors
        }
    except Exception as e:
        logger.error(f"Error syncing from sheet: {str(e)}")
        return {"success": False, "error": str(e)} 