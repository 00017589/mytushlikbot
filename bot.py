import logging
import json
import os
import datetime
import pytz
import shutil
import asyncio
import glob
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get bot token from environment variable
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not found!")

# ---------------------- Configuration and Global Variables ---------------------- #

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Set Tashkent timezone
TASHKENT_TZ = pytz.timezone("Asia/Tashkent")

# Conversation states for registration and name change
PHONE = 0
NAME = 1
NAME_CHANGE = 200

# Conversation states for admin balance modification (modify user's balance)
ADMIN_BALANCE_SELECT_USER, ADMIN_BALANCE_ENTER_AMOUNT = range(100, 102)
# Conversation states for admin daily price adjustment (set user's daily price)
ADMIN_DAILY_PRICE_SELECT_USER, ADMIN_DAILY_PRICE_ENTER_AMOUNT = range(102, 104)

# File paths
DATA_FILE = "data.json"
ADMIN_FILE = "admins.json"

# Global lunch menu options mapping (menu option number -> dish name)
MENU_OPTIONS = {
    "1": "Qovurma Lag'mon",
    "2": "Teftel Jarkob",
    "3": "Mastava",
    "4": "Sho'rva",
    "5": "Sokoro",
    "6": "Do'lma",
    "7": "Teftel sho'rva",
    "8": "Suyuq lag'mon",
    "9": "Osh",
    "10": "Qovurma Makron",
    "11": "Xonim"
}

# ---------------------- Data and Admin Initialization ---------------------- #

def initialize_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"users": {}, "daily_attendance": {}, "attendance_history": {}, "kassa": 0}
    data.setdefault("users", {})
    data.setdefault("daily_attendance", {})
    data.setdefault("attendance_history", {})
    data.setdefault("kassa", 0)
    return data


def initialize_admins():
    if os.path.exists(ADMIN_FILE):
        with open(ADMIN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"admins": []}

def set_daily_price_for_all_users(data, price=25000):
    """Set daily price for all users to the specified amount"""
    for user_id, user_data in data["users"].items():
        user_data["daily_price"] = price
    return data

async def create_backup():
    try:
        if not os.path.exists("backups"):
            os.makedirs("backups")
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        for fname in [DATA_FILE, ADMIN_FILE, "lunch_bot.db"]:
            if os.path.exists(fname):
                dest = os.path.join("backups", f"{fname}_{timestamp}")
                shutil.copy2(fname, dest)
                backups = sorted(glob.glob(os.path.join("backups", f"{fname}_*")))
                for old in backups[:-5]:
                    os.remove(old)
    except Exception as e:
        logger.error(f"Backup creation failed: {e}")
        raise


async def save_data(data):
    await create_backup()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


async def save_admins(admins):
    await create_backup()
    with open(ADMIN_FILE, "w", encoding="utf-8") as f:
        json.dump(admins, f, ensure_ascii=False, indent=4)


def is_admin(user_id, admins):
    return str(user_id) in admins.get("admins", [])

# ---------------------- Keyboards ---------------------- #

def create_admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["👥 Foydalanuvchilar", "❌ Foydalanuvchini o'chirish"],
            ["💵 Balans qo'shish", "💸 Balans kamaytirish"],
            ["📝 Kunlik narx", "📊 Bugungi qatnashuv"],
            ["🔄 Balanslarni nollash", "💰 Kassa"],
            ["⬅️ Asosiy menyu", "❓ Yordam"],
        ],
        resize_keyboard=True,
    )


def create_regular_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["💸 Balansim", "📊 Qatnashishlarim"],
            ["✏️ Ism o'zgartirish", "❌ Tushlikni bekor qilish"],
            ["❓ Yordam"],
        ],
        resize_keyboard=True,
    )

# ---------------------- Registration and Name Change ---------------------- #

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    data = initialize_data()
    admins = initialize_admins()
    
    if user_id in data["users"]:
        user = data["users"][user_id]
        if user_id in admins["admins"]:
            keyboard = create_admin_keyboard()
            await update.message.reply_text(
                f"Assalomu alaykum, {user['name']}!\n"
                f"Admin paneliga xush kelibsiz!",
                reply_markup=keyboard
            )
        else:
            keyboard = create_regular_keyboard()
            await update.message.reply_text(
                f"Assalomu alaykum, {user['name']}!\n"
                f"Botga xush kelibsiz!",
                reply_markup=keyboard
            )
        return ConversationHandler.END
    await update.message.reply_text(
        "Assalomu alaykum! Botdan foydalanish uchun ro'yxatdan o'tishingiz kerak.\n\n"
        "Iltimos, telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton(text="📱 Telefon raqamni yuborish", request_contact=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
    )
    return PHONE

async def phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact:
        phone_number = update.message.contact.phone_number
    else:
        phone_number = update.message.text.strip()
        if not phone_number.replace('+', '').isdigit() or len(phone_number) < 9:
            await update.message.reply_text("Iltimos, to'g'ri telefon raqam kiriting")
            return PHONE
    context.user_data['phone'] = phone_number
    await update.message.reply_text(
        "Iltimos, ismingizni kiriting:",
        reply_markup=ReplyKeyboardRemove()
    )
    return NAME

async def name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        name_text = update.message.text.strip()
        if not name_text:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return NAME
            
        uid = str(update.effective_user.id)
        data = initialize_data()
        
        if uid not in data["users"]:
            await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
            return
            
        old_name = data["users"][uid]["name"]
        data["users"][uid]["name"] = name_text
        
        # Set daily price for all users
        data = set_daily_price_for_all_users(data)
        
        await save_data(data)
        
        await update.message.reply_text(
            f"Sizning ismingiz {old_name} dan {name_text} ga o'zgartirildi.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["💸 Balansim", "📊 Qatnashishlarim"],
                    ["✏️ Ism o'zgartirish", "❌ Tushlikni bekor qilish"],
                    ["❓ Yordam"],
                ],
                resize_keyboard=True,
            ),
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in process_name_change: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

async def update_all_daily_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update all users' daily prices to 25000"""
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        data = initialize_data()
        
        # Update all users' daily prices
        data = set_daily_price_for_all_users(data)
        
        # Save the changes
        await save_data(data)
        
        # Verify the changes
        data = initialize_data()  # Reload data to verify
        zero_price_count = sum(1 for user in data["users"].values() 
                             if "daily_price" not in user or user["daily_price"] == 0)
        
        if zero_price_count > 0:
            await update.message.reply_text(f"⚠️ Diqqat: {zero_price_count} ta foydalanuvchining kunlik narxi hali ham 0 so'm!")
        else:
            await update.message.reply_text("✅ Barcha foydalanuvchilarning kunlik narxi 25,000 so'mga o'zgartirildi.")
            
    except Exception as e:
        logger.error(f"Error in update_all_daily_prices: {str(e)}")
        await update.message.reply_text("Kunlik narxlarni yangilashda xatolik yuz berdi.")

