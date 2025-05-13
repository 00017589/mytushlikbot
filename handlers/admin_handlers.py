# handlers/admin_handlers.py
import re
import logging
from datetime import datetime, timezone
import pytz

from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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
from utils.sheets_utils import get_worksheet, update_user_balance_in_sheet, sync_balances_incremental
from utils import get_all_users_async, get_user_async, is_admin, get_default_kb
from models.user_model import User
from config import DEFAULT_DAILY_PRICE

menu_col = None
users_col = None
logger = logging.getLogger(__name__)

# ─── STATES ────────────────────────────────────────────────────────────────────
(
    S_ADD_ADMIN,      # selecting user to promote
    S_REMOVE_ADMIN,   # selecting admin to demote
    S_SET_PRICE,      # selecting user to set price
    S_INPUT_PRICE,    # entering custom price
    S_DELETE_USER,    # selecting user to delete
    S_CANCEL_DATE,    # entering cancellation date
    S_CANCEL_REASON,  # entering cancellation reason
    S_NOTIFY_MESSAGE, # entering broadcast text
    S_NOTIFY_CONFIRM, # confirming broadcast
    S_CARD_NUMBER,    # entering new card number
    S_CARD_OWNER,     # entering new card owner name
) = range(11)

# ─── BUTTON LABELS ─────────────────────────────────────────────────────────────
FOYD_BTN         = "Foydalanuvchilar"
ADD_ADMIN_BTN    = "Admin Qo'shish"
REMOVE_ADMIN_BTN = "Admin Olish"
DAILY_PRICE_BTN  = "Kunlik Narx"
DELETE_USER_BTN  = "Foydalanuvchini O‘chirish"
CXL_LUNCH_BTN    = "Tushlikni Bekor Qilish"
CARD_BTN         = "Karta Ma’lumotlari"
MENU_BTN         = "🍽 Menyu"
BACK_BTN         = "Ortga"
KASSA_BTN        = "Kassa"
NOTIFY_BTN       = "Xabar Yuborish"

# ─── MENU SUB‑BUTTONS ──────────────────────────────────────────────────────────
VIEW_MENU1_BTN = "1‑Menuni Ko‘rish"
VIEW_MENU2_BTN = "2‑Menuni Ko‘rish"
ADD_MENU1_BTN  = "1‑Menuga Qo‘shish"
ADD_MENU2_BTN  = "2‑Menuga Qo‘shish"
DEL_MENU1_BTN  = "1‑Menudan O‘chirish"
DEL_MENU2_BTN  = "2‑Menudan O‘chirish"

# ─── ADMIN PANEL KEYBOARD ──────────────────────────────────────────────────────
async def init_collections():
    """Initialize the menu collection and ensure menu1/menu2 exist."""
    global menu_col, users_col
    menu_col  = await get_collection("menu")
    users_col = await get_collection("users")
    for name in ("menu1", "menu2"):
        if not await menu_col.find_one({"name": name}):
            await menu_col.insert_one({"name": name, "items": []})

def get_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(VIEW_MENU1_BTN, callback_data="view_menu1"),
         InlineKeyboardButton(VIEW_MENU2_BTN, callback_data="view_menu2")],
        [InlineKeyboardButton(ADD_MENU1_BTN, callback_data="add_menu1"),
         InlineKeyboardButton(ADD_MENU2_BTN, callback_data="add_menu2")],
        [InlineKeyboardButton(DEL_MENU1_BTN, callback_data="del_menu1"),
         InlineKeyboardButton(DEL_MENU2_BTN, callback_data="del_menu2")],
        [InlineKeyboardButton(BACK_BTN, callback_data="back_to_admin")],
    ])

def get_admin_kb():
    return ReplyKeyboardMarkup([
        [FOYD_BTN, MENU_BTN],
        [ADD_ADMIN_BTN, REMOVE_ADMIN_BTN],
        [DAILY_PRICE_BTN, DELETE_USER_BTN],
        [CXL_LUNCH_BTN, CARD_BTN],
        [KASSA_BTN, NOTIFY_BTN],   
        [BACK_BTN],
    ], resize_keyboard=True)  

