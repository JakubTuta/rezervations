import threading
from typing import Dict
from app.reservation_service import ReservationService


class SessionManager:
    """
    Thread-safe session manager for concurrent requests.
    Uses locks per email to prevent race conditions.
    """

    def __init__(self):
        self._locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def get_lock(self, email: str) -> threading.Lock:
        """Get or create a lock for a specific email"""
        with self._global_lock:
            if email not in self._locks:
                self._locks[email] = threading.Lock()
            return self._locks[email]

    def get_service(self, email: str, password: str) -> ReservationService:
        """Get reservation service instance (not thread-safe by itself)"""
        return ReservationService(email, password)


# Global singleton
session_manager = SessionManager()