async def start_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    data = initialize_data()
    if user_id not in data["users"]:
        await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
        return ConversationHandler.END
    await update.message.reply_text("Yangi ismingizni kiriting:")
    return NAME_CHANGE

async def process_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_name = update.message.text.strip()
        if not new_name:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return NAME_CHANGE
            
        uid = str(update.effective_user.id)
        data = initialize_data()
        
        if uid not in data["users"]:
            await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
            return
            
        old_name = data["users"][uid]["name"]
        data["users"][uid]["name"] = new_name
        
        # Update name in daily attendance if present
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        if today in data["daily_attendance"]:
            # Update in confirmed list
            if uid in data["daily_attendance"][today]["confirmed"]:
                data["daily_attendance"][today]["confirmed"].remove(uid)
                data["daily_attendance"][today]["confirmed"].append(uid)
            
            # Update in declined list
            if uid in data["daily_attendance"][today]["declined"]:
                data["daily_attendance"][today]["declined"].remove(uid)
                data["daily_attendance"][today]["declined"].append(uid)
            
            # Update in pending list
            if uid in data["daily_attendance"][today]["pending"]:
                data["daily_attendance"][today]["pending"].remove(uid)
                data["daily_attendance"][today]["pending"].append(uid)
        
        # Update name in attendance history
        for date in data["attendance_history"]:
            if uid in data["attendance_history"][date]["confirmed"]:
                data["attendance_history"][date]["confirmed"].remove(uid)
                data["attendance_history"][date]["confirmed"].append(uid)
            if uid in data["attendance_history"][date]["declined"]:
                data["attendance_history"][date]["declined"].remove(uid)
                data["attendance_history"][date]["declined"].append(uid)
        
        await save_data(data)
        
        await update.message.reply_text(
            f"Sizning ismingiz {old_name} dan {new_name} ga o'zgartirildi.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["💸 Balansim", "📊 Qatnashishlarim"],
                    ["✏️ Ism o'zgartirish", "❌ Tushlikni bekor qilish"],
                    ["❓ Yordam"],
                ],
                resize_keyboard=True,
            ),
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in process_name_change: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

