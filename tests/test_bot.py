"""Tests for bot.py's orchestration logic: find_updates (ignore/pin
filtering) and check_manifest (branch/PR creation, existing-PR reuse,
labels, and auto-merge gating), all against a mocked GitHubClient so
no network or GitHub token is needed.
"""

from unittest.mock import MagicMock, patch

import bot
from parsers import parse_package_json, bump_package_json


class TestIsExcludedPath:
    def test_builtin_noise_directories_are_excluded(self):
        assert bot._is_excluded_path("node_modules/some-pkg/package.json", set()) is True
        assert bot._is_excluded_path("apps/web/vendor/thing/composer.json", set()) is True
        assert bot._is_excluded_path("backend/.venv/lib/x/pyproject.toml", set()) is True

    def test_normal_nested_path_is_not_excluded(self):
        assert bot._is_excluded_path("apps/web/package.json", set()) is False
        assert bot._is_excluded_path("package.json", set()) is False

    def test_config_exclude_paths_matches_prefix(self):
        excludes = {"examples", "legacy-app"}
        assert bot._is_excluded_path("examples/demo/package.json", excludes) is True
        assert bot._is_excluded_path("legacy-app/Gemfile", excludes) is True
        assert bot._is_excluded_path("apps/web/package.json", excludes) is False

    def test_config_exclude_paths_matches_exact_file(self):
        assert bot._is_excluded_path("tools/one-off/package.json", {"tools/one-off/package.json"}) is True


class TestSiblingPath:
    def test_nested_manifest(self):
        assert bot._sibling_path("apps/web/package.json", "package-lock.json") == "apps/web/package-lock.json"

    def test_root_manifest(self):
        assert bot._sibling_path("package.json", "package-lock.json") == "package-lock.json"

    def test_deeply_nested(self):
        assert bot._sibling_path("a/b/c/Gemfile", "Gemfile.lock") == "a/b/c/Gemfile.lock"


class TestDiscoverManifestPaths:
    def test_finds_nested_manifests_across_the_repo(self):
        client = MagicMock()
        client.list_tree.return_value = [
            "package.json",
            "apps/web/package.json",
            "apps/api/requirements.txt",
            "README.md",
        ]
        config = {"exclude_paths": set()}
        found = bot.discover_manifest_paths(client, "x/y", "main", config)

        assert found["package.json"] == ["apps/web/package.json", "package.json"]
        assert found["requirements.txt"] == ["apps/api/requirements.txt"]
        assert found["go.mod"] == []

    def test_excludes_noise_directories_by_default(self):
        client = MagicMock()
        client.list_tree.return_value = [
            "package.json",
            "node_modules/some-dep/package.json",
            "vendor/some-lib/composer.json",
        ]
        config = {"exclude_paths": set()}
        found = bot.discover_manifest_paths(client, "x/y", "main", config)

        assert found["package.json"] == ["package.json"]
        assert found["composer.json"] == []

    def test_respects_config_exclude_paths(self):
        client = MagicMock()
        client.list_tree.return_value = ["package.json", "examples/demo/package.json"]
        config = {"exclude_paths": {"examples"}}
        found = bot.discover_manifest_paths(client, "x/y", "main", config)

        assert found["package.json"] == ["package.json"]

    def test_falls_back_to_root_only_when_tree_listing_fails(self):
        client = MagicMock()
        client.list_tree.side_effect = Exception("boom")
        config = {"exclude_paths": set()}
        found = bot.discover_manifest_paths(client, "x/y", "main", config)

        assert found["package.json"] == ["package.json"]
        assert found["Gemfile"] == ["Gemfile"]


