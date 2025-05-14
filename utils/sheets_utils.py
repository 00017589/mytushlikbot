import os
import json
import gspread
import logging
import asyncio
import pymongo
from google.oauth2.service_account import Credentials
from functools import wraps
from database import users_col, get_collection
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_NAME = 'tushlik'
WORKSHEET_NAME = 'Sheet1'

def get_creds():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)

def to_async(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return wrapper

@to_async
def _open_worksheet():
    gc = gspread.authorize(get_creds())
    sh = gc.open(SHEET_NAME)
    return sh.worksheet(WORKSHEET_NAME)

async def get_worksheet():
    return await _open_worksheet()

async def find_user_in_sheet(telegram_id: int) -> dict | None:
    ws = await get_worksheet()
    if not ws:
        return None
    for rec in ws.get_all_records():
        if str(rec.get("telegram_id")) == str(telegram_id):
            return rec
    return None

async def update_user_balance_in_sheet(telegram_id: int, new_balance: float) -> bool:
    ws = await get_worksheet()
    if not ws:
        return False
    try:
        cell = ws.find(str(telegram_id))
        ws.update_cell(cell.row, 3, new_balance)
        return True
    except Exception:
        return False

async def sync_balances_from_sheet(context=None) -> dict:
    """
    Naïve full sync: fetch every row, update each in Mongo.
    """
    ws = await get_worksheet()
    if not ws:
        return {"success": False, "error": "no worksheet"}
    updated = errors = 0
    for row in ws.get_all_records():
        tid = row.get("telegram_id")
        try:
            tid = int(tid)
            bal = float(str(row.get("balance", 0)).replace(",", ""))
            res = await users_col.update_one(
                {"telegram_id": tid},
                {"$set": {"balance": bal}}
            )
            if res.modified_count:
                updated += 1
        except Exception as e:
            logger.error("sheet sync err %s", e)
            errors += 1
    return {"success": True, "updated": updated, "errors": errors}

# in utils/sheets_utils.py

async def sync_balances_incremental():
    # 1) Grab a fresh handle to the users col
    users_collection = await get_collection("users")

    # 2) Snapshot DB
    db_users = await users_collection.find(
        {}, {"telegram_id": 1, "balance": 1}
    ).to_list(length=None)
    db_map = {u["telegram_id"]: u["balance"] for u in db_users}

    # 3) Fetch sheet rows once
    worksheet = await get_worksheet()
    rows = worksheet.get_all_records()

    updates = []
    for row in rows:
        # tolerate both "Telegram ID" and "TelegramID"
        raw_id = row.get("telegram_id") 
        if not raw_id:
            continue
        try:
            tg_id = int(raw_id)
        except ValueError:
            continue

        raw_bal = row.get("balance")
        try:
            bal_sheet = float(str(raw_bal).replace(",", ""))
        except (TypeError, ValueError):
            continue

        bal_db = db_map.get(tg_id)
        if bal_db is not None and bal_db != bal_sheet:
            updates.append((tg_id, bal_sheet))

    # 4) Bulk update
    if updates:
        ops = [
            pymongo.UpdateOne({"telegram_id": tg}, {"$set": {"balance": bal}})
            for tg, bal in updates
        ]
        await users_collection.bulk_write(ops)

    # return list of updated IDs
    return [tg for tg, _ in updates]

async def get_price_from_sheet(telegram_id: int) -> float:
    """
    Look up the row for this telegram_id in column B and return
    the 'daily_price' from column E.
    """
    ws = await get_worksheet()
    # find returns a Cell with .row
    cell = ws.find(str(telegram_id), in_column=2)
    raw = ws.cell(cell.row, 5).value  # column E is index 5 (1-based)
    return float(raw.replace(',', '').strip())

async def sync_prices_from_sheet(context: ContextTypes.DEFAULT_TYPE = None) -> dict:
    """
    Fetches every row from the sheet and updates each user's `daily_price` in MongoDB.
    """
    ws = await get_worksheet()
    if not ws:
        return {"success": False, "error": "Could not open worksheet"}
    updated = errors = 0

    # Assumes your sheet has columns: telegram_id | name | balance | ... | daily_price
    for row in ws.get_all_records():
        try:
            tid   = int(row.get("telegram_id", 0))
            price = float(str(row.get("daily_price", 0)).replace(",", "").strip())
            res = await users_col.update_one(
                {"telegram_id": tid},
                {"$set": {"daily_price": price}}
            )
            if res.modified_count:
                updated += 1
        except Exception as e:
            logger.error("sync_prices_from_sheet error on row %r: %s", row, e)
            errors += 1

    return {"success": True, "updated": updated, "errors": errors}