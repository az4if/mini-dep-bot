"""Tests for github_api.py's GitHubClient, mocking requests.Session so
no real network access or GitHub token is needed.

`_client()` builds a real GitHubClient (so its retry-decorated methods
run for real) but swaps in a MagicMock session, so every HTTP call is
intercepted. `FakeResponse` stands in for a `requests.Response`.
"""

import base64
from unittest.mock import MagicMock

import pytest
import requests

from github_api import GitHubClient, _is_transient


class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = {} if json_data is None else json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"{self.status_code} error")
            err.response = self
            raise err


def _client():
    client = GitHubClient("fake-token")
    client.session = MagicMock()
    return client


class TestIsTransient:
    def test_5xx_http_error_is_transient(self):
        err = requests.HTTPError("boom")
        err.response = FakeResponse(503)
        assert _is_transient(err) is True

    def test_4xx_http_error_is_not_transient(self):
        err = requests.HTTPError("boom")
        err.response = FakeResponse(404)
        assert _is_transient(err) is False

    def test_connection_error_is_transient(self):
        assert _is_transient(requests.ConnectionError("boom")) is True

    def test_non_request_exception_is_not_transient(self):
        assert _is_transient(ValueError("not a request error")) is False


class TestSimpleReads:
    def test_get_default_branch(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, {"default_branch": "main"})
        assert client.get_default_branch("x/y") == "main"

    def test_get_ref_sha(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, {"object": {"sha": "abc123"}})
        assert client.get_ref_sha("x/y", "main") == "abc123"


class TestGetFile:
    def test_decodes_base64_content(self):
        client = _client()
        encoded = base64.b64encode(b"hello world").decode("ascii")
        client.session.get.return_value = FakeResponse(200, {"content": encoded, "sha": "filesha"})
        content, sha = client.get_file("x/y", "README.md", "main")
        assert content == "hello world"
        assert sha == "filesha"


class TestListTree:
    def test_returns_only_blob_paths(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, {"tree": [
            {"path": "package.json", "type": "blob"},
            {"path": "apps", "type": "tree"},  # a directory — must be excluded
            {"path": "apps/web/package.json", "type": "blob"},
        ]})
        paths = client.list_tree("x/y", "main")
        assert paths == ["package.json", "apps/web/package.json"]

    def test_truncated_response_returns_partial_list_not_raises(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, {
            "tree": [{"path": "package.json", "type": "blob"}],
            "truncated": True,
        })
        assert client.list_tree("x/y", "main") == ["package.json"]


class TestFileExists:
    def test_true_when_present(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, {})
        assert client.file_exists("x/y", "package-lock.json", "main") is True

    def test_false_on_404_without_raising(self):
        client = _client()
        client.session.get.return_value = FakeResponse(404)
        assert client.file_exists("x/y", "missing.txt", "main") is False

    def test_raises_on_non_404_error(self):
        client = _client()
        client.session.get.return_value = FakeResponse(403)  # non-transient, no retry involved
        with pytest.raises(requests.HTTPError):
            client.file_exists("x/y", "secret.txt", "main")


class TestCreateBranch:
    def test_success(self):
        client = _client()
        client.session.post.return_value = FakeResponse(201)
        client.create_branch("x/y", "new-branch", "sha123")  # should not raise
        client.session.post.assert_called_once()

    def test_already_exists_is_treated_as_success(self):
        client = _client()
        client.session.post.return_value = FakeResponse(422)
        client.create_branch("x/y", "existing-branch", "sha123")  # should not raise

    def test_other_error_raises_without_retrying(self):
        client = _client()
        client.session.post.return_value = FakeResponse(400)  # non-transient
        with pytest.raises(requests.HTTPError):
            client.create_branch("x/y", "bad-branch", "sha123")
        client.session.post.assert_called_once()  # exactly one call proves no retry happened


class TestUpdateFile:
    def test_encodes_and_sends_correct_payload(self):
        client = _client()
        client.session.put.return_value = FakeResponse(200)
        client.update_file("x/y", "package.json", "a-branch", "new content", "filesha", "commit msg")

        _, kwargs = client.session.put.call_args
        payload = kwargs["json"]
        assert base64.b64decode(payload["content"]).decode("utf-8") == "new content"
        assert payload["sha"] == "filesha"
        assert payload["branch"] == "a-branch"
        assert payload["message"] == "commit msg"


