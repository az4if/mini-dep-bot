"""
A minimal wrapper around the handful of GitHub REST (and one GraphQL)
API endpoints this bot needs: reading a repo's default branch,
reading/updating a file, creating a branch, opening/finding a pull
request, labeling it, and — optionally — enabling auto-merge on it.

Deliberately hand-rolled with `requests` (rather than a wrapper library
like PyGithub) so it's obvious this project talks to the GitHub API
directly.

Docs: https://docs.github.com/en/rest, https://docs.github.com/en/graphql
"""

import base64
import requests
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

API_ROOT = "https://api.github.com"
GRAPHQL_ROOT = "https://api.github.com/graphql"

# A light, consistent color per label so repeat runs don't create
# duplicates with random GitHub-assigned colors.
LABEL_COLORS = {
    "dependencies": "0366d6",
    "npm": "cb3837",
    "python": "3776ab",
    "go": "00add8",
    "rust": "dea584",
    "ruby": "cc342d",
    "php": "777bb4",
}


def _is_transient(exc: BaseException) -> bool:
    """Retry on network-level errors and 5xx responses; not on 4xx
    (bad token, 404, validation errors, etc — retrying won't help those).
    """
    if isinstance(exc, requests.HTTPError):
        return exc.response is not None and exc.response.status_code >= 500
    return isinstance(exc, requests.RequestException)


_retry_transient = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception(_is_transient),
)


class GitHubClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    @_retry_transient
    def get_default_branch(self, repo: str) -> str:
        r = self.session.get(f"{API_ROOT}/repos/{repo}")
        r.raise_for_status()
        return r.json()["default_branch"]

    @_retry_transient
    def get_ref_sha(self, repo: str, branch: str) -> str:
        r = self.session.get(f"{API_ROOT}/repos/{repo}/git/ref/heads/{branch}")
        r.raise_for_status()
        return r.json()["object"]["sha"]

    @_retry_transient
    def create_branch(self, repo: str, new_branch: str, from_sha: str) -> None:
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{new_branch}", "sha": from_sha},
        )
        # 422 = ref already exists, which is fine — reuse it.
        if r.status_code not in (201, 422):
            r.raise_for_status()

    @_retry_transient
    def get_file(self, repo: str, path: str, branch: str):
        """Return (content_str, sha) for a file on a given branch."""
        r = self.session.get(
            f"{API_ROOT}/repos/{repo}/contents/{path}", params={"ref": branch}
        )
        r.raise_for_status()
        data = r.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        return content, data["sha"]

    @_retry_transient
    def file_exists(self, repo: str, path: str, branch: str) -> bool:
        """Cheap existence check (e.g. for a companion lockfile) that
        doesn't raise on a 404 — just returns False.
        """
        r = self.session.get(
            f"{API_ROOT}/repos/{repo}/contents/{path}", params={"ref": branch}
        )
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    @_retry_transient
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

    @staticmethod
    def _pr_summary(data: dict) -> dict:
        """The subset of a PR API response the rest of the bot needs."""
        return {
            "html_url": data["html_url"],
            "node_id": data["node_id"],
            "number": data["number"],
        }

    @_retry_transient
    def open_pull_request(self, repo: str, branch: str, base: str, title: str,
                           body: str) -> dict:
        """Returns {"html_url", "node_id", "number"} — node_id is
        needed by enable_auto_merge, which takes a GraphQL node id
        rather than a REST-style number.
        """
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body},
        )
        r.raise_for_status()
        return self._pr_summary(r.json())

    @_retry_transient
    def find_open_pr(self, repo: str, branch: str, base: str) -> dict | None:
        """Return {"html_url", "node_id", "number"} for an existing
        open PR for `branch`, or None. Used so re-running the bot
        updates an already-open PR with a new commit instead of
        opening a duplicate.
        """
        owner = repo.split("/")[0]
        r = self.session.get(
            f"{API_ROOT}/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "base": base, "state": "open"},
        )
        r.raise_for_status()
        results = r.json()
        return self._pr_summary(results[0]) if results else None

    @_retry_transient
    def ensure_label(self, repo: str, name: str, color: str | None = None) -> None:
        """Create a label if it doesn't already exist. A 422 means it
        already exists, which is fine — nothing else to do.
        """
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/labels",
            json={"name": name, "color": color or "ededed"},
        )
        if r.status_code not in (201, 422):
            r.raise_for_status()

    @_retry_transient
    def add_labels(self, repo: str, pr_number: int, labels: list) -> None:
        if not labels:
            return
        for label in labels:
            self.ensure_label(repo, label, LABEL_COLORS.get(label))
        r = self.session.post(
            f"{API_ROOT}/repos/{repo}/issues/{pr_number}/labels",
            json={"labels": labels},
        )
        r.raise_for_status()

    def enable_auto_merge(self, pr_node_id: str, merge_method: str = "SQUASH") -> bool:
        """Best-effort: ask GitHub to merge this PR automatically once
        its required checks pass. This does NOT bypass branch
        protection or status checks — it only queues the merge for
        when they succeed, and does nothing at all unless the repo has
        "Allow auto-merge" enabled in its settings.

        Returns True if GitHub accepted the request, False otherwise
        (auto-merge disabled for the repo, no branch protection
        configured, insufficient token scope, etc) — this is
        deliberately non-fatal so a rejected auto-merge request never
        breaks the rest of the run.
        """
        query = """
        mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
          enablePullRequestAutoMerge(input: {
            pullRequestId: $pullRequestId, mergeMethod: $mergeMethod
          }) {
            clientMutationId
          }
        }
        """
        try:
            r = self.session.post(
                GRAPHQL_ROOT,
                json={
                    "query": query,
                    "variables": {"pullRequestId": pr_node_id, "mergeMethod": merge_method},
                },
                timeout=15,
            )
            r.raise_for_status()
            return "errors" not in r.json()
        except requests.RequestException:
            return False
