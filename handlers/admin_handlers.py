# handlers/admin_handlers.py

import datetime
import logging
import re
import pytz
from datetime import datetime, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)
from pymongo import ReadPreference
from database import users_col, get_collection

from models.user_model import User
from utils import (
    validate_name,
    validate_phone,
    get_default_kb,
    get_user_async,
    get_all_users_async,
    is_admin,
)
from config import DEFAULT_DAILY_PRICE

# Initialize logger
logger = logging.getLogger(__name__)

# Add new constants for lunch cancellation
CANCEL_LUNCH_DAY = "cancel_lunch_day"
CANCEL_LUNCH_DATE = "cancel_lunch_date"
CANCEL_LUNCH_REASON = "cancel_lunch_reason"

# ─── BUTTON LABELS ─────────────────────────────────────────────────────────────
FOYD_BTN      = "Foydalanuvchilar"
ADD_ADMIN_BTN = "Admin Qo'shish"
REMOVE_ADMIN_BTN = "Admin olib tashlash"
DAILY_PRICE_BTN = "Kunlik narx"
ADJ_BAL_BTN   = "Balansni o'zgartirish"
DELETE_USER_BTN = "Foydalanuvchini o'chirish"
CXL_LUNCH_ALL_BTN = "Tushlikni bekor qilish"
KASSA_BTN     = "Kassa"
CARD_MANAGE_BTN = "Karta ma'lumotlarini o'zgartirish"
BACK_BTN      = "Ortga"

# ─── KASSA SUBMENU BUTTONS ─────────────────────────────────────────────────────
KASSA_BAL_BTN = "Balans"
KASSA_ADD_BTN = "Kassa qo'shish"
KASSA_SUB_BTN = "Kassa ayrish"

# ─── STATES ────────────────────────────────────────────────────────────────────
(
    S_ADD_ADMIN,      # selecting user to promote
    S_REMOVE_ADMIN,   # selecting admin to demote
    S_SET_PRICE,      # entering new daily price
    S_ADJ_USER,       # selecting user to adjust balance
    S_ADJ_AMOUNT,     # entering amount to adjust
    S_DELETE_USER,    # selecting user to delete
    S_KASSA_AMOUNT,   # entering kassa amount
    S_CANCEL_DATE,    # entering date to cancel
    S_CANCEL_REASON,  # entering reason for cancellation
    S_NOTIFY_MESSAGE, # entering notification message
    S_NOTIFY_CONFIRM, # confirming notification
    S_CARD_NUMBER,    # entering new card number
    S_CARD_OWNER      # entering new card owner name
) = range(13)

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def get_admin_kb():
    return ReplyKeyboardMarkup([
        [FOYD_BTN],
        [ADD_ADMIN_BTN, REMOVE_ADMIN_BTN],
        [DAILY_PRICE_BTN, ADJ_BAL_BTN],
        [DELETE_USER_BTN, CXL_LUNCH_ALL_BTN],
        [KASSA_BTN],
        [CARD_MANAGE_BTN],
        [BACK_BTN],
    ], resize_keyboard=True)

def get_kassa_kb():
    return ReplyKeyboardMarkup([
        [KASSA_BAL_BTN],
        [KASSA_ADD_BTN, KASSA_SUB_BTN],
        [BACK_BTN],
    ], resize_keyboard=True)

