# handlers/user_handlers.py

import datetime
import logging
import re
import pytz
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
    get_all_users_async,  # needed for scheduled jobs
)
from config import DEFAULT_DAILY_PRICE

# Initialize logger
logger = logging.getLogger(__name__)

# Define the menu items based on day of week
def get_menu_items():
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    weekday = now.weekday()
    
    # Monday (0), Wednesday (2), Friday (4)
    if weekday in [0, 2, 4]:
        return [
            "Qovurma Lag'mon", "Jarkob", "Sokoro", "Do'lma",
            "Osh", "Qovurma Makron", "Xonim", "Bifshteks"
        ]
    # Tuesday (1), Thursday (3)
    else:
        return [
            "Teftel sho'rva", "Mastava", "Chuchvara",
            "Sho'rva", "Suyuq Lag'mon"
        ]

# ─── BUTTON LABELS ─────────────────────────────────────────────────────────────

BAL_BTN   = "💸 Balansim"
NAME_BTN  = "✏️ Ism o'zgartirish"
CXL_BTN   = "❌ Tushlikni bekor qilish"
ADMIN_BTN = "🔧 Admin panel"
CARD_BTN  = "💳 Karta Raqami"

# ─── STATES ────────────────────────────────────────────────────────────────────

NAME, PHONE = range(2)
CHANGE_NAME = 2
YES, NO = "att_yes", "att_no"

# ─── /start & REGISTRATION FLOW ────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        await update.message.reply_text(
            "Ismingizni kiriting:", reply_markup=ReplyKeyboardRemove()
        )
        return NAME

    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Assalomu alaykum, {user.name}!\nNimani bajarishni hohlaysiz?",
        reply_markup=kb
    )
    return ConversationHandler.END

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not validate_name(name):
        await update.message.reply_text(
            "Ism noto'g'ri. Qaytadan kiriting:", reply_markup=ReplyKeyboardRemove()
        )
        return NAME

    context.user_data["name"] = name
    kb = [[KeyboardButton("Telefon raqamingizni yuboring", request_contact=True)]]
    await update.message.reply_text(
        "Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return PHONE

async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = (
        update.message.contact.phone_number
        if update.message.contact
        else update.message.text
    )
    if not validate_phone(phone):
        await update.message.reply_text("Raqam noto'g'ri. Qaytadan kiriting:")
        return PHONE

    user = await User.create(
        update.effective_user.id,
        context.user_data["name"],
        phone
    )

    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Ro'yxatdan o'tish yakunlandi. Balans: {user.balance} so'm.",
        reply_markup=kb
    )
    return ConversationHandler.END

# ─── NAME CHANGE FLOW ─────────────────────────────────────────────────────────

async def change_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yangi ismingizni kiriting:", reply_markup=ReplyKeyboardRemove()
    )
    return CHANGE_NAME

async def change_name_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if not validate_name(new_name):
        await update.message.reply_text("Ism noto'g'ri. Yana urinib ko'ring:")
        return CHANGE_NAME

    user = await get_user_async(update.effective_user.id)
    await user.change_name(new_name)
    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Ismingiz muvaffaqiyatli o'zgardi: {new_name}", reply_markup=kb
    )
    return ConversationHandler.END

# ─── CANCEL ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operatsiya bekor qilindi.")
    return ConversationHandler.END

# ─── SIMPLE COMMANDS & HISTORY ────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/start - Ro'yxatdan o'ting\n"
        "/menu - Taom tanlash\n"
        "/balance - Balansni ko'rish\n"
        "/attendance - Qatnashuv tarixini ko'rish\n"
        "/history - To'lovlar tarixini ko'rish\n"
        "/name - Ismingizni o'zgartirish\n"
        "/cancel_lunch - Buyurtmani bekor qilish\n"
        "/help - Yordam"
    )
    await update.message.reply_text(text)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        return await update.message.reply_text(
            "Iltimos, avval /start bilan ro'yxatdan o'ting."
        )
    await update.message.reply_text(f"Balansingiz: {user.balance} so'm.")

