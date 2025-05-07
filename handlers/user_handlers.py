# handlers/user_handlers.py

import logging
import re
from datetime import datetime, time as dt_time
import pytz
from telegram.error import BadRequest
from telegram.constants import ParseMode, ChatAction
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from database import get_collection
from models.user_model import User
from utils import (
    validate_name,
    validate_phone,
    get_default_kb,
    get_user_async,
    get_all_users_async,
)
from utils.sheets_utils import find_user_in_sheet
from handlers.admin_handlers import admin_panel

logger = logging.getLogger(__name__)

# ─── BUTTON LABELS ───────────────────────────
BAL_BTN   = "💸 Balansim"
NAME_BTN  = "✏️ Ism o'zgartirish"
CXL_BTN   = "❌ Tushlikni bekor qilish"
ADMIN_BTN = "🔧 Admin panel"
CARD_BTN  = "💳 Karta Raqami"

# ─── STATES ───────────────────────────────────
NAME, PHONE = range(2)
CHANGE_NAME = 2
YES, NO     = "att_yes", "att_no"


# ─── /start & REGISTRATION ────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        await update.message.reply_text("Ismingizni kiriting:", reply_markup=ReplyKeyboardRemove())
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
        await update.message.reply_text("Ism noto'g'ri. Qaytadan kiriting:")
        return NAME
    context.user_data["name"] = name
    kb = [[KeyboardButton("Telefon raqamingizni yuboring", request_contact=True)]]
    await update.message.reply_text(
        "Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return PHONE

async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.contact.phone_number if update.message.contact else update.message.text
    if not validate_phone(phone):
        await update.message.reply_text("Raqam noto'g'ri. Qaytadan kiriting:")
        return PHONE

    user = await User.create(update.effective_user.id, context.user_data["name"], phone)
    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Ro'yxatdan o'tish yakunlandi. Balans: {user.balance:,.0f} so'm.",
        reply_markup=kb
    )
    return ConversationHandler.END


# ─── NAME CHANGE ──────────────────────────────
async def change_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Yangi ismingizni kiriting:", reply_markup=ReplyKeyboardRemove())
    return CHANGE_NAME

async def change_name_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if not validate_name(new_name):
        await update.message.reply_text("Ism noto'g'ri. Yana urinib ko'ring:")
        return CHANGE_NAME

    user = await get_user_async(update.effective_user.id)
    await user.change_name(new_name)
    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(f"Ismingiz muvaffaqiyatli o'zgardi: {new_name}", reply_markup=kb)
    return ConversationHandler.END


# ─── CANCEL ───────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operatsiya bekor qilindi.")
    return ConversationHandler.END


# ─── HELP / BALANCE / HISTORY ─────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/start — Ro'yxatdan o'tish\n"
        "/menu — Taom tanlash\n"
        "/balance — Balansni ko'rish\n"
        "/attendance — Qatnashuv tarixini ko'rish\n"
        "/history — To'lovlar tarixini ko'rish\n"
        "/name — Ism o'zgartirish\n"
        "/cancel_lunch — Buyurtmani bekor qilish\n"
        "/help — Yordam"
    )
    await update.message.reply_text(text)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user  = await get_user_async(tg_id)
    if not user:
        return await update.message.reply_text("Iltimos, avval /start bilan ro'yxatdan o'ting.")

    await update.message.reply_text("⏳ Balans tekshirilmoqda...")
    # keep them aware:
    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    try:
        sheet_record = await find_user_in_sheet(tg_id)
        if sheet_record and "balance" in sheet_record:
            bal = float(str(sheet_record["balance"]).replace(",", ""))
            if bal != user.balance:
                users = await get_collection("users")
                await users.update_one({"telegram_id": tg_id}, {"$set": {"balance": bal}})
                user.balance = bal
    except Exception as e:
        logger.error(f"Error fetching balance from sheet: {e}")

    await update.message.reply_text(f"Balansingiz: {user.balance:,.0f} so'm.")


async def attendance_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    hist = user.attendance
    text = "Qatnashgan kunlar:\n" + "\n".join(hist) if hist else "Hech qanday qatnashuv yo'q."
    await update.message.reply_text(text)

async def transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    txs  = user.transactions
    lines = [f"{t['date'][:10]}: {t['desc']} ({t['amount']} so'm)" for t in txs[-20:]]
    text  = "To'lovlar tarixi:\n" + "\n".join(lines) if lines else "Hech qanday tranzaksiya yo'q."
    await update.message.reply_text(text)


# ─── CARD INFO ─────────────────────────────────
async def show_card_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_col = await get_collection("card_details")
    doc = await card_col.find_one({})
    if not doc:
        return await update.message.reply_text("❌ Karta ma'lumotlari topilmadi.")
    await update.message.reply_text(
        f"💳 *Karta raqami:* `{doc['card_number']}`\n"
        f"👤 *Karta egasi:* {doc['card_owner']}",
        parse_mode=ParseMode.MARKDOWN
    )


# ─── MENU & FOOD ──────────────────────────────
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        return await update.message.reply_text("Iltimos, avval /start bilan ro'yxatdan o'ting.")

    tz = pytz.timezone("Asia/Tashkent")
    today_wd = datetime.now(tz).weekday()
    menu_name = "menu1" if today_wd in (0,2,4) else "menu2"
    menu_col  = await get_collection("menu")
    doc       = await menu_col.find_one({"name": menu_name})
    items     = doc.get("items", [])

    kb = [[InlineKeyboardButton(i, callback_data=f"food:{i}")] for i in items]
    kb.append([InlineKeyboardButton("🔙 Ortga", callback_data="cancel_attendance")])
    await update.message.reply_text("Bugungi taomlar:", reply_markup=InlineKeyboardMarkup(kb))


