"""Tests for security.py, mocking the OSV.dev HTTP call so no network
access is needed.
"""

from unittest.mock import patch

import security


class TestVulnIds:
    def test_returns_ids_for_known_vulnerable_version(self):
        with patch.object(security, "_query_osv", return_value=[{"id": "GHSA-aaaa"}, {"id": "PYSEC-2018-1"}]):
            ids = security.vuln_ids("requirements.txt", "requests", "==2.6.0")
        assert ids == ["GHSA-aaaa", "PYSEC-2018-1"]

    def test_unsupported_manifest_returns_empty_without_network_call(self):
        with patch.object(security, "_query_osv") as mock_query:
            assert security.vuln_ids("some-unknown-manifest", "x", "1.0.0") == []
        mock_query.assert_not_called()

    def test_network_failure_returns_empty_not_raises(self):
        import requests
        with patch.object(security, "_query_osv", side_effect=requests.RequestException("boom")):
            assert security.vuln_ids("requirements.txt", "requests", "1.0.0") == []


class TestFixedVulnerabilities:
    def test_only_reports_vulns_resolved_by_the_bump(self):
        def fake_query(ecosystem, name, version):
            if version == "2.6.0":
                return [{"id": "GHSA-old-1"}, {"id": "GHSA-still-present"}]
            if version == "2.31.0":
                return [{"id": "GHSA-still-present"}]  # not actually fixed by this bump
            return []

        with patch.object(security, "_query_osv", side_effect=fake_query):
            fixed = security.fixed_vulnerabilities("requirements.txt", "requests", "2.6.0", "2.31.0")
        assert fixed == ["GHSA-old-1"]

    def test_no_current_vulns_skips_second_lookup(self):
        with patch.object(security, "_query_osv", return_value=[]) as mock_query:
            fixed = security.fixed_vulnerabilities("requirements.txt", "safe-pkg", "1.0.0", "2.0.0")
        assert fixed == []
        mock_query.assert_called_once()  # never bothered checking `latest`
