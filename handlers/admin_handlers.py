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
from sheets_utils import fetch_all_rows
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

# Initialize collections
kassa_col = None

async def init_collections():
    global kassa_col
    kassa_col = await get_collection("kassa")

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

# ─── KEYBOARDS ─────────────────────────────────────────────────────────────────
def get_admin_kb():
    return ReplyKeyboardMarkup([
        [FOYD_BTN],
        [ADD_ADMIN_BTN, REMOVE_ADMIN_BTN],
        [DAILY_PRICE_BTN],
        [DELETE_USER_BTN, CXL_LUNCH_ALL_BTN],
        [CARD_MANAGE_BTN],
        [KASSA_BTN],
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

async def show_kassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current kassa amount from Google Sheets"""
    try:
        # Get the worksheet
        worksheet = await get_worksheet()
        if not worksheet:
            await update.message.reply_text("❌ Google Sheets bilan bog'lanishda xatolik yuz berdi.")
            return ConversationHandler.END

        # Get all values
        all_values = worksheet.get_all_values()
        if not all_values:
            await update.message.reply_text("❌ Ma'lumotlar topilmadi.")
            return ConversationHandler.END

        # Find kassa value (assuming it's in the last column)
        kassa_value = None
        for row in all_values[1:]:  # Skip header row
            if len(row) >= 5:  # Assuming kassa is in column E (5th column)
                try:
                    kassa_value = float(row[4].replace(',', ''))  # Convert to float, handle comma-separated numbers
                    break
                except (ValueError, IndexError):
                    continue

        if kassa_value is None:
            await update.message.reply_text("❌ Kassa miqdori topilmadi.")
            return ConversationHandler.END

        # Format and send the message
        message = f"💰 *Kassa miqdori:* {kassa_value:,.0f} so'm"
        await update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error showing kassa: {str(e)}")
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_admin_kb()
        )
        return ConversationHandler.END

async def send_summary():
    """Send daily summary to admin"""
    try:
        # Get current date in Tashkent
        tz = pytz.timezone('Asia/Tashkent')
        now = datetime.now(tz)
        today = now.strftime('%Y-%m-%d')
        
        # Get all users
        users = await get_all_users_async()
        if not users:
            logger.warning("No users found for summary")
            return
            
        # Get all food choices for today
        food_choices = await get_food_choices_for_date(today)
        
        # Count statistics
        total_users = len(users)
        attending_users = sum(1 for u in users if u.attendance and today in u.attendance)
        declined_users = sum(1 for u in users if u.declined_days and today in u.declined_days)
        no_response = total_users - attending_users - declined_users
        
        # Group food choices
        food_stats = {}
        for choice in food_choices:
            food_name = choice.get('food_name', 'Unknown')
            food_stats[food_name] = food_stats.get(food_name, 0) + 1
            
        # Format message
        message = f"📊 *Daily Summary for {today}*\n\n"
        message += f"👥 Total Users: {total_users}\n"
        message += f"✅ Attending: {attending_users}\n"
        message += f"❌ Declined: {declined_users}\n"
        message += f"⏳ No Response: {no_response}\n\n"
        
        if food_stats:
            message += "*Food Choices:*\n"
            for food, count in food_stats.items():
                message += f"• {food}: {count}\n"
                
        # Send to all admins
        for user in users:
            if user.is_admin:
                try:
                    await bot.send_message(
                        user.telegram_id,
                        message,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Error sending summary to admin {user.telegram_id}: {str(e)}")
        
        # Update Google Sheets with new balances
        for user in users:
            if user.attendance and today in user.attendance:
                # Deduct daily price from balance
                user.balance -= user.daily_price
                user.save()
                
                # Update balance in Google Sheets
                await update_user_balance_in_sheet(user.telegram_id, user.balance)
                
        # Sync all balances from sheet to ensure consistency
        sync_result = await sync_balances_from_sheet()
        if not sync_result.get('success'):
            logger.error(f"Error syncing balances after summary: {sync_result.get('error')}")
            
    except Exception as e:
        logger.error(f"Error in send_summary: {str(e)}")

async def hourly_sync_balances():
    """Sync balances from Google Sheets to database every hour"""
    try:
        logger.info("Starting hourly balance sync from Sheets")
        result = await sync_balances_from_sheet()
        if result.get('success'):
            logger.info(f"Hourly sync completed: {result['updated']} users updated, {result['errors']} errors")
        else:
            logger.error(f"Hourly sync failed: {result.get('error')}")
    except Exception as e:
        logger.error(f"Error in hourly sync: {str(e)}")

# ─── 10) REGISTER ALL HANDLERS ─────────────────────────────────────────────────
def register_handlers(app):
    # Initialize collections
    app.job_queue.run_once(lambda _: init_collections(), when=0)
    
    # (1) plain commands
    app.add_handler(CommandHandler("admin", admin_panel))
    
    # Add notify_all command handler explicitly
    app.add_handler(CommandHandler("notify_all", notify_all))
    logger.info("notify_all command handler registered")

    # (2) single‐step buttons
    for txt, fn in [
        (FOYD_BTN,     list_users_exec),
        (ADD_ADMIN_BTN,start_add_admin),
        (REMOVE_ADMIN_BTN,start_remove_admin),
        (DAILY_PRICE_BTN,start_daily_price),
        (DELETE_USER_BTN,start_delete_user),
        (CXL_LUNCH_ALL_BTN, cancel_lunch_day),
        (CARD_MANAGE_BTN, start_card_management),
        (KASSA_BTN,    show_kassa),
        (BACK_BTN,     back_to_menu),
    ]:
        app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(txt)}$"), fn))

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

    # Daily price conversation handler
    price_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(DAILY_PRICE_BTN)}$"), start_daily_price)],
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

    # Register hourly sync job
    job_queue = app.job_queue
    job_queue.run_repeating(
        hourly_sync_balances,
        interval=3600,  # 1 hour in seconds
        first=10  # Start after 10 seconds
    )

