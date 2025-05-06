import os
import json
import gspread
import logging
import asyncio
import pymongo
from google.oauth2.service_account import Credentials
from functools import wraps
from database import users_col

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
        if str(rec.get("Telegram ID")) == str(telegram_id):
            return rec
    return None

async def update_user_balance_in_sheet(telegram_id: int, new_balance: float) -> bool:
    ws = await get_worksheet()
    if not ws:
        return False
    try:
        cell = ws.find(str(telegram_id))
        ws.update_cell(cell.row, 4, new_balance)
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
        tid = row.get("Telegram ID")
        try:
            tid = int(tid)
            bal = float(str(row.get("Balance", 0)).replace(",", ""))
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

async def sync_balances_incremental() -> int:
    """
    Efficient: pull DB snapshot, pull sheet once, bulk‐write only diffs.
    Returns number of changed balances.
    """
    # 1) DB snapshot
    db_users = await users_col.find({}, {"telegram_id": 1, "balance": 1}).to_list(None)
    db_map = {u["telegram_id"]: u["balance"] for u in db_users}

    # 2) Sheet snapshot
    ws = await get_worksheet()
    rows = ws.get_all_records()

    # 3) Diffs
    ops = []
    for row in rows:
        try:
            tid = int(row["Telegram ID"])
            bal_sheet = float(str(row.get("Balance", 0)).replace(",", ""))
            if tid in db_map and db_map[tid] != bal_sheet:
                ops.append(pymongo.UpdateOne(
                    {"telegram_id": tid},
                    {"$set": {"balance": bal_sheet}}
                ))
        except Exception:
            continue

    # 4) Bulk
    if ops:
        result = await users_col.bulk_write(ops)
        return len(ops)
    return 0
