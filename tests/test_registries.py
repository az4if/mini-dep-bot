"""Tests for registries.py's pure version-selection logic. The actual
HTTP lookups aren't exercised here (that needs real network access) —
just the part that decides which version string is "best" given a
list, which is where a real bug would most likely hide.
"""

from registries import _best_version


class TestBestVersion:
    versions = ["1.0.0", "1.2.0", "2.0.0", "2.3.1", "2.4.0-beta.1", "3.0.0"]

    def test_no_cap_picks_highest_stable(self):
        assert _best_version(self.versions) == "3.0.0"

    def test_capped_to_major(self):
        assert _best_version(self.versions, max_major=2) == "2.3.1"
        assert _best_version(self.versions, max_major=1) == "1.2.0"

    def test_capped_to_missing_major_returns_none(self):
        assert _best_version(self.versions, max_major=9) is None

    def test_prereleases_are_skipped(self):
        # 2.4.0-beta.1 is technically > 2.3.1 but must never win
        assert _best_version(["2.3.1", "2.4.0-beta.1"], max_major=2) == "2.3.1"

    def test_invalid_versions_are_ignored_not_fatal(self):
        assert _best_version(["not-a-version", "1.0.0", "also-bad"]) == "1.0.0"

    def test_empty_input(self):
        assert _best_version([]) is None
