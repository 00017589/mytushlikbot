import datetime
import pytz
from bson.objectid import ObjectId
from database import get_collection
from config import DEFAULT_DAILY_PRICE, DEFAULT_INITIAL_BALANCE
from pymongo.errors import DuplicateKeyError
from pymongo import ReadPreference
import logging

logger = logging.getLogger(__name__)

class User:
    def __init__(
        self,
        telegram_id: int,
        name: str,
        phone: str,
        balance: int = 0,
        daily_price: int = DEFAULT_DAILY_PRICE,
        attendance: list = None,
        transactions: list = None,
        is_admin: bool = False,
        created_at: datetime.datetime = None,
        _id: ObjectId = None,
        **kwargs
    ):
        self._id = _id
        self.telegram_id = telegram_id
        self.name = name
        self.phone = phone
        self.balance = balance
        self.daily_price = daily_price
        self.attendance = attendance or []
        self.transactions = transactions or []
        self.is_admin = is_admin
        self.created_at = created_at or datetime.datetime.utcnow()

    @classmethod
    async def create(cls, telegram_id, name, phone):
        # Check if user already exists
        users_col = await get_collection("users")
        existing = await users_col.find_one({"telegram_id": telegram_id})
        if existing:
            return cls(**existing)
        
        doc = {
            "telegram_id": telegram_id,
            "name": name,
            "phone": phone,
            "balance": DEFAULT_INITIAL_BALANCE,
            "daily_price": DEFAULT_DAILY_PRICE,
            "attendance": [],
            "transactions": [],
            "is_admin": False,
        }
        await users_col.insert_one(doc)
        return cls(**doc)

    @classmethod
    async def find_by_id(cls, telegram_id: int):
        users_col = await get_collection("users")
        raw = await users_col.find_one({"telegram_id": telegram_id})
        if not raw:
            return None
        return cls(
            telegram_id  = raw["telegram_id"],
            name         = raw["name"],
            phone        = raw["phone"],
            balance      = raw["balance"],
            daily_price  = raw["daily_price"],
            attendance   = raw.get("attendance", []),
            transactions = raw.get("transactions", []),
            is_admin     = raw.get("is_admin", False),
            _id          = raw["_id"],
        )

    @staticmethod
    async def find_all():
        logger.info("find_all: Starting to fetch all user documents using PRIMARY read preference...")
        count = 0
        users_col = await get_collection("users")
        # Force read from PRIMARY node
        async for doc in users_col.with_options(
                read_preference=ReadPreference.PRIMARY
            ).find({}):
            count += 1
            yield User(
                telegram_id=doc["telegram_id"],
                name=doc["name"],
                phone=doc["phone"],
                balance=doc.get("balance", 25000),
                daily_price=doc.get("daily_price", 25000),
                attendance=doc.get("attendance", []),
                transactions=doc.get("transactions", []),
                is_admin=doc.get("is_admin", False),
                created_at=doc.get("created_at"),
                _id=doc.get("_id")
            )
        logger.info(f"find_all: Finished fetching. Processed {count} documents from PRIMARY.")

    async def save(self):
        # Log the state and type just before the database call
        logger.info(f"save: Updating user {self.telegram_id}. Data to set: name={self.name}, balance={self.balance}, attendance={self.attendance}, is_admin={self.is_admin}")
        try:
            users_col = await get_collection("users")
            update_result = await users_col.update_one(
                {"telegram_id": self.telegram_id},
                {"$set": {
                    "name": self.name,
                    "phone": self.phone,
                    "balance": self.balance,
                    "daily_price": self.daily_price,
                    "attendance": self.attendance,
                    "transactions": self.transactions,
                    "is_admin": self.is_admin,
                }},
                upsert=False
            )
            logger.info(f"save: Update result for {self.telegram_id}: matched={update_result.matched_count}, modified={update_result.modified_count}")
        except Exception as e:
            logger.error(f"save: FAILED to update user {self.telegram_id}. Error: {e}", exc_info=True)

    async def delete(self):
        users_col = await get_collection("users")
        await users_col.delete_one({"user_id": self.telegram_id})

    def _record_txn(self, txn_type: str, amount: int, desc: str):
        now_iso = datetime.datetime.utcnow().isoformat()
        self.transactions.append({
            "type": txn_type,
            "amount": amount,
            "desc": desc,
            "date": now_iso
        })

    async def change_name(self, new_name: str):
        self.name = new_name
        self._record_txn("name_change", 0, f"Name changed to {new_name}")
        await self.save()

    async def update_balance(self, amount: int, desc: str = "Balance adjustment"):
        self.balance += amount
        self._record_txn("balance", amount, desc)
        await self.save()

    async def set_daily_price(self, price: int):
        self.daily_price = price
        self._record_txn("price_update", 0, f"Daily price set to {price}")
        await self.save()

    async def get_food_choice(self, date_str: str, is_test: bool = False) -> str:
        """Get food choice for a specific date"""
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        result = await collection.find_one(
            {"telegram_id": self.telegram_id, "date": date_str}
        )
        return result["food_choice"] if result else None

    async def set_food_choice(self, date_str: str, food: str, is_test: bool = False):
        """Set food choice for a specific date"""
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        logger.info(f"Setting {'test ' if is_test else ''}food choice for user {self.name} on {date_str}: {food}")
        await collection.update_one(
            {"telegram_id": self.telegram_id, "date": date_str},
            {"$set": {
                "telegram_id": self.telegram_id,
                "date": date_str,
                "food_choice": food,
                "user_name": self.name
            }},
            upsert=True
        )

    async def remove_food_choice(self, date_str: str, is_test: bool = False):
        """Remove food choice for a specific date"""
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        await collection.delete_one(
            {"telegram_id": self.telegram_id, "date": date_str}
        )

    @staticmethod
    async def get_daily_food_counts(date_str: str, is_test: bool = False) -> dict:
        """Get aggregated food counts for a specific date"""
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        pipeline = [
            {"$match": {"date": date_str}},
            {"$group": {
                "_id": "$food_choice",
                "count": {"$sum": 1},
                "users": {"$push": "$user_name"}
            }},
            {"$sort": {"count": -1}}
        ]
        
        result = {}
        async for doc in collection.aggregate(pipeline):
            food_choice = doc["_id"]
            if food_choice:
                result[food_choice] = {
                    "count": doc["count"],
                    "users": doc["users"]
                }
        return result

    async def add_attendance(self, date_str: str, food: str = None, is_test: bool = False):
        """Add attendance and food choice for a date"""
        logger.info(f"add_attendance: Called for user {self.name}, date {date_str}, food {food}, is_test={is_test}")
        
        if date_str not in self.attendance:
            self.attendance.append(date_str)
            self.balance -= self.daily_price
            
            # Record transaction
            now_iso = datetime.datetime.utcnow().isoformat()
            self.transactions.append({
                "type": "attendance",
                "amount": -self.daily_price,
                "desc": f"Lunch on {date_str}",
                "date": now_iso
            })
            
            # Set food choice in appropriate collection
            if food:
                await self.set_food_choice(date_str, food, is_test)
            
            await self.save()

    async def remove_attendance(self, date_str: str, is_test: bool = False):
        """Remove attendance and food choice for a date"""
        if date_str in self.attendance:
            self.attendance.remove(date_str)
            self.balance += self.daily_price
            self._record_txn("cancel", self.daily_price, f"Cancel lunch on {date_str}")
            
            # Remove food choice from appropriate collection
            await self.remove_food_choice(date_str, is_test)
            
            await self.save()

    @staticmethod
    async def cleanup_old_food_choices(is_test: bool = False):
        """Clean up food choices older than today"""
        tz = pytz.timezone("Asia/Tashkent")
        today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        
        collection = await get_collection("test_food_choices" if is_test else "daily_food_choices")
        result = await collection.delete_many(
            {"date": {"$lt": today}}
        )
        logger.info(f"Cleaned up {result.deleted_count} old {'test ' if is_test else ''}food choice records")

    async def promote_to_admin(self):
        self.is_admin = True
        self._record_txn("admin", 0, "Promoted to admin")
        await self.save()

    async def demote_from_admin(self):
        self.is_admin = False
        self._record_txn("admin", 0, "Demoted from admin")
        await self.save()