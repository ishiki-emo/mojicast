"""Local API credentials. UI secret lives in memory; only the MCP digest is persisted."""
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading

MCP_GET = frozenset(("/api/config", "/api/status", "/api/presets", "/api/boxes",
                     "/api/fonts", "/api/profiles", "/api/hotwords", "/api/banned",
                     "/api/glossary", "/api/effects", "/api/logs/recent"))
MCP_POST_FIELDS = {
    "/api/config": frozenset(("preset", "box")),
    "/api/presets": frozenset(("presets",)),
    "/api/boxes": frozenset(("boxes",)),
    "/api/hotwords": frozenset(("entries", "profile")),
    "/api/banned": frozenset(("words", "profile")),
    "/api/glossary": frozenset(("entries", "profile")),
    "/api/effects": frozenset(("effects", "profile")),
    "/api/sample-caption": frozenset(("text", "translation")),
    "/api/clear": frozenset(),
}
MCP_CONFIG_READ = ("preset", "box", "collab", "translate", "word_profile")


class LocalAuth:
    def __init__(self, path):
        self.ui_token = secrets.token_urlsafe(32)
        self.path = path
        self._lock = threading.RLock()
        self._digest = None
        self.banned_added = {}  # Per-credential additions eligible for MCP undo.
        try:
            with open(path, encoding="utf-8") as f:
                value = json.load(f).get("mcp_digest")
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                self._digest = value
        except (OSError, ValueError, AttributeError):
            pass  # Missing or corrupt state disables MCP, never opens access.

    def role(self, token):
        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        if hmac.compare_digest(token.encode(), self.ui_token.encode()):
            return "ui"
        with self._lock:
            if self._digest and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), self._digest):
                return "mcp"
        return None

    def enabled(self):
        with self._lock:
            return self._digest is not None

    def rotate(self, revoke=False):
        token = None if revoke else secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest() if token else None
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".api-auth-", dir=os.path.dirname(self.path))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"mcp_digest": digest}, f)
                os.chmod(temp, 0o600)
                os.replace(temp, self.path)
                self._digest = digest
                self.banned_added.clear()
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return token
