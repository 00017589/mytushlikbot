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
from db import db

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
ADMIN_SELECT_USER_FOR_NAME_CHANGE = 201
ADMIN_ENTER_NEW_NAME = 202

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

async def initialize_data():
    """Initialize data from MongoDB"""
    try:
        users = {str(user["user_id"]): user for user in db.get_all_users()}
        return {"users": users}
    except Exception as e:
        logger.error(f"Error initializing data: {str(e)}")
        return {"users": {}}

async def save_data(data):
    """Save data to MongoDB"""
    try:
        for user_id, user_data in data["users"].items():
            db.update_user(user_id, user_data)
        return True
    except Exception as e:
        logger.error(f"Error saving data: {str(e)}")
        return False

def initialize_admins():
    """Initialize admins from MongoDB"""
    try:
        admin_list = [str(admin["user_id"]) for admin in db.get_all_admins()]
        return {"admins": admin_list}
    except Exception as e:
        logger.error(f"Error initializing admins: {str(e)}")
        return {"admins": []}

async def save_admins(admins):
    """Save admins to MongoDB"""
    try:
        # First, remove all existing admins
        current_admins = db.get_all_admins()
        for admin in current_admins:
            db.remove_admin(admin["user_id"])
        
        # Then add the new admins
        for admin_id in admins["admins"]:
            db.add_admin(admin_id)
        return True
    except Exception as e:
        logger.error(f"Error saving admins: {str(e)}")
        return False

def set_daily_price_for_all_users(data, price=25000):
    """Set daily price for all users to the specified amount"""
    for user_id, user_data in data["users"].items():
        user_data["daily_price"] = price
    return data