# ─── 1) /admin ENTRY & FIRST‐TIME SETUP ────────────────────────────────────────
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    
    # Check if any admin exists
    admin_exists = await users_col.count_documents({"is_admin": True}, limit=1) > 0
    
    if not admin_exists:
        # Check if user already exists
        existing_user = await users_col.find_one({"telegram_id": tg_id})
        if existing_user:
            # Update existing user to admin
            await users_col.update_one(
                {"telegram_id": tg_id},
                {"$set": {"is_admin": True}}
            )
        else:
            # Create new admin user
            await users_col.insert_one({
                "telegram_id": tg_id,
                "name": update.effective_user.full_name,
                "phone": "", # Assuming empty phone for auto-created admin
                "balance": 0,
                "daily_price": 25000,
                "is_admin": True,
                "attendance": [], # Ensure required fields are present
                "transactions": [],
                "food_choices": {}
            })
            await update.message.reply_text("✅ Siz birinchi admin bo'ldingiz!")

    # Now, outside the if/else block, check if the current user is an admin
    user = await users_col.find_one({"telegram_id": tg_id})
    if user and user.get("is_admin", False):
        await update.message.reply_text(
            "🔧 Admin panelga xush kelibsiz:",
            reply_markup=get_admin_kb()
        )
    else:
        # This else corresponds to the check if user is admin
        await update.message.reply_text("❌ Siz admin emassiz!")
    
    return ConversationHandler.END

# ─── 2) BACK TO MAIN MENU ───────────────────────────────────────────────────────
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_admin = await is_admin(update.effective_user.id)
    kb = get_default_kb(is_admin)
    
    if update.callback_query:
        # Handle callback query case
        await update.callback_query.answer()
        await update.callback_query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Bosh menyu:",
            reply_markup=kb
        )
    else:
        # Handle message case
        await update.message.reply_text(
            "Bosh menyu:",
            reply_markup=kb
        )
    return ConversationHandler.END

# ─── 3) LIST USERS ──────────────────────────────────────────────────────────────
def format_users_list(users: list[User]) -> str:
    if not users:
        return "Hech qanday foydalanuvchi yo'q."
    lines = []
    for u in users:
        lines.append(
            f"• *{u.name}* `(ID: {u.telegram_id})`\n"
            f"   💰 Balans: *{u.balance:,}* so'm | 📝 Narx: *{u.daily_price:,}* so'm"
        )
    return "\n\n".join(lines)

async def list_users_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await get_all_users_async()
    await update.message.reply_text(
        format_users_list(users),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_admin_kb()
    )
    return ConversationHandler.END

# ─── 4) ADMIN PROMOTION / DEMOTION ─────────────────────────────────────────────
async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Get only non-admin users
    users = await users_col.find({"is_admin": False}).to_list(length=None)
    if not users:
        await update.message.reply_text(
            "Barcha foydalanuvchilar allaqachon admin!",
            reply_markup=get_admin_kb()
        )
        return

    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            f"{user['name']}",
            callback_data=f"add_admin:{user['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    await update.message.reply_text(
        "Admin qilmoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def add_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    if query.data.startswith("add_admin:"):
        user_id = int(query.data.split(":")[1])
        await users_col.update_one(
            {"telegram_id": user_id},
            {"$set": {"is_admin": True}}
        )
        user = await users_col.find_one({"telegram_id": user_id})
        await query.message.edit_text(
            f"✅ {user['name']} admin qilindi!"
        )
        await start_add_admin(update, context)

async def start_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Get only admin users
    users = await users_col.find({"is_admin": True}).to_list(length=None)
    if not users:
        await update.message.reply_text(
            "Adminlar mavjud emas!",
            reply_markup=get_admin_kb()
        )
        return

    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            f"{user['name']}",
            callback_data=f"remove_admin:{user['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    await update.message.reply_text(
        "Adminlikdan olib tashlamoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    if query.data.startswith("remove_admin:"):
        user_id = int(query.data.split(":")[1])
        await users_col.update_one(
            {"telegram_id": user_id},
            {"$set": {"is_admin": False}}
        )
        user = await users_col.find_one({"telegram_id": user_id})
        await query.message.edit_text(
            f"✅ {user['name']} adminlikdan olib tashlandi!"
        )
        await start_remove_admin(update, context)

