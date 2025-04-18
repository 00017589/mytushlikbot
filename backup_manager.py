import os
import json
import logging
import zipfile
import pytz
import datetime
from cryptography.fernet import Fernet
from typing import Optional, Dict, List
from database_manager import db_manager

logger = logging.getLogger(__name__)
TASHKENT_TZ = pytz.timezone("Asia/Tashkent")

class BackupManager:
    def __init__(self, backup_dir: str = "backups", max_backups: int = 7):
        self.backup_dir = backup_dir
        self.max_backups = max_backups
        self.encryption_key = os.getenv("BACKUP_ENCRYPTION_KEY")
        
        if not self.encryption_key:
            self.encryption_key = Fernet.generate_key()
            logger.warning("No encryption key found in environment. Generated new key.")
            
        self.fernet = Fernet(self.encryption_key)
        os.makedirs(self.backup_dir, exist_ok=True)
        
    def _encrypt_data(self, data: dict) -> bytes:
        """Encrypt data using Fernet"""
        try:
            json_data = json.dumps(data, ensure_ascii=False)
            return self.fernet.encrypt(json_data.encode())
        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise
            
    def _decrypt_data(self, encrypted_data: bytes) -> dict:
        """Decrypt data using Fernet"""
        try:
            decrypted_data = self.fernet.decrypt(encrypted_data)
            return json.loads(decrypted_data.decode())
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise
            
    async def create_backup(self) -> Optional[str]:
        """Create an encrypted and compressed backup"""
        try:
            timestamp = datetime.datetime.now(TASHKENT_TZ).strftime("%Y%m%d_%H%M%S")
            backup_file = f"backup_{timestamp}.zip"
            backup_path = os.path.join(self.backup_dir, backup_file)
            
            # Collect data
            backup_data = {
                "users": await db_manager.get_all_users(),
                "daily_attendance": await db_manager.get_daily_attendance(
                    datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
                ),
                "timestamp": timestamp,
                "version": "1.0"
            }
            
            # Encrypt data
            encrypted_data = self._encrypt_data(backup_data)
            
            # Create compressed backup
            with zipfile.ZipFile(backup_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('backup.enc', encrypted_data)
                
            # Cleanup old backups
            self._cleanup_old_backups()
            
            logger.info(f"Backup created successfully: {backup_file}")
            return backup_file
            
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            return None
            
    async def restore_from_backup(self, backup_file: str) -> bool:
        """Restore from an encrypted backup"""
        try:
            backup_path = os.path.join(self.backup_dir, backup_file)
            if not os.path.exists(backup_path):
                raise FileNotFoundError(f"Backup file not found: {backup_file}")
                
            # Extract and decrypt backup
            with zipfile.ZipFile(backup_path, 'r') as zf:
                encrypted_data = zf.read('backup.enc')
                backup_data = self._decrypt_data(encrypted_data)
                
            # Verify backup data
            if not self._verify_backup_data(backup_data):
                raise ValueError("Invalid backup data format")
                
            # Restore data
            await self._restore_data(backup_data)
            
            logger.info(f"Restore completed successfully from {backup_file}")
            return True
            
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False
            
    def _verify_backup_data(self, data: dict) -> bool:
        """Verify backup data structure"""
        required_keys = ["users", "daily_attendance", "timestamp", "version"]
        return all(key in data for key in required_keys)
        
    async def _restore_data(self, data: dict):
        """Restore data to database"""
        try:
            # Restore users
            for user in data["users"]:
                await db_manager.update_user(user["user_id"], user)
                
            # Restore daily attendance
            if data["daily_attendance"]:
                await db_manager.update_daily_attendance(
                    datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d"),
                    data["daily_attendance"]
                )
                
        except Exception as e:
            logger.error(f"Data restoration failed: {e}")
            raise
            
    def _cleanup_old_backups(self):
        """Remove old backups keeping only the most recent ones"""
        try:
            backup_files = sorted([
                f for f in os.listdir(self.backup_dir)
                if f.startswith("backup_") and f.endswith(".zip")
            ])
            
            while len(backup_files) > self.max_backups:
                old_backup = os.path.join(self.backup_dir, backup_files.pop(0))
                os.remove(old_backup)
                logger.info(f"Removed old backup: {old_backup}")
                
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")
            
    def list_backups(self) -> List[Dict[str, str]]:
        """List available backups with details"""
        try:
            backups = []
            for filename in os.listdir(self.backup_dir):
                if filename.startswith("backup_") and filename.endswith(".zip"):
                    path = os.path.join(self.backup_dir, filename)
                    size = os.path.getsize(path)
                    created = datetime.datetime.fromtimestamp(
                        os.path.getctime(path)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                    
                    backups.append({
                        "filename": filename,
                        "size": f"{size / 1024:.1f} KB",
                        "created": created
                    })
                    
            return sorted(backups, key=lambda x: x["created"], reverse=True)
            
        except Exception as e:
            logger.error(f"Error listing backups: {e}")
            return []
            
    def verify_backup_file(self, backup_file: str) -> bool:
        """Verify if a backup file is valid and can be decrypted"""
        try:
            backup_path = os.path.join(self.backup_dir, backup_file)
            if not os.path.exists(backup_path):
                return False
                
            with zipfile.ZipFile(backup_path, 'r') as zf:
                # Check if required file exists
                if 'backup.enc' not in zf.namelist():
                    return False
                    
                # Try to decrypt
                encrypted_data = zf.read('backup.enc')
                backup_data = self._decrypt_data(encrypted_data)
                
                # Verify structure
                return self._verify_backup_data(backup_data)
                
        except Exception as e:
            logger.error(f"Backup verification failed: {e}")
            return False

# Create a global instance
backup_manager = BackupManager() 