# Allow users to change their name via button
async def start_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        uid = str(update.effective_user.id)
        data = initialize_data()
        
        if uid not in data["users"]:
            await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
            return ConversationHandler.END
        
        await update.message.reply_text("Yangi ismingizni kiriting:")
        return NAME_CHANGE
    except Exception as e:
        logger.error(f"Error in start_name_change: {str(e)}")
        await update.message.reply_text("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

async def process_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        new_name = update.message.text.strip()
        if not new_name:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return NAME_CHANGE
            
        uid = str(update.effective_user.id)
        data = initialize_data()
        
        if uid not in data["users"]:
            await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
            return
            
        old_name = data["users"][uid]["name"]
        data["users"][uid]["name"] = new_name
        await save_data(data)
        
        await update.message.reply_text(
            f"Sizning ismingiz {old_name} dan {new_name} ga o'zgartirildi.",
            reply_markup=ReplyKeyboardMarkup(
                [
                    ["💸 Balansim", "📊 Qatnashishlarim"],
                    ["✏️ Ism o'zgartirish", "❌ Tushlikni bekor qilish"],
                    ["❓ Yordam"],
                ],
                resize_keyboard=True,
            ),
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in process_name_change: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

# ---------------------- Attendance Survey and Summary ---------------------- #

async def send_attendance_request(context: ContextTypes.DEFAULT_TYPE, test: bool = False):
    now = datetime.datetime.now(TASHKENT_TZ)
    if not test and now.weekday() >= 5:
        return
    data = initialize_data()
    today = now.strftime("%Y-%m-%d")
    if today not in data["daily_attendance"]:
        data["daily_attendance"][today] = {"confirmed": [], "declined": [], "pending": [], "menu": {}}
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Ha ✅", callback_data=f"attendance_yes_{today}"),
                InlineKeyboardButton("Yo'q ❌", callback_data=f"attendance_no_{today}")
            ]
        ]
    )
    for uid in data["users"]:
        try:
            if uid in data["daily_attendance"][today]["confirmed"] or uid in data["daily_attendance"][today]["declined"]:
                continue
            if uid not in data["daily_attendance"][today]["pending"]:
                data["daily_attendance"][today]["pending"].append(uid)
            await context.bot.send_message(
                chat_id=uid,
                text="Bugun tushlikka qatnashasizmi? (Sizning kunlik narxingiz qo'llaniladi)",
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Failed to send survey to user {uid}: {e}")
    await save_data(data)

async def send_attendance_summary(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(TASHKENT_TZ)
    if now.weekday() >= 5:  # Skip weekends
        return
    data = initialize_data()
    admins = initialize_admins()
    today = now.strftime("%Y-%m-%d")
    if today not in data["daily_attendance"]:
        return

    # Get attendance data
    confirmed = data["daily_attendance"][today]["confirmed"]
    menu_choices = data["daily_attendance"][today].get("menu", {})

    # Calculate food statistics
    food_stats = {}
    for user_id, dish in menu_choices.items():
        if user_id in confirmed:  # Only count confirmed attendees
            food_stats[dish] = food_stats.get(dish, 0) + 1

    # Sort food choices by popularity
    sorted_foods = sorted(food_stats.items(), key=lambda x: x[1], reverse=True)

    # Prepare admin summary
    admin_summary = f"🍽️ {today} - Tushlik qatnashuvchilari: {len(confirmed)}\n\n"
    if confirmed:
        admin_summary += "👥 Qatnashuvchilar:\n"
        for user_id in confirmed:
            name = data["users"].get(user_id, {}).get("name", "Noma'lum")
            dish = menu_choices.get(user_id, "N/A")
            dish_name = MENU_OPTIONS.get(dish, "N/A") if dish != "N/A" else "N/A"
            admin_summary += f"• {name} - {dish_name}\n"
        
        admin_summary += "\n📊 Ovqat tanlovlari statistikasi:\n"
        for dish, count in sorted_foods:
            dish_name = MENU_OPTIONS.get(dish, "N/A") if dish != "N/A" else "N/A"
            admin_summary += f"• {dish_name}: {count} ta\n"
        
        # Add total amount collected
        total_amount = len(confirmed) * 25000
        admin_summary += f"\n💰 Jami yig'ilgan summa: {total_amount:,} so'm"
    else:
        admin_summary += "❌ Bugun tushlik qatnashuvchilar yo'q."

    # Prepare user summary and deduct balances
    for user_id in confirmed:
        if user_id in data["users"]:
            name = data["users"][user_id]["name"]
            dish = menu_choices.get(user_id, "N/A")
            dish_name = MENU_OPTIONS.get(dish, "N/A") if dish != "N/A" else "N/A"
            user_summary = f"🍽️ {today} - Tushlik qatnashuvchisi:\n\n"
            user_summary += f"• Siz: {name}\n"
            user_summary += f"• Tanlangan ovqat: {dish_name}\n"
            
            # Deduct balance and update kassa
            old_balance = data["users"][user_id]["balance"]
            data["users"][user_id]["balance"] -= 25000
            data["kassa"] += 25000
            
            user_summary += f"• Hisobdan yechilgan summa: 25,000 so'm\n"
            user_summary += f"• Yangi balans: {data['users'][user_id]['balance']:,} so'm"
            
            try:
                await context.bot.send_message(chat_id=user_id, text=user_summary)
            except Exception as e:
                logger.error(f"Failed to send summary to user {user_id}: {e}")

    # Send admin summary to all admins
    for admin_id in admins["admins"]:
        try:
            await context.bot.send_message(chat_id=admin_id, text=admin_summary)
        except Exception as e:
            logger.error(f"Failed to send summary to admin {admin_id}: {e}")

    # Save attendance history and updated data
    if today not in data["attendance_history"]:
        data["attendance_history"][today] = {
            "confirmed": confirmed.copy(),
            "declined": data["daily_attendance"][today]["declined"].copy(),
            "menu": data["daily_attendance"][today].get("menu", {}).copy()
        }
    await save_data(data)

async def attendance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = initialize_data()
    uid = str(query.from_user.id)
    callback = query.data
    if callback.startswith("attendance_"):
        action, date = callback.replace("attendance_", "").split("_")
        if date not in data["daily_attendance"]:
            data["daily_attendance"][date] = {"confirmed": [], "declined": [], "pending": [], "menu": {}}
        for lst in [data["daily_attendance"][date]["pending"],
                    data["daily_attendance"][date]["confirmed"],
                    data["daily_attendance"][date]["declined"]]:
            if uid in lst:
                lst.remove(uid)
        if action == "yes":
            # Check if user has enough balance
            if uid in data["users"] and data["users"][uid]["balance"] < 25000:
                await query.edit_message_text("Sizning balansingiz yetarli emas. Iltimos, balansingizni to'ldiring.")
                return
            menu_kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("1. Qovurma Lag'mon", callback_data=f"menu_1_{date}"),
                     InlineKeyboardButton("2. Teftel Jarkob", callback_data=f"menu_2_{date}")],
                    [InlineKeyboardButton("3. Mastava", callback_data=f"menu_3_{date}"),
                     InlineKeyboardButton("4. Sho'rva", callback_data=f"menu_4_{date}")],
                    [InlineKeyboardButton("5. Sokoro", callback_data=f"menu_5_{date}"),
                     InlineKeyboardButton("6. Do'lma", callback_data=f"menu_6_{date}")],
                    [InlineKeyboardButton("7. Teftel sho'rva", callback_data=f"menu_7_{date}"),
                     InlineKeyboardButton("8. Suyuq lag'mon", callback_data=f"menu_8_{date}")],
                    [InlineKeyboardButton("9. Osh", callback_data=f"menu_9_{date}"),
                     InlineKeyboardButton("10. Qovurma Makron", callback_data=f"menu_10_{date}")],
                    [InlineKeyboardButton("11. Xonim", callback_data=f"menu_11_{date}")]
                ]
            )
            await query.edit_message_text("Iltimos, menyudan tanlang:", reply_markup=menu_kb)
        elif action == "no":
            data["daily_attendance"][date]["declined"].append(uid)
            await query.edit_message_text("Tushlik uchun javobingiz qayd etildi.")
    elif callback.startswith("menu_"):
        parts = callback.split("_")
        if len(parts) >= 3:
            dish = parts[1]
            date = parts[2]
            data["daily_attendance"].setdefault(date, {"confirmed": [], "declined": [], "pending": [], "menu": {}})
            if uid not in data["daily_attendance"][date]["confirmed"]:
                data["daily_attendance"][date]["confirmed"].append(uid)
            data["daily_attendance"][date].setdefault("menu", {})[uid] = dish
            dish_name = MENU_OPTIONS.get(dish, "N/A")
            await query.edit_message_text(f"Siz tanladingiz: {dish_name}")
        else:
            await query.edit_message_text("Noto'g'ri tanlov.")
    await save_data(data)

async def cancel_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.datetime.now(TASHKENT_TZ)
    if now.hour > 9 or (now.hour == 9 and now.minute >= 59):
        await update.message.reply_text("Tushlikni bekor qilish muddati o'tib ketdi.")
        return
    today = now.strftime("%Y-%m-%d")
    data = initialize_data()
    if today not in data["daily_attendance"]:
        await update.message.reply_text("Bugun uchun tushlik ma'lumotlari topilmadi.")
        return
    uid = str(update.effective_user.id)
    if uid in data["daily_attendance"][today]["confirmed"]:
        data["daily_attendance"][today]["confirmed"].remove(uid)
    if uid in data["daily_attendance"][today].get("menu", {}):
        del data["daily_attendance"][today]["menu"][uid]
    if uid not in data["daily_attendance"][today]["declined"]:
        data["daily_attendance"][today]["declined"].append(uid)
    await save_data(data)
    await update.message.reply_text("Siz tushlikni bekor qildingiz.")