class TestPullRequests:
    def test_open_pull_request_returns_summary_subset(self):
        client = _client()
        client.session.post.return_value = FakeResponse(201, {
            "html_url": "https://github.com/x/y/pull/1",
            "node_id": "PR_1",
            "number": 1,
            "extra_field_we_dont_need": "ignored",
        })
        pr = client.open_pull_request("x/y", "branch", "main", "title", "body")
        assert pr == {"html_url": "https://github.com/x/y/pull/1", "node_id": "PR_1", "number": 1}

    def test_find_open_pr_returns_none_when_no_results(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, [])
        assert client.find_open_pr("x/y", "branch", "main") is None

    def test_find_open_pr_returns_summary_of_first_match(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, [
            {"html_url": "https://github.com/x/y/pull/5", "node_id": "PR_5", "number": 5},
        ])
        pr = client.find_open_pr("x/y", "branch", "main")
        assert pr == {"html_url": "https://github.com/x/y/pull/5", "node_id": "PR_5", "number": 5}

    def test_find_open_pr_uses_owner_colon_branch_as_head_filter(self):
        client = _client()
        client.session.get.return_value = FakeResponse(200, [])
        client.find_open_pr("acme/widgets", "mini-dep-bot/updates", "main")
        _, kwargs = client.session.get.call_args
        assert kwargs["params"]["head"] == "acme:mini-dep-bot/updates"


class TestLabels:
    def test_ensure_label_creates_new_label(self):
        client = _client()
        client.session.post.return_value = FakeResponse(201)
        client.ensure_label("x/y", "dependencies", "0366d6")
        client.session.post.assert_called_once()

    def test_ensure_label_tolerates_already_exists(self):
        client = _client()
        client.session.post.return_value = FakeResponse(422)
        client.ensure_label("x/y", "dependencies")  # should not raise

    def test_add_labels_noop_on_empty_list(self):
        client = _client()
        client.add_labels("x/y", 1, [])
        client.session.post.assert_not_called()

    def test_add_labels_ensures_each_label_then_posts_once(self):
        client = _client()
        client.session.post.return_value = FakeResponse(201)
        client.add_labels("x/y", 1, ["dependencies", "npm"])
        # 2 ensure_label POSTs + 1 issues/labels POST = 3
        assert client.session.post.call_count == 3


class TestEnableAutoMerge:
    def test_returns_true_on_success(self):
        client = _client()
        client.session.post.return_value = FakeResponse(200, {"data": {"enablePullRequestAutoMerge": {}}})
        assert client.enable_auto_merge("PR_NODE_ID") is True

    def test_returns_false_when_graphql_errors_present(self):
        client = _client()
        client.session.post.return_value = FakeResponse(200, {"errors": [{"message": "not allowed"}]})
        assert client.enable_auto_merge("PR_NODE_ID") is False

    def test_returns_false_on_network_failure_not_raises(self):
        client = _client()
        client.session.post.side_effect = requests.ConnectionError("boom")
        assert client.enable_auto_merge("PR_NODE_ID") is False

    def test_not_retried_on_failure(self):
        # enable_auto_merge is deliberately NOT wrapped in _retry_transient
        # (a rejected auto-merge request shouldn't burn retries) — confirm
        # a single failure means exactly one call, not three.
        client = _client()
        client.session.post.side_effect = requests.ConnectionError("boom")
        client.enable_auto_merge("PR_NODE_ID")
        assert client.session.post.call_count == 1


class TestRetryBehavior:
    def test_recovers_after_one_transient_failure(self):
        client = _client()
        transient_error = requests.HTTPError("503 error")
        transient_error.response = FakeResponse(503)
        client.session.get.side_effect = [transient_error, FakeResponse(200, {"default_branch": "main"})]

        assert client.get_default_branch("x/y") == "main"
        assert client.session.get.call_count == 2
