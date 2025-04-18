from pymongo import MongoClient
from datetime import datetime
import os
from dotenv import load_dotenv
import logging
import json

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        # Get MongoDB connection string from environment variable
        self.connection_string = os.getenv("MONGODB_URI")
        if not self.connection_string:
            raise ValueError("MONGODB_URI environment variable is not set")
        
        # Initialize MongoDB client
        self.client = MongoClient(self.connection_string)
        self.db = self.client.lunch_bot
        
        # Initialize collections
        self.users = self.db.users
        self.daily_attendance = self.db.daily_attendance
        self.attendance_history = self.db.attendance_history
        self.kassa = self.db.kassa
        self.admins = self.db.admins
        
        # Create indexes
        self._create_indexes()
        
    def _create_indexes(self):
        """Create necessary indexes for better performance"""
        try:
            # Index for users collection
            self.users.create_index("user_id", unique=True)
            self.users.create_index("phone", unique=True, sparse=True)
            
            # Index for daily attendance collection
            self.daily_attendance.create_index("date", unique=True)
            
            # Index for attendance history collection
            self.attendance_history.create_index("date", unique=True)
            
            # Index for kassa collection
            self.kassa.create_index("date")
            
            # Index for admins collection
            self.admins.create_index("user_id", unique=True)
            
            logger.info("Database indexes created successfully")
        except Exception as e:
            logger.error(f"Error creating database indexes: {str(e)}")
            raise
    
    # User operations with improved error handling
    def get_user(self, user_id: str):
        """Get user by ID"""
        try:
            return self.users.find_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {str(e)}")
            return None
    
    def update_user(self, user_id: str, update_data: dict):
        """Update user data with validation"""
        try:
            # Ensure required fields are present
            update_data.setdefault("balance", 0)
            update_data.setdefault("daily_price", 25000)
            update_data.setdefault("last_notification_date", "")
            
            return self.users.update_one(
                {"user_id": user_id},
                {"$set": update_data},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error updating user {user_id}: {str(e)}")
            return None
    
    def get_all_users(self):
        """Get all users with error handling"""
        try:
            return list(self.users.find())
        except Exception as e:
            logger.error(f"Error getting all users: {str(e)}")
            return []
    
    # Daily attendance operations with improved error handling
    def get_daily_attendance(self, date: str):
        """Get daily attendance for a specific date"""
        try:
            return self.daily_attendance.find_one({"date": date})
        except Exception as e:
            logger.error(f"Error getting daily attendance for {date}: {str(e)}")
            return None
    
    def update_daily_attendance(self, date: str, update_data: dict):
        """Update daily attendance with validation"""
        try:
            # Ensure required fields are present
            update_data.setdefault("confirmed", [])
            update_data.setdefault("declined", [])
            update_data.setdefault("pending", [])
            update_data.setdefault("menu", {})
            
            return self.daily_attendance.update_one(
                {"date": date},
                {"$set": update_data},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error updating daily attendance for {date}: {str(e)}")
            return None
    
    # Attendance history operations with improved error handling
    def get_attendance_history(self, date: str):
        """Get attendance history for a specific date"""
        try:
            return self.attendance_history.find_one({"date": date})
        except Exception as e:
            logger.error(f"Error getting attendance history for {date}: {str(e)}")
            return None
    
    def update_attendance_history(self, date: str, update_data: dict):
        """Update attendance history with validation"""
        try:
            # Ensure required fields are present
            update_data.setdefault("confirmed", [])
            update_data.setdefault("declined", [])
            
            return self.attendance_history.update_one(
                {"date": date},
                {"$set": update_data},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error updating attendance history for {date}: {str(e)}")
            return None
    
    # Kassa operations with improved error handling
    def add_kassa_record(self, date: str, amount: float, description: str):
        """Add a new kassa record with validation"""
        try:
            if not isinstance(amount, (int, float)):
                raise ValueError("Amount must be a number")
                
            return self.kassa.insert_one({
                "date": date,
                "amount": float(amount),
                "description": description,
                "created_at": datetime.now()
            })
        except Exception as e:
            logger.error(f"Error adding kassa record: {str(e)}")
            return None
    
    def get_kassa_records(self, start_date: str = None, end_date: str = None):
        """Get kassa records with optional date range"""
        try:
            query = {}
            if start_date and end_date:
                query["date"] = {"$gte": start_date, "$lte": end_date}
            return list(self.kassa.find(query).sort("date", -1))
        except Exception as e:
            logger.error(f"Error getting kassa records: {str(e)}")
            return []
    
    # Admin operations with improved error handling
    def is_admin(self, user_id: str):
        """Check if user is admin"""
        try:
            return self.admins.find_one({"user_id": user_id}) is not None
        except Exception as e:
            logger.error(f"Error checking admin status for {user_id}: {str(e)}")
            return False
    
    def get_all_admins(self):
        """Get all admin users"""
        try:
            return list(self.admins.find())
        except Exception as e:
            logger.error(f"Error getting all admins: {str(e)}")
            return []
    
    def add_admin(self, user_id: str):
        """Add a new admin"""
        try:
            return self.admins.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Error adding admin {user_id}: {str(e)}")
            return None
    
    def remove_admin(self, user_id: str):
        """Remove an admin"""
        try:
            return self.admins.delete_one({"user_id": user_id})
        except Exception as e:
            logger.error(f"Error removing admin {user_id}: {str(e)}")
            return None
    
    # Backup and restore operations
    def create_backup(self):
        """Create a backup of all collections"""
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = "backups"
            os.makedirs(backup_dir, exist_ok=True)
            
            # Backup users
            users = list(self.users.find())
            with open(os.path.join(backup_dir, f"users_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(users, f, ensure_ascii=False, indent=4)
            
            # Backup daily attendance
            daily_attendance = list(self.daily_attendance.find())
            with open(os.path.join(backup_dir, f"daily_attendance_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(daily_attendance, f, ensure_ascii=False, indent=4)
            
            # Backup attendance history
            attendance_history = list(self.attendance_history.find())
            with open(os.path.join(backup_dir, f"attendance_history_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(attendance_history, f, ensure_ascii=False, indent=4)
            
            # Backup kassa
            kassa = list(self.kassa.find())
            with open(os.path.join(backup_dir, f"kassa_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(kassa, f, ensure_ascii=False, indent=4)
            
            # Backup admins
            admins = list(self.admins.find())
            with open(os.path.join(backup_dir, f"admins_{timestamp}.json"), "w", encoding="utf-8") as f:
                json.dump(admins, f, ensure_ascii=False, indent=4)
            
            logger.info(f"Backup created successfully at {timestamp}")
            return True
        except Exception as e:
            logger.error(f"Error creating backup: {str(e)}")
            return False
    
    def restore_from_backup(self, backup_dir: str, timestamp: str):
        """Restore data from backup"""
        try:
            # Restore users
            with open(os.path.join(backup_dir, f"users_{timestamp}.json"), "r", encoding="utf-8") as f:
                users = json.load(f)
                for user in users:
                    self.users.update_one(
                        {"user_id": user["user_id"]},
                        {"$set": user},
                        upsert=True
                    )
            
            # Restore daily attendance
            with open(os.path.join(backup_dir, f"daily_attendance_{timestamp}.json"), "r", encoding="utf-8") as f:
                daily_attendance = json.load(f)
                for record in daily_attendance:
                    self.daily_attendance.update_one(
                        {"date": record["date"]},
                        {"$set": record},
                        upsert=True
                    )
            
            # Restore attendance history
            with open(os.path.join(backup_dir, f"attendance_history_{timestamp}.json"), "r", encoding="utf-8") as f:
                attendance_history = json.load(f)
                for record in attendance_history:
                    self.attendance_history.update_one(
                        {"date": record["date"]},
                        {"$set": record},
                        upsert=True
                    )
            
            # Restore kassa
            with open(os.path.join(backup_dir, f"kassa_{timestamp}.json"), "r", encoding="utf-8") as f:
                kassa = json.load(f)
                for record in kassa:
                    self.kassa.insert_one(record)
            
            # Restore admins
            with open(os.path.join(backup_dir, f"admins_{timestamp}.json"), "r", encoding="utf-8") as f:
                admins = json.load(f)
                for admin in admins:
                    self.admins.update_one(
                        {"user_id": admin["user_id"]},
                        {"$set": admin},
                        upsert=True
                    )
            
            logger.info(f"Restored from backup {timestamp} successfully")
            return True
        except Exception as e:
            logger.error(f"Error restoring from backup: {str(e)}")
            return False

# Create a global database instance
db = Database() 