# ---------------------- Admin Functions ---------------------- #
# Admin Balance Modification
async def start_balance_modification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        action_text = update.message.text
        if action_text == "💵 Balans qo'shish":
            context.user_data["balance_action"] = "add"
        elif action_text == "💸 Balans kamaytirish":
            context.user_data["balance_action"] = "subtract"
        else:
            await update.message.reply_text("Noto'g'ri amal.")
            return ConversationHandler.END
            
        data = initialize_data()
        if not data["users"]:
            await update.message.reply_text("Foydalanuvchilar ro'yxati bo'sh.")
            return ConversationHandler.END
            
        kb = []
        for uid, info in data["users"].items():
            button = InlineKeyboardButton(f"{info['name']} ({uid})", callback_data=f"balance_mod_{uid}")
            kb.append([button])
            
        await update.message.reply_text("Iltimos, foydalanuvchini tanlang:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_BALANCE_SELECT_USER
    except Exception as e:
        logger.error(f"Error in start_balance_modification: {str(e)}")
        await update.message.reply_text("Balans o'zgartirishda xatolik yuz berdi.")
        return ConversationHandler.END

async def balance_mod_select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    if len(parts) < 3:
        await query.edit_message_text("Noto'g'ri tanlov.")
        return ConversationHandler.END
    target_id = parts[2]
    context.user_data["target_id"] = target_id
    if context.user_data.get("balance_action") == "add":
        await query.edit_message_text("Iltimos, qo'shmoqchi bo'lgan summani kiriting (musbat raqam):")
    else:
        await query.edit_message_text("Iltimos, kamaytirmoqchi bo'lgan summani kiriting (musbat raqam):")
    return ADMIN_BALANCE_ENTER_AMOUNT

async def balance_mod_enter_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount < 0:
            await update.message.reply_text("Iltimos, musbat raqam kiriting.")
            return ADMIN_BALANCE_ENTER_AMOUNT
    except ValueError:
        await update.message.reply_text("Iltimos, to'g'ri raqam kiriting.")
        return ADMIN_BALANCE_ENTER_AMOUNT
    data = initialize_data()
    target_id = context.user_data.get("target_id")
    if not target_id or target_id not in data["users"]:
        await update.message.reply_text("Foydalanuvchi topilmadi.")
        return ConversationHandler.END
    old_balance = data["users"][target_id]["balance"]
    if context.user_data.get("balance_action") == "add":
        new_balance = old_balance + amount
    else:
        new_balance = old_balance - amount
    data["users"][target_id]["balance"] = new_balance
    await save_data(data)
    await update.message.reply_text(f"{data['users'][target_id]['name']} ning balansi {old_balance:,} so'mdan {new_balance:,} so'mga o'zgartirildi.")
    return ConversationHandler.END

async def cancel_balance_modification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Balans o'zgarishi bekor qilindi.")
    return ConversationHandler.END

# Admin Daily Price Adjustment
async def start_daily_price_modification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        data = initialize_data()
        if not data["users"]:
            await update.message.reply_text("Foydalanuvchilar ro'yxati bo'sh.")
            return ConversationHandler.END
            
        kb = []
        for uid, info in data["users"].items():
            button = InlineKeyboardButton(f"{info['name']} ({uid})", callback_data=f"price_mod_{uid}")
            kb.append([button])
            
        await update.message.reply_text("Iltimos, kunlik narxni o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:", reply_markup=InlineKeyboardMarkup(kb))
        return ADMIN_DAILY_PRICE_SELECT_USER
    except Exception as e:
        logger.error(f"Error in start_daily_price_modification: {str(e)}")
        await update.message.reply_text("Kunlik narxni o'zgartirishda xatolik yuz berdi.")
        return ConversationHandler.END

async def daily_price_mod_select_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_", 2)
    if len(parts) < 3:
        await query.edit_message_text("Noto'g'ri tanlov.")
        return ConversationHandler.END
    target_id = parts[2]
    context.user_data["price_target_id"] = target_id
    await query.edit_message_text("Iltimos, yangi kunlik narxni kiriting (soumlarda, masalan: 20000):")
    return ADMIN_DAILY_PRICE_ENTER_AMOUNT

async def daily_price_mod_enter_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text)
        if price < 0:
            await update.message.reply_text("Iltimos, musbat narx kiriting.")
            return ADMIN_DAILY_PRICE_ENTER_AMOUNT
    except ValueError:
        await update.message.reply_text("Iltimos, to'g'ri narx kiriting.")
        return ADMIN_DAILY_PRICE_ENTER_AMOUNT
    data = initialize_data()
    target_id = context.user_data.get("price_target_id")
    if not target_id or target_id not in data["users"]:
        await update.message.reply_text("Foydalanuvchi topilmadi.")
        return ConversationHandler.END
    data["users"][target_id]["daily_price"] = price
    await save_data(data)
    await update.message.reply_text(f"{data['users'][target_id]['name']} ning kunlik narxi {price:,} so'mga o'zgartirildi.")
    return ConversationHandler.END

async def cancel_daily_price_modification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Kunlik narx o'zgarishi bekor qilindi.")
    return ConversationHandler.END

# ---------------------- Admin and General User Commands ---------------------- #

# General user: Check balance
async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = initialize_data()
    if uid not in data["users"]:
        await update.message.reply_text("Siz ro'yxatdan o'tmagansiz. /start buyrug'ini yuboring.")
        return
    bal = data["users"][uid]["balance"]
    sign = "+" if bal >= 0 else ""
    await update.message.reply_text(f"Sizning balansingiz: {sign}{bal:,} so'm")

# General user: Attendance history
async def check_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = initialize_data()
    if uid not in data["users"]:
        await update.message.reply_text("Siz ro'yxatdan o'tmagansiz. /start buyrug'ini yuboring.")
        return
    count = 0
    history = ""
    for date, rec in data["attendance_history"].items():
        if uid in rec.get("confirmed", []):
            count += 1
            history += f"✅ {date}\n"
    await update.message.reply_text(f"Siz jami {count} marta tushlikda qatnashgansiz.\n\nTarix:\n{history or 'Ma\'lumot topilmadi.'}")

# Admin: View all registered users
async def view_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        data = initialize_data()
        if not data["users"]:
            await update.message.reply_text("Hozircha foydalanuvchilar mavjud emas.")
            return
            
        # Sort users by name
        sorted_users = sorted(data["users"].items(), key=lambda x: x[1]["name"])
        
        # Build message
        message = "📋 Foydalanuvchilar ro'yxati:\n\n"
        
        for i, (user_id, user_data) in enumerate(sorted_users, 1):
            name = user_data.get("name", "N/A")
            phone = user_data.get("phone", "N/A")
            balance = user_data.get("balance", 0)
            daily_price = user_data.get("daily_price", 0)
            
            message += (
                f"{i}. 👤 {name}\n"
                f"   🆔 ID: {user_id}\n"
                f"   📱 Tel: {phone}\n"
                f"   💰 Balans: {balance:,} so'm\n"
                f"   💵 Kunlik narx: {daily_price:,} so'm\n"
                f"   ────────────────────\n"
            )
        
        # Add summary at the end
        total_users = len(data["users"])
        total_balance = sum(user.get("balance", 0) for user in data["users"].values())
        message += f"\n📊 Jami foydalanuvchilar: {total_users} ta\n"
        message += f"💰 Jami balans: {total_balance:,} so'm"
        
        # Send message
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error in view_users: {str(e)}")
        await update.message.reply_text("Foydalanuvchilar ro'yxatini ko'rsatishda xatolik yuz berdi.")

