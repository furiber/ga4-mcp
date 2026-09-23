"""Remote (HTTP) mode: an MCP OAuth server that delegates sign-in to Google.

MCP clients (Claude, etc.) do OAuth with this server, and this server sends each user
through Google's consent screen, so every caller uses their *own* Google account and
only the ``analytics.readonly`` scope.

It's stateless, so it runs on serverless hosts like Vercel with no database. Client
registrations, authorization codes, access and refresh tokens are all Fernet-encrypted
blobs sealed with ``TOKEN_ENCRYPTION_KEY``. Consequences:
- Rotating ``TOKEN_ENCRYPTION_KEY`` signs everyone out.
- Tokens can't be revoked server-side. A user revokes access in their Google account
  (https://myaccount.google.com/permissions), which kills the refresh token at Google.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import zlib
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from .auth import SCOPES as GA_SCOPES

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPES = ["openid", "email", *GA_SCOPES]
CALLBACK_PATH = "/oauth/google/callback"
CONSENT_PATH = "/oauth/consent"

STATE_TTL = 15 * 60
CODE_TTL = 5 * 60


# --------------------------------------------------------------------------- config


@dataclass(frozen=True)
class Settings:
    public_url: str
    google_client_id: str
    google_client_secret: str
    encryption_key: str
    allowed_emails: tuple[str, ...]

    @classmethod
    def from_env(cls) -> Settings:
        missing = [
            k
            for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY", "ALLOWED_EMAILS")
            if not os.environ.get(k)
        ]
        public_url = os.environ.get("PUBLIC_URL") or (
            f"https://{os.environ['VERCEL_PROJECT_PRODUCTION_URL']}"
            if os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
            else ""
        )
        if not public_url:
            missing.append("PUBLIC_URL")
        if missing:
            raise RuntimeError(f"Missing environment variables: {', '.join(missing)} (see README, 'Deploy to Vercel').")
        return cls(
            public_url=public_url.rstrip("/"),
            google_client_id=os.environ["GOOGLE_CLIENT_ID"],
            google_client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            encryption_key=os.environ["TOKEN_ENCRYPTION_KEY"],
            allowed_emails=tuple(e.strip().lower() for e in os.environ["ALLOWED_EMAILS"].split(",") if e.strip()),
        )

    def email_allowed(self, email: str | None) -> bool:
        if not email:
            return False
        email = email.lower()
        return any(rule == "*" or rule == email or (rule.startswith("@") and email.endswith(rule)) for rule in self.allowed_emails)


# --------------------------------------------------------------------------- sealing


class Sealer:
    """Encrypts small JSON payloads into URL-safe strings with a purpose tag and expiry."""

    def __init__(self, key: str):
        self._fernet = Fernet(key)

    def seal(self, kind: str, payload: dict, ttl: int | None = None) -> str:
        body = {"k": kind, "p": payload, "x": int(time.time()) + ttl if ttl else None}
        return self._fernet.encrypt(zlib.compress(json.dumps(body, separators=(",", ":")).encode())).decode()

    def open(self, kind: str, token: str) -> dict | None:
        try:
            body = json.loads(zlib.decompress(self._fernet.decrypt(token.encode())))
        except (InvalidToken, ValueError, zlib.error):
            return None
        if body.get("k") != kind or (body.get("x") and body["x"] < time.time()):
            return None
        return body["p"]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


# --------------------------------------------------------------------------- token types


class GoogleAuthorizationCode(AuthorizationCode):
    google: dict


class GoogleRefreshToken(RefreshToken):
    google_refresh_token: str


class GoogleAccessToken(AccessToken):
    google_token: str


# --------------------------------------------------------------------------- provider


class GoogleOAuthProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.sealer = Sealer(settings.encryption_key)
        self._transport = transport  # injectable for tests

    @property
    def callback_url(self) -> str:
        return self.settings.public_url + CALLBACK_PATH

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=20, transport=self._transport)

    # --- dynamic client registration (client_id *is* the sealed registration) ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self.sealer.open("client", client_id)
        return OAuthClientInformationFull.model_validate({**data, "client_id": client_id}) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        data = client_info.model_dump(mode="json", exclude={"client_id"}, exclude_none=True)
        # The SDK returns this same object to the client, so this becomes the issued client_id.
        client_info.client_id = self.sealer.seal("client", data)

    # --- authorization: bounce through Google ---

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        state = self.sealer.seal(
            "state",
            {
                "cid": _digest(client.client_id),
                "redirect_uri": str(params.redirect_uri),
                "explicit": params.redirect_uri_provided_explicitly,
                "challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [],
                "resource": params.resource,
                "client_name": client.client_name,
            },
            ttl=STATE_TTL,
        )
        # Show our own consent page first: Google's screen can't tell the user which MCP
        # client (and redirect target) they are authorizing, and registration is open.
        return f"{self.settings.public_url}{CONSENT_PATH}?{urlencode({'state': state})}"

    def google_authorize_url(self, state: str) -> str:
        query = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.callback_url,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(query)}"

    async def handle_consent(self, request: Request) -> Response:
        state = request.query_params.get("state", "")
        pending = self.sealer.open("state", state)
        if pending is None:
            return _page("Sign-in link expired or invalid. Start the connection again from your MCP client.", 400)
        from html import escape

        client = escape(pending.get("client_name") or "An unnamed MCP client")
        target = escape(urlparse(pending["redirect_uri"]).netloc or pending["redirect_uri"])
        google = escape(self.google_authorize_url(state))
        return HTMLResponse(
            "<!doctype html><meta name=viewport content='width=device-width'><title>GA4 MCP</title>"
            "<body style='font-family:system-ui;max-width:32rem;margin:3rem auto;padding:0 1rem'>"
            f"<h2>Allow access to your Google Analytics?</h2><p><b>{client}</b> wants read-only access to "
            f"your GA4 data through this server. You'll be returned to <b>{target}</b>.</p>"
            "<p>Only continue if you started this connection yourself.</p>"
            f"<p><a href='{google}' style='padding:.6rem 1rem;background:#1a73e8;color:#fff;border-radius:6px;"
            "text-decoration:none'>Continue with Google</a></p></body>"
        )

    async def handle_google_callback(self, request: Request) -> Response:
        q = request.query_params
        pending = self.sealer.open("state", q.get("state", ""))
        if pending is None:
            return _page("Sign-in link expired or invalid. Start the connection again from your MCP client.", 400)
        if "error" in q:
            return RedirectResponse(
                construct_redirect_uri(pending["redirect_uri"], error="access_denied", state=pending["state"]), 302
            )

        async with self.http() as http:
            resp = await http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": q.get("code", ""),
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "redirect_uri": self.callback_url,
                    "grant_type": "authorization_code",
                },
            )
        if resp.status_code != 200:
            return _page(f"Google rejected the sign-in: {resp.text}", 400)
        tok = resp.json()

        email = _id_token_email(tok.get("id_token"))
        if not self.settings.email_allowed(email):
            return _page(f"{email or 'This account'} is not allowed to use this server (ALLOWED_EMAILS).", 403)
        if GA_SCOPES[0] not in tok.get("scope", "").split():
            return _page("Google Analytics read access was not granted. Tick the Analytics checkbox and retry.", 400)
        if not tok.get("refresh_token"):
            return _page("Google did not return a refresh token. Retry the connection.", 400)

        code = self.sealer.seal(
            "code",
            {
                **pending,
                "email": email,
                "g_refresh": tok["refresh_token"],
                "g_access": tok["access_token"],
                "g_exp": int(time.time()) + int(tok.get("expires_in", 3600)),
                "nonce": secrets.token_hex(8),
            },
            ttl=CODE_TTL,
        )
        return RedirectResponse(construct_redirect_uri(pending["redirect_uri"], code=code, state=pending["state"]), 302)

    # --- code -> tokens ---

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str):
        d = self.sealer.open("code", authorization_code)
        if not d or d["cid"] != _digest(client.client_id):
            return None
        return GoogleAuthorizationCode(
            code=authorization_code,
            scopes=d["scopes"],
            expires_at=time.time() + CODE_TTL,  # sealer already enforced the real expiry
            client_id=client.client_id,
            code_challenge=d["challenge"],
            redirect_uri=d["redirect_uri"],
            redirect_uri_provided_explicitly=d["explicit"],
            resource=d["resource"],
            subject=d["email"],
            google=d,
        )

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code) -> OAuthToken:
        g = authorization_code.google
        return self._issue(client, g["email"], g["scopes"], g["resource"], g["g_refresh"], g["g_access"], g["g_exp"])

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str):
        d = self.sealer.open("refresh", refresh_token)
        if not d or d["cid"] != _digest(client.client_id) or not self.settings.email_allowed(d["email"]):
            return None
        return GoogleRefreshToken(
            token=refresh_token,
            client_id=client.client_id,
            scopes=d["scopes"],
            resource=d["resource"],
            subject=d["email"],
            google_refresh_token=d["g_refresh"],
        )

    async def exchange_refresh_token(self, client, refresh_token, scopes: list[str]) -> OAuthToken:
        async with self.http() as http:
            resp = await http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "refresh_token": refresh_token.google_refresh_token,
                    "client_id": self.settings.google_client_id,
                    "client_secret": self.settings.google_client_secret,
                    "grant_type": "refresh_token",
                },
            )
        if resp.status_code != 200:
            raise TokenError("invalid_grant", "Google refresh failed; sign in again.")
        tok = resp.json()
        return self._issue(
            client,
            refresh_token.subject,
            scopes or refresh_token.scopes,
            refresh_token.resource,
            tok.get("refresh_token") or refresh_token.google_refresh_token,
            tok["access_token"],
            int(time.time()) + int(tok.get("expires_in", 3600)),
        )

    def _issue(self, client, email, scopes, resource, g_refresh, g_access, g_exp) -> OAuthToken:
        ttl = max(60, g_exp - int(time.time()) - 60)  # expire just before Google's token does
        common = {"cid": _digest(client.client_id), "email": email, "scopes": scopes, "resource": resource}
        return OAuthToken(
            access_token=self.sealer.seal("access", {**common, "g_access": g_access}, ttl=ttl),
            token_type="Bearer",
            expires_in=ttl,
            refresh_token=self.sealer.seal("refresh", {**common, "g_refresh": g_refresh}),
            scope=" ".join(scopes) or None,
        )

    # --- resource server side ---

    async def load_access_token(self, token: str) -> GoogleAccessToken | None:
        d = self.sealer.open("access", token)
        if not d or not self.settings.email_allowed(d["email"]):
            return None
        return GoogleAccessToken(
            token=token,
            client_id=d["cid"],
            scopes=d["scopes"],
            resource=d["resource"],
            subject=d["email"],
            google_token=d["g_access"],
        )

    async def revoke_token(self, token) -> None:  # revocation endpoint is disabled
        return None


def _id_token_email(id_token: str | None) -> str | None:
    # The ID token came straight from Google's token endpoint over TLS, so per OIDC
    # Core §3.1.3.7 its signature doesn't need re-verifying here.
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return None
    return claims.get("email") if claims.get("email_verified") else None


def _page(message: str, status: int) -> HTMLResponse:
    from html import escape

    return HTMLResponse(f"<!doctype html><title>GA4 MCP</title><p>{escape(message)}</p>", status_code=status)


# --------------------------------------------------------------------------- app


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    from .server import build_server

    settings = settings or Settings.from_env()
    provider = GoogleOAuthProvider(settings, transport=transport)
    server = build_server(
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=settings.public_url,
            resource_server_url=f"{settings.public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(enabled=True),
            validate_token_resource=False,  # tokens are sealed with our own key
        ),
    )

    @server.custom_route(CALLBACK_PATH, methods=["GET"])
    async def google_callback(request: Request) -> Response:
        return await provider.handle_google_callback(request)

    @server.custom_route(CONSENT_PATH, methods=["GET"])
    async def consent(request: Request) -> Response:
        return await provider.handle_consent(request)

    @server.custom_route("/", methods=["GET"])
    async def index(request: Request) -> Response:
        return PlainTextResponse(f"GA4 MCP server. Connect your MCP client to {settings.public_url}/mcp\n")

    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # Public HTTPS endpoint protected by OAuth; DNS-rebinding checks are for localhost servers.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
