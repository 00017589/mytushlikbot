# main.py

import logging
import os
import datetime
import pytz
import asyncio
import sys

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from database import init_db
from config import BOT_TOKEN
from models.user_model import User

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout  # Ensure logs go to stdout for Railway
)
logger = logging.getLogger(__name__)

async def cleanup_old_data(context):
    """Cleanup job that runs at midnight"""
    logger.info("Running midnight cleanup job...")
    # Only clean up real data - test data is cleaned up immediately after test summary
    await User.cleanup_old_food_choices(is_test=False)
    logger.info("Midnight cleanup completed")

def main():
    """Main entrypoint: initialize DB, build app, register handlers, schedule jobs, and start polling."""
    # 1) Load .env
    load_dotenv()

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

        # 7) Start polling (this is blocking and manages its own loop)
        logger.info("Starting bot...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Error in main: {e}", exc_info=True)
        raise
    finally:
        # Clean up the loop only after everything is done
        loop.close()

if __name__ == "__main__":
    main()
