#!/usr/bin/env python3
import logging
import os
import datetime
import pytz
import asyncio
import sys
import atexit
import tempfile

if not os.path.exists("credentials.json"):
    creds = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds:
        with open("credentials.json", "w") as f:
            f.write(creds)
# Cross-platform imports for file locking
if os.name == 'nt':
    import msvcrt
else:
    import fcntl

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder
from telegram.error import TimedOut, NetworkError

from database import init_db
from config import BOT_TOKEN, MONGODB_URI
from models.user_model import User

# Configure logging for Railway
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def error_handler(update, context):
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}")
    
    # Get the error's traceback
    import traceback
    traceback.print_exc()
    
    # Send error messages to admin
    ADMIN_ID = 5192568051
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, 
            text=f"❌ Xatolik yuz berdi:\n\nXato: {context.error}\n\nTraceback: {traceback.format_exc()[:1000]}"
        )
    except Exception as e:
        logger.error(f"Failed to send error notification to admin: {e}")

def check_single_instance():
    """Ensure only one instance of the bot is running (cross-platform)"""
    lock_file = os.path.join(tempfile.gettempdir(), 'lunch_bot.lock')
    lock_fd = open(lock_file, 'w')
    try:
        if os.name == 'nt':
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atexit.register(lambda: cleanup_lock(lock_fd, lock_file))
        return lock_fd
    except Exception:
        lock_fd.close()
        logger.error("Bot is already running! Exiting.")
        sys.exit(1)

def cleanup_lock(lock_fd, lock_file):
    try:
        if os.name == 'nt':
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception:
        pass

async def cleanup_old_data(context):
    """Cleanup job that runs at midnight"""
    logger.info("Running midnight cleanup job...")
    # Only clean up real data - test data is cleaned up immediately after test summary
    await User.cleanup_old_food_choices(is_test=False)
    logger.info("Midnight cleanup completed")

def main():
    """Main entrypoint: initialize DB, build app, register handlers, schedule jobs, and start polling."""
    # 0) Check single instance
    instance_lock = check_single_instance()
    
    # 1) Load .env
    load_dotenv()

    if not os.getenv("BOT_TOKEN", BOT_TOKEN):
        logger.error("BOT_TOKEN is not set!")
        exit(1)
    if not os.getenv("MONGODB_URI", MONGODB_URI):
        logger.error("MONGODB_URI is not set!")
        exit(1)

    # 2) Create and set event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 3) Initialize database
        loop.run_until_complete(init_db())
        logger.info("Database initialized successfully")

        # 4) Build the Telegram Application
        application = (
            ApplicationBuilder()
            .token(os.getenv("BOT_TOKEN", BOT_TOKEN))
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .get_updates_read_timeout(30.0)
            .build()
        )

        # Add error handler
        application.add_error_handler(error_handler)

        # 5) Import and register handlers now that app exists
        import handlers.user_handlers as uh
        import handlers.admin_handlers as ah
        import handlers.balance_handlers as bh

        uh.register_handlers(application)
        ah.register_handlers(application)
        bh.register_handlers(application)

        # 6) Schedule daily jobs
        jq = application.job_queue
        tz = pytz.timezone("Asia/Tashkent")

        # Morning survey at 7:00 Mon–Fri
        jq.run_daily(
            callback=uh.morning_prompt,
            time=datetime.time(hour=7, minute=0, tzinfo=tz),
            days=(0, 1, 2, 3, 4),
            name="morning_survey"
        )

        # Attendance summary at 10:00 Mon–Fri
        jq.run_daily(
            callback=ah.send_summary,
            time=datetime.time(hour=10, minute=0, tzinfo=tz),
            days=(0, 1, 2, 3, 4),
            name="daily_summary"
        )

        # Add midnight cleanup job
        jq.run_daily(
            callback=cleanup_old_data,
            time=datetime.time(hour=0, minute=0, tzinfo=tz),
            name="midnight_cleanup"
        )

        logger.info("Bot is starting...")
        # 7) Start polling (this is blocking and manages its own loop)
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        # Clean up the lock file
        cleanup_lock(instance_lock, os.path.join(tempfile.gettempdir(), 'lunch_bot.lock'))

if __name__ == "__main__":
    main()
