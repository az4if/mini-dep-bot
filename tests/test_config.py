"""Tests for config.py's .mini-dep-bot.yml loading, using a fake
GitHubClient so no network access is needed.
"""

from unittest.mock import MagicMock

from config import load_config


def _client_with_file(content):
    client = MagicMock()
    client.get_file.return_value = (content, "sha")
    return client


class TestLoadConfig:
    def test_missing_file_returns_defaults(self):
        client = MagicMock()
        client.get_file.side_effect = Exception("404")
        cfg = load_config(client, "x/y", "main")
        assert cfg == {"ignore": set(), "pin": {}, "automerge_patch": False}

    def test_ignore_and_pin(self):
        client = _client_with_file("ignore:\n  - noisy-pkg\npin:\n  some-pkg: 2\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["ignore"] == {"noisy-pkg"}
        assert cfg["pin"] == {"some-pkg": 2}
        assert cfg["automerge_patch"] is False

    def test_automerge_aliases(self):
        for value, expected in [("patch", True), ("true", True), ("yes", True), ("false", False), ("", False)]:
            client = _client_with_file(f"automerge: {value}\n" if value else "ignore: []\n")
            assert load_config(client, "x/y", "main")["automerge_patch"] is expected

    def test_malformed_pin_entry_is_skipped_not_fatal(self):
        client = _client_with_file("pin:\n  some-pkg: not-a-number\n")
        cfg = load_config(client, "x/y", "main")
        assert cfg["pin"] == {}

    def test_empty_file(self):
        client = _client_with_file("")
        assert load_config(client, "x/y", "main") == {
            "ignore": set(), "pin": {}, "automerge_patch": False,
        }
