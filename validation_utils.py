import re
from typing import Optional, List
from telegram import ReplyKeyboardMarkup
from models.user_model import User
from database import users_col

_NAME_RE  = re.compile(r"^[A-Za-z\u0400-\u04FF'][A-Za-z\u0400-\u04FF' ]{1,49}$")
_PHONE_RE = re.compile(r"^\+?998\d{9}$")

def validate_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name.strip()))

def validate_phone(phone: str) -> bool:
    # Remove any non-digit characters except + at the start
    cleaned = re.sub(r'[^\d+]', '', phone)
    # Add + if not present
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return bool(_PHONE_RE.fullmatch(cleaned))

# in utils.py or wherever you have users_col
async def any_admins_exist() -> bool:
    return (await users_col.count_documents({"is_admin": True}, limit=1)) > 0


def get_default_kb(is_admin: bool, has_food_selection: bool = False):
    row1 = ["💸 Balansim", "✏️ Ism o'zgartirish"]
    row2 = ["💳 Karta Raqami"]
    if has_food_selection:
        row2.append("❌ Tushlikni bekor qilish")
    if is_admin:
        row1.append("🔧 Admin panel")
    return ReplyKeyboardMarkup([row1, row2], resize_keyboard=True)

async def get_user_async(telegram_id: int) -> Optional[User]:
    # first try the new field, then fall back to the old one
    doc = await users_col.find_one({
        "$or": [
            {"telegram_id": telegram_id},
            {"user_id":     telegram_id},
        ]
    })
    if not doc:
        return None

    # unify on telegram_id
    t_id = doc.get("telegram_id") or doc.get("user_id")
    return User(
        telegram_id=t_id,
        name        = doc.get("name",     ""),
        phone       = doc.get("phone",    ""),
        balance     = doc.get("balance",  0),
        daily_price = doc.get("daily_price", 0),
        attendance  = doc.get("attendance", []),
        transactions= doc.get("transactions", []),
        is_admin    = bool(doc.get("is_admin")),
        declined_days = doc.get("declined_days", []),
        created_at  = doc.get("created_at"),
        _id         = doc.get("_id")
    )

async def get_all_users_async() -> List[User]:
    cursor = users_col.find({})
    users = []
    async for doc in cursor:
        t_id = doc.get("telegram_id") or doc.get("user_id")
        users.append(User(
            telegram_id=t_id,
            name        = doc.get("name",     ""),
            phone       = doc.get("phone",    ""),
            balance     = doc.get("balance",  0),
            daily_price = doc.get("daily_price", 0),
            attendance  = doc.get("attendance", []),
            transactions= doc.get("transactions", []),
            is_admin    = bool(doc.get("is_admin")),
            declined_days = doc.get("declined_days", []),
            created_at  = doc.get("created_at"),
            _id         = doc.get("_id")
        ))
    return users

async def is_admin(user_id):
    from database import users_col
    user = await users_col.find_one({"telegram_id": user_id})
    return user and user.get("is_admin", False)
