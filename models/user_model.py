import datetime
import pytz
from bson.objectid import ObjectId
from database import get_collection
from config import DEFAULT_DAILY_PRICE, DEFAULT_INITIAL_BALANCE
from pymongo import ReadPreference
import logging

logger = logging.getLogger(__name__)

class User:
    def __init__(
        self,
        telegram_id: int,
        name: str,
        phone: str,
        balance: int = DEFAULT_INITIAL_BALANCE,
        daily_price: int = DEFAULT_DAILY_PRICE,
        attendance: list = None,
        transactions: list = None,
        is_admin: bool = False,
        created_at: datetime.datetime = None,
        declined_days: list = None,
        _id: ObjectId = None,
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
        self.declined_days = declined_days or []

    @classmethod
    async def create(cls, telegram_id, name, phone):
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
            "declined_days": [],
            "created_at": datetime.datetime.utcnow(),
        }
        await users_col.insert_one(doc)
        return cls(**doc)

    @classmethod
    async def find_by_id(cls, telegram_id: int):
        users_col = await get_collection("users")
        raw = await users_col.find_one({"telegram_id": telegram_id})
        if not raw:
            return None
        return cls(**raw)

    @staticmethod
    async def find_all():
        users_col = await get_collection("users")
        async for doc in users_col.with_options(read_preference=ReadPreference.PRIMARY).find({}):
            yield User(**doc)

    async def save(self):
        users_col = await get_collection("users")
        await users_col.update_one(
            {"telegram_id": self.telegram_id},
            {"$set": {
                "name": self.name,
                "phone": self.phone,
                "balance": self.balance,
                "daily_price": self.daily_price,
                "attendance": self.attendance,
                "transactions": self.transactions,
                "is_admin": self.is_admin,
                "declined_days": self.declined_days,
            }}
        )

    def _record_txn(self, txn_type: str, amount: int, desc: str):
        now_iso = datetime.datetime.utcnow().isoformat()
        self.transactions.append({
            "type": txn_type,
            "amount": amount,
            "desc": desc,
            "date": now_iso
        })

    @staticmethod
    async def get_daily_food_counts(date_str: str) -> dict:
        col = await get_collection("daily_food_choices")
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
        async for doc in col.aggregate(pipeline):
            if doc["_id"]:
                result[doc["_id"]] = {
                    "count": doc["count"],
                    "users": doc["users"]
                }
        return result

    async def add_attendance(self, date_str: str, food: str = None):
        if date_str not in self.attendance:
            self.attendance.append(date_str)
            self.balance -= self.daily_price
            self._record_txn("attendance", -self.daily_price, f"Lunch on {date_str}")

            # record food choice
            if food:
                col = await get_collection("daily_food_choices")
                await col.update_one(
                    {"telegram_id": self.telegram_id, "date": date_str},
                    {"$set": {
                        "telegram_id": self.telegram_id,
                        "date": date_str,
                        "food_choice": food,
                        "user_name": self.name
                    }},
                    upsert=True
                )

            await self.save()

    async def remove_attendance(self, date_str: str):
        if date_str in self.attendance:
            self.attendance.remove(date_str)
            self.balance += self.daily_price
            self._record_txn("cancel", self.daily_price, f"Cancel lunch on {date_str}")

            # remove food choice
            col = await get_collection("daily_food_choices")
            await col.delete_one({"telegram_id": self.telegram_id, "date": date_str})

            await self.save()

    async def decline_attendance(self, date_str: str):
        if date_str not in self.declined_days:
            self.declined_days.append(date_str)
            await self.save()

    async def remove_decline(self, date_str: str):
        if date_str in self.declined_days:
            self.declined_days.remove(date_str)
            await self.save()

    @staticmethod
    async def cleanup_old_food_choices():
        tz = pytz.timezone("Asia/Tashkent")
        today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
        col = await get_collection("daily_food_choices")
        await col.delete_many({"date": {"$lt": today}})

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

    async def promote_to_admin(self):
        self.is_admin = True
        self._record_txn("admin", 0, "Promoted to admin")
        await self.save()

    async def demote_from_admin(self):
        self.is_admin = False
        self._record_txn("admin", 0, "Demoted from admin")
        await self.save()
    async def set_food_choice(self, date: str, food: str):
        # save into MongoDB daily_food_choices collection
        col = await get_collection("daily_food_choices")
        await col.update_one(
            {"telegram_id": self.telegram_id, "date": date},
            {"$set": {"food_choice": food, "user_name": self.name}},
            upsert=True
        )
        # also keep it in the in‑memory object if you want
        self.food_choices[date] = food
