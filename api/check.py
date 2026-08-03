"""
Vercel serverless function entrypoint.

Vercel Cron sends a GET request to /api/check on the schedule defined in
vercel.json. This reuses the same MANIFESTS / check_manifest logic that
bot.py uses for local/Actions runs, so the dependency-checking code only
lives in one place.
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Make the project root importable (github_api.py, parsers.py, bot.py
# live one directory up from this file, in api/).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from github_api import GitHubClient  # noqa: E402
from bot import MANIFESTS, check_manifest  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Vercel Cron requests carry `Authorization: Bearer <CRON_SECRET>`.
        # If CRON_SECRET is set, reject anything else so random requests
        # to this URL can't trigger the bot.
        cron_secret = os.environ.get("CRON_SECRET")
        if cron_secret:
            auth_header = self.headers.get("Authorization", "")
            if auth_header != f"Bearer {cron_secret}":
                self._respond(401, {"error": "unauthorized"})
                return

        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("TARGET_REPO")
        if not token or not repo:
            self._respond(500, {"error": "GITHUB_TOKEN and TARGET_REPO must be set"})
            return

        try:
            client = GitHubClient(token)
            base_branch = client.get_default_branch(repo)

            all_prs = []
            for path, parse_fn, lookup_fn, bump_fn in MANIFESTS:
                all_prs += check_manifest(
                    client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn
                )

            self._respond(200, {"opened_pull_requests": all_prs, "count": len(all_prs)})
        except Exception as exc:
            self._respond(500, {"error": str(exc)})

    def _respond(self, status: int, payload: dict) -> None:
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))
