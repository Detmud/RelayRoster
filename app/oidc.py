"""Sign-in via OIDC: authorization code flow with PKCE against a central IdP, e.g. Pocket ID.

Flow:
1. /auth/login creates state, nonce and PKCE verifier, puts them into a signed short-lived cookie and redirects to
   the authorization_endpoint of the IdP
2. /auth/callback checks state against the cookie, exchanges the code at the token_endpoint (client secret via basic
   auth, plus the PKCE verifier) and checks the ID token: signature via JWKS, iss, aud, azp, exp, iat, nonce
3. Then RelayRoster sets the session cookie. The IdP is asked exactly once per sign-in,
   not on every request

The configuration comes via discovery (/.well-known/openid-configuration) and is cached for an hour, the
keys (JWKS) as well. An unknown key (kid) reloads the JWKS once, so a key rotation at the
IdP takes effect without a restart.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import ssl
import time
from typing import Any
from urllib.parse import urlencode

import certifi
import httpx
import jwt

from .config import Settings

log = logging.getLogger(__name__)

CACHE_S = 3600
LEEWAY_S = 60
# Only asymmetric algorithms: no "none", no HS256 with the client secret as key
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")


class OIDCError(Exception):
    """Sign-in failed. The text goes to the sign-in page as is."""


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


# Tolerant of what form fields do to PEM: spaces instead of line breaks, \n as text,
# a different number of dashes
_PEM_BLOCK = re.compile(r"-{1,5}\s*BEGIN CERTIFICATE\s*-{1,5}(.*?)-{1,5}\s*END CERTIFICATE\s*-{1,5}", re.S)
_NOT_B64 = re.compile(r"[^A-Za-z0-9+/=]")


def normalize_pem(text: str) -> str:
    """Builds clean certificate blocks from a mangled PEM text. Empty if none can be recognised."""
    blocks = []
    for body in _PEM_BLOCK.findall(text.replace("\\n", "\n")):
        b64 = _NOT_B64.sub("", body)
        if b64:
            lines = "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64))
            blocks.append(f"-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n")
    return "".join(blocks)


def ssl_context(ca_pem: str) -> ssl.SSLContext | bool:
    """Default CAs (certifi) plus an own root certificate, e.g. your own internal CA.

    Raises OIDCError if OIDC_CA_CERT is set but contains no usable certificate.
    """
    if not ca_pem.strip():
        return True
    pem = normalize_pem(ca_pem)
    if not pem:
        raise OIDCError("OIDC_CA_CERT contains no certificate (expected -----BEGIN CERTIFICATE----- … "
                        "-----END CERTIFICATE-----)")
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        ctx.load_verify_locations(cadata=pem)
    except ssl.SSLError as exc:
        raise OIDCError(f"OIDC_CA_CERT is not a valid certificate ({exc.reason or exc})") from exc
    return ctx


class OIDC:
    def __init__(self, cfg: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.cfg = cfg
        self.transport = transport
        # An unusable OIDC_CA_CERT must not prevent the start, otherwise the whole sign-in fails.
        # The error appears in the log and when clicking "Sign in with …".
        self.config_error: str | None = None
        self._verify: ssl.SSLContext | bool = True
        if self.enabled:
            try:
                self._verify = ssl_context(cfg.oidc_ca_cert)
            except OIDCError as exc:
                self.config_error = str(exc)
                log.error("OIDC: %s", exc)
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._jwks: tuple[float, list[dict[str, Any]]] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.oidc_issuer and self.cfg.oidc_client_id)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport, verify=self._verify, timeout=10, follow_redirects=False)

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._client() as http:
                resp = await http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OIDCError(f"{self.cfg.oidc_provider_name} unreachable ({type(exc).__name__})") from exc
        if resp.status_code != 200:
            raise OIDCError(f"{self.cfg.oidc_provider_name} answered HTTP {resp.status_code} for {url}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise OIDCError(f"{self.cfg.oidc_provider_name} sent no JSON for {url}") from exc
        if not isinstance(data, dict):
            raise OIDCError(f"{self.cfg.oidc_provider_name} sent unexpected JSON for {url}")
        return data

    async def discovery(self) -> dict[str, Any]:
        if self.config_error:
            raise OIDCError(self.config_error)
        if self._discovery and time.monotonic() - self._discovery[0] < CACHE_S:
            return self._discovery[1]
        doc = await self._get_json(f"{self.cfg.oidc_issuer}/.well-known/openid-configuration")
        if (doc.get("issuer") or "").rstrip("/") != self.cfg.oidc_issuer:
            raise OIDCError(f"Issuer mismatch: OIDC_ISSUER is {self.cfg.oidc_issuer}, "
                            f"the provider reports {doc.get('issuer')!r}")
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(doc.get(key), str) or not doc[key]:
                raise OIDCError(f"Provider configuration lacks {key}")
        self._discovery = (time.monotonic(), doc)
        return doc

    async def _keys(self, force: bool = False) -> list[dict[str, Any]]:
        if not force and self._jwks and time.monotonic() - self._jwks[0] < CACHE_S:
            return self._jwks[1]
        doc = await self.discovery()
        keys = (await self._get_json(doc["jwks_uri"])).get("keys")
        if not isinstance(keys, list):
            raise OIDCError("Provider JWKS has no keys")
        self._jwks = (time.monotonic(), [k for k in keys if isinstance(k, dict)])
        return self._jwks[1]

    async def authorize_url(self, *, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        doc = await self.discovery()
        params = {
            "response_type": "code",
            "client_id": self.cfg.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": self.cfg.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        sep = "&" if "?" in doc["authorization_endpoint"] else "?"
        return doc["authorization_endpoint"] + sep + urlencode(params)

    async def exchange(self, *, code: str, verifier: str, redirect_uri: str) -> dict[str, Any]:
        doc = await self.discovery()
        data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                "code_verifier": verifier, "client_id": self.cfg.oidc_client_id}
        auth = (self.cfg.oidc_client_id, self.cfg.oidc_client_secret) if self.cfg.oidc_client_secret else None
        try:
            async with self._client() as http:
                resp = await http.post(doc["token_endpoint"], data=data, auth=auth,
                                       headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise OIDCError(f"{self.cfg.oidc_provider_name} unreachable ({type(exc).__name__})") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200:
            reason = body.get("error_description") or body.get("error") if isinstance(body, dict) else None
            raise OIDCError(f"Token request failed (HTTP {resp.status_code}{': ' + str(reason) if reason else ''})")
        if not isinstance(body, dict) or not isinstance(body.get("id_token"), str):
            raise OIDCError("Token response contains no id_token")
        return body

    async def _key_for(self, kid: str | None, alg: str) -> Any:
        for force in (False, True):
            keys = await self._keys(force=force)
            match = [k for k in keys if k.get("kid") == kid] if kid else keys
            match = [k for k in match if k.get("use", "sig") == "sig" and k.get("alg", alg) == alg]
            if len(match) == 1:
                try:
                    return jwt.PyJWK(match[0], algorithm=alg).key
                except jwt.PyJWTError as exc:
                    raise OIDCError(f"Unusable signing key ({exc})") from exc
        raise OIDCError("No matching signing key in the provider JWKS")

    async def validate(self, id_token: str, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as exc:
            raise OIDCError("Malformed id_token") from exc
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            raise OIDCError(f"id_token algorithm {alg!r} not accepted")
        key = await self._key_for(header.get("kid"), alg)
        try:
            claims = jwt.decode(id_token, key, algorithms=[alg], audience=self.cfg.oidc_client_id,
                                issuer=self.cfg.oidc_issuer, leeway=LEEWAY_S,
                                options={"require": ["exp", "iat", "iss", "aud", "sub"]})
        except jwt.ExpiredSignatureError as exc:
            raise OIDCError("id_token expired") from exc
        except jwt.InvalidAudienceError as exc:
            raise OIDCError("id_token is not meant for this client (aud)") from exc
        except jwt.InvalidIssuerError as exc:
            raise OIDCError("id_token from wrong issuer") from exc
        except jwt.PyJWTError as exc:
            raise OIDCError(f"id_token rejected ({exc})") from exc
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.cfg.oidc_client_id:
            raise OIDCError("id_token azp does not match this client")
        if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OIDCError("id_token nonce mismatch")
        return claims

    def user_from(self, claims: dict[str, Any]) -> str:
        """User name from the claims, with a check of the allowed groups."""
        allowed = self.cfg.oidc_allowed_groups
        if allowed:
            groups = claims.get("groups")
            groups = [str(g) for g in groups] if isinstance(groups, list) else []
            if not set(groups) & set(allowed):
                raise OIDCError("Your account is not in a group allowed to use RelayRoster")
        for key in ("preferred_username", "email", "sub"):
            value = claims.get(key)
            if isinstance(value, str) and value.strip() and "\n" not in value:
                return value.strip()[:128]
        raise OIDCError("id_token carries no user name")
