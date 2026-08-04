"""
Best-effort security-advisory lookups against OSV.dev
(https://osv.dev/docs), used to annotate a PR body when a version
bump also happens to close a known vulnerability.

This is decoration for a PR, never a gate on whether an update
happens: every function here swallows network/parsing failures and
returns an empty result rather than raising, so a flaky OSV lookup
can never block or fail a dependency bump.
"""

import re

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

OSV_API = "https://api.osv.dev/v1/query"
TIMEOUT = 10

# Maps this bot's manifest path constants (as used in bot.py's
# MANIFESTS list) to the ecosystem names OSV's schema expects
# (https://ossf.github.io/osv-schema/#affectedpackage-field).
OSV_ECOSYSTEMS = {
    "package.json": "npm",
    "requirements.txt": "PyPI",
    "pyproject.toml": "PyPI",
    "go.mod": "Go",
    "Cargo.toml": "crates.io",
    "Gemfile": "RubyGems",
    "composer.json": "Packagist",
}

_retry_network = retry(
    reraise=True,
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=2),
    retry=retry_if_exception_type(requests.RequestException),
)


@_retry_network
def _query_osv(ecosystem: str, name: str, version: str) -> list:
    resp = requests.post(
        OSV_API,
        json={"package": {"name": name, "ecosystem": ecosystem}, "version": version},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("vulns", [])


def _bare_version(version: str) -> str:
    """Strip a leading pin operator/prefix (^, ~, >=, ~=, v, ...) —
    OSV wants a plain version, not this project's stored spec string.
    """
    return re.sub(r"^[^\d]*", "", version)


def vuln_ids(manifest_path: str, name: str, version: str) -> list:
    """IDs of known vulnerabilities affecting this exact package at
    this exact version, or [] on no findings or any failure (network
    error, unsupported ecosystem, unparseable response).
    """
    ecosystem = OSV_ECOSYSTEMS.get(manifest_path)
    if not ecosystem:
        return []
    try:
        vulns = _query_osv(ecosystem, name, _bare_version(version))
    except requests.RequestException:
        return []
    return sorted({v.get("id") for v in vulns if v.get("id")})


def fixed_vulnerabilities(manifest_path: str, name: str, current: str, latest: str) -> list:
    """IDs of vulnerabilities that affect `current` but not `latest` —
    i.e. genuinely resolved by bumping to `latest`, not just any
    advisory that happens to mention the package.
    """
    current_vulns = set(vuln_ids(manifest_path, name, current))
    if not current_vulns:
        return []
    latest_vulns = set(vuln_ids(manifest_path, name, latest))
    return sorted(current_vulns - latest_vulns)