class TestFindLockfile:
    def test_returns_first_matching_candidate(self):
        client = MagicMock()
        # package-lock.json missing, yarn.lock present
        client.file_exists.side_effect = [False, True]
        result = bot._find_lockfile(client, "x/y", "main", "package.json", "package.json")
        assert result == ("yarn.lock", "yarn install --mode update-lockfile")

    def test_checks_sibling_directory_for_nested_manifest(self):
        client = MagicMock()
        client.file_exists.return_value = True
        result = bot._find_lockfile(client, "x/y", "main", "apps/web/package.json", "package.json")
        assert result[0] == "apps/web/package-lock.json"
        client.file_exists.assert_called_with("x/y", "apps/web/package-lock.json", "main")

    def test_none_when_no_candidate_exists(self):
        client = MagicMock()
        client.file_exists.return_value = False
        assert bot._find_lockfile(client, "x/y", "main", "package.json", "package.json") is None

    def test_none_for_manifest_type_with_no_lockfile_mapping(self):
        client = MagicMock()
        assert bot._find_lockfile(client, "x/y", "main", "requirements.txt", "requirements.txt") is None
        client.file_exists.assert_not_called()


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

    def test_updates_log_records_branch_and_lockfile_for_workflow_step(self):
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}', lockfile_exists=True)
        client.open_pull_request.return_value = _pr(5, "PR_5")
        updates_log = []

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            bot.check_manifest(
                client, "x/y", "main", "package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,
                {"ignore": set(), "pin": {}, "automerge_patch": False},
                updates_log=updates_log,
            )

        assert updates_log == [{
            "path": "package.json",
            "branch": "mini-dep-bot/package-json/updates",
            "lockfile": "package-lock.json",
        }]

    def test_updates_log_untouched_when_nothing_to_update(self):
        client = self._client('{"dependencies": {"lodash": "4.18.0"}}')
        updates_log = []

        bot.check_manifest(
            client, "x/y", "main", "package.json", parse_package_json,
            lambda name, max_major=None: "4.18.0", bump_package_json,  # already current
            {"ignore": set(), "pin": {}, "automerge_patch": False},
            updates_log=updates_log,
        )

        assert updates_log == []

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

    def test_nested_monorepo_path_uses_manifest_type_for_ecosystem_lookups(self):
        # path is nested (apps/web/...) but manifest_type is the plain
        # basename — labels, lockfile candidates, and the branch name
        # should all come out right for both.
        client = self._client('{"dependencies": {"lodash": "4.17.0"}}', lockfile_exists=True)
        client.open_pull_request.return_value = _pr(11, "PR_11")

        with patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.check_manifest(
                client, "x/y", "main", "apps/web/package.json", parse_package_json,
                lambda name, max_major=None: "4.18.0", bump_package_json,
                {"ignore": set(), "pin": {}, "automerge_patch": False},
                manifest_type="package.json",
            )

        assert url == "https://github.com/x/y/pull/11"
        # branch name is derived from the full nested path
        branch_arg = client.create_branch.call_args.args[1]
        assert branch_arg == "mini-dep-bot/apps/web/package-json/updates"
        # label lookup used manifest_type, not the nested path
        client.add_labels.assert_called_once_with("x/y", 11, ["dependencies", "npm"])
        # lockfile note references the nested lockfile path
        body = client.open_pull_request.call_args.kwargs["body"]
        assert "apps/web/package-lock.json" in body


def _get_file_side_effect(content_map):
    """A client.get_file side_effect that serves fixed content per
    manifest path regardless of which ref/branch is asked for (as if
    a freshly created branch has no prior differences from base), and
    raises for any path not in the map (manifest not present).
    """
    def _get_file(repo, path, ref):
        if path not in content_map:
            raise Exception("404 not found")
        return content_map[path], f"sha-{path}"
    return _get_file


