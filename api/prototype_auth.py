"""Simple shared-password gate for the internal John prototype.

This is a minimal, prototype-grade authentication layer. It is NOT suitable
for real patient data or production use without replacing it with proper
identity management (OAuth2, IAP, Firebase Auth, etc.).
"""

import hmac
import os
import time
from collections import defaultdict
from typing import Optional


COOKIE_NAME = "avinia_demo_session"

# Prototype credentials must be supplied outside source control.
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "")
DEMO_SESSION_TOKEN = os.environ.get("DEMO_SESSION_TOKEN", "")

# Rate limiting config
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_BLOCK_SECONDS = 300


class PrototypeAuthConfig:
    """Minimal shared-password authentication used only by the internal demo."""

    session_seconds = 24 * 60 * 60
    cookie_secure = os.environ.get("DEMO_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")

    def __init__(self):
        # In-memory rate limit state. Reset on process restart.
        self._login_attempts = defaultdict(list)
        self._blocked_ips = {}

    def is_rate_limited(self, client_ip: str) -> bool:
        """Return True if the client IP is currently blocked."""
        now = time.time()

        # Clear expired block
        if client_ip in self._blocked_ips:
            if self._blocked_ips[client_ip] > now:
                return True
            del self._blocked_ips[client_ip]

        # Clean old attempts outside the window
        window_start = now - RATE_LIMIT_WINDOW_SECONDS
        self._login_attempts[client_ip] = [
            ts for ts in self._login_attempts[client_ip] if ts > window_start
        ]

        # If too many attempts in the window, block the IP
        if len(self._login_attempts[client_ip]) >= RATE_LIMIT_MAX_ATTEMPTS:
            self._blocked_ips[client_ip] = now + RATE_LIMIT_BLOCK_SECONDS
            return True

        return False

    def record_login_attempt(self, client_ip: str, success: bool):
        """Record a login attempt. Successful logins clear the attempt history."""
        if success:
            self._login_attempts[client_ip] = []
            if client_ip in self._blocked_ips:
                del self._blocked_ips[client_ip]
        else:
            self._login_attempts[client_ip].append(time.time())

    def password_matches(self, candidate: str) -> bool:
        return bool(DEMO_PASSWORD) and hmac.compare_digest(DEMO_PASSWORD, candidate)

    def create_session_token(self) -> str:
        if not DEMO_SESSION_TOKEN:
            raise RuntimeError("DEMO_SESSION_TOKEN is not configured")
        return DEMO_SESSION_TOKEN

    def verify_session_token(self, token: str) -> Optional[dict]:
        if DEMO_SESSION_TOKEN and token and hmac.compare_digest(DEMO_SESSION_TOKEN, token):
            return {"sub": "prototype-john"}
        return None