# ─── 1) /admin ENTRY & FIRST-TIME SETUP ────────────────────────────────────────
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin or “Ortga” from other admin flows: assign first admin if needed, then show panel."""
    is_callback = bool(update.callback_query)
    tg_id = update.effective_user.id

    # Ensure users_col is initialized
    global users_col
    if users_col is None:
        users_col = await get_collection("users")

    # First admin bootstrapping
    admin_exists = await users_col.count_documents({"is_admin": True}, limit=1) > 0
    if not admin_exists:
        await users_col.update_one(
            {"telegram_id": tg_id},
            {
                "$setOnInsert": {
                    "telegram_id": tg_id,
                    "name": update.effective_user.full_name,
                    "phone": "",
                    "balance": 0,
                    "daily_price": DEFAULT_DAILY_PRICE,
                    "attendance": [],
                    "transactions": [],
                    "food_choices": {},
                },
                "$set": {"is_admin": True},
            },
            upsert=True,
        )
        # Acknowledge first‐admin creation
        if is_callback:
            await update.callback_query.answer()
            # delete any old inline message
            try:
                await update.callback_query.message.delete()
            except BadRequest:
                pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Siz birinchi admin bo‘ldingiz!"
        )

    # Fetch and show panel
    user = await users_col.find_one({"telegram_id": tg_id})
    if user and user.get("is_admin", False):
        text, kb = "🔧 Admin panelga xush kelibsiz:", get_admin_kb()
    else:
        text, kb = "❌ Siz admin emassiz!", None

    # If invoked by callback, answer + delete old message
    if is_callback:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=kb
        )
    else:
        await update.message.reply_text(text, reply_markup=kb)

    return ConversationHandler.END


# ─── 2) BACK TO MAIN MENU ───────────────────────────────────────────────────────
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to the main menu with the correct reply keyboard."""
    from utils import get_default_kb

    tg_id = update.effective_user.id
    user = await users_col.find_one({"telegram_id": tg_id})
    is_admin = bool(user and user.get("is_admin", False))
    kb = get_default_kb(is_admin)
    text = "Bosh menyu:"

    if update.callback_query:
        await update.callback_query.answer()
        # Delete the current inline‐keyboard message
        try:
            await update.callback_query.message.delete()
        except BadRequest:
            pass
        # Send a fresh reply with the reply‐keyboard
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=kb
        )
    else:
        await update.message.reply_text(text, reply_markup=kb)

    return ConversationHandler.END

# ─── 3) LIST USERS ──────────────────────────────────────────────────────────────

def format_users_list(users: list[User]) -> str:
    if not users:
        return "Hech qanday foydalanuvchi yo‘q."
    lines = [
        f"• *{u.name}* (ID: {u.telegram_id})\n"
        f"   💰 Balans: *{u.balance:,}* so‘m | 📝 Narx: *{u.daily_price:,}* so‘m"
        for u in users
    ]
    return "\n\n".join(lines)

from telegram.constants import ParseMode
from utils.sheets_utils import sync_balances_incremental

