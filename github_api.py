"""
A minimal wrapper around the handful of GitHub REST API endpoints
this bot needs: reading a repo's default branch, reading/updating a
file, creating a branch, and opening a pull request.

Deliberately hand-rolled with `requests` (rather than a wrapper library
like PyGithub) so it's obvious this project talks to the GitHub API
directly — that's the part that matters for the GitHub Developer
Program's "integration ... using the GitHub API" requirement.

Docs: https://docs.github.com/en/rest
"""

import base64
import requests

API_ROOT = "https://api.github.com"


class GitHubClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def get_default_branch(self, repo: str) -> str:
        r = self.session.get(f"{API_ROOT}/repos/{repo}")
        r.raise_for_status()
        return r.json()["default_branch"]

    def get_ref_sha(self, repo: str, branch: str) -> str:
        r = self.session.get(f"{API_ROOT}/repos/{repo}/git/ref/heads/{branch}")
        r.raise_for_status()
        return r.json()["object"]["sha"]

    def create_branch(self, repo: str, new_branch: str, from_sha: str) -> None:
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
        )
        # 422 = ref already exists, which is fine — reuse it.
        if r.status_code not in (201, 422):
            r.raise_for_status()

    def get_file(self, repo: str, path: str, branch: str):
        """Return (content_str, sha) for a file on a given branch."""
        r = self.session.get(
            f"{API_ROOT}/repos/{repo}/contents/{path}", params={"ref": branch}
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]

    def update_file(self, repo: str, path: str, branch: str, new_content: str,
                     sha: str, message: str) -> None:
        encoded = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        r = self.session.put(
            f"{API_ROOT}/repos/{repo}/contents/{path}",
            json={
                "message": message,
                "content": encoded,
                "sha": sha,
                "branch": branch,
            },
        )
        r.raise_for_status()

    def open_pull_request(self, repo: str, branch: str, base: str, title: str,
                           body: str) -> str:
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body},
        )
        r.raise_for_status()
        return r.json()["html_url"]
