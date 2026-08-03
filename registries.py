"""
Functions to look up the latest published version of a package from
public package registries: npm, PyPI, and the Go module proxy.

Each function returns a version string, or None if the lookup failed
(package not found, network error, unexpected response shape, etc).

Each also accepts an optional `max_major` — when set, the "latest"
returned is capped to that major version instead of the registry's
overall newest release. This backs the `pin:` config in
`.mini-dep-bot.yml` (see config.py), so a package can be kept off a
major bump while still picking up minor/patch fixes within its
current major version.
"""

import requests
from packaging.version import InvalidVersion, Version
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"
GO_PROXY = "https://proxy.golang.org"

TIMEOUT = 10

# Retry transient failures (timeouts, connection errors, 5xx) with
# exponential backoff before giving up; a 404 (package not found) raises
# immediately via raise_for_status and is not retried.
_retry_network = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type(requests.RequestException),
)


@_retry_network
def _get_json(url: str) -> dict:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


@_retry_network
def _get_text(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _best_within_major(version_strings, max_major: int) -> str | None:
    """Highest stable version in `version_strings` whose major segment
    equals `max_major`, or None if there isn't one. Pre-releases are
    skipped so a pin never surfaces e.g. a release candidate.
    """
    best_parsed = None
    best_raw = None
    for raw in version_strings:
        try:
            parsed = Version(raw)
        except InvalidVersion:
            continue
        if parsed.is_prerelease or parsed.major != max_major:
            continue
        if best_parsed is None or parsed > best_parsed:
            best_parsed, best_raw = parsed, raw
    return best_raw


def latest_npm_version(package: str, max_major: int | None = None) -> str | None:
    """Latest 'dist-tags.latest' version of an npm package, or the
    highest version within `max_major` when a cap is given."""
    url = f"{NPM_REGISTRY}/{package}"
    try:
        data = _get_json(url)
    except requests.RequestException:
        return None
    if max_major is None:
        return data.get("dist-tags", {}).get("latest")
    return _best_within_major(data.get("versions", {}).keys(), max_major)


def latest_pypi_version(package: str, max_major: int | None = None) -> str | None:
    """Latest version of a PyPI package from the PyPI JSON API, or the
    highest version within `max_major` when a cap is given."""
    url = f"{PYPI_REGISTRY}/{package}/json"
    try:
        data = _get_json(url)
    except requests.RequestException:
        return None
    if max_major is None:
        return data.get("info", {}).get("version")
    return _best_within_major(data.get("releases", {}).keys(), max_major)


def latest_go_version(module: str, max_major: int | None = None) -> str | None:
    """Latest version of a Go module from the Go module proxy, or the
    highest version within `max_major` when a cap is given.

    Module paths must be lowercased per the module proxy spec
    (https://go.dev/ref/mod#module-proxy).
    """
    module = module.lower()
    try:
        if max_major is None:
            data = _get_json(f"{GO_PROXY}/{module}/@latest")
            return data.get("Version")
        text = _get_text(f"{GO_PROXY}/{module}/@v/list")
        versions = [line.strip() for line in text.splitlines() if line.strip()]
        return _best_within_major(versions, max_major)
    except requests.RequestException:
        return None
