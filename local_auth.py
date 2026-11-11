"""Local API credentials. UI secret lives in memory; only the MCP/deck digests are persisted."""
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
# Stream Deck などの物理ボタン（外部操作キー）。配信中に押す操作だけを1つの窓口に集め、
# 任意の文の表示・伏せ字の解除・辞書の編集・ログ読み出しには届かないようにする
DECK_GET = frozenset(("/api/deck/state",))
DECK_POST = frozenset(("/api/deck/action",))
# 鍵の種類 → api_auth.json に保存する digest のキー名
KINDS = {"mcp": "mcp_digest", "deck": "deck_digest"}
# Release smoke tests launch the frozen app with a known UI secret. Only a process that can
# already set this process's environment (the same OS user) can use it, so no boundary moves.
TEST_UI_TOKEN_ENV = "MOJICAST_TEST_UI_TOKEN"


def _test_ui_token():
    value = os.environ.pop(TEST_UI_TOKEN_ENV, "")  # Do not leak it to child processes.
    return value if re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value) else None


class LocalAuth:
    def __init__(self, path):
        self.ui_token = _test_ui_token() or secrets.token_urlsafe(32)
        self.path = path
        self._lock = threading.RLock()
        self._digests = {kind: None for kind in KINDS}
        self.banned_added = {}  # Per-credential additions eligible for MCP undo.
        try:
            with open(path, encoding="utf-8") as f:
                saved = json.load(f)
            for kind, field in KINDS.items():
                value = saved.get(field)
                if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                    self._digests[kind] = value
        except (OSError, ValueError, AttributeError):
            pass  # Missing or corrupt state disables MCP/deck, never opens access.

    def role(self, token):
        if not isinstance(token, str) or not token or len(token) > 256:
            return None
        if hmac.compare_digest(token.encode(), self.ui_token.encode()):
            return "ui"
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            for kind, saved in self._digests.items():
                if saved and hmac.compare_digest(digest, saved):
                    return kind
        return None

    def enabled(self, kind="mcp"):
        with self._lock:
            return self._digests[kind] is not None

    def rotate(self, revoke=False, kind="mcp"):
        token = None if revoke else secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest() if token else None
        with self._lock:
            digests = dict(self._digests, **{kind: digest})
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".api-auth-", dir=os.path.dirname(self.path))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({KINDS[k]: v for k, v in digests.items()}, f)
                os.chmod(temp, 0o600)
                os.replace(temp, self.path)
                self._digests = digests
                if kind == "mcp":
                    self.banned_added.clear()
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return token
