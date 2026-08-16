"""
Session management for tracking PDF processing history
"""
import json
import os
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from config import Config
from logger_config import setup_logger

logger = setup_logger(__name__)


class SessionManager:
    """Manages user session history"""

    def __init__(self, session_dir: str = Config.SESSION_HISTORY_DIR):
        self.session_dir = session_dir
        Path(session_dir).mkdir(parents=True, exist_ok=True)
        self.history_file = os.path.join(session_dir, "history.json")

    def _load_history(self) -> Dict[str, Any]:
        """Load session history from file"""
        if not os.path.exists(self.history_file):
            return {"sessions": []}

        try:
            with open(self.history_file, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {str(e)}")
            return {"sessions": []}

    def _save_history(self, history: Dict[str, Any]) -> bool:
        """Save session history to file"""
        try:
            with open(self.history_file, "w") as f:
                json.dump(history, f, indent=2)
            return True
        except Exception as e:
            logger.error(f"Error saving history: {str(e)}")
            return False

    def add_session(self, file_name: str, file_size: int, summary_type: str, 
                    processing_time: float, num_chunks: int, status: str = "success") -> bool:
        """
        Add a new session to history
        
        Args:
            file_name: Name of processed PDF file
            file_size: File size in bytes
            summary_type: Type of summary generated
            processing_time: Time taken to process in seconds
            num_chunks: Number of chunks processed
            status: Processing status
        
        Returns:
            True if successful, False otherwise
        """
        if not Config.ENABLE_SESSION_HISTORY:
            return True

        history = self._load_history()

        session = {
            "id": len(history["sessions"]) + 1,
            "file_name": file_name,
            "file_size": file_size,
            "summary_type": summary_type,
            "processing_time": processing_time,
            "num_chunks": num_chunks,
            "status": status,
            "timestamp": datetime.now().isoformat(),
        }

        history["sessions"].append(session)

        # Keep only recent sessions
        if len(history["sessions"]) > Config.MAX_SESSION_HISTORY_ITEMS:
            history["sessions"] = history["sessions"][-Config.MAX_SESSION_HISTORY_ITEMS:]

        success = self._save_history(history)
        if success:
            logger.info(f"Session added: {file_name} ({summary_type})")
        
        return success

    def get_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get session history
        
        Args:
            limit: Maximum number of sessions to return (None for all)
        
        Returns:
            List of sessions
        """
        history = self._load_history()
        sessions = history.get("sessions", [])

        if limit:
            sessions = sessions[-limit:]

        return list(reversed(sessions))

    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics about processed documents"""
        history = self._load_history()
        sessions = history.get("sessions", [])

        if not sessions:
            return {
                "total_sessions": 0,
                "success_rate": 0.0,
                "avg_processing_time": 0.0,
                "total_documents": 0,
            }

        successful = sum(1 for s in sessions if s.get("status") == "success")
        processing_times = [s.get("processing_time", 0) for s in sessions]

        return {
            "total_sessions": len(sessions),
            "success_rate": (successful / len(sessions) * 100) if sessions else 0,
            "avg_processing_time": sum(processing_times) / len(processing_times) if processing_times else 0,
            "total_documents": len(sessions),
            "summary_types": list(set(s.get("summary_type") for s in sessions)),
        }

    def clear_history(self) -> bool:
        """Clear all session history"""
        try:
            if os.path.exists(self.history_file):
                os.remove(self.history_file)
            logger.info("Session history cleared")
            return True
        except Exception as e:
            logger.error(f"Error clearing history: {str(e)}")
            return False


# Global session manager instance
session_manager = SessionManager()
