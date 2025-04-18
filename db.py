from pymongo import MongoClient
from datetime import datetime
import os
from dotenv import load_dotenv
import logging

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
            
            # Index for daily attendance collection
            self.daily_attendance.create_index("date", unique=True)
            
            # Index for attendance history collection
            self.attendance_history.create_index("date", unique=True)
            
            # Index for kassa collection
            self.kassa.create_index("date")
            
            logger.info("Database indexes created successfully")
        except Exception as e:
            logger.error(f"Error creating database indexes: {str(e)}")
    
    # User operations
    def get_user(self, user_id: str):
        """Get user by ID"""
        return self.users.find_one({"user_id": user_id})
    
    def update_user(self, user_id: str, update_data: dict):
        """Update user data"""
        return self.users.update_one(
            {"user_id": user_id},
            {"$set": update_data},
            upsert=True
        )
    
    def get_all_users(self):
        """Get all users"""
        return list(self.users.find())
    
    # Daily attendance operations
    def get_daily_attendance(self, date: str):
        """Get daily attendance for a specific date"""
        return self.daily_attendance.find_one({"date": date})
    
    def update_daily_attendance(self, date: str, update_data: dict):
        """Update daily attendance"""
        return self.daily_attendance.update_one(
            {"date": date},
            {"$set": update_data},
            upsert=True
        )
    
    # Attendance history operations
    def get_attendance_history(self, date: str):
        """Get attendance history for a specific date"""
        return self.attendance_history.find_one({"date": date})
    
    def update_attendance_history(self, date: str, update_data: dict):
        """Update attendance history"""
        return self.attendance_history.update_one(
            {"date": date},
            {"$set": update_data},
            upsert=True
        )
    
    # Kassa operations
    def add_kassa_record(self, date: str, amount: float, description: str):
        """Add a new kassa record"""
        return self.kassa.insert_one({
            "date": date,
            "amount": amount,
            "description": description,
            "created_at": datetime.now()
        })
    
    def get_kassa_records(self, start_date: str = None, end_date: str = None):
        """Get kassa records with optional date range"""
        query = {}
        if start_date and end_date:
            query["date"] = {"$gte": start_date, "$lte": end_date}
        return list(self.kassa.find(query).sort("date", -1))
    
    # Admin operations
    def is_admin(self, user_id: str):
        """Check if user is admin"""
        return self.admins.find_one({"user_id": user_id}) is not None
    
    def get_all_admins(self):
        """Get all admin users"""
        return list(self.admins.find())
    
    def add_admin(self, user_id: str):
        """Add a new admin"""
        return self.admins.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id}},
            upsert=True
        )
    
    def remove_admin(self, user_id: str):
        """Remove an admin"""
        return self.admins.delete_one({"user_id": user_id})
    
    # Data migration helpers
    def migrate_user(self, user_id: str, user_data: dict):
        """Migrate a single user to MongoDB format"""
        return self.users.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "name": user_data.get("name", ""),
                "balance": user_data.get("balance", 0),
                "daily_price": user_data.get("daily_price", 25000),
                "last_notification_date": user_data.get("last_notification_date", ""),
                "last_attendance_date": user_data.get("last_attendance_date", "")
            }},
            upsert=True
        )
    
    def migrate_daily_attendance(self, date: str, attendance_data: dict):
        """Migrate daily attendance to MongoDB format"""
        return self.daily_attendance.update_one(
            {"date": date},
            {"$set": {
                "date": date,
                "confirmed": attendance_data.get("confirmed", []),
                "declined": attendance_data.get("declined", []),
                "pending": attendance_data.get("pending", [])
            }},
            upsert=True
        )
    
    def migrate_attendance_history(self, date: str, history_data: dict):
        """Migrate attendance history to MongoDB format"""
        return self.attendance_history.update_one(
            {"date": date},
            {"$set": {
                "date": date,
                "confirmed": history_data.get("confirmed", []),
                "declined": history_data.get("declined", [])
            }},
            upsert=True
        )

# Create a global database instance
db = Database() 