class TestRunCombined:
    def _config(self, **overrides):
        return {"ignore": set(), "pin": {}, "automerge_patch": False, "combined_pr": True, **overrides}

    def test_bundles_multiple_manifests_into_one_pr(self):
        from parsers import parse_requirements_txt, bump_requirements_txt

        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "package.json": '{"dependencies": {"lodash": "4.17.0"}}',
            "requirements.txt": "requests==2.31.0\n",
        })
        client.find_open_pr.return_value = None
        client.get_ref_sha.return_value = "ref-sha"
        client.file_exists.return_value = False
        client.open_pull_request.return_value = _pr(10, "PR_10")

        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.18.0", bump_package_json),
            ("requirements.txt", parse_requirements_txt, lambda name, max_major=None: "2.34.2", bump_requirements_txt),
        ]

        with patch.object(bot, "MANIFESTS", test_manifests), \
             patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.run_combined(client, "x/y", "main", self._config())

        assert url == "https://github.com/x/y/pull/10"
        assert client.update_file.call_count == 2  # one commit per touched manifest
        client.open_pull_request.assert_called_once()
        client.create_branch.assert_called_once()
        assert client.create_branch.call_args.args[1] == "mini-dep-bot/all-updates"

        body = client.open_pull_request.call_args.kwargs["body"]
        assert "`package.json`" in body and "`requirements.txt`" in body

        labels = client.add_labels.call_args.args[2]
        assert set(labels) == {"dependencies", "npm", "python"}

    def test_bundles_nested_monorepo_manifests_via_manifest_paths(self):
        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "apps/web/package.json": '{"dependencies": {"lodash": "4.17.0"}}',
            "apps/api/package.json": '{"dependencies": {"express": "4.17.0"}}',
        })
        client.find_open_pr.return_value = None
        client.get_ref_sha.return_value = "ref-sha"
        client.file_exists.return_value = False
        client.open_pull_request.return_value = _pr(40, "PR_40")

        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.18.0", bump_package_json),
        ]
        manifest_paths = {"package.json": ["apps/api/package.json", "apps/web/package.json"]}

        with patch.object(bot, "MANIFESTS", test_manifests), \
             patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.run_combined(
                client, "x/y", "main", self._config(), manifest_paths=manifest_paths,
            )

        assert url == "https://github.com/x/y/pull/40"
        assert client.update_file.call_count == 2  # one commit per nested manifest
        body = client.open_pull_request.call_args.kwargs["body"]
        assert "`apps/api/package.json`" in body and "`apps/web/package.json`" in body

    def test_no_updates_anywhere_returns_none(self):
        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "package.json": '{"dependencies": {"lodash": "4.18.0"}}',
        })
        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.18.0", bump_package_json),
        ]

        with patch.object(bot, "MANIFESTS", test_manifests):
            url = bot.run_combined(client, "x/y", "main", self._config())

        assert url is None
        client.create_branch.assert_not_called()

    def test_reuses_existing_open_pr(self):
        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "package.json": '{"dependencies": {"lodash": "4.17.0"}}',
        })
        client.find_open_pr.return_value = _pr(20, "PR_20")
        client.get_ref_sha.return_value = "ref-sha"
        client.file_exists.return_value = False
        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.18.0", bump_package_json),
        ]

        with patch.object(bot, "MANIFESTS", test_manifests), \
             patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            url = bot.run_combined(client, "x/y", "main", self._config())

        assert url == "https://github.com/x/y/pull/20"
        client.open_pull_request.assert_not_called()

    def test_automerge_only_when_every_manifest_is_patch_level(self):
        from parsers import parse_requirements_txt, bump_requirements_txt

        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "package.json": '{"dependencies": {"lodash": "4.17.0"}}',
            "requirements.txt": "requests==2.31.0\n",
        })
        client.find_open_pr.return_value = None
        client.get_ref_sha.return_value = "ref-sha"
        client.file_exists.return_value = False
        client.open_pull_request.return_value = _pr(30, "PR_30")

        # package.json gets a patch bump, requirements.txt gets a minor bump
        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.17.9", bump_package_json),
            ("requirements.txt", parse_requirements_txt, lambda name, max_major=None: "2.32.0", bump_requirements_txt),
        ]

        with patch.object(bot, "MANIFESTS", test_manifests), \
             patch("bot.homepage_url", return_value=None), \
             patch("bot.security.fixed_vulnerabilities", return_value=[]):
            bot.run_combined(client, "x/y", "main", self._config(automerge_patch=True))

        client.enable_auto_merge.assert_not_called()  # one manifest wasn't patch-level

    def test_dry_run_touches_nothing(self):
        client = MagicMock()
        client.get_file.side_effect = _get_file_side_effect({
            "package.json": '{"dependencies": {"lodash": "4.17.0"}}',
        })
        client.find_open_pr.return_value = None
        test_manifests = [
            ("package.json", parse_package_json, lambda name, max_major=None: "4.18.0", bump_package_json),
        ]

        with patch.object(bot, "MANIFESTS", test_manifests):
            result = bot.run_combined(client, "x/y", "main", self._config(), dry_run=True)

        assert result is not None
        client.create_branch.assert_not_called()
        client.update_file.assert_not_called()
        client.open_pull_request.assert_not_called()


class TestWriteStepSummary:
    def test_writes_nothing_outside_github_actions(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        # Should simply return without raising, and without needing a real file.
        bot._write_step_summary(
            "x/y", "main", False,
            {"ignore": set(), "pin": {}, "automerge_patch": False, "combined_pr": False},
            [],
        )

    def test_writes_summary_when_env_var_set(self, tmp_path, monkeypatch):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

        bot._write_step_summary(
            "x/y", "main", False,
            {"ignore": {"noisy-pkg"}, "pin": {"some-pkg": 2}, "automerge_patch": True, "combined_pr": False},
            ["https://github.com/x/y/pull/1", "https://github.com/x/y/pull/2"],
        )

        content = summary_file.read_text()
        assert "x/y" in content and "main" in content
        assert "noisy-pkg" in content
        assert "some-pkg → v2" in content
        assert "Auto-merge: patch-only bumps" in content
        assert "https://github.com/x/y/pull/1" in content
        assert "https://github.com/x/y/pull/2" in content

    def test_up_to_date_message_when_no_prs(self, tmp_path, monkeypatch):
        summary_file = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

        bot._write_step_summary(
            "x/y", "main", False,
            {"ignore": set(), "pin": {}, "automerge_patch": False, "combined_pr": False},
            [],
        )

        assert "up to date" in summary_file.read_text()
