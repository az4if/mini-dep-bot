"""
Functions to look up the latest published version of a package from
public package registries: npm, PyPI, and the Go module proxy.

Each function returns a version string, or None if the lookup failed
(package not found, network error, unexpected response shape, etc).
"""

import requests
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


def latest_npm_version(package: str) -> str | None:
    """Latest 'dist-tags.latest' version of an npm package."""
    url = f"{NPM_REGISTRY}/{package}"
    try:
        data = _get_json(url)
        return data.get("dist-tags", {}).get("latest")
    except requests.RequestException:
        return None


def latest_pypi_version(package: str) -> str | None:
    """Latest version of a PyPI package, from the PyPI JSON API."""
    url = f"{PYPI_REGISTRY}/{package}/json"
    try:
        data = _get_json(url)
        return data.get("info", {}).get("version")
    except requests.RequestException:
        return None


def latest_go_version(module: str) -> str | None:
    """Latest version of a Go module, from the Go module proxy.

    Module paths must be lowercased per the module proxy spec
    (https://go.dev/ref/mod#module-proxy).
    """
    url = f"{GO_PROXY}/{module.lower()}/@latest"
    try:
        data = _get_json(url)
        return data.get("Version")
    except requests.RequestException:
        return None
