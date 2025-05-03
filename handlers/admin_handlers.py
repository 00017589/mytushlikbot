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
from sheets_utils import (
    fetch_all_rows,
    get_worksheet,
    update_user_balance_in_sheet,
    sync_balances_from_sheet
)
from utils.food_utils import get_food_choices_for_date
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
from telegram.error import BadRequest

# Initialize collections
kassa_col = None
menu_col = None

async def init_collections():
    global kassa_col, menu_col
    kassa_col = await get_collection("kassa")
    menu_col = await get_collection("menu")
    
    # Initialize menu collections if they don't exist
    if not await menu_col.find_one({"name": "menu1"}):
        await menu_col.insert_one({"name": "menu1", "items": []})
    if not await menu_col.find_one({"name": "menu2"}):
        await menu_col.insert_one({"name": "menu2", "items": []})

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
DELETE_USER_BTN = "Foydalanuvchini o'chirish"
CXL_LUNCH_ALL_BTN = "Tushlikni bekor qilish"
CARD_MANAGE_BTN = "Karta ma'lumotlarini o'zgartirish"
KASSA_BTN = "Kassa"
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

# Uzbek, short, button-based menu management for admins
MENU_BTN = "🍽 Menyu"
VIEW_MENU1_BTN = "1-Menuni ko'rish"
VIEW_MENU2_BTN = "2-Menuni ko'rish"
ADD_MENU1_BTN = "1-Menuga qo'shish"
ADD_MENU2_BTN = "2-Menuga qo'shish"
DEL_MENU1_BTN = "1-Menudan o'chirish"
DEL_MENU2_BTN = "2-Menudan o'chirish"
ORTGA_BTN = "Ortga"

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def get_admin_kb():
    return ReplyKeyboardMarkup([
        [FOYD_BTN],
        [ADD_ADMIN_BTN, REMOVE_ADMIN_BTN],
        [DAILY_PRICE_BTN],
        [DELETE_USER_BTN, CXL_LUNCH_ALL_BTN],
        [CARD_MANAGE_BTN],
        [KASSA_BTN],
        [MENU_BTN],
        [BACK_BTN],
    ], resize_keyboard=True)

def get_kassa_kb():
    return ReplyKeyboardMarkup([
        [KASSA_BAL_BTN],
        [KASSA_ADD_BTN, KASSA_SUB_BTN],
        [BACK_BTN],
    ], resize_keyboard=True)

# Menyu panel
def get_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(VIEW_MENU1_BTN, callback_data="view_menu1"), InlineKeyboardButton(VIEW_MENU2_BTN, callback_data="view_menu2")],
        [InlineKeyboardButton(ADD_MENU1_BTN, callback_data="add_menu1"), InlineKeyboardButton(ADD_MENU2_BTN, callback_data="add_menu2")],
        [InlineKeyboardButton(DEL_MENU1_BTN, callback_data="del_menu1"), InlineKeyboardButton(DEL_MENU2_BTN, callback_data="del_menu2")],
        [InlineKeyboardButton(ORTGA_BTN, callback_data="menu_back")],
    ])