async def list_users_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user list from DB; incrementally sync balances from Sheets first."""
    try:
        # 1) Fetch all users from Mongo (only the needed fields)
        cursor = users_col.find(
            {}, 
            {"telegram_id": 1, "name": 1, "balance": 1, "daily_price": 1}
        )
        mongo_users = await cursor.to_list(length=None)

        # 2) Notify & perform incremental balance sync
        await update.message.reply_text("⏳ Balanslar yangilanmoqda…")
        updated_count = await sync_balances_incremental()

        # 3) If any balances changed, re-fetch those users for fresh data
        if updated_count:
            changed_users = await users_col.find(
                {"telegram_id": {"$in": [u["telegram_id"] for u in mongo_users]}},
                {"telegram_id": 1, "balance": 1}
            ).to_list(length=None)
            balance_map = {u["telegram_id"]: u["balance"] for u in changed_users}
            for u in mongo_users:
                if u["telegram_id"] in balance_map:
                    u["balance"] = balance_map[u["telegram_id"]]

        # 4) Build and send the formatted list
        if mongo_users:
            lines = [
                f"• *{u['name']}* (ID: {u['telegram_id']})\n"
                f"   💰 Balans: *{u['balance']:,}* so‘m | 📝 Narx: *{u.get('daily_price', 0):,}* so‘m"
                for u in mongo_users
            ]
            text = "\n\n".join(lines)
        else:
            text = "Hech qanday foydalanuvchi yo‘q."

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        # 5) Return to admin panel
        await update.message.reply_text("Admin panel:", reply_markup=get_admin_kb())

    except Exception as e:
        logger.error(f"Error in list_users_exec: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Xatolik yuz berdi.", reply_markup=get_admin_kb()
        )

    return ConversationHandler.END


# ─── 4) ADMIN PROMOTION / DEMOTION ─────────────────────────────────────────────

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # get the message container (either query.message or update.message)
    msg = update.callback_query.message if update.callback_query else update.message

    users = await users_col.find({"is_admin": False}).to_list(length=None)
    if not users:
        return await msg.reply_text("Barcha foydalanuvchilar allaqachon admin!", reply_markup=get_admin_kb())

    keyboard = [
        [InlineKeyboardButton(u["name"], callback_data=f"add_admin:{u['telegram_id']}")]
        for u in users
    ]
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_admin")])

    await msg.reply_text(
        "Admin qilmoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── Promote to admin ──────────────────────────────────────────────────────────
async def add_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        # delete the inline menu and show admin panel
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    # data is "add_admin:<id>"
    user_id = int(query.data.split(":", 1)[1])
    await users_col.update_one({"telegram_id": user_id}, {"$set": {"is_admin": True}})
    user = await users_col.find_one({"telegram_id": user_id})

    # update inline menu to confirm
    await query.message.edit_text(f"✅ {user['name']} admin qilindi!")
    # re‑display the promotion list
    await start_add_admin(update, context)

async def start_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of admin users to demote, with a working back button."""
    # Determine where to send replies
    msg = update.callback_query.message if update.callback_query else update.message

    # Fetch current admins
    admins = await users_col.find({"is_admin": True}).to_list(length=None)
    if not admins:
        return await msg.reply_text(
            "Adminlar mavjud emas!",
            reply_markup=get_admin_kb()
        )

    # Build inline keyboard
    keyboard = [
        [InlineKeyboardButton(a["name"], callback_data=f"remove_admin:{a['telegram_id']}")]
        for a in admins
    ]
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_admin")])

    await msg.reply_text(
        "Adminlikdan olib tashlamoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ─── Demote from admin ─────────────────────────────────────────────────────────
async def remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    user_id = int(query.data.split(":", 1)[1])
    await users_col.update_one({"telegram_id": user_id}, {"$set": {"is_admin": False}})
    user = await users_col.find_one({"telegram_id": user_id})

    await query.message.edit_text(f"✅ {user['name']} adminlikdan olib tashlandi!")
    await start_remove_admin(update, context)


# ─── 5) SET PRICE ───────────────────────────────────────────────────────────────
async def start_daily_price(update, context):
    users = await users_col.find().to_list(length=None)
    if not users:
        return await update.message.reply_text("Hech qanday foydalanuvchi yo‘q.", reply_markup=get_admin_kb())

    kb = [
      [InlineKeyboardButton(f"{u['name']} ({u.get('daily_price',0):,} so‘m)",
                            callback_data=f"set_price:{u['telegram_id']}")]
      for u in users
    ]
    kb.append([InlineKeyboardButton(BACK_BTN, callback_data="back_to_menu")])

    text = "Kunlik narxini o‘zgartirmoqchi bo‘lgan foydalanuvchini tanlang:"
    # if via reply-keyboard
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

    return S_INPUT_PRICE


async def daily_price_callback(update, context):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        await query.message.delete()
        return await query.message.reply_text("🔧 Admin panel:", reply_markup=get_admin_kb())

    # pick the user
    uid = int(query.data.split(":",1)[1])
    user = await users_col.find_one({"telegram_id": uid})
    if not user:
        return await query.message.edit_text("❌ Foydalanuvchi topilmadi.", reply_markup=get_admin_kb())

    context.user_data["pending_price_user"] = uid
    await query.message.edit_text(
      f"{user['name']} uchun yangi kunlik narxni raqam ko‘rinishida kiriting:",
      reply_markup=ReplyKeyboardRemove()
    )
    # **this return is crucial** so the conv stays in the same state
    return S_INPUT_PRICE


async def handle_daily_price(update, context):
    uid = context.user_data.get("pending_price_user")
    if uid is None:
        return ConversationHandler.END

    text = update.message.text.replace(",", "").strip()
    try:
        price = float(text)
        if price < 0:
            raise ValueError()
    except ValueError:
        return await update.message.reply_text(
            "❌ Iltimos, haqiqiy raqam kiriting!",
            reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
        )

    await users_col.update_one({"telegram_id": uid}, {"$set": {"daily_price": price}})
    u = await users_col.find_one({"telegram_id": uid})
    await update.message.reply_text(
      f"✅ {u['name']} uchun kunlik narx {price:,.0f} so‘mga o‘zgartirildi!",
      reply_markup=get_admin_kb()
    )
    context.user_data.pop("pending_price_user", None)
    return ConversationHandler.END


# ─── 6) DELETE USER ─────────────────────────────────────────────────────────────

async def start_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of users for deletion."""
    # pick the right message object
    msg = update.callback_query.message if update.callback_query else update.message

    users = await users_col.find().to_list(length=None)
    if not users:
        return await msg.reply_text("Hech qanday foydalanuvchi yo‘q.", reply_markup=get_admin_kb())

    keyboard = [
        [InlineKeyboardButton(u["name"], callback_data=f"delete_user:{u['telegram_id']}")]
        for u in users
    ]
    # use the same back callback as your other panels
    keyboard.append([InlineKeyboardButton(BACK_BTN, callback_data="back_to_admin")])

    text = "O‘chirmoqchi bo‘lgan foydalanuvchini tanlang:"
    if update.callback_query:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Delete a user ─────────────────────────────────────────────────────────────
async def delete_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        # go back
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    user_id = int(query.data.split(":", 1)[1])
    user = await users_col.find_one({"telegram_id": user_id})
    if not user:
        await query.message.edit_text("❌ Foydalanuvchi topilmadi.", reply_markup=get_menu_kb())
        return

    # clean up
    await (await get_collection("daily_food_choices")).delete_many({"telegram_id": user_id})
    await users_col.delete_one({"telegram_id": user_id})

    # confirm and then show panel
    await query.message.delete()
    await query.message.reply_text(
        f"✅ {user['name']} muvaffaqiyatli o‘chirildi!\n🔧 Admin panelga qaytdingiz.",
        reply_markup=get_admin_kb()
    )

async def show_kassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current kassa amount from Google Sheets and save to DB."""
    try:
        await update.message.reply_text("⌛️ Kassa tekshirilmoqda…")
        # 1) Fetch the worksheet
        worksheet = await get_worksheet()
        if not worksheet:
            await update.message.reply_text("❌ Google Sheets bilan bog'lanishda xatolik yuz berdi.")
            return

        # 2) Read the kassa cell (D2)
        raw = worksheet.cell(2, 4).value  # row 2, col 4
        if not raw:
            await update.message.reply_text("❌ Kassa miqdori topilmadi.")
            return

        # 3) Parse as float
        try:
            kassa_value = float(str(raw).replace(',', '').strip())
        except ValueError:
            await update.message.reply_text("❌ Kassa miqdorini o'qishda xatolik.")
            return

        # 4) Save to MongoDB (single-document "kassa" collection)
        kassa_col = await get_collection("kassa")
        await kassa_col.update_one(
            {},
            {"$set": {
                "amount": kassa_value,
                "last_updated": datetime.now(timezone.utc)
            }},
            upsert=True
        )

        # 5) Send result back to admin with the admin keyboard
        text = f"💰 *Kassa miqdori:* {kassa_value:,.0f} so‘m"
        await update.message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_admin_kb()
        )

    except Exception as e:
        logger.error(f"Error in show_kassa: {e}", exc_info=True)
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
    """Send final summary at 10:00 AM for a broadcast."""
    job = context.job
    chat_id = job.data['chat_id']

    if 'notify_responses' not in context.user_data:
        return

    resp = context.user_data['notify_responses']
    total = resp['total_sent']
    yes = len(resp['yes'])
    no  = len(resp['no'])
    pending = total - yes - no

    summary = [
        "📊 Xabar yuborish yakuniy natijalari:",
        f"👥 Jami yuborilgan: {total}",
        f"✅ Ha: {yes}",
        f"❌ Yoʻq: {no}",
        f"⏳ Javob bermaganlar: {pending}",
    ]
    if resp['failed']:
        summary.append(f"⚠️ Yuborilmadi: {len(resp['failed'])}")

    await context.bot.send_message(chat_id, "\n".join(summary))

    # Clean up
    context.user_data.pop('notify_responses', None)
    context.user_data.pop('notify_message_id', None)