# ─── 5) SET PRICE ───────────────────────────────────────────────────────────────
async def start_daily_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await users_col.find().to_list(length=None)
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            f"{user['name']} ({user.get('daily_price', 0):,} so'm)",
            callback_data=f"set_price:{user['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    await update.message.reply_text(
        "Kunlik narxini o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def daily_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    if query.data == "back_to_price_list":
        await start_daily_price(update, context)
        return
    
    if query.data.startswith("set_price:"):
        user_id = int(query.data.split(":")[1])
        user = await users_col.find_one({"telegram_id": user_id})
        keyboard = [[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]]
        await query.message.edit_text(
            f"{user['name']} uchun yangi kunlik narxni kiriting:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["pending_price_user"] = user_id
        return S_SET_PRICE

async def handle_daily_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the daily price input"""
    try:
        price = int(update.message.text)
        if price < 0:
            raise ValueError
        
        user_id = context.user_data.get("pending_price_user")
        if not user_id:
            await update.message.reply_text(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_admin_kb()
            )
            return ConversationHandler.END
        
        # Update user's daily price
        await users_col.update_one(
            {"telegram_id": user_id},
            {"$set": {"daily_price": price}}
        )
        
        # Get updated user info
        user = await users_col.find_one({"telegram_id": user_id})
        
        # Send confirmation and return to admin panel
        await update.message.reply_text(
            f"✅ {user['name']} uchun kunlik narx {price:,} so'mga o'zgartirildi!",
            reply_markup=get_admin_kb()
        )
        
        # Clear the stored data
        if "pending_price_user" in context.user_data:
            del context.user_data["pending_price_user"]
        
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Iltimos, to'g'ri raqam kiriting!",
            reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
        )
        return S_SET_PRICE

# ─── 6) DELETE USER ─────────────────────────────────────────────────────────────
async def start_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await users_col.find().to_list(length=None)
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            f"{user['name']}",
            callback_data=f"delete_user:{user['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    if update.callback_query:
        await update.callback_query.message.edit_text(
            "O'chirmoqchi bo'lgan foydalanuvchini tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "O'chirmoqchi bo'lgan foydalanuvchini tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def delete_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    if query.data.startswith("delete_user:"):
        user_id = int(query.data.split(":")[1])
        user = await users_col.find_one({"telegram_id": user_id})
        await users_col.delete_one({"telegram_id": user_id})
        await query.message.edit_text(
            f"✅ {user['name']} o'chirildi!"
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔧 Admin panelga xush kelibsiz:",
            reply_markup=get_admin_kb()
        )

# ─── 7) KASSA PANEL & ACTIONS ─────────────────────────────────────────────────
async def start_kassa_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = await kassa_col.find_one({}) or {}
    bal = doc.get("balance", 0)
    
    keyboard = [
        [
            InlineKeyboardButton("Qo'shish", callback_data="kassa_add"),
            InlineKeyboardButton("Ayrish", callback_data="kassa_sub")
        ],
        [InlineKeyboardButton("Ortga", callback_data="back_to_menu")]
    ]
    
    await update.message.reply_text(
        f"Kassa balansi: {bal:,} so'm\n"
        "Qo'shish yoki Ayrish tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def kassa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    if query.data == "kassa_add":
        keyboard = [[InlineKeyboardButton("Ortga", callback_data="kassa_back")]]
        await query.message.edit_text(
            "Summani raqamda kiriting:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["pending_kassa"] = "add"
    
    elif query.data == "kassa_sub":
        keyboard = [[InlineKeyboardButton("Ortga", callback_data="kassa_back")]]
        await query.message.edit_text(
            "Summani raqamda kiriting:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["pending_kassa"] = "sub"
    
    elif query.data == "kassa_back":
        await start_kassa_panel(update, context)

async def handle_kassa_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending_kassa" not in context.user_data:
        return
    
    try:
        amount = int(update.message.text)
        if amount == 0:  # Only prevent zero amounts
            raise ValueError
        
        action = context.user_data["pending_kassa"]
        doc = await kassa_col.find_one({}) or {"balance": 0}
        current_balance = doc.get("balance", 0)
        
        if action == "add":
            new_balance = current_balance + amount
            await kassa_col.update_one(
                {},
                {"$set": {"balance": new_balance}},
                upsert=True
            )
            await update.message.reply_text(
                f"✅ Kassa balansiga +{amount:,} so'm qo'shildi.\n"
                f"Yangi balans: {new_balance:,} so'm"
            )
        else:
            new_balance = current_balance - amount
            await kassa_col.update_one(
                {},
                {"$set": {"balance": new_balance}},
                upsert=True
            )
            await update.message.reply_text(
                f"✅ Kassa balansidan -{amount:,} so'm ayirildi.\n"
                f"Yangi balans: {new_balance:,} so'm"
            )
        
        del context.user_data["pending_kassa"]
        await start_kassa_panel(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting!")

# ─── BALANCE ADJUSTMENT ─────────────────────────────────────────────────────
async def start_adjust_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = await users_col.find().to_list(length=None)
    keyboard = []
    for user in users:
        keyboard.append([InlineKeyboardButton(
            f"{user['name']} ({user['balance']:,} so'm)",
            callback_data=f"adj_user:{user['telegram_id']}"
        )])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    await update.message.reply_text(
        "Balansni o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def adjust_balance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    if query.data.startswith("adj_user:"):
        user_id = int(query.data.split(":")[1])
        user = await users_col.find_one({"telegram_id": user_id})
        keyboard = [
            [
                InlineKeyboardButton("Qo'shish", callback_data=f"add_bal:{user_id}"),
                InlineKeyboardButton("Ayrish", callback_data=f"sub_bal:{user_id}")
            ],
            [InlineKeyboardButton("Ortga", callback_data="back_to_menu")]
        ]
        await query.message.edit_text(
            f"{user['name']} balansi: {user['balance']:,} so'm\n"
            "Qo'shish yoki Ayrish tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif query.data.startswith(("add_bal:", "sub_bal:")):
        action, user_id = query.data.split(":")
        user_id = int(user_id)
        user = await users_col.find_one({"telegram_id": user_id})
        
        keyboard = [[InlineKeyboardButton("Ortga", callback_data=f"adj_user:{user_id}")]]
        await query.message.edit_text(
            f"{user['name']} balansi: {user['balance']:,} so'm\n"
            "Summani raqamda kiriting:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["pending_amount"] = {
            "user_id": user_id,
            "action": "add" if action == "add_bal" else "sub"
        }

async def handle_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "pending_amount" not in context.user_data:
        return
    
    try:
        amount = int(update.message.text)
        if amount == 0:  # Only prevent zero amounts
            raise ValueError
        
        data = context.user_data["pending_amount"]
        user_id = data["user_id"]
        action = data["action"]
        
        user = await users_col.find_one({"telegram_id": user_id})
        if action == "add":
            new_balance = user["balance"] + amount
            await users_col.update_one(
                {"telegram_id": user_id},
                {"$set": {"balance": new_balance}}
            )
            await update.message.reply_text(
                f"✅ {user['name']} balansiga +{amount:,} so'm qo'shildi.\n"
                f"Yangi balans: {new_balance:,} so'm"
            )
        else:
            new_balance = user["balance"] - amount
            await users_col.update_one(
                {"telegram_id": user_id},
                {"$set": {"balance": new_balance}}
            )
            await update.message.reply_text(
                f"✅ {user['name']} balansidan -{amount:,} so'm ayirildi.\n"
                f"Yangi balans: {new_balance:,} so'm"
            )
        
        del context.user_data["pending_amount"]
        await start_adjust_balance(update, context)
        
    except ValueError:
        await update.message.reply_text("❌ Raqam kiriting!")

# ─── 9) BROADCAST & TEST SURVEY ─────────────────────────────────────────────────
async def notify_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = await get_user_async(update.effective_user.id)
    if not caller or not caller.is_admin:
        return await update.message.reply_text("❌ Siz admin emassiz.")
    
    await update.message.reply_text(
        "⚠️ Barcha foydalanuvchilarga yubormoqchi bo'lgan xabarni kiriting:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
    )
    return S_NOTIFY_MESSAGE

async def handle_notify_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the notification message input"""
    if update.message.text == BACK_BTN:
        await update.message.reply_text(
            "Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    # Store the message temporarily
    context.user_data['notify_message'] = update.message.text
    
    # Add confirmation step with proper keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ha", callback_data="notify_confirm")],
        [InlineKeyboardButton("Yo'q", callback_data="notify_cancel")]
    ])
    
    await update.message.reply_text(
        f"⚠️ Quyidagi xabarni barcha foydalanuvchilarga yuborishni tasdiqlaysizmi?\n\n"
        f"{update.message.text}",
        reply_markup=keyboard
    )
    return S_NOTIFY_CONFIRM