# ─── 1) /admin ENTRY & FIRST-TIME SETUP ────────────────────────────────────────
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
    """Return to the main menu"""
    from utils import get_default_kb
    
    # Get user's admin status
    user = await users_col.find_one({"telegram_id": update.effective_user.id})
    is_admin = user and user.get("is_admin", False)
    
    # Get appropriate keyboard
    kb = get_default_kb(is_admin)
    
    if update.callback_query:
        # Handle callback query case
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(
            "Bosh menyu:",
            reply_markup=kb
        )
    else:
        # Handle message case
        await update.message.reply_text(
            "Bosh menyu:",
            reply_markup=kb
        )
    
    # Clear any conversation state
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
    """Show user list immediately and sync balances in background"""
    try:
        # First show the current list immediately
        users = await get_all_users_async()
        await update.message.reply_text(
            format_users_list(users),
            parse_mode=ParseMode.MARKDOWN
        )

        # Then start syncing in background
        loading_msg = await update.message.reply_text("⏳ Balanslar yangilanmoqda...")
        try:
            updated = await sync_all_user_balances_from_sheet()
            if updated > 0:
                # Only show updated list if there were changes
                users = await get_all_users_async()
                await loading_msg.edit_text(
                    format_users_list(users),
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await loading_msg.delete()
        except Exception as e:
            logger.error(f"Error syncing balances: {e}")
            await loading_msg.edit_text("❌ Balanslarni yangilashda xatolik yuz berdi.")

        # Show the admin keyboard
        await update.message.reply_text(
            "Admin panel:",
            reply_markup=get_admin_kb()
        )
    except Exception as e:
        logger.error(f"Error in list_users_exec: {e}")
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_admin_kb()
        )
    return ConversationHandler.END

