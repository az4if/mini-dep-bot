"""Tests for bot.py's orchestration logic: find_updates (ignore/pin
filtering) and check_manifest (branch/PR creation, existing-PR reuse,
labels, and auto-merge gating), all against a mocked GitHubClient so
no network or GitHub token is needed.
"""

from unittest.mock import MagicMock, patch

import bot
from parsers import parse_package_json, bump_package_json


class TestFindUpdates:
    def test_ignore_and_pin_are_respected(self):
        deps = {"requests": "2.0.0", "flask": "1.0.0", "ignored-pkg": "1.0.0"}

        def fake_lookup(name, max_major=None):
            latest = {"requests": "2.34.2", "flask": "3.1.0", "ignored-pkg": "9.0.0"}[name]
            return f"{max_major}.9.9" if max_major is not None else latest

        config = {"ignore": {"ignored-pkg"}, "pin": {"flask": 1}}
        updates = bot.find_updates(deps, fake_lookup, config)

        assert updates == [("requests", "2.0.0", "2.34.2"), ("flask", "1.0.0", "1.9.9")]

    def test_no_lookup_result_is_skipped(self):
        deps = {"unknown-pkg": "1.0.0"}
        updates = bot.find_updates(deps, lambda name, max_major=None: None, {"ignore": set(), "pin": {}})
        assert updates == []


def _pr(number, node_id="PR_NODE"):
    return {"html_url": f"https://github.com/x/y/pull/{number}", "node_id": node_id, "number": number}


class TestCheckManifest:
    def _client(self, base_content, branch_content=None, existing_pr=None, lockfile_exists=False):
        client = MagicMock()
        client.get_file.side_effect = [
            (base_content, "base-sha"),
            (branch_content if branch_content is not None else base_content, "branch-sha"),
        ]
        client.find_open_pr.return_value = existing_pr
        client.get_ref_sha.return_value = "ref-sha"
        client.file_exists.return_value = lockfile_exists
        return client

    def test_opens_new_pr_and_labels_it(self):
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}')
        client.open_pull_request.return_value = _pr(1, "PR_1")

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,
                {"ignore": set(), "pin": {}, "automerge_patch": False},
            )

        assert url == "https://github.com/x/y/pull/1"
        client.open_pull_request.assert_called_once()
        client.add_labels.assert_called_once_with("x/y", 1, ["dependencies", "npm"])
        client.enable_auto_merge.assert_not_called()

    def test_reuses_existing_open_pr_instead_of_duplicating(self):
        client = self._client(
            '{"dependencies": {"lodash": "4.17.0"}}',
            existing_pr=_pr(7, "PR_7"),
        )

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,
                {"ignore": set(), "pin": {}, "automerge_patch": False},
            )

        assert url == "https://github.com/x/y/pull/7"
        client.open_pull_request.assert_not_called()
        client.update_file.assert_called_once()

    def test_patch_only_bumps_trigger_automerge_when_configured(self):
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}')
        client.open_pull_request.return_value = _pr(2, "PR_2")

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.17.9", bump_package_json,  # patch bump
                {"ignore": set(), "pin": {}, "automerge_patch": True},
            )

        client.enable_auto_merge.assert_called_once_with("PR_2")

    def test_minor_bump_does_not_trigger_automerge(self):
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}')
        client.open_pull_request.return_value = _pr(3, "PR_3")

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,  # minor bump
                {"ignore": set(), "pin": {}, "automerge_patch": True},
            )

        client.enable_auto_merge.assert_not_called()

    def test_lockfile_note_included_when_lockfile_present(self):
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}', lockfile_exists=True)
        client.open_pull_request.return_value = _pr(4, "PR_4")

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,
                {"ignore": set(), "pin": {}, "automerge_patch": False},
            )

        body = client.open_pull_request.call_args.kwargs["body"]
        assert "package-lock.json" in body

    def test_no_updates_returns_none_and_touches_nothing(self):
        client = self._client('{"dependencies": {"lodash": "4.18.0"}}')

        url = bot.check_manifest(
            client, "x/y", "main", "package.json", parse_package_json,
            lambda name, max_major=None: "4.18.0", bump_package_json,  # already current
            {"ignore": set(), "pin": {}, "automerge_patch": False},
        )

        assert url is None
        client.create_branch.assert_not_called()
        client.open_pull_request.assert_not_called()

    def test_missing_manifest_is_skipped_silently(self):
        client = MagicMock()
        client.get_file.side_effect = Exception("404 not found")

        url = bot.check_manifest(
            client, "x/y", "main", "package.json", parse_package_json,
            lambda name, max_major=None: "4.18.0", bump_package_json,
            {"ignore": set(), "pin": {}, "automerge_patch": False},
        )

        assert url is None