# Admin: View today's attendance
async def view_attendance_today_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        data = initialize_data()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        current_time = datetime.datetime.now(TASHKENT_TZ)
        
        if today not in data["daily_attendance"]:
            await update.message.reply_text("Bugun tushlik ma'lumotlari topilmadi.")
            return
            
        confirmed = data["daily_attendance"][today]["confirmed"]
        if not confirmed:
            await update.message.reply_text("Bugun tushlik qatnashuvchilar yo'q.")
            return
            
        # Calculate most popular food
        food_stats = {}
        menu_choices = data["daily_attendance"][today].get("menu", {})
        for user_id, dish in menu_choices.items():
            if user_id in confirmed:
                food_stats[dish] = food_stats.get(dish, 0) + 1
        
        # Get most popular food
        most_popular = max(food_stats.items(), key=lambda x: x[1]) if food_stats else ("N/A", 0)
        popular_dish_name = MENU_OPTIONS.get(most_popular[0], "N/A") if most_popular[0] != "N/A" else "N/A"
        
        message = f"🍽️ {today} - Bugungi Tushlik:\n\n"
        message += f"📊 Eng ko'p tanlangan ovqat: {popular_dish_name} ({most_popular[1]} ta)\n\n"
        message += "👥 Qatnashuvchilar:\n"
        
        # Show brief list of attendees
        for i, user_id in enumerate(confirmed, 1):
            if user_id in data["users"]:
                name = data["users"][user_id]["name"]
                message += f"{i}. {name}\n"
        
        # Add lunch status based on time
        if current_time.hour >= 14:
            message += "\n⚠️ Tushlik yakunlandi"
        else:
            message += "\n⏳ Tushlik davom etmoqda"
        
        await update.message.reply_text(message)
        
    except Exception as e:
        logger.error(f"Error in view_attendance_today_admin: {str(e)}")
        await update.message.reply_text("Tushlik qatnashuvchilarini ko'rsatishda xatolik yuz berdi.")

