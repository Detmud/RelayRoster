"""OIDC sign-in against a simulated IdP: discovery, JWKS, token endpoint with PKCE.

Usage: python tests/test_oidc.py   (or pytest)
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import time
from urllib.parse import parse_qs, urlparse

os.environ["RELAYROSTER_NO_AUTOAPP"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import httpx  # noqa: E402
import jwt  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import COOKIE, FLOW_COOKIE  # noqa: E402
from app.main import create_app  # noqa: E402
from app.oidc import OIDCError, normalize_pem, ssl_context  # noqa: E402
from fake_shelly import make_fleet  # noqa: E402
from test_core import make_settings  # noqa: E402

ISSUER = "https://id.example.com"
CLIENT_ID = "relayroster-client"
SECRET = "client-secret"
H = {"X-Requested-With": "relayroster"}


def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeIdP:
    """A small identity provider: serves discovery and JWKS, exchanges codes for ID tokens."""

    def __init__(self):
        self.key, self.kid = _rsa(), "k1"
        self.extra_keys: list[dict] = []
        self.issuer_reported = ISSUER
        self.codes: dict[str, dict] = {}
        self.token_requests: list[dict] = []
        self.jwks_hits = 0
        self.claims_override: dict = {}
        self.token_factory = None

    def jwk(self, key=None, kid=None) -> dict:
        data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk((key or self.key).public_key()))
        return data | {"kid": kid or self.kid, "use": "sig", "alg": "RS256"}

    def authorize(self, url: str, user="alice", groups=("relayroster",)) -> tuple[str, str]:
        """Simulates the sign-in at the IdP: remembers nonce and PKCE challenge, returns (code, state)."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID and q["code_challenge_method"] == "S256"
        assert q["scope"] == "openid profile email groups"
        code = f"code-{len(self.codes)}"
        self.codes[code] = {"nonce": q["nonce"], "challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"],
                            "user": user, "groups": list(groups)}
        return code, q["state"]

    def id_token(self, grant: dict, **override) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "u-123", "iat": now, "exp": now + 300,
                  "nonce": grant["nonce"], "preferred_username": grant["user"], "email": "s@example.org",
                  "groups": grant["groups"]} | self.claims_override | override
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": self.kid})

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": self.issuer_reported, "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/api/oidc/token", "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                "end_session_endpoint": f"{ISSUER}/api/oidc/end-session"})
        if url == f"{ISSUER}/.well-known/jwks.json":
            self.jwks_hits += 1
            return httpx.Response(200, json={"keys": [self.jwk()] + self.extra_keys})
        if url == f"{ISSUER}/api/oidc/token" and request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.token_requests.append({"form": form, "auth": request.headers.get("authorization", "")})
            expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
            if request.headers.get("authorization") != expected:
                return httpx.Response(401, json={"error": "invalid_client"})
            grant = self.codes.pop(form.get("code", ""), None)
            if not grant:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "code unknown"})
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(form.get("code_verifier", "").encode()).digest()).decode().rstrip("=")
            if challenge != grant["challenge"] or form.get("redirect_uri") != grant["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE or redirect"})
            token = self.token_factory(grant) if self.token_factory else self.id_token(grant)
            return httpx.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": token})
        return httpx.Response(404)


def setup(tmp: str, idp: FakeIdP | None = None, **kw):
    idp = idp or FakeIdP()
    base = {"oidc_issuer": ISSUER, "oidc_client_id": CLIENT_ID, "oidc_client_secret": SECRET}
    cfg = make_settings(tmp, **(base | kw))
    app = create_app(cfg, transport=make_fleet().transport(), autostart=False,
                     oidc_transport=httpx.MockTransport(idp.handler))
    return idp, app, TestClient(app, follow_redirects=False)


def start(c: TestClient, idp: FakeIdP, next_hash="", **authorize_kw):
    r = c.get("/auth/login", params={"next": next_hash} if next_hash else None)
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith(f"{ISSUER}/authorize?")
    assert FLOW_COOKIE in c.cookies
    return idp.authorize(r.headers["location"], **authorize_kw)


