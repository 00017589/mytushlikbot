#!/usr/bin/env python3
import os
import sys
import tempfile
import atexit
import asyncio
import logging
from datetime import time as dt_time

import pytz
from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from database import init_db
from config import BOT_TOKEN, MONGODB_URI
from models.user_model import User
import handlers.admin_handlers as ah
import handlers.user_handlers as uh

# ─── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── Single‐instance lock ───────────────────────────────────────────────────
_lock_file = os.path.join(tempfile.gettempdir(), "lunch_bot.lock")
_lock_fd = None

def _cleanup_lock():
    global _lock_fd
    if _lock_fd:
        try:
            # on Unix
            import fcntl
            fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        except:
            pass
        _lock_fd.close()
        try:
            os.remove(_lock_file)
        except:
            pass

def _acquire_lock():
    global _lock_fd
    _lock_fd = open(_lock_file, "w")
    try:
        # on Unix
        import fcntl
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        logger.error("Another instance is already running.")
        sys.exit(1)
    atexit.register(_cleanup_lock)

# ─── Error handler ─────────────────────────────────────────────────────────
async def error_handler(update, context):
    logger.error(f"Update {update!r} caused error {context.error!r}", exc_info=True)
    ADMIN_ID = 5192568051  # replace with your admin Telegram ID
    tb = getattr(context.error, "__traceback__", None)
    text = f"❌ Error:\n{context.error}\n"
    try:
        await context.bot.send_message(chat_id=ADMIN_ID, text=text)
    except Exception as e:
        logger.error(f"Failed to send error to admin: {e}", exc_info=True)

# ─── Async main ─────────────────────────────────────────────────────────────
async def main():
    # 1) Single‐instance guard
    _acquire_lock()

    # 2) Load .env and validate
    load_dotenv(override=True)
    token = os.getenv("BOT_TOKEN", BOT_TOKEN)
    mongo_uri = os.getenv("MONGODB_URI", MONGODB_URI)
    if not token or not mongo_uri:
        logger.error("Missing BOT_TOKEN or MONGODB_URI; aborting.")
        sys.exit(1)

    # 3) Init database
    await init_db()
    logger.info("Database initialized.")

    # 4) Build the application
    app = ApplicationBuilder().token(token).build()
    app.add_error_handler(error_handler)

    # 5) Ensure webhook is cleared before polling
    try:
        await app.bot.delete_webhook(drop_pending_updates=True)
        logger.info("Existing webhook deleted.")
    except Exception as e:
        logger.warning(f"Could not delete webhook: {e}")

    # 6) Register handlers in correct order
    ah.register_handlers(app)  # admin first
    uh.register_handlers(app)  # then user flows

    # 7) Schedule recurring jobs in Asia/Tashkent
    tz = pytz.timezone("Asia/Tashkent")
    jq = app.job_queue
    jq.run_daily(
        uh.morning_prompt,
        time=dt_time(7, 0, tzinfo=tz),
        days=(1, 2, 3, 4, 5),
        name="morning_prompt"
    )
    jq.run_daily(
        ah.send_summary,
        time=dt_time(10, 0, tzinfo=tz),
        days=(1, 2, 3, 4, 5),
        name="daily_summary"
    )
    jq.run_daily(
        uh.check_debts,
        time=dt_time(12, 0, tzinfo=tz),
        days=(1, 3, 5),
        name="debt_check"
    )
    jq.run_daily(
        lambda ctx: User.cleanup_old_food_choices(),
        time=dt_time(0, 0, tzinfo=tz),
        name="midnight_cleanup"
    )

    # 8) Start polling
    logger.info("Starting polling…")
    await app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"]
    )

if __name__ == "__main__":
    asyncio.run(main())
