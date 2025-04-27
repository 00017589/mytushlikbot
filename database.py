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

# Motor client & database handle
_client = None
db = None
users_col = None
kassa_col = None
daily_food_choices_col = None
test_food_choices_col = None  # New collection for test data
card_details_col = None  # New collection for card details

async def get_collection(collection_name: str):
    """Get a collection, initializing the database if needed"""
    global _client, db, users_col, kassa_col, daily_food_choices_col, test_food_choices_col, card_details_col
    
    if _client is None:
        await init_db()
    
    if collection_name == "users":
        return users_col
    elif collection_name == "kassa":
        return kassa_col
    elif collection_name == "daily_food_choices":
        return daily_food_choices_col
    elif collection_name == "test_food_choices":
        return test_food_choices_col
    elif collection_name == "card_details":
        return card_details_col
    else:
        raise ValueError(f"Unknown collection: {collection_name}")

async def init_db():
    """Initialize database connection and indexes"""
    global _client, db, users_col, kassa_col, daily_food_choices_col, test_food_choices_col, card_details_col
    
    try:
        # Initialize client and collections
        _client = AsyncIOMotorClient(MONGODB_URI)
        db = _client["lunch_bot"]
        users_col = db["users"]
        kassa_col = db["kassa"]
        daily_food_choices_col = db["daily_food_choices"]
        test_food_choices_col = db["test_food_choices"]  # Initialize test collection
        card_details_col = db["card_details"]  # Initialize card details collection
        
        # Create indexes
        await users_col.create_index("telegram_id", unique=True)
        await users_col.create_index("is_admin")
        await users_col.create_index("attendance")
        await kassa_col.create_index("date", unique=True)
        await daily_food_choices_col.create_index([("date", 1), ("telegram_id", 1)], unique=True)
        await test_food_choices_col.create_index([("date", 1), ("telegram_id", 1)], unique=True)  # Test collection index
        
        # Insert default kassa record if not exists
        tz = pytz.timezone("Asia/Tashkent")
        today = datetime.now(tz).strftime("%Y-%m-%d")
        
        if not await kassa_col.find_one({"date": today}):
            await kassa_col.insert_one({
                "date": today,
                "balance": 0,
                "transactions": []
            })
            
        # Insert default card details if not exists
        if not await card_details_col.find_one({}):
            await card_details_col.insert_one({
                "card_number": "4097840201138901",
                "card_owner": "Abdukarimov Hasan"
            })
            
        print("Database initialized successfully")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise

def run_init():
    """
    Synchronous helper to initialize indexes before polling starts.
    Call this once in main.py.
    """
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
