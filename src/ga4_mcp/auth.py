"""Credential handling.

Only the ``analytics.readonly`` scope is ever requested, so the server cannot
modify GA4 configuration even if a tool tried to.

Credential sources, in order:
1. A token saved by ``ga4-mcp auth`` (user OAuth via your own Desktop client).
2. Application Default Credentials (``gcloud auth application-default login``).
"""

from __future__ import annotations

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
            "No Google credentials found. Run `ga4-mcp auth --client-secrets <file>` "
            "or `gcloud auth application-default login` (see README)."
        ) from e
    return creds