# ─── MENU MANAGEMENT ───────────────────────────────────────────────────────
async def menu_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu management panel."""
    if menu_col is None:
        await init_collections()
    kb = get_menu_kb()
    text = "Menyu boshqaruvi:"
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

async def view_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """List all items in menu1 or menu2."""
    if menu_col is None:
        await init_collections()
    query = update.callback_query
    await query.answer()
    doc = await menu_col.find_one({"name": menu_name})
    items = doc.get("items", [])
    text = f"🍽 {menu_name} taomlari:\n\n" + ("\n".join(f"• {i}" for i in items) or "— Bo‘sh")
    await query.message.edit_text(text, reply_markup=get_menu_kb())

async def add_menu_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """Ask admin to type a new item for menu1 or menu2."""
    if menu_col is None:
        await init_collections()
    query = update.callback_query
    await query.answer()
    context.user_data["pending_menu_add"] = menu_name
    await query.message.edit_text(
        f"Yangi taom nomini kiriting ({menu_name}):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(BACK_BTN, callback_data="menu_back")]])
    )

async def handle_menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the text input for a new menu item."""
    menu_name = context.user_data.pop("pending_menu_add", None)
    if not menu_name:
        return  # no menu in progress
    food = update.message.text.strip()
    if not food:
        await update.message.reply_text("❌ Bo‘sh nom bo‘lmaydi.", reply_markup=get_menu_kb())
        return
    await menu_col.update_one({"name": menu_name}, {"$addToSet": {"items": food}}, upsert=True)
    await update.message.reply_text(f"✅ «{food}» {menu_name} ga qo‘shildi!", reply_markup=get_menu_kb())