async def notify_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "notify_cancel":
        await query.message.edit_text("❌ Xabar yuborish bekor qilindi.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    message = context.user_data.get('notify_message')
    if not message:
        await query.message.edit_text("❌ Xabar topilmadi. Iltimos, qaytadan boshlang.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    cnt = 0
    failed = []
    users = await get_all_users_async()
    
    # First edit message to show progress
    await query.message.edit_text("⏳ Xabar yuborilmoqda...")
    
    # Store message ID for later updates
    context.user_data['notify_message_id'] = query.message.message_id
    
    # Initialize response tracking with food choices
    context.user_data['notify_responses'] = {
        'yes': [],
        'no': [],
        'total_sent': 0,
        'failed': [],
        'message': message,
        'food_choices': {}  # Track food choices
    }
    
    for u in users:
        try:
            # Send message with inline keyboard for response
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Ha", callback_data=f"notify_response:yes:{u.telegram_id}")],
                [InlineKeyboardButton("Yo'q", callback_data=f"notify_response:no:{u.telegram_id}")]
            ])
            await context.bot.send_message(
                u.telegram_id,
                f"{message}\n\nJavob bering:",
                reply_markup=keyboard
            )
            cnt += 1
        except Exception as e:
            failed.append(f"{u.name} ({u.telegram_id})")
    
    # Update response tracking
    context.user_data['notify_responses']['total_sent'] = cnt
    context.user_data['notify_responses']['failed'] = failed
    
    # Show initial results
    result_text = f"✅ {cnt} foydalanuvchiga yuborildi."
    if failed:
        result_text += f"\n❌ {len(failed)} foydalanuvchiga yuborilmadi:\n" + "\n".join(failed)
    
    await query.message.edit_text(result_text)
    
    # Schedule final summary at 10:00 AM
    now = datetime.now()
    target_time = now.replace(hour=10, minute=0, second=0, microsecond=0)
    if now > target_time:
        target_time += timedelta(days=1)
    
    delay = (target_time - now).total_seconds()
    context.job_queue.run_once(send_final_summary, delay, data={
        'chat_id': update.effective_chat.id,
        'message_id': query.message.message_id
    })
    
    # Return to admin panel
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Admin panel:",
        reply_markup=get_admin_kb()
    )
    
    return ConversationHandler.END