async def create_backup():
    """Create a backup of all data files with timestamp"""
    try:
        # Create backups directory if it doesn't exist
        if not os.path.exists("backups"):
            os.makedirs("backups")
            
        timestamp = datetime.datetime.now(TASHKENT_TZ).strftime("%Y%m%d_%H%M%S")
        backup_files = []
        
        # Backup data.json
        if os.path.exists(DATA_FILE):
            backup_path = os.path.join("backups", f"data_{timestamp}.json")
            shutil.copy2(DATA_FILE, backup_path)
            backup_files.append(backup_path)
            
        # Backup admins.json
        if os.path.exists(ADMIN_FILE):
            backup_path = os.path.join("backups", f"admins_{timestamp}.json")
            shutil.copy2(ADMIN_FILE, backup_path)
            backup_files.append(backup_path)
            
        # Keep only last 7 days of backups
        for file_pattern in ["data_*.json", "admins_*.json"]:
            backup_files = sorted(glob.glob(os.path.join("backups", file_pattern)))
            for old_backup in backup_files[:-7]:  # Keep last 7 days
                try:
                    os.remove(old_backup)
                except Exception as e:
                    logger.error(f"Failed to remove old backup {old_backup}: {e}")
                    
        return backup_files
        
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
    """Create keyboard for admin panel"""
    keyboard = [
        [KeyboardButton("👥 Foydalanuvchilar"), KeyboardButton("❌ Foydalanuvchini o'chirish")],
        [KeyboardButton("💵 Balans qo'shish"), KeyboardButton("💸 Balans kamaytirish")],
        [KeyboardButton("📝 Kunlik narx"), KeyboardButton("📊 Bugungi qatnashuv")],
        [KeyboardButton("🔄 Balanslarni nollash"), KeyboardButton("💰 Kassa")],
        [KeyboardButton("❓ Yordam")],
        [KeyboardButton("⬅️ Asosiy menyu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def create_regular_keyboard():
    """Create keyboard for regular users"""
    keyboard = [
        ["💸 Balansim", "📊 Qatnashishlarim"],
        ["✏️ Ism o'zgartirish", "❌ Tushlikni bekor qilish"],
        ["❓ Yordam"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ---------------------- Registration and Name Change ---------------------- #

async def cancel_registration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Ro'yxatdan o'tish bekor qilindi.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = str(update.effective_user.id)
    data = await initialize_data()
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
    try:
        if update.message.contact:
            phone_number = update.message.contact.phone_number
            # Log the received phone number from contact
            logger.info(f"Received phone number from contact: {phone_number}")
        else:
            phone_number = update.message.text.strip()
            # Log the manually entered phone number
            logger.info(f"Received manual phone number: {phone_number}")
            
            # Basic phone number validation
            if not phone_number.replace('+', '').isdigit() or len(phone_number) < 9:
                await update.message.reply_text("Iltimos, to'g'ri telefon raqam kiriting")
                return PHONE
                
        # Ensure phone number is properly formatted
        if not phone_number.startswith('+'):
            phone_number = '+' + phone_number.lstrip('0')
            
        # Store phone number in context
        context.user_data['phone'] = phone_number
        
        # Log the final stored phone number
        logger.info(f"Storing phone number in context: {phone_number}")
        
        await update.message.reply_text(
            "Iltimos, ismingizni kiriting:",
            reply_markup=ReplyKeyboardRemove()
        )
        return NAME
        
    except Exception as e:
        logger.error(f"Error in phone registration: {str(e)}")
        await update.message.reply_text("Telefon raqamni saqlashda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return PHONE

async def name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        name_text = update.message.text.strip()
        if not name_text:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return NAME
            
        uid = str(update.effective_user.id)
        data = await initialize_data()
        
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
            
        data = await initialize_data()
        updated_count = 0
        failed_count = 0
        
        # Update each user's daily price
        for user_id, user_data in data["users"].items():
            try:
                user_data["daily_price"] = 25000
                db.update_user(user_id, user_data)
                updated_count += 1
            except Exception as e:
                logger.error(f"Failed to update daily price for user {user_id}: {e}")
                failed_count += 1
        
        # Save the changes
        await save_data(data)
        
        # Verify the changes
        verification_failed = 0
        data = await initialize_data()  # Reload data to verify
        for user_id, user_data in data["users"].items():
            if user_data.get("daily_price", 0) != 25000:
                verification_failed += 1
        
        if verification_failed > 0:
            await update.message.reply_text(
                f"⚠️ Diqqat: {verification_failed} ta foydalanuvchining kunlik narxi to'g'ri saqlanmadi!"
            )
        else:
            await update.message.reply_text(
                f"✅ {updated_count} ta foydalanuvchining kunlik narxi 25,000 so'mga o'zgartirildi.\n"
                f"❌ {failed_count} ta foydalanuvchida xatolik yuz berdi."
            )
            
    except Exception as e:
        logger.error(f"Error in update_all_daily_prices: {str(e)}")
        await update.message.reply_text("Kunlik narxlarni yangilashda xatolik yuz berdi.")

async def start_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the name change process for both regular users and admins"""
    try:
        uid = str(update.effective_user.id)
        data = await initialize_data()
        admins = initialize_admins()
        
        # Check if user exists
        if uid not in data["users"]:
            await update.message.reply_text("Iltimos, /start orqali ro'yxatdan o'ting.")
            return ConversationHandler.END
            
        # If admin is using the admin panel button
        if uid in admins["admins"] and update.message.text == "✏️ Ism o'zgartirish":
            # Create keyboard with user buttons
            keyboard = []
            for user_id, info in data["users"].items():
                name = info.get("name", "Noma'lum")
                button = InlineKeyboardButton(f"{name} (ID: {user_id})", callback_data=f"name_change_{user_id}")
                keyboard.append([button])
                
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                "Ismini o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:",
                reply_markup=reply_markup
            )
            return ADMIN_SELECT_USER_FOR_NAME_CHANGE
            
        # Regular user changing their own name
        await update.message.reply_text("Yangi ismingizni kiriting:")
        context.user_data['name_change_target'] = uid  # Store target as self
        return NAME_CHANGE
        
    except Exception as e:
        logger.error(f"Error in start_name_change: {str(e)}")
        await update.message.reply_text("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

async def handle_name_change_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle admin's selection of user for name change"""
    try:
        query = update.callback_query
        await query.answer()
        
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await query.edit_message_text("Siz admin emassiz.")
            return ConversationHandler.END
            
        target_id = query.data.split("_")[2]
        context.user_data['name_change_target'] = target_id
        
        await query.edit_message_text("Yangi ismni kiriting:")
        return ADMIN_ENTER_NEW_NAME
        
    except Exception as e:
        logger.error(f"Error in handle_name_change_selection: {str(e)}")
        await query.edit_message_text("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

async def process_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process name change for both regular users and admins"""
    try:
        new_name = update.message.text.strip()
        if not new_name:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return context.user_data.get('admin_mode', False) and ADMIN_ENTER_NEW_NAME or NAME_CHANGE
            
        uid = str(update.effective_user.id)
        target_id = context.user_data.get('name_change_target')
        
        if not target_id:
            await update.message.reply_text("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
            return ConversationHandler.END
            
        # Get current data
        data = await initialize_data()
        if target_id not in data["users"]:
            await update.message.reply_text("Foydalanuvchi topilmadi.")
            return ConversationHandler.END
            
        # Store old name for confirmation message
        old_name = data["users"][target_id]["name"]
        
        # Update user data in database first
        user_data = data["users"][target_id].copy()
        user_data["name"] = new_name
        
        # Update in database
        db_result = db.update_user(target_id, user_data)
        if not db_result.acknowledged:
            logger.error(f"Database update failed for user {target_id}")
            await update.message.reply_text(
                "Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
                reply_markup=create_regular_keyboard() if uid == target_id else create_admin_keyboard()
            )
            return ConversationHandler.END
            
        # Update local data
        data["users"][target_id]["name"] = new_name
        
        # Update daily attendance if present
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        if today in data.get("daily_attendance", {}):
            daily_data = data["daily_attendance"][today]
            for status in ["confirmed", "declined", "pending"]:
                if target_id in daily_data.get(status, []):
                    daily_data[status].remove(target_id)
                    daily_data[status].append(target_id)
            db.update_daily_attendance(today, daily_data)
            
        # Update attendance history
        for date in data.get("attendance_history", {}):
            history = db.get_attendance_history(date)
            if history:
                for status in ["confirmed", "declined"]:
                    if target_id in history.get(status, []):
                        history[status].remove(target_id)
                        history[status].append(target_id)
                db.update_attendance_history(date, history)
                
        # Save local data changes
        await save_data(data)
        
        # Verify the change in database
        updated_user = db.get_user(target_id)
        if not updated_user or updated_user.get("name") != new_name:
            logger.error(f"Name change verification failed for user {target_id}")
            await update.message.reply_text(
                "Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
                reply_markup=create_regular_keyboard() if uid == target_id else create_admin_keyboard()
            )
            return ConversationHandler.END
            
        # Send appropriate confirmation message with correct keyboard
        if uid == target_id:
            await update.message.reply_text(
                f"Sizning ismingiz {old_name} dan {new_name} ga o'zgartirildi.",
                reply_markup=create_regular_keyboard()
            )
        else:
            await update.message.reply_text(
                f"Foydalanuvchi {old_name} ning ismi {new_name} ga o'zgartirildi.",
                reply_markup=create_admin_keyboard()
            )
            
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in process_name_change: {str(e)}")
        await update.message.reply_text(
            "Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.",
            f"{user_data['name']} ning kunlik narxi {old_price:,} so'mdan {price:,} so'mga o'zgartirildi.",
            reply_markup=create_admin_keyboard()
        )
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text("Iltimos, to'g'ri narx kiriting.")
        return ADMIN_DAILY_PRICE_ENTER_AMOUNT
    except Exception as e:
        logger.error(f"Error in daily_price_mod_enter_amount: {str(e)}")
        await update.message.reply_text("Kunlik narx o'zgartirishda xatolik yuz berdi.")
        return ConversationHandler.END

async def cancel_daily_price_modification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Kunlik narx o'zgarishi bekor qilindi.")
    return ConversationHandler.END

# ---------------------- Admin and General User Commands ---------------------- #

# General user: Check balance
async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = await initialize_data()
    if uid not in data["users"]:
        await update.message.reply_text("Siz ro'yxatdan o'tmagansiz. /start buyrug'ini yuboring.")
        return
    bal = data["users"][uid]["balance"]
    sign = "+" if bal >= 0 else ""
    await update.message.reply_text(f"Sizning balansingiz: {sign}{bal:,} so'm")

# General user: Attendance history
async def check_attendance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    data = await initialize_data()
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
            
        data = await initialize_data()
        if not data["users"]:
            await update.message.reply_text("Hozircha foydalanuvchilar mavjud emas.")
            return
            
        # Sort users by name
        sorted_users = sorted(data["users"].items(), key=lambda x: x[1]["name"])
        
        # Build message
        message = "📋 Foydalanuvchilar ro'yxati:\n\n"
        
        # Log data for debugging
        logger.info(f"Total users found: {len(sorted_users)}")
        
        for i, (user_id, user_data) in enumerate(sorted_users, 1):
            name = user_data.get("name", "N/A")
            phone = user_data.get("phone", "")  # Get phone number, empty string if not found
            balance = user_data.get("balance", 0)
            daily_price = user_data.get("daily_price", 25000)
            
            # Format phone number if it exists
            if not phone:
                phone = "N/A"
            elif not phone.startswith("+"):
                phone = "+" + phone
            
            # Log user data for debugging
            logger.info(f"User {user_id} data - Name: {name}, Phone: {phone}, Balance: {balance}")
            
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
        total_daily_price = sum(user.get("daily_price", 25000) for user in data["users"].values())
        
        message += f"\n📊 Jami foydalanuvchilar: {total_users} ta\n"
        message += f"💰 Jami balans: {total_balance:,} so'm\n"
        message += f"💵 Jami kunlik narxlar yig'indisi: {total_daily_price:,} so'm"
        
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
        data = await initialize_data()
        
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
    data = await initialize_data()
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
            
        data = await initialize_data()
        
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
    data = await initialize_data()
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
        data = await initialize_data()
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
    data = await initialize_data()
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
    data = await initialize_data()
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
    data = await initialize_data()
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
        data = await initialize_data()
        
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
        data = await initialize_data()
        
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

async def change_user_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command to change a user's name"""
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        if not context.args or len(context.args) < 2:
            await update.message.reply_text("Iltimos, foydalanuvchi ID va yangi ismni kiriting.\nMasalan: /change_name 123456789 Yangi Ism")
            return
            
        target_id = context.args[0]
        new_name = " ".join(context.args[1:])
        
        data = initialize_data()
        if target_id not in data["users"]:
            await update.message.reply_text("Bu foydalanuvchi topilmadi.")
            return
            
        old_name = data["users"][target_id]["name"]
        data["users"][target_id]["name"] = new_name
        
        # Update name in daily attendance if present
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        if today in data["daily_attendance"]:
            # Update in confirmed list
            if target_id in data["daily_attendance"][today]["confirmed"]:
                data["daily_attendance"][today]["confirmed"].remove(target_id)
                data["daily_attendance"][today]["confirmed"].append(target_id)
            
            # Update in declined list
            if target_id in data["daily_attendance"][today]["declined"]:
                data["daily_attendance"][today]["declined"].remove(target_id)
                data["daily_attendance"][today]["declined"].append(target_id)
            
            # Update in pending list
            if target_id in data["daily_attendance"][today]["pending"]:
                data["daily_attendance"][today]["pending"].remove(target_id)
                data["daily_attendance"][today]["pending"].append(target_id)
        
        # Update name in attendance history
        for date in data["attendance_history"]:
            if target_id in data["attendance_history"][date]["confirmed"]:
                data["attendance_history"][date]["confirmed"].remove(target_id)
                data["attendance_history"][date]["confirmed"].append(target_id)
            if target_id in data["attendance_history"][date]["declined"]:
                data["attendance_history"][date]["declined"].remove(target_id)
                data["attendance_history"][date]["declined"].append(target_id)
        
        await save_data(data)
        
        await update.message.reply_text(f"Foydalanuvchi {old_name} ning ismi {new_name} ga o'zgartirildi.")
        
    except Exception as e:
        logger.error(f"Error in change_user_name: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def start_name_change_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the process of changing a user's name as admin"""
    try:
        uid = str(update.effective_user.id)
        admins = initialize_admins()
        
        if uid not in admins["admins"]:
            await update.message.reply_text("Siz admin emassiz.")
            return
            
        data = initialize_data()
        if not data["users"]:
            await update.message.reply_text("Foydalanuvchilar ro'yxati bo'sh.")
            return
            
        # Create keyboard with user buttons
        keyboard = []
        for user_id, info in data["users"].items():
            name = info.get("name", "Noma'lum")
            button = InlineKeyboardButton(f"{name} (ID: {user_id})", callback_data=f"admin_change_name_{user_id}")
            keyboard.append([button])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Ismini o'zgartirmoqchi bo'lgan foydalanuvchini tanlang:", reply_markup=reply_markup)
        
    except Exception as e:
        logger.error(f"Error in start_name_change_admin: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def admin_name_change_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the admin name change callback"""
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
        target_id = query.data.split("_")[3]
        data = initialize_data()
        
        if target_id not in data["users"]:
            await query.edit_message_text("Foydalanuvchi topilmadi.")
            return
        
        # Store target ID in context for the next step
        context.user_data["admin_name_change_target"] = target_id
        await query.edit_message_text("Yangi ismni kiriting:")
        return "ADMIN_WAITING_FOR_NEW_NAME"
        
    except Exception as e:
        logger.error(f"Error in admin_name_change_callback: {str(e)}")
        await query.edit_message_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")

async def process_admin_name_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process the new name for the selected user in admin mode"""
    try:
        new_name = update.message.text.strip()
        if not new_name:
            await update.message.reply_text("Ism bo'sh bo'lmasligi kerak. Iltimos, qayta kiriting:")
            return "ADMIN_WAITING_FOR_NEW_NAME"
            
        target_id = context.user_data.get("admin_name_change_target")
        if not target_id:
            await update.message.reply_text("Xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
            return ConversationHandler.END
            
        data = initialize_data()
        if target_id not in data["users"]:
            await update.message.reply_text("Foydalanuvchi topilmadi.")
            return ConversationHandler.END
            
        old_name = data["users"][target_id]["name"]
        data["users"][target_id]["name"] = new_name
        
        # Update name in daily attendance if present
        today = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
        if today in data["daily_attendance"]:
            # Update in confirmed list
            if target_id in data["daily_attendance"][today]["confirmed"]:
                data["daily_attendance"][today]["confirmed"].remove(target_id)
                data["daily_attendance"][today]["confirmed"].append(target_id)
            
            # Update in declined list
            if target_id in data["daily_attendance"][today]["declined"]:
                data["daily_attendance"][today]["declined"].remove(target_id)
                data["daily_attendance"][today]["declined"].append(target_id)
            
            # Update in pending list
            if target_id in data["daily_attendance"][today]["pending"]:
                data["daily_attendance"][today]["pending"].remove(target_id)
                data["daily_attendance"][today]["pending"].append(target_id)
        
        # Update name in attendance history
        for date in data["attendance_history"]:
            if target_id in data["attendance_history"][date]["confirmed"]:
                data["attendance_history"][date]["confirmed"].remove(target_id)
                data["attendance_history"][date]["confirmed"].append(target_id)
            if target_id in data["attendance_history"][date]["declined"]:
                data["attendance_history"][date]["declined"].remove(target_id)
                data["attendance_history"][date]["declined"].append(target_id)
        
        # Save the changes
        await save_data(data)
        
        # Verify the changes were saved
        data = initialize_data()  # Reload data to verify
        if data["users"][target_id]["name"] != new_name:
            logger.error(f"Name change not saved properly for user {target_id}")
            await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
            return ConversationHandler.END
        
        await update.message.reply_text(
            f"Foydalanuvchi {old_name} ning ismi {new_name} ga o'zgartirildi.",
            reply_markup=create_admin_keyboard()
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in process_admin_name_change: {str(e)}")
        await update.message.reply_text("Ism o'zgartirishda xatolik yuz berdi. Iltimos, qayta urinib ko'ring.")
        return ConversationHandler.END

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

    # Add name change conversation handler with proper pattern matching
    name_change_conv = ConversationHandler(
        entry_points=[
            CommandHandler('ism_ozgartirish', start_name_change),
            MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change)
        ],
        states={
            NAME_CHANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_name_change)]
        },
        fallbacks=[CommandHandler('cancel', lambda u, c: ConversationHandler.END)]
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
    application.add_handler(CommandHandler('change_name', change_user_name))

    # Add message handlers for regular buttons
    application.add_handler(MessageHandler(filters.Regex("^💸 Balansim$"), check_balance))
    application.add_handler(MessageHandler(filters.Regex("^📊 Qatnashishlarim$"), check_attendance))
    application.add_handler(MessageHandler(filters.Regex("^❌ Tushlikni bekor qilish$"), cancel_lunch))
    application.add_handler(MessageHandler(filters.Regex("^❓ Yordam$"), help_command))
    application.add_handler(MessageHandler(filters.Regex("^👑 Admin panel$"), admin_panel_handler))
    application.add_handler(MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change_admin))

    # Add message handlers for admin buttons
    application.add_handler(MessageHandler(filters.Regex("^👥 Foydalanuvchilar$"), view_users))
    application.add_handler(MessageHandler(filters.Regex("^❌ Foydalanuvchini o'chirish$"), remove_user))
    application.add_handler(MessageHandler(filters.Regex("^💵 Balans qo'shish$"), start_balance_modification))
    application.add_handler(MessageHandler(filters.Regex("^💸 Balans kamaytirish$"), start_balance_modification))
    application.add_handler(MessageHandler(filters.Regex("^📝 Kunlik narx$"), start_daily_price_modification))
    application.add_handler(MessageHandler(filters.Regex("^📊 Bugungi qatnashuv$"), view_attendance_today_admin))
    application.add_handler(MessageHandler(filters.Regex("^🔄 Balanslarni nollash$"), reset_balance))
    application.add_handler(MessageHandler(filters.Regex("^💰 Kassa$"), view_kassa))
    application.add_handler(MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change_admin))
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

    # Add name change conversation handler for admin
    name_change_admin_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^✏️ Ism o'zgartirish$"), start_name_change_admin)
        ],
        states={
            "ADMIN_WAITING_FOR_NEW_NAME": [MessageHandler(filters.TEXT & ~filters.COMMAND, process_admin_name_change)]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)]
    )
    application.add_handler(name_change_admin_conv)
    
    # Add callback query handler for name change
    application.add_handler(CallbackQueryHandler(admin_name_change_callback, pattern="^admin_change_name_"))

    # Start the bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
