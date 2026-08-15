"""Tests for config.py's .mini-dep-bot.yml loading, using a fake
GitHubClient so no network access is needed.
"""

from unittest.mock import MagicMock

from config import load_config


def _client_with_file(content):
    client = MagicMock()
    client.get_file.return_value = (content, "sha")
    return client


_DEFAULTS = {
    "ignore": set(), "pin": {}, "automerge_patch": False,
    "combined_pr": False, "exclude_paths": set(), "warnings": [],
}


class TestLoadConfig:
    def test_missing_file_returns_defaults(self):
        client = MagicMock()
        client.get_file.side_effect = Exception("404")
        assert load_config(client, "x/y", "main") == _DEFAULTS

    def test_ignore_and_pin(self):
        client = _client_with_file("ignore:\n  - noisy-pkg\npin:\n  some-pkg: 2\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["ignore"] == {"noisy-pkg"}
        assert cfg["pin"] == {"some-pkg": 2}
        assert cfg["automerge_patch"] is False
        assert cfg["combined_pr"] is False
        assert cfg["warnings"] == []

    def test_automerge_aliases(self):
        for value, expected in [("patch", True), ("true", True), ("yes", True), ("false", False), ("", False)]:
            client = _client_with_file(f"automerge: {value}\n" if value else "ignore: []\n")
            assert load_config(client, "x/y", "main")["automerge_patch"] is expected

    def test_combined_pr_aliases(self):
        for value, expected in [("true", True), ("yes", True), ("false", False), ("", False)]:
            client = _client_with_file(f"combined_pr: {value}\n" if value else "ignore: []\n")
            assert load_config(client, "x/y", "main")["combined_pr"] is expected

    def test_combined_pr_yaml_boolean_true(self):
        # YAML parses bare `true` as an actual bool, not the string "true"
        client = _client_with_file("combined_pr: true\n")
        assert load_config(client, "x/y", "main")["combined_pr"] is True

    def test_exclude_paths_strips_trailing_slash(self):
        client = _client_with_file("exclude_paths:\n  - examples/\n  - legacy-app\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["exclude_paths"] == {"examples", "legacy-app"}

    def test_empty_file(self):
        client = _client_with_file("")
        assert load_config(client, "x/y", "main") == _DEFAULTS

    def test_defaults_are_independent_across_calls(self):
        # Guards against a shared-mutable-default bug: mutating one
        # call's result must never leak into another call's defaults.
        client_bad = _client_with_file("ignore: not-a-list\n")
        client_good = _client_with_file("ignore:\n  - real-pkg\n")

        cfg1 = load_config(client_bad, "x/y", "main")
        cfg2 = load_config(client_good, "x/y", "main")
        cfg1["ignore"].add("should-not-leak")

        assert "should-not-leak" not in cfg2["ignore"]


class TestConfigWarnings:
    def test_malformed_pin_entry_is_skipped_and_warned(self):
        client = _client_with_file("pin:\n  some-pkg: two\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["pin"] == {}
        assert len(cfg["warnings"]) == 1
        assert "some-pkg" in cfg["warnings"][0]

    def test_ignore_as_bare_string_is_rejected_not_char_split(self):
        # Regression test: `ignore: some-pkg` (a bare string instead of
        # a list) must never silently become a set of individual
        # characters via `set("some-pkg")`.
        client = _client_with_file("ignore: some-noisy-package\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["ignore"] == set()
        assert len(cfg["warnings"]) == 1
        assert "ignore" in cfg["warnings"][0]

    def test_exclude_paths_as_bare_string_is_rejected(self):
        client = _client_with_file("exclude_paths: examples\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["exclude_paths"] == set()
        assert len(cfg["warnings"]) == 1

    def test_pin_wrong_type_is_rejected(self):
        client = _client_with_file("pin: yes\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["pin"] == {}
        assert len(cfg["warnings"]) == 1

    def test_automerge_unrecognized_value_is_warned(self):
        client = _client_with_file("automerge: yaaas\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["automerge_patch"] is False
        assert len(cfg["warnings"]) == 1

    def test_combined_pr_unrecognized_value_is_warned(self):
        client = _client_with_file("combined_pr: yse\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["combined_pr"] is False
        assert len(cfg["warnings"]) == 1

    def test_non_mapping_top_level_is_rejected_wholesale(self):
        client = _client_with_file("- just\n- a\n- list\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["ignore"] == set()
        assert len(cfg["warnings"]) == 1

    def test_valid_config_has_no_warnings(self):
        client = _client_with_file(
            "ignore:\n  - foo\npin:\n  bar: 2\nautomerge: patch\ncombined_pr: true\n"
        )
        cfg = load_config(client, "x/y", "main")
        assert cfg["warnings"] == []

    def test_multiple_malformed_entries_each_produce_a_warning(self):
        client = _client_with_file("pin:\n  a: one\n  b: 2\n  c: three\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["pin"] == {"b": 2}
        assert len(cfg["warnings"]) == 2