async def del_menu_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """Show inline buttons to delete an existing item."""
    if menu_col is None:
        await init_collections()
    query = update.callback_query
    await query.answer()
    doc = await menu_col.find_one({"name": menu_name})
    items = doc.get("items", [])
    kb = [[InlineKeyboardButton(i, callback_data=f"del_{menu_name}:{i}")] for i in items]
    kb.append([InlineKeyboardButton(BACK_BTN, callback_data="menu_back")])
    await query.message.edit_text(f"{menu_name} dan o‘chirish:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_menu_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform the deletion of a menu item."""
    query = update.callback_query
    await query.answer()
    data = query.data  # e.g. "del_menu1:Qovurma Lag'mon"
    _, rest = data.split("_", 1)
    menu_name, food = rest.split(":", 1)
    await menu_col.update_one({"name": menu_name}, {"$pull": {"items": food}})
    await query.message.edit_text(f"✅ «{food}» {menu_name} dan o‘chirildi.", reply_markup=get_menu_kb())

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch menu panel callbacks to the correct helper."""
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
        await add_menu_prompt(update, context, "menu1")
    elif data == "add_menu2":
        await add_menu_prompt(update, context, "menu2")
    elif data == "del_menu1":
        await del_menu_prompt(update, context, "menu1")
    elif data == "del_menu2":
        await del_menu_prompt(update, context, "menu2")
    elif data == "menu_back":
        # go back to admin panel or remove menu message
        try:
            await query.message.delete()
        except BadRequest:
            await update.callback_query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
    else:
        # catch delete callbacks
        if data.startswith("del_menu1:") or data.startswith("del_menu2:"):
            await handle_menu_del(update, context)
        else:
            await query.message.edit_text("❌ Noma’lum buyruq.", reply_markup=get_menu_kb())

async def send_final_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send final summary of broadcast at 10:00 AM."""
    job = context.job
    chat_id = job.data.get("chat_id")
    if not chat_id or "notify_responses" not in context.user_data:
        return

    resp = context.user_data["notify_responses"]
    total   = resp.get("total_sent", 0)
    yes     = len(resp.get("yes", []))
    no      = len(resp.get("no", []))
    pending = total - yes - no

    lines = [
        "📊 Xabar yuborish yakuniy natijalari:",
        f"👥 Jami yuborilgan: {total}",
        f"✅ Ha: {yes}",
        f"❌ Yoʻq: {no}",
        f"⏳ Javob bermaganlar: {pending}",
    ]
    if resp.get("failed"):
        lines.append(f"⚠️ Yuborilmadi: {len(resp['failed'])}")

    await context.bot.send_message(chat_id, "\n".join(lines))

    # clean up
    context.user_data.pop("notify_responses", None)
    context.user_data.pop("notify_message_id", None)


# ─── 9b) Daily lunch summary & deduction ────────────────────────────────────
async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    """
    Send daily attendance summary to all admins and users, then deduct balances.
    Scheduled at 10:00 Asia/Tashkent.
    """
    tz    = pytz.timezone("Asia/Tashkent")
    now   = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    # skip weekends
    if now.weekday() >= 5:
        return

    users = await get_all_users_async()
    attendees, attendee_details, declined, pending = [], [], [], []

    # categorize
    for u in users:
        if today in u.attendance:
            attendees.append(u)
            choice = await u.get_food_choice(today)
            attendee_details.append((u.name, choice))
        elif today in u.declined_days:
            declined.append(u.name)
        else:
            pending.append(u.name)

    # aggregate counts
    counts = await User.get_daily_food_counts(today)
    most   = []
    if counts:
        max_count = max(d["count"] for d in counts.values())
        tied = [f for f, d in counts.items() if d["count"] == max_count]
        most = sorted(tied) if len(tied) > 1 else [tied[0]]

    # build admin summary
    admin_lines = [
        "📊 *Bugungi tushlik uchun yig‘ilish:*",
        f"👥 Jami: *{len(attendees)}* kishi",
        "",
        "📝 *Ro‘yxat:*"
    ]
    if attendee_details:
        admin_lines += [f"{i+1}. {n} — {f or 'Tanlanmagan'}"
                        for i, (n, f) in enumerate(attendee_details)]
    else:
        admin_lines.append("Hech kim yo‘q")

    admin_lines.append("\n🍽 *Taomlar statistikasi:*")
    if counts:
        admin_lines += [f"{i+1}. {food} — {data['count']} ta"
                        for i, (food, data) in enumerate(counts.items())]
    else:
        admin_lines.append("— Hech qanday taom tanlanmadi")

    if declined:
        admin_lines += ["\n❌ *Rad etganlar:*"] + [
            f"{i+1}. {n}" for i, n in enumerate(declined)
        ]
    if pending:
        admin_lines += ["\n⏳ *Javob bermaganlar:*"] + [
            f"{i+1}. {n}" for i, n in enumerate(pending)
        ]

    admin_text = "\n".join(admin_lines)

    # send to each admin
    for u in users:
        if u.is_admin:
            try:
                await context.bot.send_message(u.telegram_id, admin_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed sending summary to admin {u.telegram_id}: {e}")

    # notify each attendee
    for u in attendees:
        try:
            if most:
                if len(most) > 1:
                    foods = " va ".join(most)
                    text = (
                        "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                        f"🥇 Bugun tanlangan taomlar: 🍛 {foods}\n"
                        f"💰 Balansingiz: {u.balance:,.0f} so‘m"
                    )
                else:
                    text = (
                        "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                        f"🥇 Bugun tanlangan taom: 🍛 {most[0]}\n"
                        f"💰 Balansingiz: {u.balance:,.0f} so‘m"
                    )
            else:
                text = (
                    "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                    "🥄 Bugun asosiy taom aniqlanmadi.\n"
                    f"💰 Balansingiz: {u.balance:,.0f} so‘m"
                )
            await context.bot.send_message(u.telegram_id, text, reply_markup=get_default_kb(u.is_admin))
        except Exception as e:
            logger.error(f"Failed user recap for {u.telegram_id}: {e}")

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
    """Handle the new card owner name input and save both to database."""
    # If they tapped “Ortga”, send them back into the admin panel
    if update.message.text == BACK_BTN:
        return await admin_panel(update, context)

    # Otherwise save the new details
    card_details_col = await get_collection("card_details")

    await card_details_col.update_one(
        {},  # match the single doc
        {
            "$set": {
                "card_number": context.user_data['new_card_number'],
                "card_owner": update.message.text
            }
        },
        upsert=True
    )

    # Clear temp storage
    context.user_data.pop('new_card_number', None)

    # Confirm and show the admin panel again
    await update.message.reply_text(
        "✅ Karta ma'lumotlari muvaffaqiyatli o'zgartirildi!",
        reply_markup=get_admin_kb()
    )
    return ConversationHandler.END


# ─── 9) NOTIFY ALL ─────────────────────────────────────────────────────────────
async def notify_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("notify_all called")
    caller = await get_user_async(update.effective_user.id)
    if not (caller and caller.is_admin):
        return await update.message.reply_text("❌ Siz admin emassiz.")
    await update.message.reply_text(
        "⚠️ Yuboriladigan xabarni kiriting yoki Ortga bosing:",
        reply_markup=ReplyKeyboardMarkup([[BACK_BTN]], resize_keyboard=True)
    )
    return S_NOTIFY_MESSAGE

async def handle_notify_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == BACK_BTN:
        await update.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return ConversationHandler.END

    context.user_data['notify_message'] = text
    confirm_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, yubor", callback_data="notify_confirm")],
        [InlineKeyboardButton("❌ Bekor qil", callback_data="notify_cancel")],
    ])
    await update.message.reply_text(
        f"⚠️ Quyidagichani yuborishga rozimisiz?\n\n{text}",
        reply_markup=confirm_kb
    )
    return S_NOTIFY_CONFIRM

