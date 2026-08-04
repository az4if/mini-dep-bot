"""
Functions to look up the latest published version of a package from
public package registries: npm, PyPI, the Go module proxy, crates.io,
RubyGems, and Packagist.

Each `latest_*_version` function returns a version string, or None if
the lookup failed (package not found, network error, unexpected
response shape, etc), and accepts an optional `max_major` — when set,
the "latest" returned is capped to that major version instead of the
registry's overall newest release. This backs the `pin:` config in
`.mini-dep-bot.yml` (see config.py), so a package can be kept off a
major bump while still picking up minor/patch fixes within its
current major version.

Results are cached for the lifetime of one bot run (`lru_cache`) since
the same package can legitimately show up in more than one manifest
(e.g. a monorepo with several `requirements.txt` files) and there's no
reason to look it up twice in a single run.

`homepage_url` is a separate, best-effort lookup used only when
building a PR body — it never raises and never blocks an update from
happening if it fails.
"""

from functools import lru_cache

import requests
from packaging.version import InvalidVersion, Version
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

NPM_REGISTRY = "https://registry.npmjs.org"
PYPI_REGISTRY = "https://pypi.org/pypi"
GO_PROXY = "https://proxy.golang.org"
CRATES_REGISTRY = "https://crates.io/api/v1/crates"
RUBYGEMS_REGISTRY = "https://rubygems.org/api/v1"
PACKAGIST_REGISTRY = "https://repo.packagist.org/p2"

# crates.io asks API clients to identify themselves with a descriptive
# User-Agent (https://crates.io/policies#crawlers) rather than a
# generic one; using requests' default gets throttled/blocked.
CRATES_USER_AGENT = "mini-dep-bot (contact: az4if@proton.me)"

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
def _get_json(url: str, headers: dict | None = None) -> dict:
    resp = requests.get(url, timeout=TIMEOUT, headers=headers)
    resp.raise_for_status()
    return resp.json()


@_retry_network
def _get_text(url: str) -> str:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _best_version(version_strings, max_major: int | None = None) -> str | None:
    """Highest stable version in `version_strings`, optionally capped
    to major version `max_major`. Pre-releases are always skipped so
    this never surfaces e.g. a release candidate as "latest".
    """
    best_parsed = None
    best_raw = None
    for raw in version_strings:
        try:
            parsed = Version(raw)
        except InvalidVersion:
            continue
        if parsed.is_prerelease:
            continue
        if max_major is not None and parsed.major != max_major:
            continue
        if best_parsed is None or parsed > best_parsed:
            best_parsed, best_raw = parsed, raw
    return best_raw


@lru_cache(maxsize=None)
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
    return _best_version(data.get("versions", {}).keys(), max_major)


@lru_cache(maxsize=None)
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
    return _best_version(data.get("releases", {}).keys(), max_major)


@lru_cache(maxsize=None)
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
        return _best_version(versions, max_major)
    except requests.RequestException:
        return None


@lru_cache(maxsize=None)
def latest_crate_version(package: str, max_major: int | None = None) -> str | None:
    """Latest stable version of a Rust crate from crates.io, or the
    highest non-yanked version within `max_major` when a cap is given.
    """
    url = f"{CRATES_REGISTRY}/{package}"
    try:
        data = _get_json(url, headers={"User-Agent": CRATES_USER_AGENT})
    except requests.RequestException:
        return None
    if max_major is None:
        crate = data.get("crate", {})
        return crate.get("max_stable_version") or crate.get("newest_version")
    versions = [
        v.get("num") for v in data.get("versions", [])
        if v.get("num") and not v.get("yanked")
    ]
    return _best_version(versions, max_major)


@lru_cache(maxsize=None)
def latest_rubygems_version(package: str, max_major: int | None = None) -> str | None:
    """Latest version of a Ruby gem from RubyGems, or the highest
    non-prerelease version within `max_major` when a cap is given.
    """
    try:
        if max_major is None:
            data = _get_json(f"{RUBYGEMS_REGISTRY}/gems/{package}.json")
            return data.get("version")
        data = _get_json(f"{RUBYGEMS_REGISTRY}/versions/{package}.json")
    except requests.RequestException:
        return None
    versions = [
        v.get("number") for v in data
        if isinstance(v, dict) and v.get("number") and not v.get("prerelease")
    ]
    return _best_version(versions, max_major)


@lru_cache(maxsize=None)
def latest_packagist_version(package: str, max_major: int | None = None) -> str | None:
    """Latest stable version of a PHP package from Packagist, or the
    highest version within `max_major` when a cap is given.

    `package` is the full `vendor/name` Packagist identifier, taken
    directly from composer.json's `require`/`require-dev` keys.
    """
    url = f"{PACKAGIST_REGISTRY}/{package}.json"
    try:
        data = _get_json(url)
    except requests.RequestException:
        return None
    entries = data.get("packages", {}).get(package, [])
    versions = []
    for entry in entries:
        raw = entry.get("version", "")
        if not raw or raw.startswith("dev-") or raw.endswith("-dev"):
            continue  # branch aliases, not real releases
        versions.append(raw[1:] if raw[:1] in ("v", "V") else raw)
    return _best_version(versions, max_major)


def homepage_url(ecosystem: str, package: str) -> str | None:
    """Best-effort repo/homepage URL for `package` in `ecosystem`, for
    linking out from a PR body. `ecosystem` is one of the manifest
    path constants bot.py uses as MANIFESTS keys ("package.json",
    "requirements.txt", etc). Returns None on any failure — this is
    decoration for a PR body, never something an update should block on.
    """
    try:
        if ecosystem == "package.json":
            data = _get_json(f"{NPM_REGISTRY}/{package}")
            repo = data.get("repository")
            url = data.get("homepage") or (repo.get("url") if isinstance(repo, dict) else repo)
        elif ecosystem in ("requirements.txt", "pyproject.toml"):
            data = _get_json(f"{PYPI_REGISTRY}/{package}/json")
            info = data.get("info", {})
            project_urls = info.get("project_urls") or {}
            url = (
                project_urls.get("Homepage") or project_urls.get("Source")
                or project_urls.get("Repository") or info.get("home_page")
            )
        elif ecosystem == "go.mod":
            host = package.split("/")[0]
            url = f"https://{package}" if "." in host else None
        elif ecosystem == "Cargo.toml":
            data = _get_json(f"{CRATES_REGISTRY}/{package}", headers={"User-Agent": CRATES_USER_AGENT})
            crate = data.get("crate", {})
            url = crate.get("repository") or crate.get("homepage")
        elif ecosystem == "Gemfile":
            data = _get_json(f"{RUBYGEMS_REGISTRY}/gems/{package}.json")
            url = data.get("source_code_uri") or data.get("homepage_uri")
        elif ecosystem == "composer.json":
            data = _get_json(f"{PACKAGIST_REGISTRY}/{package}.json")
            entries = data.get("packages", {}).get(package, [])
            source = entries[0].get("source") if entries else None
            url = source.get("url") if isinstance(source, dict) else None
        else:
            url = None
    except requests.RequestException:
        return None

    if not url:
        return None
    return url.removeprefix("git+").removesuffix(".git")