async def attendance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q        = update.callback_query
    await q.answer()
    user     = await get_user_async(q.from_user.id)
    tz       = pytz.timezone("Asia/Tashkent")
    today_dt = datetime.now(tz)
    today_str= today_dt.strftime("%Y-%m-%d")

    if q.data == NO:
        if today_str in user.attendance:
            await user.remove_attendance(today_str)
        await user.decline_attendance(today_str)
        kb = get_default_kb(user.is_admin)
        await q.message.edit_text("❌ Bugungi tushlik rad etildi.", reply_markup=kb)
        return

    if today_str in user.attendance:
        kb = get_default_kb(user.is_admin, has_food_selection=False)
        await q.message.edit_text(
            f"⚠️ Allaqachon ro'yxatdasiz. Balans: {user.balance:,.0f} so'm.",
            reply_markup=kb
        )
        return

    if today_str in user.declined_days:
        await user.remove_decline(today_str)

    tz        = pytz.timezone("Asia/Tashkent")
    today_wd  = datetime.now(tz).weekday()
    menu_name = "menu1" if today_wd in (0,2,4) else "menu2"
    # show food options
    menu_col  = await get_collection("menu")
    doc       = await menu_col.find_one({"name": menu_name})
    foods     = doc.get("items", [])
    kb = [[InlineKeyboardButton(f, callback_data=f"food:{f}")] for f in foods]
    kb.append([InlineKeyboardButton("🔙 Ortga", callback_data="cancel_attendance")])
    await q.message.edit_text("🍽 Iltimos, taom tanlang:", reply_markup=InlineKeyboardMarkup(kb))


async def food_selection_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = await get_user_async(q.from_user.id)
    data = q.data

    if q.data == "cancel_attendance":
        # cancel
        await q.message.edit_text("✅ Bekor qilindi.")
        await q.message.reply_text("Nimani xohlaysiz?", reply_markup=get_default_kb(user.is_admin))
        return

    food = q.data.split(":",1)[1]
    tz = pytz.timezone("Asia/Tashkent")
    today_str = datetime.now(tz).strftime("%Y-%m-%d")

    # record
    await user.set_food_choice(today_str, food)
    await user.add_attendance(today_str, food)

    # Safely edit the inline message:
    try:
        await q.message.edit_text(f"✅ {food} tanlandi!")
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            raise

    # Then send the follow‑up
    await q.message.reply_text(
        f"Balansingiz: {user.balance:,} so‘m",
        reply_markup=get_default_kb(user.is_admin)
    )


# ─── CANCEL LUNCH ──────────────────────────────
async def cancel_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.now(tz)
    if now.hour >= 10:
        return await update.message.reply_text("Bekor qilish vaqti o'tdi.")

    user = await get_user_async(update.effective_user.id)
    today_str = now.strftime("%Y-%m-%d")
    if today_str not in user.attendance:
        return await update.message.reply_text("Siz bugun ro'yxatda emassiz.")

    await user.remove_attendance(today_str)
    await update.message.reply_text(
        f"{today_str} uchun buyurtma bekor qilindi. Balans: {user.balance:,.0f} so'm.",
        reply_markup=get_default_kb(user.is_admin)
    )


# ─── SCHEDULED JOBS ────────────────────────────
async def morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.now(tz)
    if now.weekday() >= 5:
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ha", callback_data=YES),
         InlineKeyboardButton("Yo'q", callback_data=NO)]
    ])
    for u in await get_all_users_async():
        await context.bot.send_message(u.telegram_id, "Bugun tushlikka borasizmi?", reply_markup=kb)

async def check_debts(context: ContextTypes.DEFAULT_TYPE):
    for u in await get_all_users_async():
        if u.balance < 0:
            try:
                await context.bot.send_message(
                    chat_id=u.telegram_id,
                    text=(
                        f"👋 Assalomu alaykum!\n"
                        f"Sizning balansingizda {abs(u.balance):,.0f} so‘m qarzdorlik mavjud.\n"
                        "Iltimos, balansingizni to‘ldiring. 🙏"
                    )
                )
            except Exception as e:
                logger.error(f"Error notifying debt: {e}")


# ─── ADMIN SHORTCUT ───────────────────────────
async def admin_button_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_panel(update, context)


# ─── REGISTER HANDLERS ────────────────────────
def register_handlers(app):
    # registration
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

    # name change
    name_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(NAME_BTN)}$"), change_name_start)],
        states={CHANGE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_name_exec)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(name_conv)

    # core
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("attendance", attendance_history))
    app.add_handler(CommandHandler("history", transaction_history))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("cancel_lunch", cancel_lunch))

    # shortcuts
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BAL_BTN)}$"), balance))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(NAME_BTN)}$"), change_name_start))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(CXL_BTN)}$"), cancel_lunch))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(CARD_BTN)}$"), show_card_info))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(ADMIN_BTN)}$"), admin_panel))

    # inline
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^{YES}$"))
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^{NO}$"))
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^food:"))
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^cancel_attendance$"))