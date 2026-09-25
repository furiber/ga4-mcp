"""Command line entry point: `ga4-mcp` (serve) and `ga4-mcp auth` (login)."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ga4-mcp", description="Read-only GA4 MCP server")
    sub = parser.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="Run the MCP server (default)")
    serve.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")

    auth = sub.add_parser("auth", help="Sign in with your Google account and store a token")
    auth.add_argument("--client-secrets", required=True, help="OAuth Desktop client JSON from Google Cloud")
    auth.add_argument("--port", type=int, default=0, help="Local redirect port (0 = random)")
    auth.add_argument("--no-browser", action="store_true", help="Print the URL instead of opening a browser")

    sub.add_parser("token", help="Print the saved token as one line for the GA4_MCP_TOKEN_JSON secret")
    sub.add_parser("whoami", help="Verify credentials by listing accessible GA4 properties")

    remote = sub.add_parser("remote", help="Run the multi-user HTTP server with Google OAuth (self-hosting, e.g. Render)")
    remote.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    remote.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))

    args = parser.parse_args(argv)

    if args.cmd == "auth":
        from .auth import run_oauth_flow

        path = run_oauth_flow(args.client_secrets, port=args.port, open_browser=not args.no_browser)
        print(f"Saved token to {path}", file=sys.stderr)
    elif args.cmd == "token":
        import json

        from .auth import token_path

        path = token_path()
        if not path.exists():
            sys.exit(f"No token at {path}. Run `ga4-mcp auth --client-secrets <file>` first.")
        print(json.dumps(json.loads(path.read_text()), separators=(",", ":")))
        print("Treat this like a password: it grants read access to your GA4 data.", file=sys.stderr)
    elif args.cmd == "whoami":
        from .server import list_account_summaries

        for acc in list_account_summaries():
            print(f"{acc['account']}  {acc['account_name']}")
            for p in acc["properties"]:
                print(f"    {p['property_id']}  {p['display_name']}")
    elif args.cmd == "remote":
        import uvicorn

        from .remote import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
    else:
        from .server import mcp

        mcp.run(transport=getattr(args, "transport", "stdio"))


if __name__ == "__main__":
    main()
