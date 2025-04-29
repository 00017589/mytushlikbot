# main.py

import logging
import os
import datetime
import pytz
import asyncio
import socket
import sys

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from database import init_db
from config import BOT_TOKEN
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
    """Ensure only one instance of the bot is running"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Try to bind to port 6789 (or any unused port)
        sock.bind(('localhost', 6789))
    except socket.error:
        logger.error("Bot is already running! Exiting.")
        sys.exit(1)
    return sock

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
    if not os.getenv("MONGODB_URI"):
        logger.error("MONGODB_URI is not set!")
        exit(1)

    # 2) Create and set event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # 3) Initialize database
        loop.run_until_complete(init_db())

        # 4) Build the Telegram Application
        application = (
            ApplicationBuilder()
            .token(os.getenv("BOT_TOKEN", BOT_TOKEN))
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
            callback=uh.send_summary,
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
        application.run_polling(allowed_updates=["message", "callback_query"])
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
    finally:
        # Clean up the socket
        instance_lock.close()


if __name__ == "__main__":
    main()