async def send_final_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send final summary at 10:00 AM"""
    job = context.job
    chat_id = job.data['chat_id']
    message_id = job.data['message_id']
    
    if 'notify_responses' in context.user_data:
        responses = context.user_data['notify_responses']
        total_sent = responses['total_sent']
        yes_count = len(responses['yes'])
        no_count = len(responses['no'])
        pending = total_sent - yes_count - no_count
        
        summary = (
            f"📊 Xabar yuborish yakuniy natijalari:\n\n"
            f"👥 Jami: {total_sent} kishi\n\n"
            f"📝 Ro'yxat:\n"
        )
        
        # Add yes responses
        for i, user in enumerate(responses['yes'], 1):
            summary += f"{i}. {user}\n"
        
        # Add food choices if available
        if responses['food_choices']:
            summary += f"\n🍽 Taomlar statistikasi:\n"
            for food, users in responses['food_choices'].items():
                summary += f"{len(users)}. {food} — {len(users)} ta\n"
        
        # Add no responses
        if responses['no']:
            summary += f"\n❌ Rad etganlar:\n"
            for i, user in enumerate(responses['no'], 1):
                summary += f"{i}. {user}\n"
        
        # Add pending responses
        if pending > 0:
            summary += f"\n⏳ Javob bermaganlar:\n"
            all_users = set(f"{u['name']} ({u['telegram_id']})" for u in await get_all_users_async())
            responded = set(responses['yes'] + responses['no'])
            pending_users = all_users - responded
            for i, user in enumerate(pending_users, 1):
                summary += f"{i}. {user}\n"
        
        # Add failed deliveries
        if responses['failed']:
            summary += f"\n❌ Yuborilmadi:\n"
            for i, user in enumerate(responses['failed'], 1):
                summary += f"{i}. {user}\n"
        
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=summary
            )
        except Exception as e:
            print(f"Error sending final summary: {e}")
        
        # Clear the stored data
        if 'notify_responses' in context.user_data:
            del context.user_data['notify_responses']
        if 'notify_message_id' in context.user_data:
            del context.user_data['notify_message_id']

async def test_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = await get_user_async(update.effective_user.id)
    if not caller or not caller.is_admin:
        return await update.message.reply_text("❌ Siz admin emassiz.")
    
    # Add confirmation step
    keyboard = [
        [InlineKeyboardButton("Ha", callback_data="survey_confirm")],
        [InlineKeyboardButton("Yo'q", callback_data="survey_cancel")]
    ]
    await update.message.reply_text(
        "⚠️ Test so'rovini boshlashni tasdiqlaysizmi? So'rov 3 daqiqadan keyin yakunlanadi.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def survey_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "survey_cancel":
        await query.message.edit_text("❌ Test so'rovi bekor qilindi.")
        return
    
    from handlers.user_handlers import daily_attendance_request, send_summary
    try:
        await daily_attendance_request(context, is_test=True)
        context.job_queue.run_once(send_summary, when=180, data={'is_test': True})
        await query.message.edit_text(
            "✅ Test so'rovi yuborildi.\n"
            "⏳ 3 daqiqadan keyin natijalar yuboriladi."
        )
    except Exception as e:
        await query.message.edit_text(f"❌ Xatolik yuz berdi: {str(e)}")

# ─── CONVERSATION HANDLERS ──────────────────────────────────────────────────────
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing conversation and return to admin panel"""
    if update.message:
        await update.message.reply_text(
            "❌ Operatsiya bekor qilindi.",
            reply_markup=get_admin_kb()
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "❌ Operatsiya bekor qilindi."
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔧 Admin panelga xush kelibsiz:",
            reply_markup=get_admin_kb()
        )
    
    # Clear any pending data
    context.user_data.clear()
    return ConversationHandler.END