async def notify_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("callback data:", update.callback_query.data)
    query = update.callback_query
    await query.answer()
    if query.data == "notify_cancel":
        await query.message.edit_text("❌ Xabar yuborish bekor qilindi.")
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return ConversationHandler.END

    # confirmed path
    message = context.user_data['notify_message']
    users = await get_all_users_async()
    await query.message.edit_text("⏳ Xabar yuborilmoqda…")
    sent = failed = 0
    for u in users:
        if not u.is_admin:
            try:
                await context.bot.send_message(u.telegram_id, message)
                sent += 1
            except:
                failed += 1

    summary = f"✅ {sent}/{len([u for u in users if not u.is_admin])} foydalanuvchiga yuborildi."
    if failed:
        summary += f"\n⚠️ {failed} kishi ololmadi."
    await query.message.edit_text(summary)
    await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
    return ConversationHandler.END


# ─── CONVERSATION HANDLERS ──────────────────────────────────────────────────────
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing conversation and return to admin panel."""
    if update.message:
        await update.message.reply_text(
            "❌ Operatsiya bekor qilindi.",
            reply_markup=get_admin_kb()
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text("❌ Operatsiya bekor qilindi.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔧 Admin panelga qaytdingiz:",
            reply_markup=get_admin_kb()
        )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_lunch_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the lunch cancellation process"""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Bu buyruq faqat adminlar uchun.")
        return

    await update.message.reply_text(
        "Qaysi kun uchun tushlikni bekor qilmoqchisiz? (YYYY-MM-DD formatida)\n"
        "Bugungi kun uchun bo'lsa, 'bugun' deb yozing."
    )
    return S_CANCEL_DATE

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
            return S_CANCEL_DATE

    context.user_data['cancel_date'] = date_str
    await update.message.reply_text("Bekor qilish sababini kiriting:")
    return S_CANCEL_REASON

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

