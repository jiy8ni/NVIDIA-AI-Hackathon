"""One-time local OAuth callback for a Slack user token."""

from __future__ import annotations

import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
SCOPES = "search:read.public,search:read.private,channels:history,groups:history"


def save_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    replacement = f"{key}={value}"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    client_id = os.getenv("SLACK_CLIENT_ID")
    client_secret = os.getenv("SLACK_CLIENT_SECRET")
    redirect_uri = os.getenv("SLACK_OAUTH_REDIRECT_URI", "http://localhost:3333/slack/callback")
    if not ENV_PATH.exists() or not client_id or not client_secret:
        raise SystemExit("Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env before running this helper.")

    redirect = urlparse(redirect_uri)
    if redirect.scheme != "http" or redirect.hostname not in {"localhost", "127.0.0.1"} or not redirect.port:
        raise SystemExit("SLACK_OAUTH_REDIRECT_URI must be a local http://localhost:<port>/... URL.")

    state = secrets.token_urlsafe(24)
    authorization_url = "https://slack.com/oauth/v2_user/authorize?" + urlencode(
        {"client_id": client_id, "scope": SCOPES, "redirect_uri": redirect_uri, "state": state}
    )
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            callback = urlparse(self.path)
            values = parse_qs(callback.query)
            if callback.path != redirect.path or values.get("state", [None])[0] != state:
                self.send_error(400, "Invalid Slack OAuth callback.")
                result["error"] = "Invalid OAuth state."
                return
            if "error" in values:
                self.send_error(403, "Slack OAuth approval was denied.")
                result["error"] = values["error"][0]
                return

            response = httpx.post(
                "https://slack.com/api/oauth.v2.user.access",
                auth=(client_id, client_secret),
                data={"code": values.get("code", [""])[0], "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
                timeout=20,
            ).json()
            token = response.get("access_token") if response.get("ok") else None
            if not token:
                self.send_error(502, "Slack did not issue a user token.")
                result["error"] = str(response.get("error", "Token exchange failed."))
                return

            save_env_value(ENV_PATH, "SLACK_USER_TOKEN", token)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h1>Slack connected.</h1><p>You can close this tab and restart the Retrieval MCP server.</p>".encode())
            result["ok"] = "true"

        def log_message(self, *_: object) -> None:
            pass

    print("Open this Slack approval URL in your browser:\n")
    print(authorization_url)
    print("\nWaiting for approval at", redirect_uri)
    server = HTTPServer((redirect.hostname, redirect.port), CallbackHandler)
    server.handle_request()
    if result.get("ok"):
        print("Slack user token saved to .env. Restart the Retrieval MCP server.")
        return
    raise SystemExit(f"Slack OAuth failed: {result.get('error', 'No callback received.')}")


if __name__ == "__main__":
    main()
