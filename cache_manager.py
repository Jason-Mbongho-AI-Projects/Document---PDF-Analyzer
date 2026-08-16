"""
Caching system for PDF processing results
"""
import json
import hashlib
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path
from config import Config
from logger_config import setup_logger

logger = setup_logger(__name__)


class CacheManager:
    """Manages caching of PDF processing results"""

    def __init__(self, cache_dir: str = Config.CACHE_DIR):
        self.cache_dir = cache_dir
        Path(cache_dir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _generate_cache_key(file_name: str, file_hash: str, summary_type: str) -> str:
        """Generate a unique cache key"""
        key_str = f"{file_name}_{file_hash}_{summary_type}"
        return hashlib.md5(key_str.encode()).hexdigest()

    @staticmethod
    def _compute_file_hash(file_content: bytes) -> str:
        """Compute SHA256 hash of file content"""
        return hashlib.sha256(file_content).hexdigest()

    def get(self, file_name: str, file_hash: str, summary_type: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached result if it exists and is not expired
        
        Args:
            file_name: Original file name
            file_hash: SHA256 hash of file content
            summary_type: Type of summary
        
        Returns:
            Cached data or None if not found/expired
        """
        if not Config.ENABLE_CACHING:
            return None

        cache_key = self._generate_cache_key(file_name, file_hash, summary_type)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")

        if not os.path.exists(cache_file):
            logger.debug(f"Cache miss for {file_name} ({summary_type})")
            return None

        try:
            with open(cache_file, "r") as f:
                cache_data = json.load(f)

            # Check expiration
            created_time = datetime.fromisoformat(cache_data["created_at"])
            expiration_time = created_time + timedelta(hours=Config.CACHE_EXPIRATION_HOURS)

            if datetime.now() > expiration_time:
                logger.info(f"Cache expired for {file_name}")
                os.remove(cache_file)
                return None

            logger.info(f"Cache hit for {file_name} ({summary_type})")
            return cache_data["data"]

        except Exception as e:
            logger.error(f"Error reading cache: {str(e)}")
            return None

    def set(self, file_name: str, file_hash: str, summary_type: str, data: Dict[str, Any]) -> bool:
        """
        Store result in cache
        
        Args:
            file_name: Original file name
            file_hash: SHA256 hash of file content
            summary_type: Type of summary
            data: Data to cache
        
        Returns:
            True if successful, False otherwise
        """
        if not Config.ENABLE_CACHING:
            return True

        cache_key = self._generate_cache_key(file_name, file_hash, summary_type)
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.json")

        try:
            cache_data = {
                "file_name": file_name,
                "file_hash": file_hash,
                "summary_type": summary_type,
                "created_at": datetime.now().isoformat(),
                "data": data,
            }

            with open(cache_file, "w") as f:
                json.dump(cache_data, f, indent=2)

            logger.info(f"Cached result for {file_name} ({summary_type})")
            return True

        except Exception as e:
            logger.error(f"Error writing cache: {str(e)}")
            return False

    def clear_expired(self) -> int:
        """Remove expired cache entries"""
        if not os.path.exists(self.cache_dir):
            return 0

        removed_count = 0
        try:
            for cache_file in os.listdir(self.cache_dir):
                cache_path = os.path.join(self.cache_dir, cache_file)
                try:
                    with open(cache_path, "r") as f:
                        cache_data = json.load(f)

                    created_time = datetime.fromisoformat(cache_data["created_at"])
                    expiration_time = created_time + timedelta(hours=Config.CACHE_EXPIRATION_HOURS)

                    if datetime.now() > expiration_time:
                        os.remove(cache_path)
                        removed_count += 1

                except Exception as e:
                    logger.warning(f"Error processing cache file {cache_file}: {str(e)}")

        except Exception as e:
            logger.error(f"Error clearing expired cache: {str(e)}")

        if removed_count > 0:
            logger.info(f"Removed {removed_count} expired cache entries")

        return removed_count

    def clear_all(self) -> bool:
        """Clear all cache"""
        try:
            for cache_file in os.listdir(self.cache_dir):
                os.remove(os.path.join(self.cache_dir, cache_file))
            logger.info("Cache cleared successfully")
            return True
        except Exception as e:
            logger.error(f"Error clearing cache: {str(e)}")
            return False


# Global cache manager instance
cache_manager = CacheManager()