async def test_debts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.user_handlers import check_debts
    await update.message.reply_text("🚀 Testing debt check…")
    await check_debts(context)
    await update.message.reply_text("✅ Done.")

async def run_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Siz admin emassiz!")
    await update.message.reply_text("⏳ Today’s summary being sent…")
    # reuse send_summary logic
    await send_summary(context)
    return ConversationHandler.END

def register_handlers(app):
    # ─── INITIALIZATION ────────────────────────────────────────────────
    app.job_queue.run_once(lambda _: init_collections(), when=0)

    # ─── 1) CORE COMMANDS ──────────────────────────────────────────────
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("run_summary", run_summary_command))
    app.add_handler(CommandHandler("test_debts", test_debts_command))

    # ─── 2) NOTIFY CONVERSATION (/notify_all) ──────────────────────────
    notify_conv = ConversationHandler(
        entry_points=[
            CommandHandler("notify_all", notify_all),
            MessageHandler(filters.Regex(fr"^{re.escape(NOTIFY_BTN)}$"), notify_all),
        ],
        states={
            S_NOTIFY_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_notify_message)
            ],
            S_NOTIFY_CONFIRM: [
                CallbackQueryHandler(notify_confirm_callback, pattern=r"^notify_(confirm|cancel)$")
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), cancel_conversation),
            CommandHandler("cancel", cancel_conversation),
        ],
        allow_reentry=True,
        per_message=True,
        name="notify_conversation",
    )
    app.add_handler(notify_conv)

    # ─── 3) ADMIN SHORTCUTS (Reply‑Keyboard Buttons) ──────────────────
    single_buttons = [
        (FOYD_BTN,         list_users_exec),
        (ADD_ADMIN_BTN,    start_add_admin),
        (REMOVE_ADMIN_BTN, start_remove_admin),
        (DAILY_PRICE_BTN,  start_daily_price),
        (DELETE_USER_BTN,  start_delete_user),
        (CXL_LUNCH_BTN,    cancel_lunch_day),
        (CARD_BTN,         start_card_management),
        (KASSA_BTN,        show_kassa),
        (MENU_BTN,         menu_panel),
        (BACK_BTN,         back_to_menu),  # Ortga always goes to menu
    ]
    for text, handler in single_buttons:
        app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(text)}$"), handler))

    # ─── 4) ORTGA SHORTCUT (Reply & Inline) ────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^back_to_admin$"))

    # ─── 5) CANCEL LUNCH CONVERSATION ──────────────────────────────────
    cancel_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CXL_LUNCH_BTN)}$"), cancel_lunch_day)],
        states={
            S_CANCEL_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_date)],
            S_CANCEL_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_cancel_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    app.add_handler(cancel_conv)

    # ─── 6) CARD MANAGEMENT CONVERSATION ───────────────────────────────
    card_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(CARD_BTN)}$"), start_card_management)],
        states={
            S_CARD_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_card_number)
            ],
            S_CARD_OWNER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(BACK_BTN)}$"), handle_card_owner)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), cancel_conversation),
            CommandHandler("cancel", cancel_conversation),
        ],
        allow_reentry=True,
        per_message=True,
        name="card_management_conversation"
    )
    app.add_handler(card_conv)

    # ─── 7) INLINE CALLBACKS FOR USER MGMT ─────────────────────────────
    app.add_handler(CallbackQueryHandler(add_admin_callback,    pattern=r"^add_admin:\d+$"))
    app.add_handler(CallbackQueryHandler(remove_admin_callback, pattern=r"^remove_admin:\d+$"))
    app.add_handler(CallbackQueryHandler(delete_user_callback,  pattern=r"^delete_user:\d+$"))

    # ─── 8) MENU INLINE FLOW & TEXT HANDLER ────────────────────────────
    menu_pattern = r"^(view_menu1|view_menu2|add_menu1|add_menu2|del_menu1|del_menu2|menu_back)$"
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=menu_pattern))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_add))

    # ─── 9) PRICE SETTING INLINE FLOW ──────────────────────────────────
    price_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{re.escape(DAILY_PRICE_BTN)}$"), start_daily_price)
        ],
        states={
            S_INPUT_PRICE: [
                CallbackQueryHandler(daily_price_callback, pattern=r"^set_price:\d+$"),
            ],
            S_INPUT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_daily_price),
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), cancel_conversation),
            CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"),
        ],
        per_message=True,
        allow_reentry=True,
        name="price_conversation",
    )
    app.add_handler(price_conv)

    # ─── 10) NOTIFY RESPONSE INLINE (Optional) ─────────────────────────
    app.add_handler(CallbackQueryHandler(notify_response_callback, pattern=r"^notify_response:(yes|no):\d+$"))

    logging.info("✅ All admin handlers registered.") 