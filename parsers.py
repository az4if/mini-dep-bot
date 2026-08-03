"""
Parsers and editors for dependency manifest files:
package.json, requirements.txt, and go.mod.

Each manifest type has:
  - a parse_* function: manifest content -> {name: current_version}
  - a bump_* function: (content, name, new_version) -> updated content
plus shared version-comparison helpers.
"""

import json
import re

from packaging.version import InvalidVersion, Version


# ---------------------------------------------------------------- package.json

def parse_package_json(content: str) -> dict:
    """Return {name: version} for dependencies + devDependencies."""
    data = json.loads(content)
    deps = {}
    for section in ("dependencies", "devDependencies"):
        deps.update(data.get(section, {}))
    return deps


def bump_package_json(content: str, name: str, new_version: str) -> str:
    """Update the version for `name`, preserving any ^/~ range prefix."""
    data = json.loads(content)
    for section in ("dependencies", "devDependencies"):
        if name in data.get(section, {}):
            old = data[section][name]
            prefix = re.match(r"^[^\d]*", old).group()
            data[section][name] = f"{prefix}{new_version}"
    return json.dumps(data, indent=2) + "\n"


# ------------------------------------------------------------ requirements.txt

_REQ_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)==([A-Za-z0-9_.\-]+)")


def parse_requirements_txt(content: str) -> dict:
    """Return {name: version} for exact ('==') pinned requirements.

    Lines using other operators (>=, ~=, etc.) or unpinned lines are
    left alone — this bot only rewrites exact pins, to stay predictable.
    """
    deps = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQ_LINE.match(line)
        if match:
            deps[match.group(1)] = match.group(2)
    return deps


def bump_requirements_txt(content: str, name: str, new_version: str) -> str:
    out = []
    for line in content.splitlines():
        if re.match(rf"^{re.escape(name)}==", line.strip()):
            out.append(f"{name}=={new_version}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------- go.mod

def parse_go_mod(content: str) -> dict:
    """Return {module_path: version} for entries in `require` statements
    (both single-line `require x v1.2.3` and `require ( ... )` blocks).
    """
    deps = {}
    in_block = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("require (") :
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue

        candidate = line if in_block else (
            line[len("require "):].strip() if line.startswith("require ") else None
        )
        if not candidate:
            continue

        parts = candidate.split()
        if len(parts) >= 2 and parts[1].startswith("v"):
            deps[parts[0]] = parts[1]
    return deps


def bump_go_mod(content: str, module: str, new_version: str) -> str:
    out = []
    for line in content.splitlines():
        if module in line and re.search(r"v\d+\.\d+\.\d+", line):
            out.append(re.sub(r"v\d+\.\d+\.\d+\S*", new_version, line, count=1))
        else:
            out.append(line)
    return "\n".join(out) + "\n"


# --------------------------------------------------------- version comparison

def _strip_prefix(version: str) -> str:
    """Strip leading non-digit characters, e.g. 'v', '^', '~', '>='."""
    return re.sub(r"^[^\d]*", "", version)


def _parse_version(version: str) -> Version | None:
    """Parse a version string via `packaging`, tolerating a leading
    'v'/'^'/'~'/'>=' the way this project's manifests use them.
    Returns None if it still isn't a valid PEP 440 / semver-ish version.
    """
    try:
        return Version(_strip_prefix(version))
    except InvalidVersion:
        return None


def _version_tuple(version: str) -> tuple:
    """Fallback comparator for version strings `packaging` can't parse
    (e.g. Go's 'v1.2.3-0.2024...' pseudo-versions). '1.2.3' -> (1, 2, 3);
    non-numeric trailing parts are ignored.
    """
    clean = _strip_prefix(version)
    parts = re.split(r"[.\-+]", clean)
    nums = []
    for part in parts:
        m = re.match(r"\d+", part)
        if m:
            nums.append(int(m.group()))
        else:
            break
    return tuple(nums) if nums else (0,)


def is_outdated(current: str, latest: str) -> bool:
    """True if `latest` is a strictly newer version than `current`.

    Uses proper PEP 440 / semver comparison (via `packaging`) when both
    versions parse cleanly — this correctly handles pre-releases like
    `2.0.0rc1` and build metadata, rather than the previous purely
    numeric comparator. Falls back to numeric-segment comparison for
    version strings `packaging` can't parse (e.g. some Go pseudo-versions).
    """
    if not current or not latest:
        return False

    current_v = _parse_version(current)
    latest_v = _parse_version(latest)
    if current_v is not None and latest_v is not None:
        return latest_v > current_v

    return _version_tuple(latest) > _version_tuple(current)
