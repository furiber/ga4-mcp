"""Credential handling.

Only the ``analytics.readonly`` scope is ever requested, so the server cannot
modify GA4 configuration even if a tool tried to.

Credential sources, in order:
1. ``GA4_MCP_TOKEN_JSON`` env var: the contents of the token file below, for hosts
   such as Horizon where the server should act as *your* Google account
   (print it with ``ga4-mcp token``).
2. A token saved by ``ga4-mcp auth`` (user OAuth via your own Desktop client).
3. Application Default Credentials (``gcloud auth application-default login``).

Per-request tokens (Horizon delegated authorization, the built-in OAuth server)
take precedence over all of these; see ``server._google_token``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import google.auth
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as UserCredentials

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "ga4-mcp"


def token_path() -> Path:
    return Path(os.environ.get("GA4_MCP_TOKEN_FILE") or config_dir() / "token.json")


def run_oauth_flow(client_secrets: str | Path, port: int = 0, open_browser: bool = True) -> Path:
    """Interactive browser login; stores a refreshable user token on disk."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser, prompt="consent")
    _save(creds)
    return token_path()


def _save(creds: UserCredentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(creds.to_json())


def load_credentials() -> Credentials:
    if env_token := os.environ.get("GA4_MCP_TOKEN_JSON"):
        try:
            info = json.loads(env_token)
        except ValueError as e:
            raise RuntimeError("GA4_MCP_TOKEN_JSON is not valid JSON; paste the output of `ga4-mcp token`.") from e
        # Refreshed lazily by the Google client on first use; nothing is written back.
        return UserCredentials.from_authorized_user_info(info, SCOPES)

    path = token_path()
    if path.exists():
        creds = UserCredentials.from_authorized_user_file(str(path), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            _save(creds)
        return creds

    try:
        creds, _ = google.auth.default(scopes=SCOPES)
    except google.auth.exceptions.DefaultCredentialsError as e:
        raise RuntimeError(
            "No Google credentials found. Locally, run `ga4-mcp auth --client-secrets <file>`. "
            "On Horizon, set the GA4_MCP_TOKEN_JSON secret or link a Google auth source (see README)."
        ) from e
    return creds