async def sync_all_user_balances_from_sheet():
    """Sync all user balances from Google Sheets to the database. Returns the number of updated users."""
    try:
        worksheet = await get_worksheet()
        if not worksheet:
            logger.error("sync_all_user_balances_from_sheet: Could not get worksheet.")
            return 0

        # Get all values at once instead of cell by cell
        all_values = worksheet.get_all_values()
        if len(all_values) <= 1:  # Only header or empty
            return 0

        # Prepare bulk update operations
        bulk_ops = []
        updated = 0
        
        # Skip header row
        for row in all_values[1:]:
            try:
                if len(row) < 2 or not row[1].strip().isdigit():
                    continue
                    
                tg_id = int(row[1])
                try:
                    balance = float(str(row[2]).replace(',', ''))
                except (ValueError, TypeError):
                    continue

                bulk_ops.append(
                    UpdateOne(
                        {"telegram_id": tg_id},
                        {"$set": {"balance": balance}},
                        upsert=False
                    )
                )
            except Exception as e:
                logger.error(f"Error processing row {row}: {e}")
                continue

        if bulk_ops:
            # Execute bulk update
            result = await users_col.bulk_write(bulk_ops, ordered=False)
            updated = result.modified_count

        logger.info(f"sync_all_user_balances_from_sheet: Updated {updated} users.")
        return updated
    except Exception as e:
        logger.error(f"sync_all_user_balances_from_sheet: Fatal error: {e}")
        return 0

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
    """Start the daily price change flow"""
    logger.info("start_daily_price: Starting price change flow")
    try:
        users = await users_col.find().to_list(length=None)
        logger.info(f"start_daily_price: Found {len(users)} users")
        
        keyboard = []
        for user in users:
            keyboard.append([InlineKeyboardButton(
                f"{user['name']} ({user.get('daily_price', 0):,} so'm)",
                callback_data=f"set_price:{user['telegram_id']}"
            )])
        keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
        
        message_text = "Kunlik narxini o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:"
        
        if update.callback_query:
            logger.info("start_daily_price: Handling callback query")
            await update.callback_query.message.edit_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            logger.info("start_daily_price: Handling message")
            await update.message.reply_text(
                message_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logger.error(f"start_daily_price: Error occurred: {str(e)}", exc_info=True)
        raise

async def daily_price_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle price change callbacks"""
    logger.info("daily_price_callback: Received callback")
    try:
        query = update.callback_query
        await query.answer()
        logger.info(f"daily_price_callback: Callback data: {query.data}")
        
        if query.data == "back_to_menu":
            logger.info("daily_price_callback: Handling back to menu")
            await query.message.delete()
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Admin panel:",
                reply_markup=get_admin_kb()
            )
            return
        
        if query.data == "back_to_price_list":
            logger.info("daily_price_callback: Handling back to price list")
            await start_daily_price(update, context)
            return
        
        if query.data.startswith("set_price:"):
            logger.info("daily_price_callback: Handling set price selection")
            user_id = int(query.data.split(":")[1])
            logger.info(f"daily_price_callback: Selected user_id: {user_id}")
            
            user = await users_col.find_one({"telegram_id": user_id})
            if not user:
                logger.warning(f"daily_price_callback: User not found for id {user_id}")
                await query.message.edit_text(
                    "❌ Foydalanuvchi topilmadi.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
                )
                return
            
            # Store user_id in context for later use
            context.user_data["pending_price_user"] = user_id
            
            # Create a unique callback data for price input
            callback_data = f"confirm_price:{user_id}"
            
            keyboard = [
                [InlineKeyboardButton("25000", callback_data=f"{callback_data}:25000")],
                [InlineKeyboardButton("30000", callback_data=f"{callback_data}:30000")],
                [InlineKeyboardButton("35000", callback_data=f"{callback_data}:35000")],
                [InlineKeyboardButton("40000", callback_data=f"{callback_data}:40000")],
                [InlineKeyboardButton("Boshqa narx", callback_data=f"custom_price:{user_id}")],
                [InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]
            ]
            
            await query.message.edit_text(
                f"{user['name']} uchun yangi kunlik narxni tanlang:\n"
                f"Joriy narx: {user.get('daily_price', 0):,} so'm",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        if query.data.startswith("confirm_price:"):
            logger.info("daily_price_callback: Handling price confirmation")
            _, user_id, price = query.data.split(":")
            user_id = int(user_id)
            price = float(price)
            
            user = await users_col.find_one({"telegram_id": user_id})
            if not user:
                logger.warning(f"daily_price_callback: User not found for id {user_id}")
                await query.message.edit_text(
                    "❌ Foydalanuvchi topilmadi.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
                )
                return
            
            # Update user's daily price
            result = await users_col.update_one(
                {"telegram_id": user_id},
                {"$set": {"daily_price": price}}
            )
            
            if result.modified_count > 0:
                await query.message.edit_text(
                    f"✅ {user['name']} uchun kunlik narx {price:,.2f} so'mga o'zgartirildi!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
                )
            else:
                await query.message.edit_text(
                    "❌ O'zgarishlar kiritilmadi.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
                )
            return
        
        if query.data.startswith("custom_price:"):
            logger.info("daily_price_callback: Handling custom price input")
            user_id = int(query.data.split(":")[1])
            user = await users_col.find_one({"telegram_id": user_id})
            if not user:
                logger.warning(f"daily_price_callback: User not found for id {user_id}")
                await query.message.edit_text(
                    "❌ Foydalanuvchi topilmadi.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
                )
                return
            # Store user_id in context for later use
            context.user_data["pending_price_user"] = user_id
            keyboard = [[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]]
            await query.message.edit_text(
                f"{user['name']} uchun yangi kunlik narxni kiriting:\n"
                f"Joriy narx: {user.get('daily_price', 0):,} so'm",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    except Exception as e:
        logger.error(f"daily_price_callback: Error occurred: {str(e)}", exc_info=True)
        raise

async def handle_daily_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the daily price input"""
    logger.info("handle_daily_price: Received price input")
    try:
        user_id = context.user_data.get("pending_price_user")
        if not user_id:
            logger.warning("handle_daily_price: No pending user found")
            await update.message.reply_text(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_admin_kb()
            )
            return
        # Remove any spaces and commas from the input
        price_text = update.message.text.replace(" ", "").replace(",", "")
        logger.info(f"handle_daily_price: Cleaned price text: {price_text}")
        try:
            price = float(price_text)
            price = round(price, 2)
            logger.info(f"handle_daily_price: Parsed price: {price}")
        except ValueError:
            logger.warning(f"handle_daily_price: Invalid price format: {price_text}")
            await update.message.reply_text(
                "❌ Iltimos, to'g'ri raqam kiriting! (Masalan: 25000 yoki 25000.50)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
            )
            return
        if price < 0:
            logger.warning(f"handle_daily_price: Negative price: {price}")
            await update.message.reply_text(
                "❌ Narx manfiy bo'lishi mumkin emas!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Ortga", callback_data="back_to_price_list")]])
            )
            return
        user = await users_col.find_one({"telegram_id": user_id})
        if not user:
            logger.warning(f"handle_daily_price: User not found for id {user_id}")
            await update.message.reply_text(
                "❌ Foydalanuvchi topilmadi.",
                reply_markup=get_admin_kb()
            )
            return
        # Update user's daily price
        result = await users_col.update_one(
            {"telegram_id": user_id},
            {"$set": {"daily_price": price}}
        )
        if result.modified_count > 0:
            await update.message.reply_text(
                f"✅ {user['name']} uchun kunlik narx {price:,.2f} so'mga o'zgartirildi!",
                reply_markup=get_admin_kb()
            )
        else:
            await update.message.reply_text(
                "❌ O'zgarishlar kiritilmadi.",
                reply_markup=get_admin_kb()
            )
        # Clear the stored data
        if "pending_price_user" in context.user_data:
            del context.user_data["pending_price_user"]
            logger.info("handle_daily_price: Cleared pending_price_user from context")
    except Exception as e:
        logger.error(f"handle_daily_price: Unexpected error: {str(e)}", exc_info=True)
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_admin_kb()
        )

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