# ─── LUNCH CANCELLATION HANDLERS ────────────────────────────────────────────────
async def cancel_lunch_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the lunch cancellation process"""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Bu buyruq faqat adminlar uchun.")
        return

    await update.message.reply_text(
        "Qaysi kun uchun tushlikni bekor qilmoqchisiz? (YYYY-MM-DD formatida)\n"
        "Bugungi kun uchun bo'lsa, 'bugun' deb yozing."
    )
    return CANCEL_LUNCH_DATE

async def handle_cancel_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the date input for lunch cancellation"""
    date_input = update.message.text.strip().lower()
    
    if date_input == "bugun":
        tz = pytz.timezone("Asia/Tashkent")
        date_str = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    else:
        try:
            # Validate date format
            datetime.datetime.strptime(date_input, "%Y-%m-%d")
            date_str = date_input
        except ValueError:
            await update.message.reply_text(
                "Noto'g'ri format. Iltimos, YYYY-MM-DD formatida yoki 'bugun' deb yozing."
            )
            return CANCEL_LUNCH_DATE

    context.user_data['cancel_date'] = date_str
    await update.message.reply_text("Bekor qilish sababini kiriting:")
    return CANCEL_LUNCH_REASON

async def handle_cancel_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the reason input and process the cancellation"""
    reason = update.message.text.strip()
    date_str = context.user_data.get('cancel_date')
    
    if not date_str:
        await update.message.reply_text("Xatolik yuz berdi. Iltimos, qaytadan boshlang.")
        return ConversationHandler.END

    # Process cancellation
    users = await get_all_users_async()
    affected_users = []
    
    for user in users:
        if date_str in user.attendance:
            # Refund the user
            user.balance += user.daily_price
            user._record_txn("refund", user.daily_price, f"Lunch cancellation on {date_str}")
            # Remove attendance
            user.attendance.remove(date_str)
            if date_str in user.food_choices:
                del user.food_choices[date_str]
            await user.save()
            affected_users.append(user)

    # Send notifications
    for user in users:
        try:
            message = (
                f"⚠️ Eslatma: {date_str} kuni tushlik bekor qilindi.\n"
                f"Sabab: {reason}\n"
            )
            if user in affected_users:
                message += f"Balansingizga {user.daily_price} so'm qaytarildi."
            
            await context.bot.send_message(user.telegram_id, message)
        except:
            pass

    # Clear user data
    context.user_data.pop('cancel_date', None)
    
    await update.message.reply_text(
        f"✅ {date_str} uchun tushlik bekor qilindi.\n"
        f"Jami {len(affected_users)} ta foydalanuvchi ta'sirlandi."
    )
    return ConversationHandler.END

# ─── CARD MANAGEMENT ─────────────────────────────────────────────────────────
async def start_card_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the card management flow"""
    await update.message.reply_text(
        "Yangi karta raqamini kiriting:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
    )
    return S_CARD_NUMBER

