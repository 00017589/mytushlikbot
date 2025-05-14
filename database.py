# database.py

import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from datetime import datetime
import pytz

# Load environment variables from .env
load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI must be set in your environment or .env")

# Global handles
_client = None
db = None
users_col = None
kassa_col = None
daily_food_choices_col = None
card_details_col = None
menu_col = None

async def init_db():
    """Initialize MongoDB client, collections, and indexes."""
    global _client, db, users_col, kassa_col, daily_food_choices_col, card_details_col, menu_col, cancelled_lunches_col

    _client = AsyncIOMotorClient(MONGODB_URI)
    db = _client["lunch_bot"]

    users_col = db["users"]
    kassa_col = db["kassa"]
    daily_food_choices_col = db["daily_food_choices"]
    card_details_col = db["card_details"]
    menu_col = db["menu"]

    # Users
    await users_col.create_index("telegram_id", unique=True)
    await users_col.create_index("is_admin")
    await users_col.create_index("attendance")
    await users_col.update_many(
        {"declined_days": {"$exists": False}},
        {"$set": {"declined_days": []}}
    )

    # Kassa
    await kassa_col.create_index("date", unique=True)

    # Daily food choices
    await daily_food_choices_col.create_index(
        [("date", 1), ("telegram_id", 1)], unique=True
    )

    cancelled_lunches_col = db.get_collection("cancelled_lunches")
    # ensure it exists
    await cancelled_lunches_col.update_one(
        {"_meta": "init"},
        {"$setOnInsert": {"_meta": "init"}},
        upsert=True
    )

    # Card details
    await card_details_col.create_index("card_number", unique=True)

    # Menu
    await menu_col.create_index("name", unique=True)

    # Ensure today’s kassa record
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).strftime("%Y-%m-%d")
    if not await kassa_col.find_one({"date": today}):
        await kassa_col.insert_one({"date": today, "balance": 0, "transactions": []})

    # Ensure card details exist
    if not await card_details_col.find_one({}):
        await card_details_col.insert_one({
            "card_number": "4097840201138901",
            "card_owner": "Abdukarimov Hasan"
        })

    # Ensure menu1 & menu2
    for name, defaults in (("menu1", []), ("menu2", [])):
        if not await menu_col.find_one({"name": name}):
            await menu_col.insert_one({"name": name, "items": defaults})

async def get_collection(name: str):
    """Return the requested collection, initializing DB if needed."""
    global cancelled_lunches_col, _client, db, users_col, kassa_col, daily_food_choices_col, card_details_col, menu_col

    if _client is None:
        await init_db()

    if name == "users":
        return users_col
    if name == "kassa":
        return kassa_col
    if name == "daily_food_choices":
        return daily_food_choices_col
    if name == "card_details":
        return card_details_col
    if name == "menu":
        return menu_col

    raise ValueError(f"Unknown collection: {name}")

def run_init():
    """Sync helper to initialize DB before polling starts."""
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