# ─── 7) CARD MANAGEMENT ─────────────────────────────────────────────────────────
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

# ─── 9) BROADCAST & TEST SURVEY ─────────────────────────────────────────────────
async def notify_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the notification process"""
    try:
        logger.info("notify_all: Command received")
        
        # Log the user who sent the command
        user_id = update.effective_user.id
        logger.info(f"notify_all: Command sent by user {user_id}")
        
        # Get user from database
        caller = await get_user_async(user_id)
        logger.info(f"notify_all: Database lookup result: {caller is not None}")
        
        if not caller:
            logger.warning(f"notify_all: User {user_id} not found in database")
            await update.message.reply_text("❌ Siz ro'yxatdan o'tmagansiz.")
            return ConversationHandler.END
        
        # Log admin status
        logger.info(f"notify_all: User {user_id} admin status: {caller.is_admin}")
        
        if not caller.is_admin:
            logger.warning(f"notify_all: User {user_id} is not an admin")
            await update.message.reply_text("❌ Siz admin emassiz.")
            return ConversationHandler.END
        
        logger.info(f"notify_all: Starting notification process for admin {caller.name}")
        
        # Send prompt message
        await update.message.reply_text(
            "⚠️ Barcha foydalanuvchilarga yubormoqchi bo'lgan xabarni kiriting:",
            reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
        )
        
        logger.info("notify_all: Prompt message sent, returning S_NOTIFY_MESSAGE state")
        return S_NOTIFY_MESSAGE
        
    except Exception as e:
        logger.error(f"notify_all: Error occurred: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")
        return ConversationHandler.END

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
    total_users = len(users)
    
    logger.info(f"Starting to send notification to {total_users} users")
    
    # First edit message to show progress
    await query.message.edit_text("⏳ Xabar yuborilmoqda...")
    
    # Store message ID for later updates
    context.user_data['notify_message_id'] = query.message.message_id
    
    # Initialize response tracking
    context.user_data['notify_responses'] = {
        'yes': [],
        'no': [],
        'total_sent': 0,
        'failed': [],
        'message': message,
        'food_choices': {}
    }
    
    # Send to each user with retry logic
    for u in users:
        try:
            # Send message with inline keyboard for response
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Ha", callback_data=f"notify_response:yes:{u.telegram_id}")],
                [InlineKeyboardButton("Yo'q", callback_data=f"notify_response:no:{u.telegram_id}")]
            ])
            
            # Try to send message with retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await context.bot.send_message(
                        u.telegram_id,
                        f"{message}\n\nJavob bering:",
                        reply_markup=keyboard
                    )
                    cnt += 1
                    logger.info(f"Successfully sent message to user {u.name} ({u.telegram_id})")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:  # Last attempt
                        logger.error(f"Failed to send message to user {u.name} ({u.telegram_id}) after {max_retries} attempts: {str(e)}")
                    else:
                        logger.warning(f"Retry {attempt + 1}/{max_retries} for user {u.name} ({u.telegram_id})")
                        await asyncio.sleep(1)  # Wait before retry
                        
        except Exception as e:
            logger.error(f"Error processing user {u.name} ({u.telegram_id}): {str(e)}")
            failed.append(f"{u.name} ({u.telegram_id})")
    
    # Update response tracking
    context.user_data['notify_responses']['total_sent'] = cnt
    context.user_data['notify_responses']['failed'] = failed
    
    # Show initial results
    result_text = f"✅ {cnt}/{total_users} foydalanuvchiga yuborildi."
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
    # Initialize collections
    app.job_queue.run_once(lambda _: init_collections(), when=0)
    
    # (1) plain commands
    app.add_handler(CommandHandler("admin", admin_panel))
    
    # Add notify_all command handler explicitly
    app.add_handler(CommandHandler("notify_all", notify_all))
    logger.info("notify_all command handler registered")

    # (2) single-step buttons
    for txt, fn in [
        (FOYD_BTN,     list_users_exec),
        (ADD_ADMIN_BTN,start_add_admin),
        (REMOVE_ADMIN_BTN,start_remove_admin),
        (DAILY_PRICE_BTN,start_daily_price),
        (DELETE_USER_BTN,start_delete_user),
        (CXL_LUNCH_ALL_BTN, cancel_lunch_day),
        (CARD_MANAGE_BTN, start_card_management),
        (KASSA_BTN,    show_kassa),
        (MENU_BTN,     menu_panel),
        (BACK_BTN,     back_to_menu),
    ]:
        app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(txt)}$"), fn))

    # Add menu callback handlers
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(view_menu1|view_menu2|add_menu1|add_menu2|del_menu1|del_menu2|menu_back|menu_panel)$"))
    app.add_handler(CallbackQueryHandler(handle_menu_del, pattern=r"^del_(menu1|menu2):.*$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_add))

    # Lunch cancellation conversation handler
    cancel_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CXL_LUNCH_ALL_BTN)}$"), cancel_lunch_day)],
        states={
            CANCEL_LUNCH_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_cancel_date)
            ],
            CANCEL_LUNCH_REASON: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_cancel_reason)
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="cancel_lunch_conversation",
        per_message=True
    )
    app.add_handler(cancel_conv)

    # Card management conversation handler
    card_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CARD_MANAGE_BTN)}$"), start_card_management)],
        states={
            S_CARD_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_card_number)
            ],
            S_CARD_OWNER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_card_owner)
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu),
            CommandHandler("cancel", cancel_conversation)
        ],
        allow_reentry=True,
        name="card_management_conversation",
        per_message=True
    )
    app.add_handler(card_conv)

    # Add price change handlers
    app.add_handler(CallbackQueryHandler(daily_price_callback, pattern=r"^set_price:\d+$"))
    app.add_handler(CallbackQueryHandler(daily_price_callback, pattern=r"^confirm_price:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(daily_price_callback, pattern=r"^custom_price:\d+$"))
    app.add_handler(CallbackQueryHandler(daily_price_callback, pattern=r"^back_to_price_list$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_daily_price))

    # Notify all conversation handler
    notify_conv = ConversationHandler(
        entry_points=[
            CommandHandler("notify_all", notify_all),
            MessageHandler(filters.Regex(r"^/notify_all$"), notify_all)
        ],
        states={
            S_NOTIFY_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_notify_message)
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
    logger.info("notify conversation handler registered")

    # Add notify response callback handler
    app.add_handler(CallbackQueryHandler(notify_response_callback, pattern=r"^notify_response:(yes|no):\d+$"))
    logger.info("notify response callback handler registered")

    # Add MENU_BTN to admin keyboard (already done above)
    # Add MessageHandler for MENU_BTN
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(MENU_BTN)}$"), menu_panel))
    # Add CallbackQueryHandler for menu_callback
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^(view_menu1|view_menu2|add_menu1|add_menu2|del_menu1|del_menu2|menu_back|menu_panel)$"))
    # Add MessageHandler for handle_menu_add (text input for adding food)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_add))

async def show_kassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current kassa amount from Google Sheets"""
    try:
        worksheet = await get_worksheet()
        if not worksheet:
            await update.message.reply_text("❌ Google Sheets bilan bog'lanishda xatolik yuz berdi.")
            return

        kassa_value = worksheet.cell(2, 4).value  # D2
        if not kassa_value:
            await update.message.reply_text("❌ Kassa miqdori topilmadi.")
            return

        try:
            kassa_value = float(str(kassa_value).replace(',', ''))
        except Exception:
            await update.message.reply_text("❌ Kassa miqdorini o'qishda xatolik.")
            return

        message = f"💰 *Kassa miqdori:* {kassa_value:,.0f} so'm"
        await update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=get_admin_kb()
        )
    except Exception as e:
        logger.error(f"Error showing kassa: {str(e)}")
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_admin_kb()
        )

