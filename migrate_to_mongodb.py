import json
import os
from db import db
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_json_file(filename):
    """Load data from JSON file"""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Error loading {filename}: {str(e)}")
        return {}

def migrate_data():
    """Migrate data from JSON files to MongoDB"""
    try:
        # Load data from JSON files
        data = load_json_file('data.json')
        admins = load_json_file('admins.json')
        
        # Migrate users
        users = data.get('users', {})
        for user_id, user_data in users.items():
            db.migrate_user(user_id, user_data)
        logger.info(f"Migrated {len(users)} users")
        
        # Migrate daily attendance
        daily_attendance = data.get('daily_attendance', {})
        for date, attendance_data in daily_attendance.items():
            db.migrate_daily_attendance(date, attendance_data)
        logger.info(f"Migrated {len(daily_attendance)} daily attendance records")
        
        # Migrate attendance history
        attendance_history = data.get('attendance_history', {})
        for date, history_data in attendance_history.items():
            db.migrate_attendance_history(date, history_data)
        logger.info(f"Migrated {len(attendance_history)} attendance history records")
        
        # Migrate kassa records - handle integer values
        kassa = data.get('kassa', {})
        if isinstance(kassa, dict):
            for date, amount in kassa.items():
                if isinstance(amount, (int, float)):
                    db.add_kassa_record(date, float(amount), "Migrated from JSON")
        logger.info(f"Migrated kassa records")
        
        # Migrate admins
        admin_list = admins.get('admins', [])
        for admin_id in admin_list:
            db.add_admin(admin_id)
        logger.info(f"Migrated {len(admin_list)} admins")
        
        logger.info("Migration completed successfully")
        
    except Exception as e:
        logger.error(f"Error during migration: {str(e)}")
        # Print more detailed error information
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    migrate_data() 