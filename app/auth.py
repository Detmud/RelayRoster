"""Sign-in: only via OIDC, session as a signed cookie.

current_user() is the only place that decides who is signed in. Without OIDC configuration there is no
sign-in (only sensible behind a proxy with its own auth). Password login, basic auth and UI_USER/UI_PASSWORD
were removed; cookies with origin "local" or without origin are no longer valid.

Cookie format: <base64url("user|expiry|origin")>.<hmac-sha256 hex>. The HMAC key is derived from a
random value in the database, issuer and client ID: a different IdP client signs out all browsers.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from fastapi import Request

from .config import Settings

# Own name per app: cookies are not separated by port, and one host often runs several apps
COOKIE = "relayroster_session"
FLOW_COOKIE = "relayroster_oidc"
MAX_AGE = 30 * 24 * 3600
FLOW_MAX_AGE = 600
META_KEY = "session_secret"
SOURCES = ("local", "oidc")   # "local" only to recognise old cookies
ANON = "admin"                 # user name when no sign-in is set up


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Auth:
    def __init__(self, cfg: Settings, store):
        self.cfg = cfg
        secret = store.get_meta(META_KEY)
        if not secret:
            secret = secrets.token_hex(32)
            store.set_meta(META_KEY, secret)
        self._secret = bytes.fromhex(secret)

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.cfg.oidc_issuer and self.cfg.oidc_client_id)

    @property
    def enabled(self) -> bool:
        """Sign-in only with OIDC; without OIDC the UI is open."""
        return self.oidc_enabled

    def _key(self) -> bytes:
        material = f"oidc\x00{self.cfg.oidc_issuer}\x00{self.cfg.oidc_client_id}"
        return hmac.new(self._secret, material.encode(), hashlib.sha256).digest()

    def _sign(self, body: str, purpose: str = "") -> str:
        key = self._key() if not purpose else hmac.new(self._key(), purpose.encode(), hashlib.sha256).digest()
        return hmac.new(key, body.encode(), hashlib.sha256).hexdigest()

    def issue(self, user: str, now: float | None = None, source: str = "oidc") -> str:
        if source not in SOURCES:
            raise ValueError(source)
        expires = int((time.time() if now is None else now) + MAX_AGE)
        body = _b64(f"{user}|{expires}|{source}".encode())
        return f"{body}.{self._sign(body)}"

    def session(self, token: str | None, now: float | None = None) -> tuple[str, str] | None:
        """(user, origin) from a valid, unexpired cookie, otherwise None."""
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        if not secrets.compare_digest(sig, self._sign(body)):
            return None
        try:
            text = _unb64(body).decode()
            head, last = text.rsplit("|", 1)
            if last in SOURCES:
                user, expires = head.rsplit("|", 1)
                source = last
            else:                                   # cookie format of older versions
                user, expires, source = head, last, "local"
            expires_at = int(expires)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if expires_at < (time.time() if now is None else now):
            return None
        if source != "oidc" or not self.oidc_enabled:
            return None
        return user, source

    def verify(self, token: str | None, now: float | None = None) -> str | None:
        s = self.session(token, now)
        return s[0] if s else None

    # ---- Short-lived cookie for the OIDC flow (state, nonce, PKCE verifier, return target) --------------------

    def seal(self, data: dict[str, Any], now: float | None = None) -> str:
        payload = dict(data, exp=int((time.time() if now is None else now) + FLOW_MAX_AGE))
        body = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body, 'oidc-flow')}"

    def unseal(self, token: str | None, now: float | None = None) -> dict[str, Any] | None:
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        if not secrets.compare_digest(sig, self._sign(body, "oidc-flow")):
            return None
        try:
            data = json.loads(_unb64(body))
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("exp"), int):
            return None
        if data["exp"] < (time.time() if now is None else now):
            return None
        return data

    def current_user(self, request: Request) -> str | None:
        """Signed-in user or None. Without OIDC everyone counts as ANON."""
        if not self.enabled:
            return ANON
        return self.verify(request.cookies.get(COOKIE))

    @staticmethod
    def secure(request: Request) -> bool:
        """Send the cookie only over HTTPS if the page comes via HTTPS (directly or through a reverse proxy)."""
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