async def attendance_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    hist = user.attendance
    text = "Qatnashgan kunlar:\n" + "\n".join(hist) if hist else "Hech qanday qatnashuv yo'q."
    await update.message.reply_text(text)

async def transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    txs = user.transactions
    lines = [f"{t['date'][:10]}: {t['desc']} ({t['amount']} so'm)" for t in txs[-20:]]
    text = "To'lovlar tarixi:\n" + "\n".join(lines) if lines else "Hech qanday tranzaksiya yo'q."
    await update.message.reply_text(text)

# ─── MENU SELECTION ───────────────────────────────────────────────────────────

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's menu"""
    user = await get_user_async(update.effective_user.id)
    if not user:
        return await update.message.reply_text(
            "Iltimos, avval /start bilan ro'yxatdan o'ting."
        )
    
    # Get menu items based on day of week
    menu_items = get_menu_items()
    
    # Create keyboard with menu items
    keyboard = []
    for item in menu_items:
        keyboard.append([InlineKeyboardButton(item, callback_data=item)])
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_menu")])
    
    await update.message.reply_text(
        "Bugungi taomlar:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This function seems redundant if food selection happens via attendance_cb/food_selection_cb
    # Consider removing or refactoring if it's unused.
    q = update.callback_query
    await q.answer()
    choice = q.data.split(":")[1] # Get food name directly

    user = await get_user_async(update.effective_user.id)
    today = datetime.datetime.now(
        pytz.timezone("Asia/Tashkent")
    ).strftime("%Y-%m-%d")
    # Ensure add_attendance handles food choice
    await user.add_attendance(today, food=choice)
    await q.edit_message_text(f"{choice} buyurtma qilindi. Balansingiz: {user.balance} so'm.")

# ─── "Today?" INLINE CALLBACK (Ha/Yo'q) ────────────────────────────────────────

async def attendance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    logger.info(f"--- attendance_cb START --- Data: {query.data}")
    user = await get_user_async(update.effective_user.id)
    today = datetime.datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%Y-%m-%d")
    
    # Determine if test based on callback data prefix
    is_test = query.data.startswith("test_")
    # Set/clear the flag in user_data for this user's context
    if is_test:
        context.user_data['test_mode'] = True
        logger.info(f"attendance_cb: Test mode DETECTED via callback for User={user.name}. Setting user_data flag.")
    else:
        context.user_data.pop('test_mode', None)
        logger.info(f"attendance_cb: Regular mode DETECTED via callback for User={user.name}. Ensuring user_data flag is clear.")
        
    # Log status
    logger.info(f"attendance_cb: User={user.name}, Today={today}, IsTest={is_test}, UserData={context.user_data}")
    
    # Define now for hour check
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    
    # Check for YES response (test_att_yes or att_yes)
    if query.data.endswith(YES):
        if today in user.attendance and not is_test:
            logger.warning(f"attendance_cb: Blocking regular user {user.name} who already attended today.")
            await query.message.edit_text(
                f"⚠️ Siz bugun allaqachon ro'yxatdasiz. Balansingiz: {user.balance} so'm."
            )
            return

        if is_test and today in user.attendance:
            logger.info(f"attendance_cb: TEST MODE - User {user.name} attended today ({today}), removing previous record.")
            await user.remove_attendance(today)
            logger.info(f"attendance_cb: TEST MODE - Previous attendance removed for {user.name}.")
        elif is_test:
             logger.info(f"attendance_cb: TEST MODE - User {user.name} did NOT attend today, proceeding.")

        # Get menu items based on day of week
        menu_items = get_menu_items()
        
        # Create keyboard with food options
        keyboard = [[InlineKeyboardButton(item, callback_data=f"food:{item}")] for item in menu_items]
        if now.hour < 10:
            keyboard.append([InlineKeyboardButton("❌ Tushlikni bekor qilish", callback_data="cancel_lunch")])
        keyboard.append([InlineKeyboardButton("🔙 Ortga", callback_data="cancel_attendance")])
        prefix = "⚠️ TEST: " if is_test else ""
        await query.message.edit_text(
            f"{prefix}🍽 Iltimos, taom tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    # Check for NO response (test_att_no or att_no)
    elif query.data.endswith(NO):
        prefix = "⚠️ TEST: " if is_test else ""
        if today in user.attendance:
            # Remove attendance only if it exists (relevant for test mode mainly)
            await user.remove_attendance(today)
            await query.message.edit_text(
                f"{prefix}❌ {today} uchun buyurtma bekor qilindi. Balans: {user.balance} so'm."
            )
        else:
            await query.message.edit_text(f"{prefix}Siz bugun ro'yxatda emassiz.")

async def test_survey(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await user_is_admin(update.effective_user.id):
        await update.message.reply_text("Bu buyruq faqat adminlar uchun.")
        return

    logger.info(f"--- test_survey START --- User: {update.effective_user.id}")
    
    # Add _test suffix to callback data for test survey
    keyboard = [[
        InlineKeyboardButton("Ha", callback_data=f"{YES}_test"),
        InlineKeyboardButton("Yo'q", callback_data=f"{NO}_test")
    ]]

    users = await get_all_users_async()
    sent_count = 0
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u.telegram_id,
                text="⚠️ TEST: Bugun tushlikka borasizmi?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send test survey prompt to {u.name}: {e}")
            pass
    logger.info(f"test_survey: Sent prompts to {sent_count} users.")

    # Schedule summary for 3 minutes later, passing test flag in job data
    context.job_queue.run_once(send_summary, when=180, data={'is_test': True})
    logger.info(f"test_survey: Summary job scheduled.")

async def food_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    logger.info(f"--- food_selection_cb START --- Data: {query.data}")
    user = await get_user_async(update.effective_user.id)
    today = datetime.datetime.now(pytz.timezone("Asia/Tashkent")).strftime("%Y-%m-%d")
    
    # Determine test mode and prefix based on user_data set by attendance_cb
    is_test_callback = context.user_data.get('test_mode', False)
    prefix = "⚠️ TEST: " if is_test_callback else ""
    logger.info(f"food_selection_cb: User={user.name}, Today={today}, IsTestCallback={is_test_callback}, Prefix='{prefix}', UserData={context.user_data}")

    # Use base_data for comparisons
    if query.data.endswith("cancel_attendance"):
        await query.message.edit_text("❌ Ro'yxatga olish bekor qilindi.")
        return

    if query.data.endswith("cancel_lunch"):
        if today in user.attendance:
            await user.remove_attendance(today, is_test=is_test_callback)
            await query.message.edit_text(
                f"{prefix}❌ {today} uchun buyurtma bekor qilindi. Balans: {user.balance} so'm."
            )
        else:
            await query.message.edit_text(f"{prefix}❌ Buyurtma bekor qilindi.")
        return
    
    # Handle actual food selection
    food = query.data.split(":")[1]
    logger.info(f"food_selection_cb: Food selected: {food}")
    
    # Check if already attended
    logger.info(f"food_selection_cb: Checking attendance for {today}. Current attendance: {user.attendance}")
    if today in user.attendance:
        logger.info(f"food_selection_cb: User {user.name} IS marked as attended for {today}.")
        # Use the is_test_callback determined from user_data at the start
        logger.info(f"food_selection_cb: Checking IsTestCallback={is_test_callback} before blocking.")
        
        if not is_test_callback: # Block changes in regular mode if already attended
            logger.warning(f"food_selection_cb: BLOCKING user {user.name}. IsTestCallback={is_test_callback}.")
            food_chosen = await user.get_food_choice(today, is_test=is_test_callback)
            await query.message.edit_text(
                 f"{prefix}⚠️ Siz bugun allaqachon ro'yxatdasiz ({food_chosen or 'Tanlanmagan'}). Balansingiz: {user.balance} so'm."
            )
            return
        else:
             # If it's test mode (even if attended), allow change by removing first
             logger.info(f"food_selection_cb: TEST MODE - Allowing food change for {user.name} on {today}. Removing previous attendance.")
             await user.remove_attendance(today, is_test=True) # Remove previous test attendance
             # Prefix is already set correctly
    else:
        logger.info(f"food_selection_cb: User {user.name} is NOT marked as attended for {today}. Proceeding to add.")

    # Add attendance with food choice
    logger.info(f"---> Calling add_attendance for {user.name} on {today} with food: {food}")
    logger.info(f"---> User attendance BEFORE call: {user.attendance}")
    await user.add_attendance(today, food, is_test=is_test_callback)
    
    # Get the food choice after adding attendance
    food_choice = await user.get_food_choice(today, is_test=is_test_callback)
    await query.message.edit_text(
        f"{prefix}✅ {food_choice} tanlandi. Balansingiz: {user.balance} so'm."
    )

async def cancel_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    if now.hour >= 10:
        return await update.message.reply_text("Bekor qilish vaqti o'tdi.")

    user = await get_user_async(update.effective_user.id)
    if today not in user.attendance:
        return await update.message.reply_text("Siz bugun ro'yxatda emassiz.")

    await user.remove_attendance(today)
    await update.message.reply_text(
        f"{today} uchun buyurtma bekor qilindi. Balans: {user.balance} so'm.",
        reply_markup=ReplyKeyboardRemove()
    )

# ─── PING & SCHEDULED JOBS ────────────────────────────────────────────────────

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.debug(f"PING from {update.effective_user.id}")
    await update.message.reply_text("pong")

async def daily_attendance_request(context: ContextTypes.DEFAULT_TYPE, is_test: bool = False):
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5 and not is_test:  # skip weekends for non-tests
        logger.info("Skipping daily attendance request on weekend.")
        return

    # Define callback data based on whether it's a test
    yes_callback = f"{'test_' if is_test else ''}{YES}"
    no_callback = f"{'test_' if is_test else ''}{NO}"
    message_prefix = "⚠️ TEST: " if is_test else ""
    
    keyboard = [[
        InlineKeyboardButton("Ha", callback_data=yes_callback),
        InlineKeyboardButton("Yo'q", callback_data=no_callback)
    ]]
    
    users = await get_all_users_async()
    logger.info(f"Sending {'test ' if is_test else ''}attendance request to {len(users)} users.")
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u.telegram_id,
                text=f"{message_prefix}Bugun tushlikka borasizmi?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            logger.error(f"Failed to send attendance prompt to {u.name}: {e}")

async def low_balance_alert(context: ContextTypes.DEFAULT_TYPE):
    users = await get_all_users_async()
    for u in users:
        if u.balance < 50000:
            try:
                await context.bot.send_message(
                    chat_id=u.telegram_id,
                    text=(
                        f"Eslatma: balansingiz {u.balance} so'm, "
                        "iltimos to'ldiring."
                    )
                )
            except:
                pass

async def morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:
        return

    kb = [[
        InlineKeyboardButton("Ha", callback_data=YES),
        InlineKeyboardButton("Yo'q", callback_data=NO),
    ]]
    users = await get_all_users_async()
    for u in users:
        try:
            await context.bot.send_message(
                chat_id=u.telegram_id,
                text="Bugun tushlikka borasizmi?",
                reply_markup=InlineKeyboardMarkup(kb),
            )
        except:
            pass

async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send summary of today's attendance to admins and users."""
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    # Skip weekends for non-test summaries
    is_test_summary = context.job.data.get('is_test', False) if context.job and context.job.data else False
    if now.weekday() >= 5 and not is_test_summary:
        logger.info("Skipping summary on weekend.")
        return

    logger.info(f"Running send_summary. is_test_summary = {is_test_summary}")

    users = await get_all_users_async()
    attendees = []
    attendee_details = []  # List to store name and food choice pairs

    logger.info(f"--- Generating Summary for {today} ---")
    for u in users:
        if today in u.attendance:
            attendees.append(u)
            food_choice = await u.get_food_choice(today, is_test=is_test_summary)
            attendee_details.append((u.name, food_choice))

    # Get food counts using the new aggregation method
    food_counts = await User.get_daily_food_counts(today, is_test=is_test_summary)
    logger.info(f"Food counts from aggregation: {food_counts}")

    # Find the most popular food(s) with proper tie handling
    most_popular_foods = []
    if food_counts:
        # Get the highest count
        max_count = max(data['count'] for data in food_counts.values())
        # Get all foods with the highest count
        tied_foods = [food for food, data in food_counts.items() if data['count'] == max_count]
        
        if len(tied_foods) > 1:
            # If there's a tie, use all tied foods
            most_popular_foods = sorted(tied_foods)
            logger.info(f"Tie detected between foods: {tied_foods}")
        else:
            most_popular_foods = [tied_foods[0]]
            logger.info(f"Most popular food: {tied_foods[0]} with count {max_count}")

    # --- Build the summary message ---
    admin_summary = f"""📊 *{'TEST: ' if is_test_summary else ''}Bugungi tushlik uchun yig'ilish:*

👥 Jami: *{len(attendees)}* kishi

"""

    # Add list of names with their food choices
    admin_summary += "📝 *Ro'yxat:*\n"
    if attendee_details:
        for i, (name, food) in enumerate(attendee_details, 1):
            food_text = f" - {food}" if food else " - Tanlanmagan"
            admin_summary += f"{i}. {name}{food_text}\n"
    else:
        admin_summary += "Hech kim yo'q"
    admin_summary += "\n\n"

    # Add food statistics with ranking
    admin_summary += "🍽 *Taomlar statistikasi:*\n"
    if food_counts:
        rank = 1
        for food, data in food_counts.items():
            admin_summary += f"{rank}. {food} — {data['count']} ta\n"
            rank += 1
    else:
        admin_summary += "— Hech qanday taom tanlanmadi"

    # Send to admins
    for u_admin_check in users:
        if u_admin_check.is_admin:
            try:
                await context.bot.send_message(
                    u_admin_check.telegram_id,
                    admin_summary,
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.error(f"Failed to send admin summary to {u_admin_check.name}: {e}")

    # Send individual summaries (for both real and test surveys)
    for u in attendees:
        try:
            if most_popular_foods:
                if len(most_popular_foods) > 1:
                    # If there's a tie, show all tied foods
                    foods_text = " va ".join(most_popular_foods)
                    await context.bot.send_message(
                        u.telegram_id,
                        f"✅🍽️ Siz bugungi tushlik ro'yxatidasiz.\n\n🥇 Bugun tushlik uchun tanlangan taomlar: 🍛 {foods_text}",
                        reply_markup=get_default_kb(u.is_admin)  # Remove cancel button
                    )
                else:
                    # Single most popular food
                    await context.bot.send_message(
                        u.telegram_id,
                        f"✅🍽️ Siz bugungi tushlik ro'yxatidasiz.\n\n🥇 Bugun tushlik uchun tanlangan taom: 🍛 {most_popular_foods[0]}",
                        reply_markup=get_default_kb(u.is_admin)  # Remove cancel button
                    )
            else:
                await context.bot.send_message(
                    u.telegram_id,
                    "✅🍽️ Siz bugungi tushlik ro'yxatidasiz.\n\n🥄 Bugun asosiy taom aniqlanmadi.",
                    reply_markup=get_default_kb(u.is_admin)  # Remove cancel button
                )
        except Exception as e:
            logger.error(f"Failed to send user summary to {u.name}: {e}")

    # For test summary, clean up immediately after sending to admins and users
    if is_test_summary:
        logger.info("Cleaning up test data immediately after test summary...")
        
        # First, clean up the test food choices
        await User.cleanup_old_food_choices(is_test=True)
        
        # Then clean up attendance for users who participated in the test
        for user_id in [u.telegram_id for u in attendees]:
            try:
                user = await User.find_by_id(user_id)
                if user and today in user.attendance:
                    await user.remove_attendance(today, is_test=True)
                    logger.info(f"Cleaned up test attendance for user {user.name}")
            except Exception as e:
                logger.error(f"Error cleaning up test data for user {user_id}: {e}")
        
        logger.info("Test data cleanup complete")
        return  # Exit early for test summary

async def check_debts(context: ContextTypes.DEFAULT_TYPE):
    """Check for users with debt > 50,000 and send notifications."""
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.datetime.now(tz)
    
    # Only run on weekdays
    if now.weekday() >= 5:  # skip weekends
        return
        
    users = await get_all_users_async()
    for u in users:
        if u.balance < -50000:  # Negative balance means debt
            try:
                await context.bot.send_message(
                    chat_id=u.telegram_id,
                    text=(
                        f"⚠️ Eslatma: qarzingiz {abs(u.balance)} so'm. "
                        "Iltimos, balansingizni to'ldiring."
                    )
                )
            except:
                pass

# ─── SYNCHRONOUS admin-check (for your keyboard builder) ────────────────────────

async def is_admin(telegram_id: int) -> bool:
    u = await User.find_by_id(telegram_id)
    return bool(u and u.is_admin)

# ─── ASYNC admin-check (for admin_handlers.py) ────────────────────────────────

async def user_is_admin(telegram_id: int) -> bool:
    """Async check.  Admin-flows can safely `await user_is_admin(...)`."""
    u = await get_user_async(telegram_id)
    return bool(u and u.is_admin)

async def show_card_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show card details in monospace font with copy option"""
    card_details_col = await get_collection("card_details")
    card_info = await card_details_col.find_one({})
    
    if not card_info:
        await update.message.reply_text("Karta ma'lumotlari topilmadi.")
        return
    
    message = (
        f"*Karta raqami:*\n`{card_info['card_number']}`\n\n"
        f"*Karta egasi:*\n{card_info['card_owner']}"
    )
    
    await update.message.reply_text(
        message,
        parse_mode=ParseMode.MARKDOWN
    )

# ─── REGISTER HANDLERS ────────────────────────────────────────────────────────

def register_handlers(app):
    # 1) Registration flow
    reg = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
            PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), register_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(reg)

    # 2) Name-change flow
    name_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(fr"^{re.escape(NAME_BTN)}$"), change_name_start)],
        states={CHANGE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_name_exec)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(name_conv)

    # 3) Core slash-commands
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("attendance", attendance_history))
    app.add_handler(CommandHandler("history", transaction_history))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("cancel_lunch", cancel_lunch))

    # 4) Reply-keyboard shortcuts
    app.add_handler(MessageHandler(filters.Regex(fr"^{re.escape(BAL_BTN)}$"), balance))
    app.add_handler(MessageHandler(filters.Regex(fr"^{re.escape(NAME_BTN)}$"), change_name_start))
    app.add_handler(MessageHandler(filters.Regex(fr"^{re.escape(CXL_BTN)}$"), cancel_lunch))
    app.add_handler(MessageHandler(filters.Regex(fr"^{re.escape(CARD_BTN)}$"), show_card_details))

    # ← import here so user_handlers doesn't circularly import admin_handlers
    from handlers.admin_handlers import admin_panel
    app.add_handler(MessageHandler(filters.Regex(fr"^{re.escape(ADMIN_BTN)}$"), admin_panel))

    # 5) Inline callbacks - Updated patterns
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^(test_)?{YES}$")) # Catches att_yes and test_att_yes
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^(test_)?{NO}$"))   # Catches att_no and test_att_no
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^food:"))
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^cancel_lunch$"))
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^cancel_attendance$"))

    # Add debt check job (runs every 2 days at 12:00 PM Tashkent time)
    tz = pytz.timezone("Asia/Tashkent")
    first_time = datetime.time(hour=12, minute=0, tzinfo=tz)
    app.job_queue.run_repeating(
        check_debts,
        interval=datetime.timedelta(days=2),
        first=first_time,
        name="debt_check"
    )

async def handle_food_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.message.delete()
        return
    
    user = await get_user_async(update.effective_user.id)
    if not user:
        await query.message.edit_text("❌ Foydalanuvchi topilmadi.")
        return
    
    # Check if user has already selected food today
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    
    if today in user.food_choices:
        await query.message.edit_text("❌ Siz bugun allaqachon taom tanlagansiz.")
        return
    
    # Store the food choice
    user.food_choices[today] = query.data
    await user.save()
    
    # Update keyboard to show cancel button
    kb = get_default_kb(user.is_admin, has_food_selection=True)
    
    await query.message.edit_text(
        f"✅ {query.data} tanlandi!\n"
        f"Tushlikni bekor qilish uchun '❌ Tushlikni bekor qilish' tugmasini bosing.",
        reply_markup=kb
    )
