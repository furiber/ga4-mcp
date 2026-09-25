import base64
import hashlib
import html
import re
import json
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from ga4_mcp import remote, server
from ga4_mcp.auth import SCOPES

BASE = "https://ga4.example.com"
REDIRECT = "http://localhost:3333/callback"


def _id_token(email):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'email': email, 'email_verified': True})}.sig"


class FakeGoogle:
    def __init__(self, email="me@example.com"):
        self.email = email
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        self.calls.append(form)
        grant = form["grant_type"][0]
        body = {"access_token": f"g-access-{len(self.calls)}", "expires_in": 3599, "token_type": "Bearer"}
        if grant == "authorization_code":
            body |= {"refresh_token": "g-refresh", "id_token": _id_token(self.email), "scope": " ".join(["openid", *SCOPES])}
        elif form["refresh_token"][0] != "g-refresh":
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=body)


def make_client(google, allowed=("me@example.com",)):
    settings = remote.Settings(
        public_url=BASE,
        google_client_id="gid",
        google_client_secret="gsecret",
        encryption_key=Fernet.generate_key().decode(),
        allowed_emails=allowed,
    )
    app = remote.create_app(settings, transport=httpx.MockTransport(google))
    return TestClient(app, base_url=BASE, follow_redirects=False)


def login(client):
    reg = client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "test",
        },
    )
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    auth = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state",
        },
    )
    assert auth.status_code == 302, auth.text
    consent = client.get(auth.headers["location"])
    assert consent.status_code == 200 and "test" in consent.text and "localhost:3333" in consent.text

    google_url = urlparse(html.unescape(re.search(r"href='([^']+)'", consent.text).group(1)))
    assert google_url.netloc == "accounts.google.com"
    gq = parse_qs(google_url.query)
    assert SCOPES[0] in gq["scope"][0] and gq["redirect_uri"] == [BASE + remote.CALLBACK_PATH]

    cb = client.get(remote.CALLBACK_PATH, params={"code": "google-code", "state": gq["state"][0]})
    return client_id, verifier, cb


def test_full_oauth_flow_and_tool_call(monkeypatch):
    google = FakeGoogle()
    seen = {}

    class FakeAdmin:
        def list_account_summaries(self):
            seen["token"] = server._google_token()
            return []

    monkeypatch.setattr(server, "_admin_client", FakeAdmin)

    with make_client(google) as client:
        assert client.post("/mcp", json={}).status_code == 401

        client_id, verifier, cb = login(client)
        assert cb.status_code == 302, cb.text
        back = parse_qs(urlparse(cb.headers["location"]).query)
        assert back["state"] == ["client-state"]

        tok = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": back["code"][0],
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert tok.status_code == 200, tok.text
        tokens = tok.json()

        headers = {
            "Authorization": f"Bearer {tokens['access_token']}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        init = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
        )
        assert init.status_code == 200, init.text
        call = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "list_account_summaries", "arguments": {}}},
        )
        assert call.status_code == 200, call.text
        assert not call.json()["result"].get("isError"), call.text
        assert seen["token"] == "g-access-1"  # the caller's own Google token reached the tool

        ref = client.post(
            "/token",
            data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id},
        )
        assert ref.status_code == 200, ref.text
        assert google.calls[-1]["grant_type"] == ["refresh_token"]


def test_email_not_allowed_is_rejected():
    with make_client(FakeGoogle("intruder@evil.com"), allowed=("me@example.com", "@corp.com")) as client:
        _, _, cb = login(client)
        assert cb.status_code == 403


def test_domain_rule():
    s = remote.Settings("u", "i", "s", Fernet.generate_key().decode(), ("@corp.com",))
    assert s.email_allowed("a@corp.com") and not s.email_allowed("a@notcorp.com")


def test_sealer_rejects_wrong_kind_and_tampering():
    s = remote.Sealer(Fernet.generate_key().decode())
    t = s.seal("access", {"a": 1})
    assert s.open("access", t) == {"a": 1}
    assert s.open("refresh", t) is None
    assert s.open("access", t[:-4] + "AAAA") is None
    assert remote.Sealer(Fernet.generate_key().decode()).open("access", t) is None
