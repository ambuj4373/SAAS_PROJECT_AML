"""
Server-side session manager for persistent authentication across browser tabs.
Sessions are stored in memory with a 2-hour expiry.
"""
import uuid
import time
from datetime import datetime, timedelta
from threading import Lock

# In-memory session store
_SESSIONS = {}
_LOCK = Lock()

SESSION_TIMEOUT_HOURS = 2


def create_session(password_hash: str) -> str:
    """Create a new session token and store it server-side."""
    with _LOCK:
        session_token = str(uuid.uuid4())
        _SESSIONS[session_token] = {
            "created_at": datetime.now(),
            "password_hash": password_hash,
            "last_accessed": datetime.now(),
        }
    return session_token


def validate_session(session_token: str) -> bool:
    """Check if session is valid (exists and not expired)."""
    with _LOCK:
        if session_token not in _SESSIONS:
            return False
        
        session = _SESSIONS[session_token]
        elapsed = datetime.now() - session["created_at"]
        
        # Session expired
        if elapsed > timedelta(hours=SESSION_TIMEOUT_HOURS):
            del _SESSIONS[session_token]
            return False
        
        # Update last accessed time
        session["last_accessed"] = datetime.now()
        return True


def get_remaining_time(session_token: str) -> int:
    """Get remaining session time in minutes."""
    with _LOCK:
        if session_token not in _SESSIONS:
            return 0
        
        session = _SESSIONS[session_token]
        elapsed = datetime.now() - session["created_at"]
        remaining = timedelta(hours=SESSION_TIMEOUT_HOURS) - elapsed
        remaining_mins = int(remaining.total_seconds() / 60)
        
        return max(0, remaining_mins)


def clear_session(session_token: str) -> None:
    """Clear a specific session."""
    with _LOCK:
        if session_token in _SESSIONS:
            del _SESSIONS[session_token]


def cleanup_expired_sessions() -> None:
    """Clean up expired sessions (call periodically)."""
    with _LOCK:
        expired = [
            token for token, session in _SESSIONS.items()
            if datetime.now() - session["created_at"] > timedelta(hours=SESSION_TIMEOUT_HOURS)
        ]
        for token in expired:
            del _SESSIONS[token]