async def handle_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the new card number input"""
    if update.message.text == BACK_BTN:
        await update.message.reply_text(
            "Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    # Store the card number temporarily
    context.user_data['new_card_number'] = update.message.text
    
    await update.message.reply_text(
        "Karta egasining ismini kiriting:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
    )
    return S_CARD_OWNER

async def handle_card_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the new card owner name input and save both to database"""
    if update.message.text == BACK_BTN:
        await update.message.reply_text(
            "Admin panel:",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END
    
    from database import get_collection
    
    card_details_col = await get_collection("card_details")
    
    # Update or insert new card details
    await card_details_col.update_one(
        {},  # empty filter to match any document
        {
            "$set": {
                "card_number": context.user_data['new_card_number'],
                "card_owner": update.message.text
            }
        },
        upsert=True  # create if doesn't exist
    )
    
    # Clear temporary data
    if 'new_card_number' in context.user_data:
        del context.user_data['new_card_number']
    
    await update.message.reply_text(
        "✅ Karta ma'lumotlari muvaffaqiyatli o'zgartirildi!",
        reply_markup=get_admin_kb()
    )
    return ConversationHandler.END

# ─── 10) REGISTER ALL HANDLERS ─────────────────────────────────────────────────
def register_handlers(app):
    # (1) plain commands
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("test_survey", test_survey))

    # (2) single‐step buttons
    for txt, fn in [
        (FOYD_BTN,     list_users_exec),
        (ADD_ADMIN_BTN,start_add_admin),
        (REMOVE_ADMIN_BTN,start_remove_admin),
        (DAILY_PRICE_BTN,start_daily_price),
        (ADJ_BAL_BTN, start_adjust_balance),
        (DELETE_USER_BTN,start_delete_user),
        (KASSA_BTN,    start_kassa_panel),
        (BACK_BTN,     back_to_menu),
    ]:
        app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(txt)}$"), fn))

    # Daily price conversation handler
    price_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{re.escape(DAILY_PRICE_BTN)}$"), start_daily_price),
            CallbackQueryHandler(daily_price_callback, pattern=r"^set_price:\d+$")
        ],
        states={
            S_SET_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_daily_price),
                CallbackQueryHandler(daily_price_callback, pattern=r"^back_to_price_list$")
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CallbackQueryHandler(daily_price_callback, pattern=r"^back_to_menu$"),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="price_conversation",
        per_message=True
    )
    app.add_handler(price_conv)

    # Balance adjustment conversation handler
    balance_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(ADJ_BAL_BTN)}$"), start_adjust_balance)],
        states={
            S_ADJ_USER: [CallbackQueryHandler(adjust_balance_callback, pattern=r"^(adj_user:\d+|back_to_menu)$")],
            S_ADJ_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_amount)]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="balance_conversation",
        per_message=True
    )
    app.add_handler(balance_conv)

    # Kassa conversation handler
    kassa_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(KASSA_BTN)}$"), start_kassa_panel)],
        states={
            S_KASSA_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_kassa_amount)]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="kassa_conversation",
        per_message=True
    )
    app.add_handler(kassa_conv)

    # Notify all conversation handler
    notify_conv = ConversationHandler(
        entry_points=[
            CommandHandler("notify_all", notify_all),
            MessageHandler(filters.Regex(r"^/notify_all$"), notify_all)
        ],
        states={
            S_NOTIFY_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notify_message)
            ],
            S_NOTIFY_CONFIRM: [
                CallbackQueryHandler(notify_confirm_callback, pattern=r"^notify_(confirm|cancel)$")
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="notify_conversation",
        per_message=True
    )
    app.add_handler(notify_conv)

    # Card management conversation handler
    card_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CARD_MANAGE_BTN)}$"), start_card_management)],
        states={
            S_CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_number)],
            S_CARD_OWNER: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_card_owner)],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="card_conversation",
        per_message=True
    )
    app.add_handler(card_conv)

    # Lunch cancellation conversation handler
    cancel_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CXL_LUNCH_ALL_BTN)}$"), cancel_lunch_day)],
        states={
            S_CANCEL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_date)],
            S_CANCEL_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_reason)],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="cancel_conversation",
        per_message=True
    )
    app.add_handler(cancel_conv)

    # (3) inline callbacks
    app.add_handler(CallbackQueryHandler(add_admin_callback, pattern=r"^(add_admin:\d+|back_to_menu)$"))
    app.add_handler(CallbackQueryHandler(remove_admin_callback, pattern=r"^(remove_admin:\d+|back_to_menu)$"))
    app.add_handler(CallbackQueryHandler(daily_price_callback, pattern=r"^(set_price:\d+|back_to_menu|back_to_price_list)$"))
    app.add_handler(CallbackQueryHandler(adjust_balance_callback, pattern=r"^(adj_user:\d+|add_bal:\d+|sub_bal:\d+|back_to_menu)$"))
    app.add_handler(CallbackQueryHandler(delete_user_callback, pattern=r"^(delete_user:\d+|back_to_menu)$"))
    app.add_handler(CallbackQueryHandler(kassa_callback, pattern=r"^(kassa_add|kassa_sub|kassa_back|back_to_menu)$"))
    app.add_handler(CallbackQueryHandler(survey_confirm_callback, pattern=r"^survey_(confirm|cancel)$"))
    app.add_handler(CallbackQueryHandler(notify_response_callback, pattern=r"^notify_response:(yes|no):\d+$"))

async def notify_response_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Parse the callback data
    _, response, user_id = query.data.split(":")
    user_id = int(user_id)

    # Get the user
    user = await users_col.find_one({"telegram_id": user_id})
    if not user:
        return

    # Update the message to show the response
    if response == "yes":
        await query.message.edit_text(f"{query.message.text}\n\n✅ Siz javob berdingiz: Ha")
    else:
        await query.message.edit_text(f"{query.message.text}\n\n❌ Siz javob berdingiz: Yo'q")

    # Track the response
    if 'notify_responses' in context.user_data:
        responses = context.user_data['notify_responses']
        if response == "yes":
            responses['yes'].append(f"{user['name']} ({user_id})")
            # Track food choice if available
            if 'food_choices' in user and user['food_choices']:
                latest_date = max(user['food_choices'].keys())
                food_choice = user['food_choices'][latest_date]
                if food_choice not in responses['food_choices']:
                    responses['food_choices'][food_choice] = []
                responses['food_choices'][food_choice].append(f"{user['name']} ({user_id})")
        else:
            responses['no'].append(f"{user['name']} ({user_id})")