def error_of(r) -> str:
    assert r.status_code == 303 and r.headers["location"].startswith("../login?"), (r.status_code, r.headers)
    assert COOKIE not in r.cookies
    return parse_qs(urlparse(r.headers["location"]).query)["error"][0]


def test_without_oidc_open():
    """No sign-in without OIDC; old cookies (local, without origin) are valid nowhere."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_settings(tmp)
        c = TestClient(create_app(cfg, autostart=False), follow_redirects=False)
        assert c.get("/auth/login").status_code == 404 and c.get("/auth/callback").status_code == 404
        assert c.get("/login").headers["location"] == "./"
        authn = c.app.state.auth
        legacy_body = base64.urlsafe_b64encode(f"admin|{int(time.time()) + 3600}".encode()).decode().rstrip("=")
        assert authn.session(f"{legacy_body}.{authn._sign(legacy_body)}") is None, "cookie of an older format"
        assert authn.session(authn.issue("admin", source="local")) is None
        assert authn.session(authn.issue("alice", source="oidc")) is None, "no OIDC session without OIDC"


def test_login_page():
    with tempfile.TemporaryDirectory() as tmp:
        _, _, c = setup(tmp, oidc_provider_name="ID <home>")
        html = c.get("/login").text
        assert 'id="ssoBtn"' in html and "Sign in with ID &lt;home&gt;" in html
        assert 'id="loginForm"' not in html and "password" not in html.lower() and "<!--SSO-->" not in html
        assert "static/style.css?v=" in html, "sign-in page with version parameter on the assets"
        assert c.get("/", follow_redirects=False).headers["location"] == "login"
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/overview", auth=("admin", "pw")).status_code == 401, "no basic auth any more"
        assert c.post("/api/login", json={"user": "admin", "password": "pw"}, headers=H).status_code in (404, 405)


def test_full_login():
    with tempfile.TemporaryDirectory() as tmp:
        idp, app, c = setup(tmp)
        code, state = start(c, idp, "#device/shelly2pmg4-1020ba000201/settings")
        r = c.get("/auth/callback", params={"code": code, "state": state})
        assert r.status_code == 303 and r.headers["location"] == "../#device/shelly2pmg4-1020ba000201/settings"
        assert COOKIE in c.cookies and FLOW_COOKIE not in c.cookies, "the flow cookie is deleted"
        st = c.get("/api/state").json()
        assert st["user"] == "alice" and st["auth"] is True
        assert app.state.auth.session(c.cookies[COOKIE])[1] == "oidc"
        req = idp.token_requests[0]
        assert req["form"]["grant_type"] == "authorization_code" and req["form"]["code_verifier"]
        assert req["form"]["redirect_uri"] == "http://testserver/auth/callback"
        # The code is used up, a second redemption fails
        c.cookies.clear()
        start(c, idp)
        r = c.get("/auth/callback", params={"code": code, "state": state})
        assert "Sign-in check failed (state)" in error_of(r)
        # Signing out only deletes our own cookie
        c2 = TestClient(app, follow_redirects=False)
        code, state = start(c2, idp)
        c2.get("/auth/callback", params={"code": code, "state": state})
        assert c2.post("/api/logout", headers=H).status_code == 200
        assert c2.get("/api/state").status_code == 401


def test_rejected_tokens():
    cases = [
        ({"aud": "other-client"}, "not meant for this client"),
        ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 7200}, "expired"),
        ({"nonce": "falsch"}, "nonce mismatch"),
        ({"nonce": None}, "nonce mismatch"),
        ({"iss": "https://evil.example"}, "wrong issuer"),
        ({"aud": [CLIENT_ID, "other"], "azp": "other"}, "azp"),
        ({"sub": None}, "rejected"),
    ]
    for override, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            idp, _, c = setup(tmp)
            idp.claims_override = override
            code, state = start(c, idp)
            msg = error_of(c.get("/auth/callback", params={"code": code, "state": state}))
            assert want in msg, (override, msg)
            assert c.get("/api/state").status_code == 401


def test_bad_signatures():
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp)
        foreign = _rsa()
        now = int(time.time())

        def claims(grant):
            return {"iss": ISSUER, "aud": CLIENT_ID, "sub": "x", "iat": now, "exp": now + 60, "nonce": grant["nonce"]}
        factories = {
            "foreign key": lambda g: jwt.encode(claims(g), foreign, algorithm="RS256", headers={"kid": "k1"}),
            "HS256 with secret": lambda g: jwt.encode(claims(g), SECRET, algorithm="HS256", headers={"kid": "k1"}),
            "alg none": lambda g: jwt.encode(claims(g), None, algorithm="none"),
            "unbekannter kid": lambda g: jwt.encode(claims(g), idp.key, algorithm="RS256", headers={"kid": "k9"}),
            "broken": lambda g: "not.a.token",
        }
        for name, factory in factories.items():
            idp.token_factory = factory
            code, state = start(c, idp)
            msg = error_of(c.get("/auth/callback", params={"code": code, "state": state}))
            assert msg, name
            assert c.get("/api/state").status_code == 401, name
        assert idp.jwks_hits >= 2, "an unknown kid reloads the JWKS"


def test_key_rotation_and_ec_keys():
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp)
        code, state = start(c, idp)
        assert c.get("/auth/callback", params={"code": code, "state": state}).status_code == 303
        hits = idp.jwks_hits
        idp.key, idp.kid = _rsa(), "k2"                  # the IdP rotates its key
        c.cookies.clear()
        code, state = start(c, idp)
        r = c.get("/auth/callback", params={"code": code, "state": state})
        assert r.headers["location"] == "../" and idp.jwks_hits == hits + 1, "without restart after one reload"
        # ES256 (some providers use EC keys)
        ec_key = ec.generate_private_key(ec.SECP256R1())
        idp.extra_keys = [json.loads(jwt.algorithms.ECAlgorithm.to_jwk(ec_key.public_key()))
                          | {"kid": "ec1", "use": "sig", "alg": "ES256"}]
        now = int(time.time())
        idp.token_factory = lambda g: jwt.encode(
            {"iss": ISSUER, "aud": CLIENT_ID, "sub": "u", "iat": now, "exp": now + 60, "nonce": g["nonce"],
             "email": "ec@example.org"}, ec_key, algorithm="ES256", headers={"kid": "ec1"})
        c.cookies.clear()
        code, state = start(c, idp)
        assert c.get("/auth/callback", params={"code": code, "state": state}).status_code == 303
        assert c.get("/api/state").json()["user"] == "ec@example.org", "without preferred_username: e-mail"


def test_state_and_flow_cookie():
    with tempfile.TemporaryDirectory() as tmp:
        idp, app, c = setup(tmp)
        code, state = start(c, idp)
        assert "state" in error_of(c.get("/auth/callback", params={"code": code, "state": state + "x"}))
        # Without the flow cookie (other browser, expired)
        c.cookies.clear()
        assert "took too long" in error_of(c.get("/auth/callback", params={"code": code, "state": state}))
        # Manipulated flow cookie
        code, state = start(c, idp)
        tok = c.cookies[FLOW_COOKIE]
        c.cookies.clear()
        c.cookies.set(FLOW_COOKIE, tok[:-1] + ("0" if tok[-1] != "0" else "1"))
        assert "took too long" in error_of(c.get("/auth/callback", params={"code": code, "state": state}))
        c.cookies.clear()
        authn = app.state.auth
        assert authn.unseal(authn.seal({"a": 1}, now=time.time() - 3600)) is None, "expires after 10 minutes"
        # Error from the IdP (user cancels)
        start(c, idp)
        r = c.get("/auth/callback", params={"error": "access_denied", "error_description": "User denied"})
        assert error_of(r) == "SSO: User denied"
        # Foreign return targets are discarded
        c.cookies.clear()
        for bad in ("//evil.example", "https://evil.example", "#a\"b", "javascript:alert(1)"):
            code, state = start(c, idp, bad)
            r = c.get("/auth/callback", params={"code": code, "state": state})
            assert r.headers["location"] == "../", bad
            c.cookies.clear()


def test_allowed_groups():
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp, oidc_allowed_groups=["relayroster_admins", "smarthome"])
        code, state = start(c, idp, groups=["family"])
        assert "not in a group" in error_of(c.get("/auth/callback", params={"code": code, "state": state}))
        idp.claims_override = {"groups": None}
        code, state = start(c, idp)
        assert "not in a group" in error_of(c.get("/auth/callback", params={"code": code, "state": state}))
        idp.claims_override = {}
        code, state = start(c, idp, groups=["family", "smarthome"])
        assert c.get("/auth/callback", params={"code": code, "state": state}).headers["location"] == "../"
        assert c.get("/api/state").json()["user"] == "alice"


def test_provider_problems():
    with tempfile.TemporaryDirectory() as tmp:
        idp = FakeIdP()
        idp.issuer_reported = "https://other.example"
        _, _, c = setup(tmp, idp)
        assert "Issuer mismatch" in error_of(c.get("/auth/login"))
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_settings(tmp, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID)

        def down(request):
            raise httpx.ConnectError("refused")
        c = TestClient(create_app(cfg, autostart=False, oidc_transport=httpx.MockTransport(down)),
                       follow_redirects=False)
        assert "SSO unreachable (ConnectError)" in error_of(c.get("/auth/login"))
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp, oidc_client_secret="falsch")
        code, state = start(c, idp)
        assert "HTTP 401: invalid_client" in error_of(c.get("/auth/callback", params={"code": code, "state": state}))


def test_session_cookie():
    """Signed session cookie after OIDC sign-in, expiry, restart, sign-out; local sessions are not valid."""
    from app.auth import MAX_AGE
    with tempfile.TemporaryDirectory() as tmp:
        idp, app, c = setup(tmp)
        code, state = start(c, idp)
        c.get("/auth/callback", params={"code": code, "state": state})
        token = c.cookies[COOKIE]
        authn = app.state.auth
        assert authn.verify(token) == "alice"
        assert authn.verify(token[:-1] + ("0" if token[-1] != "0" else "1")) is None, "manipulierte Signatur"
        assert authn.verify("x" + token) is None and authn.verify("") is None and authn.verify("broken") is None
        assert authn.verify(authn.issue("alice", now=time.time() - MAX_AGE - 10)) is None, "abgelaufen"
        assert authn.verify(authn.issue("admin", source="local")) is None, "local sessions are no longer valid"
        # The key lives in /data: after a restart the session stays valid
        app2 = create_app(make_settings(tmp, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, oidc_client_secret=SECRET),
                          autostart=False)
        assert app2.state.auth.verify(token) == "alice"
        assert c.post("/api/logout").status_code == 403, "CSRF-Header Pflicht"
        assert c.post("/api/logout", headers=H).status_code == 200 and COOKIE not in c.cookies
        assert c.get("/api/overview").status_code == 401


def test_redirect_uri():
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp)
        r = c.get("/auth/login", headers={"x-forwarded-proto": "https", "host": "relayroster.example.com"})
        assert parse_qs(urlparse(r.headers["location"]).query)["redirect_uri"] == [
            "https://relayroster.example.com/auth/callback"]
        assert "Secure" in r.headers["set-cookie"], "flow cookie behind HTTPS with Secure"
    with tempfile.TemporaryDirectory() as tmp:
        idp, _, c = setup(tmp, oidc_redirect_uri="https://fleet.example/x/auth/callback")
        code, state = start(c, idp)
        c.get("/auth/callback", params={"code": code, "state": state})
        assert idp.token_requests[0]["form"]["redirect_uri"] == "https://fleet.example/x/auth/callback"


def test_ca_certificate():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Example Local Authority")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    ctx = ssl_context(pem)
    assert any(c.get("subject") == ((("commonName", "Example Local Authority"),),) for c in ctx.get_ca_certs())
    assert ssl_context(pem.replace("\n", "\\n")).get_ca_certs(), "one line with \\n"
    assert ssl_context("") is True


# This is how a value arrived from a form field: spaces instead of line breaks, one dash missing at the front.
# Self-generated root certificate, only for this test.
EXAMPLE_ROOT_MANGLED = (
    "----BEGIN CERTIFICATE----- MIIBcjCCARigAwIBAgIUSkYqmr2w9zds926YPsnRLDh6DRAwCgYIKoZIzj0EAwIw LTErMCkGA1UEAwwiRXhhbXBsZSBMb2NhbCBBdX"
    "Rob3JpdHkgLSBFQ0MgUm9vdDAe Fw0yNjAxMDEwMDAwMDBaFw0zNTEyMzAwMDAwMDBaMC0xKzApBgNVBAMMIkV4YW1w bGUgTG9jYWwgQXV0aG9yaXR5IC0gRUNDIFJvb3"
    "QwWTATBgcqhkjOPQIBBggqhkjO PQMBBwNCAAR3F6ETLTqRY6bo62C3BP3SSxY9d2ZS8T7M3ylUIOb8ENjKdNSgRXkr 0P4QzK3P7oZf28+FwaCuxV83QpDCLxSToxYwFD"
    "ASBgNVHRMBAf8ECDAGAQH/AgEB MAoGCCqGSM49BAMCA0gAMEUCIB1d7y2IIfCN3B9r1x9SHMVVB7DPGLKHJmjKUkic VZ8XAiEA/aIlGSRJaBXFFswApIJmcLs+La23Tq"
    "Z/pcQlqN4l0Jc= -----END CERTIFICATE-----")


def test_ca_certificate_tolerant():
    """Mangled PEM values from form fields are repaired, unusable ones do not break the app."""
    ctx = ssl_context(EXAMPLE_ROOT_MANGLED)
    subjects = [c.get("subject") for c in ctx.get_ca_certs()]
    assert ((("commonName", "Example Local Authority - ECC Root"),),) in subjects
    pem = normalize_pem(EXAMPLE_ROOT_MANGLED)
    assert pem.startswith("-----BEGIN CERTIFICATE-----\nMIIBcjCC") and pem.endswith("-----END CERTIFICATE-----\n")
    assert all(len(line) <= 64 for line in pem.splitlines())
    assert normalize_pem(EXAMPLE_ROOT_MANGLED + EXAMPLE_ROOT_MANGLED).count("BEGIN CERTIFICATE") == 2
    for bad in ("Zertifikat", "-----BEGIN CERTIFICATE----- ----END CERTIFICATE-----",
                "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"):
        try:
            ssl_context(bad)
            raise AssertionError(f"{bad!r} must be rejected")
        except OIDCError as exc:
            assert "OIDC_CA_CERT" in str(exc)

    # Unusable value: the app starts, the sign-in button reports the error
    with tempfile.TemporaryDirectory() as tmp:
        idp, app, c = setup(tmp, oidc_ca_cert="broken")
        assert app.state.oidc.config_error and c.get("/login").status_code == 200
        assert "OIDC_CA_CERT contains no certificate" in error_of(c.get("/auth/login"))
    # Repaired value: sign-in via the IdP works
    with tempfile.TemporaryDirectory() as tmp:
        idp, app, c = setup(tmp, oidc_ca_cert=EXAMPLE_ROOT_MANGLED)
        assert app.state.oidc.config_error is None
        code, state = start(c, idp)
        assert c.get("/auth/callback", params={"code": code, "state": state}).headers["location"] == "../"


if __name__ == "__main__":
    failed = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