async def notify_response_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user responses to notifications"""
    query = update.callback_query
    await query.answer()
    
    # Parse the callback data
    _, response, user_id = query.data.split(':')
    user_id = int(user_id)
    
    # Get the user who responded
    user = await get_user_async(user_id)
    if not user:
        return
    
    # Get the notification responses from context
    if 'notify_responses' not in context.user_data:
        return
    
    responses = context.user_data['notify_responses']
    user_info = f"{user.name} ({user.telegram_id})"
    
    # Update the response tracking
    if response == 'yes':
        if user_info not in responses['yes']:
            responses['yes'].append(user_info)
    else:  # response == 'no'
        if user_info not in responses['no']:
            responses['no'].append(user_info)
    
    # Edit the message to remove the buttons
    await query.message.edit_text(
        f"{query.message.text}\n\n✅ Javobingiz qabul qilindi."
    )

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

# Show Menyu panel
async def menu_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = get_menu_kb()
    text = "Menyu boshqaruvi:"
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

# View menu1/menu2
async def view_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name):
    try:
        if menu_col is None:
            await init_collections()
        doc = await menu_col.find_one({"name": menu_name})
        items = doc["items"] if doc and "items" in doc else []
        if not items:
            await update.callback_query.edit_message_text(
                f"{menu_name}da taom yo'q.", 
                reply_markup=get_menu_kb()
            )
        else:
            menu_text = f"🍽 {menu_name} taomlari:\n\n" + "\n".join(f"• {item}" for item in items)
            await update.callback_query.edit_message_text(
                menu_text,
                reply_markup=get_menu_kb()
            )
    except Exception as e:
        logger.error(f"Error in view_menu: {e}")
        await update.callback_query.edit_message_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

# Add food to menu1/menu2
async def add_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name):
    try:
        if menu_col is None:
            await init_collections()
        context.user_data["pending_menu_add"] = menu_name
        await update.callback_query.edit_message_text(
            f"Yangi taom nomini kiriting ({menu_name}):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(ORTGA_BTN, callback_data="menu_panel")]])
        )
    except Exception as e:
        logger.error(f"Error in add_menu: {e}")
        await update.callback_query.edit_message_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

async def handle_menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if menu_col is None:
            await init_collections()
        menu_name = context.user_data.get("pending_menu_add")
        if not menu_name:
            await update.message.reply_text(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_menu_kb()
            )
            return
        food = update.message.text.strip()
        if not food:
            await update.message.reply_text(
                "❌ Taom nomi bo'sh bo'lmasligi kerak.",
                reply_markup=get_menu_kb()
            )
            return
        result = await menu_col.update_one(
            {"name": menu_name},
            {"$addToSet": {"items": food}},
            upsert=True
        )
        if result.modified_count > 0 or result.upserted_id:
            await update.message.reply_text(
                f"✅ {food} {menu_name}ga qo'shildi!",
                reply_markup=get_menu_kb()
            )
        else:
            await update.message.reply_text(
                "❌ Taom qo'shilmadi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_menu_kb()
            )
        context.user_data.pop("pending_menu_add", None)
    except Exception as e:
        logger.error(f"Error in handle_menu_add: {e}")
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

# Remove food from menu1/menu2
async def del_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name):
    try:
        if menu_col is None:
            await init_collections()
        doc = await menu_col.find_one({"name": menu_name})
        items = doc["items"] if doc and "items" in doc else []
        if not items:
            await update.callback_query.edit_message_text(
                f"{menu_name}da taom yo'q.",
                reply_markup=get_menu_kb()
            )
            return
        kb = [[InlineKeyboardButton(item, callback_data=f"del_{menu_name}:{item}")] for item in items]
        kb.append([InlineKeyboardButton(ORTGA_BTN, callback_data="menu_panel")])
        await update.callback_query.edit_message_text(
            f"O'chirish uchun taom tanlang ({menu_name}):",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        logger.error(f"Error in del_menu: {e}")
        await update.callback_query.edit_message_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

async def handle_menu_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if menu_col is None:
            await init_collections()
        query = update.callback_query
        await query.answer()
        data = query.data
        if data.startswith("del_menu1:"):
            menu_name = "menu1"
            food = data[len("del_menu1:"):]
        elif data.startswith("del_menu2:"):
            menu_name = "menu2"
            food = data[len("del_menu2:"):]
        else:
            await query.edit_message_text(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_menu_kb()
            )
            return
        result = await menu_col.update_one(
            {"name": menu_name},
            {"$pull": {"items": food}}
        )
        if result.modified_count > 0:
            await query.edit_message_text(
                f"✅ {food} {menu_name}dan o'chirildi!",
                reply_markup=get_menu_kb()
            )
        else:
            await query.edit_message_text(
                "❌ Taom o'chirilmadi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=get_menu_kb()
            )
    except Exception as e:
        logger.error(f"Error in handle_menu_del: {e}")
        await update.callback_query.edit_message_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

# Menu callback handler
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if menu_col is None:
            await init_collections()
        query = update.callback_query
        await query.answer()
        data = query.data
        if data == "view_menu1":
            await view_menu(update, context, "menu1")
        elif data == "view_menu2":
            await view_menu(update, context, "menu2")
        elif data == "add_menu1":
            await add_menu(update, context, "menu1")
        elif data == "add_menu2":
            await add_menu(update, context, "menu2")
        elif data == "del_menu1":
            await del_menu(update, context, "menu1")
        elif data == "del_menu2":
            await del_menu(update, context, "menu2")
        elif data == "menu_panel" or data == "menu_back":
            await menu_panel(update, context)
        elif data.startswith("del_menu1:") or data.startswith("del_menu2:"):
            await handle_menu_del(update, context)
        else:
            await query.edit_message_text(
                "❌ Noma'lum buyruq.",
                reply_markup=get_menu_kb()
            )
    except Exception as e:
        logger.error(f"Error in menu_callback: {e}")
        await update.callback_query.edit_message_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_menu_kb()
        )

async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send daily summary to admin with defensive checks and error logging."""
    try:
        tz = pytz.timezone('Asia/Tashkent')
        now = datetime.now(tz)
        today = now.strftime('%Y-%m-%d')

        users = await get_all_users_async()
        if not users:
            logger.warning("No users found for summary")
            return

        attendees = []
        attendee_details = []
        declined_users = []
        non_attendees = []

        for u in users:
            try:
                attendance = getattr(u, 'attendance', []) or []
                declined_days = getattr(u, 'declined_days', []) or []
                food_choices = getattr(u, 'food_choices', {}) or {}
                name = getattr(u, 'name', str(u))
                if today in attendance:
                    attendees.append(u)
                    # Defensive: get food choice safely
                    try:
                        food_choice = await u.get_food_choice(today) if hasattr(u, 'get_food_choice') else food_choices.get(today, None)
                    except Exception as e:
                        logger.error(f"Error getting food choice for {name}: {e}")
                        food_choice = None
                    attendee_details.append((name, food_choice))
                elif today in declined_days:
                    declined_users.append(name)
                else:
                    non_attendees.append(name)
            except Exception as e:
                logger.error(f"Error processing user in summary: {e}")
                continue

        # Get food counts using aggregation, with error handling
        try:
            food_counts = await User.get_daily_food_counts(today)
        except Exception as e:
            logger.error(f"Error in get_daily_food_counts: {e}")
            food_counts = {}

        # Find the most popular food(s) with proper tie handling
        most_popular_foods = []
        if food_counts:
            try:
                max_count = max(data['count'] for data in food_counts.values())
                tied_foods = [food for food, data in food_counts.items() if data['count'] == max_count]
                if len(tied_foods) > 1:
                    most_popular_foods = sorted(tied_foods)
                else:
                    most_popular_foods = [tied_foods[0]]
            except Exception as e:
                logger.error(f"Error finding most popular foods: {e}")

        # Build the summary message
        admin_summary = "📊 *Bugungi tushlik uchun yig'ilish:*\n\n"
        admin_summary += f"👥 Jami: *{len(attendees)}* kishi\n\n"
        admin_summary += "📝 *Ro'yxat:*\n"
        if attendee_details:
            for i, (name, food) in enumerate(attendee_details, 1):
                food_text = f" - {food}" if food else " - Tanlanmagan"
                admin_summary += f"{i}. {name}{food_text}\n"
        else:
            admin_summary += "Hech kim yo'q\n"
        admin_summary += "\n"
        admin_summary += "🍽 *Taomlar statistikasi:*\n"
        if food_counts:
            rank = 1
            for food, data in food_counts.items():
                admin_summary += f"{rank}. {food} — {data['count']} ta\n"
                rank += 1
        else:
            admin_summary += "— Hech qanday taom tanlanmadi\n"
        if declined_users:
            admin_summary += "\n❌ *Rad etganlar:*\n"
            for i, name in enumerate(declined_users, 1):
                admin_summary += f"{i}. {name}\n"
        if non_attendees:
            admin_summary += "\n❓ *Javob bermaganlar:*\n"
            for i, name in enumerate(non_attendees, 1):
                admin_summary += f"{i}. {name}\n"

        # Send to admins
        for u_admin_check in users:
            try:
                if getattr(u_admin_check, 'is_admin', False):
                    await context.bot.send_message(
                        u_admin_check.telegram_id,
                        admin_summary,
                        parse_mode=ParseMode.MARKDOWN
                    )
            except Exception as e:
                logger.error(f"Failed to send admin summary to {getattr(u_admin_check, 'name', str(u_admin_check))}: {e}")

        # Update Google Sheets with new balances
        for user in attendees:
            try:
                if hasattr(user, 'balance') and hasattr(user, 'daily_price'):
                    user.balance -= user.daily_price
                    await user.save()
                    await update_user_balance_in_sheet(user.telegram_id, user.balance)
            except Exception as e:
                logger.error(f"Error updating balance in Sheets for {getattr(user, 'name', str(user))}: {e}")

        # Sync all balances from sheet to ensure consistency
        try:
            sync_result = await sync_balances_from_sheet()
            if not sync_result.get('success'):
                logger.error(f"Error syncing balances after summary: {sync_result.get('error')}")
        except Exception as e:
            logger.error(f"Error in sync_balances_from_sheet: {e}")

    except Exception as e:
        logger.error(f"Error in send_summary: {str(e)}")