# Admin: View all balances
async def view_all_balances(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    data = initialize_data()
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    sorted_users = sorted(data["users"].items(), key=lambda x: x[1]["balance"])
    total_balance = sum(info["balance"] for _, info in sorted_users)
    msg = "📊 BALANSLAR RO'YXATI:\n\n"
    i = 1
    for user_id, info in sorted_users:
        msg += f"{i}. {info['name']}: {info['balance']:,} so'm\n"
        i += 1
    msg += f"\n💰 Jami balans: {total_balance:,} so'm"
    await update.message.reply_text(msg)

# Admin: View Kassa (with emoji)
async def view_kassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        data = initialize_data()
        
        # Calculate total balance from all users
        total_balance = sum(user.get("balance", 0) for user in data["users"].values())
        
        # Format the message with emojis and proper formatting
        await update.message.reply_text(
            f"💰 Kassa: {total_balance:,} so'm\n\n"
            f"📊 Jami foydalanuvchilar: {len(data['users'])} ta\n"
            f"💵 Har bir foydalanuvchining balansi yig'indisi"
        )
    except Exception as e:
        logger.error(f"Error in view_kassa: {str(e)}")
        await update.message.reply_text("Kassani ko'rsatishda xatolik yuz berdi.")

# Admin: Reset balances
async def reset_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        if context.args:
            target_id = context.args[0]
            if target_id not in data["users"]:
                await update.message.reply_text("Bu foydalanuvchi topilmadi.")
                return
            old_bal = data["users"][target_id]["balance"]
            data["users"][target_id]["balance"] = 0
            await save_data(data)
            await update.message.reply_text(f"{data['users'][target_id]['name']} ning balansi {old_bal:,} so'mdan 0 so'mga tushirildi.")
        else:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Ha ✅", callback_data="reset_all_balances_confirm"),
                  InlineKeyboardButton("Yo'q ❌", callback_data="reset_all_balances_cancel")]]
            )
            await update.message.reply_text("Hamma foydalanuvchilarning balanslarini nolga tushurishni xohlaysizmi?", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error in reset_balance: {str(e)}")
        await update.message.reply_text("Balanslarni nolga tushirishda xatolik yuz berdi.")

async def balance_reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = str(query.from_user.id)
    admins = initialize_admins()
    data = initialize_data()
    if uid not in admins["admins"]:
        await query.edit_message_text("Siz admin emassiz.")
        return
    if query.data == "reset_all_balances_confirm":
        count = sum(1 for info in data["users"].values() if info["balance"] != 0)
        total = sum(info["balance"] for info in data["users"].values())
        for user_id in data["users"]:
            data["users"][user_id]["balance"] = 0
        await save_data(data)
        await query.edit_message_text(f"✅ {count} foydalanuvchining jami {total:,} so'mli balansi nolga tushirildi.")
    else:
        await query.edit_message_text("Balanslarni nolga tushirish bekor qilindi.")

# Admin: Make admin
async def make_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    if not admins["admins"]:
        admins["admins"].append(uid)
        await save_admins(admins)
        await update.message.reply_text("Siz admin sifatida tayinlandingiz!")
        return
    if uid in admins["admins"]:
        if not context.args:
            await update.message.reply_text("Yangi admin uchun foydalanuvchi ID kiriting. Masalan: /admin_qoshish 123456789")
            return
        new_admin = context.args[0]
        if new_admin in admins["admins"]:
            await update.message.reply_text("Bu foydalanuvchi allaqachon admin.")
            return
        data = initialize_data()
        if new_admin not in data["users"]:
            await update.message.reply_text("Bu foydalanuvchi topilmadi.")
            return
        admins["admins"].append(new_admin)
        await save_admins(admins)
        try:
            await context.bot.send_message(chat_id=new_admin, text="Tabriklaymiz! Siz admin sifatida tayinlandingiz.")
        except Exception as e:
            logger.error(f"Failed to notify new admin: {e}")
        await show_admin_keyboard(update, context)
        await update.message.reply_text(f"Foydalanuvchi {new_admin} admin sifatida tayinlandi.")
    else:
        await update.message.reply_text("Siz admin emassiz.")

# Admin: Remove admin
async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    if not context.args:
        await update.message.reply_text("Adminni o'chirish uchun foydalanuvchi ID kiriting. Masalan: /admin_ochirish 123456789")
        return
    target = context.args[0]
    if target not in admins["admins"]:
        await update.message.reply_text("Bu foydalanuvchi admin emas.")
        return
    if target == uid and len(admins["admins"]) == 1:
        await update.message.reply_text("Siz yagona admin, o'zingizni o'chira olmaysiz.")
        return
    admins["admins"].remove(target)
    await save_admins(admins)
    try:
        await context.bot.send_message(chat_id=target, text="Sizning admin huquqlaringiz bekor qilindi.")
    except Exception as e:
        logger.error(f"Failed to notify removed admin: {e}")
    await update.message.reply_text(f"Foydalanuvchi {target} admin ro'yxatidan o'chirildi.")

# Admin: Export data
async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    data = initialize_data()
    exp = {
        "users": {},
        "total_balance": 0,
        "export_date": datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }
    for user_id, info in data["users"].items():
        exp["users"][user_id] = {"name": info["name"], "phone": info["phone"], "balance": info["balance"]}
        exp["total_balance"] += info["balance"]
    export_file = "export.json"
    with open(export_file, "w", encoding="utf-8") as f:
        json.dump(exp, f, ensure_ascii=False, indent=4)
    try:
        await update.message.reply_document(
            document=open(export_file, "rb"),
            caption=f"Ma'lumotlar eksporti. Jami balans: {exp['total_balance']:,} so'm"
        )
    except Exception as e:
        logger.error(f"Failed to send export file: {e}")
        await update.message.reply_text("Ma'lumotlarni eksport qilishda xatolik yuz berdi.")

# ---------------------- Low Balance Notification ---------------------- #

async def send_low_balance_notifications(context: ContextTypes.DEFAULT_TYPE):
    data = initialize_data()
    today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
    for user_id, info in data["users"].items():
        if info["balance"] < 100000:
            last_notif = info.get("last_balance_notification", "")
            if last_notif == today:
                continue
            try:
                msg = (f"Hurmatli foydalanuvchi, sizning balansingiz {info['balance']:,} so'mga yetdi.\n"
                       "Iltimos, balansingizni to'ldiring. Rahmat!")
                await context.bot.send_message(chat_id=user_id, text=msg)
                data["users"][user_id]["last_balance_notification"] = today
            except Exception as e:
                logger.error(f"Failed to send low balance notification to user {user_id}: {e}")
    await save_data(data)

# Legacy reminder function (optional)
async def remind_debtors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    data = initialize_data()
    debtors = [(uid, info) for uid, info in data["users"].items() if info["balance"] < 100000]
    if not debtors:
        await update.message.reply_text("Hech kimda balans muammosi yo'q.")
        return
    sent, failed = 0, 0
    for user_id, info in debtors:
        try:
            await context.bot.send_message(chat_id=user_id, text=f"Sizning balansingiz: {info['balance']:,} so'm.")
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send reminder to user {user_id}: {e}")
            failed += 1
    await update.message.reply_text(f"✅ {sent} ta foydalanuvchiga eslatma yuborildi.\n❌ {failed} ta yuborilmadi.")

# ---------------------- Help Command ---------------------- #

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    
    # Base help message for all users
    msg = "🍽️ MY TUSHLIK BOT BUYRUQLARI:\n\n"
    msg += "👤 FOYDALANUVCHI UCHUN:\n"
    msg += "/start - Botni ishga tushirish va ro'yxatdan o'tish\n"
    msg += "/balansim - Balansingizni ko'rish\n"
    msg += "/qatnashishlarim - Qatnashishlaringiz tarixi\n"
    msg += "/ism_ozgartirish - Ismingizni o'zgartirish\n"
    msg += "/yordam - Yordam\n\n"
    
    # Admin-specific commands
    if uid in admins["admins"]:
        msg += "👑 ADMINISTRATOR UCHUN:\n"
        msg += "Interaktiv tugmalar:\n"
        msg += " • 👥 Foydalanuvchilar - Barcha foydalanuvchilar ro'yxati\n"
        msg += " • ❌ Foydalanuvchini o'chirish - Foydalanuvchini o'chirish\n"
        msg += " • 💵 Balans qo'shish - Foydalanuvchi balansini oshirish\n"
        msg += " • 💸 Balans kamaytirish - Foydalanuvchi balansini kamaytirish\n"
        msg += " • 📝 Kunlik narx - Foydalanuvchi kunlik narxini o'zgartirish\n"
        msg += " • 📊 Bugungi qatnashuv - Bugungi tushlik qatnashuvchilari\n"
        msg += " • 🔄 Balanslarni nollash - Barcha balanslarni nolga tushirish\n"
        msg += " • 💰 Kassa - Kassa balansini ko'rish\n"
        msg += " • ⬅️ Asosiy menyu - Asosiy menyuga qaytish\n"
        msg += " • ❓ Yordam - Yordam\n\n"
        msg += "Buyruqlar:\n"
        msg += "/admin_qoshish [id] - Yangi admin qo'shish\n"
        msg += "/admin_ochirish [id] - Adminni o'chirish\n"
        msg += "/balans_nol - Barcha balanslarni nolga tushirish\n"
        msg += "/bugun - Bugungi tushlik qatnashuvchilari\n"
        msg += "/eksport - Ma'lumotlarni eksport qilish\n"
        msg += "/eslatma - Kam balansli foydalanuvchilarga eslatma yuborish\n"
        msg += "/kassa - Kassa balansini ko'rish\n"
        msg += "/test_survey - (Test) Tushlik so'rovini yuborish\n"
    
    await update.message.reply_text(msg)

# ---------------------- Keyboard Functions ---------------------- #

async def show_admin_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    keyboard = create_admin_keyboard()
    await update.message.reply_text(
        "Admin paneli:",
        reply_markup=keyboard
    )

async def show_regular_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = create_regular_keyboard()
    await update.message.reply_text(
        "Quyidagi tugmalardan birini tanlang:",
        reply_markup=keyboard
    )

async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        await show_admin_keyboard(update, context)
    except Exception as e:
        logger.error(f"Error in admin_panel_handler: {str(e)}")
        await update.message.reply_text("Admin paneliga kirishda xatolik yuz berdi.")

# ---------------------- Testing Command ---------------------- #

async def test_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_attendance_request(context, test=True)
    await update.message.reply_text("Test survey yuborildi!")

# ---------------------- Scheduled Low Balance Notification ---------------------- #

async def scheduled_low_balance_notification(context: ContextTypes.DEFAULT_TYPE):
    await send_low_balance_notifications(context)

# ---------------------- Admin: Remove user ---------------------- #

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        data = initialize_data()
        
        # Check if user is admin
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
        
        # Check if there are any users
        if not data.get("users"):
            await update.message.reply_text("Foydalanuvchilar ro'yxati bo'sh.")
            return
        
        # Create keyboard with user buttons
        keyboard = []
        for user_id, info in data["users"].items():
            name = info.get("name", "Noma'lum")
            button = InlineKeyboardButton(f"{name} (ID: {user_id})", callback_data=f"remove_user_{user_id}")
            keyboard.append([button])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("O'chirmoqchi bo'lgan foydalanuvchini tanlang:", reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error in remove_user: {str(e)}")
        await update.message.reply_text("Foydalanuvchini o'chirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def remove_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        
        # Check if user is admin
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        if uid not in admins["admins"]:
            await query.edit_message_text("Siz admin emassiz.")
            return
        
        # Get user ID from callback data
        target_id = query.data.split("_")[2]
        data = initialize_data()
        
        if target_id not in data["users"]:
            await query.edit_message_text("Foydalanuvchi topilmadi.")
            return
        
        # Get user info before removal
        user_info = data["users"][target_id]
        user_name = user_info.get("name", "Noma'lum")
        
        # Remove user from all relevant data
        if target_id in data["users"]:
            del data["users"][target_id]
        
        # Remove from daily attendance if present
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        if today in data["daily_attendance"]:
            for status in ["confirmed", "declined", "pending"]:
                if target_id in data["daily_attendance"][today][status]:
                    data["daily_attendance"][today][status].remove(target_id)
            if target_id in data["daily_attendance"][today].get("menu", {}):
                del data["daily_attendance"][today]["menu"][target_id]
        
        # Remove from attendance history
        for date in data["attendance_history"]:
            if target_id in data["attendance_history"][date]["confirmed"]:
                data["attendance_history"][date]["confirmed"].remove(target_id)
            if target_id in data["attendance_history"][date].get("menu", {}):
                del data["attendance_history"][date]["menu"][target_id]
        
        await save_data(data)
        await query.edit_message_text(f"✅ Foydalanuvchi {user_name} (ID: {target_id}) muvaffaqiyatli o'chirildi.")
        
    except Exception as e:
        logger.error(f"Error in remove_user_callback: {str(e)}")
        await query.edit_message_text("Foydalanuvchini o'chirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

# ---------------------- Add this function after other functions
async def daily_backup(context: ContextTypes.DEFAULT_TYPE):
    """Create a daily backup at midnight"""
    try:
        logger.info("Starting daily backup...")
        await create_backup()
        logger.info("Daily backup completed successfully")
        
        # Notify all admins about successful backup
        admins = initialize_admins()
        for admin_id in admins["admins"]:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text="✅ Kunlik zaxira nusxasi muvaffaqiyatli yaratildi"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
    except Exception as e:
        logger.error(f"Daily backup failed: {e}")
        # Notify admins about backup failure
        admins = initialize_admins()
        for admin_id in admins["admins"]:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"❌ Kunlik zaxira nusxasi yaratishda xatolik yuz berdi: {e}"
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")

# ---------------------- Add these functions after other functions
async def verify_backup(backup_path: str) -> bool:
    """Verify that a backup file is valid"""
    try:
        if backup_path.endswith('.json'):
            with open(backup_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Basic validation
                if not isinstance(data, dict):
                    return False
                if 'users' not in data:
                    return False
                return True
        elif backup_path.endswith('.db'):
            # Basic SQLite database validation
            import sqlite3
            conn = sqlite3.connect(backup_path)
            cursor = conn.cursor()
            # Check if tables exist
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            conn.close()
            return len(tables) > 0
        return False
    except Exception as e:
        logger.error(f"Backup verification failed: {e}")
        return False

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the backup command with verification"""
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    
    await update.message.reply_text("Ma'lumotlarni zaxiralash boshlandi...")
    
    try:
        # Create backup
        backup_files = await create_backup()
        
        # Verify backups
        verification_results = []
        for backup_file in backup_files:
            is_valid = await verify_backup(backup_file)
            verification_results.append((backup_file, is_valid))
        
        # Prepare status message
        status_message = "Zaxira nusxalari holati:\n\n"
        for file_path, is_valid in verification_results:
            file_name = os.path.basename(file_path)
            status = "✅" if is_valid else "❌"
            status_message += f"{status} {file_name}\n"
        
        await update.message.reply_text(status_message)
        
        # If any backup failed verification, notify admins
        if not all(is_valid for _, is_valid in verification_results):
            for admin_id in admins["admins"]:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text="⚠️ Diqqat: Ba'zi zaxira nusxalari noto'g'ri yaratildi!"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_id}: {e}")
                    
    except Exception as e:
        logger.error(f"Backup command failed: {e}")
        await update.message.reply_text(f"Zaxira nusxasi yaratishda xatolik yuz berdi: {e}")

# ---------------------- Add this function before the main function
async def notify_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a notification to all users to restart the bot"""
    uid = str(update.effective_user.id)
    admins = initialize_admins()
    
    if uid not in admins["admins"]:
        await update.message.reply_text("Siz admin emassiz.")
        return
    
    # Get all users from data
    data = initialize_data()
    users = data.get("users", {})
    
    if not users:
        await update.message.reply_text("Foydalanuvchilar topilmadi.")
        return
    
    # Ask for confirmation
    keyboard = [
        [
            InlineKeyboardButton("✅ Ha", callback_data="confirm_notify_all"),
            InlineKeyboardButton("❌ Yo'q", callback_data="cancel_notify_all")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚠️ Diqqat!\n"
        f"Barcha {len(users)} ta foydalanuvchiga xabar yuborishni tasdiqlaysizmi?\n"
        f"Bu xabar ularga botni qayta ishga tushirish va ro'yxatdan o'tishni so'raydi.",
        reply_markup=reply_markup
    )

async def notify_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the notification confirmation callback"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_notify_all":
        await query.edit_message_text("Bildirishnoma yuborish bekor qilindi.")
        return
    
    # Get all users
    data = initialize_data()
    users = data.get("users", {})
    
    # Prepare the message
    message = (
        "⚠️ Muhim xabar!\n\n"
        "Bot yangilandi va sizning ma'lumotlaringiz yangilanishi kerak.\n\n"
        "Iltimos, quyidagi amallarni bajaring:\n"
        "1. /start buyrug'ini yuboring\n"
        "2. Yangi ro'yxatdan o'ting\n"
        "3. Telefon raqamingizni qayta kiritishingiz mumkin\n\n"
        "Bu jarayon bir necha soniyani oladi. Rahmat!"
    )
    
    # Send to all users
    success_count = 0
    failed_count = 0
    
    for user_id in users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=message
            )
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send message to user {user_id}: {e}")
            failed_count += 1
    
    # Send status to admin
    status_message = (
        f"Bildirishnoma yuborish yakunlandi:\n"
        f"✅ Muvaffaqiyatli: {success_count}\n"
        f"❌ Muvaffaqiyatsiz: {failed_count}"
    )
    
    await query.edit_message_text(status_message)

# ---------------------- Main Function ---------------------- #

def main():
    # Create the Application and pass it your bot's token
    token = os.getenv('BOT_TOKEN')
    if not token:
        logger.error("BOT_TOKEN environment variable not found!")
        return
        
    application = Application.builder().token(token).build()

    # Add registration conversation handler
    registration_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            PHONE: [MessageHandler(filters.CONTACT | filters.TEXT & ~filters.COMMAND, phone)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, name)]
        },
        fallbacks=[CommandHandler("cancel", cancel_registration)],
        allow_reentry=True
    )
    application.add_handler(registration_handler)

    # Add name change conversation handler
    name_change_conv = ConversationHandler(
        entry_points=[
            CommandHandler('ism_ozgartirish', start_name_change),
            MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change)
        ],
        states={
            NAME_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_name_change)]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    application.add_handler(name_change_conv)
    # Add admin balance modification conversation handler
    balance_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(💵 Balans qo'shish|💸 Balans kamaytirish)$"), start_balance_modification)],
        states={
            ADMIN_BALANCE_SELECT_USER: [CallbackQueryHandler(balance_mod_select_user_callback, pattern="^balance_mod_")],
            ADMIN_BALANCE_ENTER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, balance_mod_enter_amount)]
        },
        fallbacks=[CommandHandler("cancel", cancel_balance_modification)]
    )
    application.add_handler(balance_conv)

    # Add admin daily price adjustment conversation handler
    daily_price_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(📝 Kunlik narx)$"), start_daily_price_modification)],
        states={
            ADMIN_DAILY_PRICE_SELECT_USER: [CallbackQueryHandler(daily_price_mod_select_user_callback, pattern="^price_mod_")],
            ADMIN_DAILY_PRICE_ENTER_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, daily_price_mod_enter_amount)]
        },
        fallbacks=[CommandHandler("cancel", cancel_daily_price_modification)]
    )
    application.add_handler(daily_price_conv)

    # Add command handlers
    application.add_handler(CommandHandler('balansim', check_balance))
    application.add_handler(CommandHandler('qatnashishlarim', check_attendance))
    application.add_handler(CommandHandler('yordam', help_command))
    application.add_handler(CommandHandler('bekor_qilish', cancel_lunch))
    application.add_handler(CommandHandler('admin', show_admin_keyboard))
    application.add_handler(CommandHandler('admin_qoshish', make_admin))
    application.add_handler(CommandHandler('admin_ochirish', remove_admin))
    application.add_handler(CommandHandler('balans_nol', reset_balance))
    application.add_handler(CommandHandler('balanslar', view_all_balances))
    application.add_handler(CommandHandler('bugun', view_attendance_today_admin))
    application.add_handler(CommandHandler('eksport', export_data))
    application.add_handler(CommandHandler('eslatma', remind_debtors))
    application.add_handler(CommandHandler('kassa', view_kassa))
    application.add_handler(CommandHandler('test_survey', test_survey))
    application.add_handler(CommandHandler('backup', backup_command))
    application.add_handler(CommandHandler('notify_all', notify_all_users))
    application.add_handler(CommandHandler('update_daily_prices', update_all_daily_prices))

    # Add message handlers for regular buttons
    application.add_handler(MessageHandler(filters.Regex("^💸 Balansim$"), check_balance))
    application.add_handler(MessageHandler(filters.Regex("^📊 Qatnashishlarim$"), check_attendance))
    application.add_handler(MessageHandler(filters.Regex("^❌ Tushlikni bekor qilish$"), cancel_lunch))
    application.add_handler(MessageHandler(filters.Regex("^❓ Yordam$"), help_command))
    application.add_handler(MessageHandler(filters.Regex("^👑 Admin panel$"), admin_panel_handler))
    application.add_handler(MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change))

    # Add message handlers for admin buttons
    application.add_handler(MessageHandler(filters.Regex("^👥 Foydalanuvchilar$"), view_users))
    application.add_handler(MessageHandler(filters.Regex("^❌ Foydalanuvchini o'chirish$"), remove_user))
    application.add_handler(MessageHandler(filters.Regex("^💵 Balans qo'shish$"), start_balance_modification))
    application.add_handler(MessageHandler(filters.Regex("^💸 Balans kamaytirish$"), start_balance_modification))
    application.add_handler(MessageHandler(filters.Regex("^📝 Kunlik narx$"), start_daily_price_modification))
    application.add_handler(MessageHandler(filters.Regex("^📊 Bugungi qatnashuv$"), view_attendance_today_admin))
    application.add_handler(MessageHandler(filters.Regex("^🔄 Balanslarni nollash$"), reset_balance))
    application.add_handler(MessageHandler(filters.Regex("^💰 Kassa$"), view_kassa))
    application.add_handler(MessageHandler(filters.Regex("^⬅️ Asosiy menyu$"), show_regular_keyboard))

    # Add callback query handlers
    application.add_handler(CallbackQueryHandler(attendance_callback, pattern="^(attendance_|menu_)"))
    application.add_handler(CallbackQueryHandler(balance_reset_callback, pattern="^reset_all_balances_"))
    application.add_handler(CallbackQueryHandler(remove_user_callback, pattern="^remove_user_"))
    application.add_handler(CallbackQueryHandler(balance_mod_select_user_callback, pattern="^balance_mod_"))
    application.add_handler(CallbackQueryHandler(daily_price_mod_select_user_callback, pattern="^price_mod_"))

    # Schedule daily jobs
    job_queue = application.job_queue
    
    # Morning poll at 7:00 AM Tashkent time
    job_queue.run_daily(
        send_attendance_request,
        time=datetime.time(7, 0, tzinfo=TASHKENT_TZ)
    )
    
    # Summary at 10:00 AM Tashkent time
    job_queue.run_daily(
        send_attendance_summary,
        time=datetime.time(10, 0, tzinfo=TASHKENT_TZ)
    )

    # Low balance notification at 12:00 PM Tashkent time
    job_queue.run_daily(
        scheduled_low_balance_notification,
        time=datetime.time(12, 0, tzinfo=TASHKENT_TZ)
    )

    # Add daily backup job
    job_queue.run_daily(
        daily_backup,
        time=datetime.time(0, 0, tzinfo=TASHKENT_TZ)  # Run at midnight
    )

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
