from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters
import re
from database import users_col, kassa_col

# Button texts
ADJ_BAL_BTN = "Balansni o'zgartirish"
KASSA_BTN = "Kassa"
BACK_BTN = "Ortga"

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

def register_handlers(app):
    # Balance adjustment handlers
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(ADJ_BAL_BTN)}$"), start_adjust_balance))
    app.add_handler(CallbackQueryHandler(adjust_balance_callback, pattern=r"^(adj_user:\d+|add_bal:\d+|sub_bal:\d+|back_to_menu)$"))
    
    # Kassa handlers
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(KASSA_BTN)}$"), start_kassa_panel))
    app.add_handler(CallbackQueryHandler(kassa_callback, pattern=r"^(kassa_add|kassa_sub|kassa_back|back_to_menu)$"))
    
    # Amount handlers
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(ADJ_BAL_BTN)}$") & ~filters.Regex(f"^{re.escape(KASSA_BTN)}$"),
        handle_amount
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(f"^{re.escape(ADJ_BAL_BTN)}$") & ~filters.Regex(f"^{re.escape(KASSA_BTN)}$"),
        handle_kassa_amount
    ), group=1) 