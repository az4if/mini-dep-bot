"""
Parsers and editors for dependency manifest files:
package.json, requirements.txt, go.mod, pyproject.toml (Poetry-style
and PEP 621 array style), Cargo.toml, Gemfile, and composer.json.

Each manifest type has:
  - a parse_* function: manifest content -> {name: current_version}
  - a bump_* function: (content, name, new_version) -> updated content
plus shared version-comparison helpers (`is_outdated`, `bump_severity`).

The TOML/JSON-based formats are edited with line-based regex matching
(TOML) or a parse/re-serialize round trip (JSON), same philosophy as
requirements.txt and go.mod: it's a few lines of regex instead of a
dependency on a TOML writer, and it can't reformat or drop comments
elsewhere in the file the way a full TOML parse-then-serialize would.
JSON has no comments to lose, so package.json/composer.json just
round-trip through `json`.
"""

import json
import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# Matches a TOML section header line, e.g. "[tool.poetry.dependencies]"
_TOML_SECTION_RE = re.compile(r"^\[.*\]\s*$")

# Matches a simple `name = "version"` TOML entry.
_TOML_SIMPLE_RE = re.compile(r'^([A-Za-z0-9_.\-]+)\s*=\s*"([^"]*)"')

# Matches an inline-table entry with a version field, e.g.
# `requests = { version = "^2.31.0", extras = ["async"] }`
_TOML_TABLE_VERSION_RE = re.compile(r'^([A-Za-z0-9_.\-]+)\s*=\s*\{[^}]*\bversion\s*=\s*"([^"]*)"')


def _toml_dep_line_match(stripped_line: str):
    """Match either TOML dependency-entry style against one stripped
    line, returning (name, version) or None."""
    m = _TOML_TABLE_VERSION_RE.match(stripped_line) or _TOML_SIMPLE_RE.match(stripped_line)
    return (m.group(1), m.group(2)) if m else None


def _bump_toml_line(line: str, old_version: str, new_version: str) -> str:
    """Replace `old_version` with `new_version` inside a quoted value,
    preserving any non-numeric prefix (^, ~, >=, etc.) and whichever
    quote style (' or ") the line already uses.
    """
    prefix = re.match(r"^[^\d]*", old_version).group()
    replacement = f"{prefix}{new_version}"
    if f'"{old_version}"' in line:
        return line.replace(f'"{old_version}"', f'"{replacement}"', 1)
    if f"'{old_version}'" in line:
        return line.replace(f"'{old_version}'", f"'{replacement}'", 1)
    return line


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

# Only == / >= / ~= are parsed. <=, <, and != represent an explicit
# ceiling or exclusion the person put there on purpose — bumping past
# one would silently violate a constraint they set, so those lines are
# left untouched (same reasoning as config.py's `pin`, just expressed
# in the manifest itself instead of the bot's config file).
_REQ_LINE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|~=)\s*([A-Za-z0-9_.\-]+)")


def parse_requirements_txt(content: str) -> dict:
    """Return {name: "<op><version>"} for ==, >=, and ~= pinned
    requirements. Unpinned lines and other operators are left alone.
    """
    deps = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQ_LINE.match(line)
        if match:
            name, op, version = match.groups()
            deps[name] = f"{op}{version}"
    return deps


def bump_requirements_txt(content: str, name: str, new_version: str) -> str:
    out = []
    for line in content.splitlines():
        match = _REQ_LINE.match(line.strip())
        if match and match.group(1) == name:
            out.append(f"{name}{match.group(2)}{new_version}")
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


# -------------------------------------------------------------- pyproject.toml

_POETRY_SECTION_RE = re.compile(r"^\[tool\.poetry\.(?:dependencies|group\.[\w\-]+\.dependencies)\]\s*$")

# Poetry's "python" entry is a language constraint, not a package —
# never a real dependency to look up or bump.
_PYPROJECT_SKIP_NAMES = {"python"}

# PEP 621 `[project] dependencies = [...]` array style.
_PEP621_DEPS_START_RE = re.compile(r"^dependencies\s*=\s*\[")
_PEP508_ITEM_RE = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(==|>=|~=)\s*([A-Za-z0-9_.\-]+)\s*$")
_QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'')


def _find_pep621_dependencies_block(lines: list):
    """Return (start_idx, end_idx) inclusive line range of a top-level
    `dependencies = [...]` array inside a `[project]` table, or None.
    Handles both a single-line array and one-item-per-line style using
    a bracket-depth count — safe here since none of the string items
    can themselves contain '[' or ']'.
    """
    in_project = False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if _TOML_SECTION_RE.match(stripped):
            in_project = stripped == "[project]"
            continue
        if in_project and _PEP621_DEPS_START_RE.match(stripped):
            depth = 0
            for j in range(i, len(lines)):
                depth += lines[j].count("[") - lines[j].count("]")
                if depth == 0:
                    return i, j
            return i, len(lines) - 1  # unterminated array — best effort
    return None


def _parse_pep508_item(item: str):
    """Parse one PEP 508-ish requirement string into (name, op, version),
    or None if it has no recognized version operator — unpinned, uses
    an operator this simple parser doesn't touch (<, <=, !=), or has
    extras/environment markers this doesn't attempt to understand.
    """
    match = _PEP508_ITEM_RE.match(item.strip())
    return match.groups() if match else None


def _pep621_dependencies(content: str) -> dict:
    lines = content.splitlines()
    block = _find_pep621_dependencies_block(lines)
    if not block:
        return {}
    start, end = block
    deps = {}
    for raw in lines[start:end + 1]:
        for m in _QUOTED_RE.finditer(raw):
            item = m.group(1) if m.group(1) is not None else m.group(2)
            parsed = _parse_pep508_item(item)
            if parsed:
                name, op, version = parsed
                deps[name] = f"{op}{version}"
    return deps


def _bump_pep621_dependencies(content: str, name: str, new_version: str) -> str:
    lines = content.splitlines()
    block = _find_pep621_dependencies_block(lines)
    if not block:
        return content
    start, end = block
    pattern = re.compile(rf'([\'"]){re.escape(name)}\s*(==|>=|~=)\s*[A-Za-z0-9_.\-]+\1')
    for idx in range(start, end + 1):
        if pattern.search(lines[idx]):
            lines[idx] = pattern.sub(
                lambda m: f"{m.group(1)}{name}{m.group(2)}{new_version}{m.group(1)}",
                lines[idx], count=1,
            )
            break
    return "\n".join(lines) + "\n"


def parse_pyproject_toml(content: str) -> dict:
    """Return {name: version} combining Poetry-style
    `[tool.poetry.dependencies]` / `[tool.poetry.group.*.dependencies]`
    tables and PEP 621 `[project] dependencies = [...]` array entries
    (only entries using ==, >=, or ~= — see `_parse_pep508_item`).
    """
    deps = {}
    in_section = False
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if _TOML_SECTION_RE.match(stripped):
            in_section = bool(_POETRY_SECTION_RE.match(stripped))
            continue
        if not in_section or not stripped or stripped.startswith("#"):
            continue
        match = _toml_dep_line_match(stripped)
        if not match:
            continue
        name, version = match
        if name in _PYPROJECT_SKIP_NAMES:
            continue
        deps[name] = version
    deps.update(_pep621_dependencies(content))
    return deps


def bump_pyproject_toml(content: str, name: str, new_version: str) -> str:
    out = []
    in_section = False
    found = False
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if _TOML_SECTION_RE.match(stripped):
            in_section = bool(_POETRY_SECTION_RE.match(stripped))
            out.append(raw_line)
            continue
        if in_section:
            match = _toml_dep_line_match(stripped)
            if match and match[0] == name:
                raw_line = _bump_toml_line(raw_line, match[1], new_version)
                found = True
        out.append(raw_line)
    updated = "\n".join(out) + "\n"
    return updated if found else _bump_pep621_dependencies(updated, name, new_version)


# ------------------------------------------------------------------- Cargo.toml

_CARGO_SECTION_RE = re.compile(r"^\[(?:dependencies|dev-dependencies|build-dependencies)\]\s*$")


def parse_cargo_toml(content: str) -> dict:
    """Return {name: version} from `[dependencies]`, `[dev-dependencies]`,
    and `[build-dependencies]` sections.

    The `[dependencies.name]` nested-table style isn't handled — only
    the inline `name = "..."` / `name = { version = "...", ... }` style.
    """
    deps = {}
    in_section = False
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if _TOML_SECTION_RE.match(stripped):
            in_section = bool(_CARGO_SECTION_RE.match(stripped))
            continue
        if not in_section or not stripped or stripped.startswith("#"):
            continue
        match = _toml_dep_line_match(stripped)
        if match:
            deps[match[0]] = match[1]
    return deps


def bump_cargo_toml(content: str, name: str, new_version: str) -> str:
    out = []
    in_section = False
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if _TOML_SECTION_RE.match(stripped):
            in_section = bool(_CARGO_SECTION_RE.match(stripped))
            out.append(raw_line)
            continue
        if in_section:
            match = _toml_dep_line_match(stripped)
            if match and match[0] == name:
                raw_line = _bump_toml_line(raw_line, match[1], new_version)
        out.append(raw_line)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------- Gemfile

_GEM_LINE_RE = re.compile(r'^gem\s+[\'"]([A-Za-z0-9_.\-]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]')


def parse_gemfile(content: str) -> dict:
    """Return {name: version} for `gem "name", "version"` lines with an
    explicit version constraint. Unpinned lines (`gem "name"`) and
    lines with a `git:`/`path:` source are skipped — there's no
    registry version to compare those against.
    """
    deps = {}
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _GEM_LINE_RE.match(stripped)
        if match:
            deps[match.group(1)] = match.group(2)
    return deps


def bump_gemfile(content: str, name: str, new_version: str) -> str:
    out = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        match = _GEM_LINE_RE.match(stripped)
        if match and match.group(1) == name:
            raw_line = _bump_toml_line(raw_line, match.group(2), new_version)
        out.append(raw_line)
    return "\n".join(out) + "\n"


# ----------------------------------------------------------------- composer.json

# Platform packages describe the runtime, not a real Packagist package.
_COMPOSER_SKIP_NAMES = {"php"}
_COMPOSER_SKIP_PREFIXES = ("ext-", "lib-")


def parse_composer_json(content: str) -> dict:
    """Return {name: version} for `require` + `require-dev` entries,
    skipping platform packages (`php`, `ext-*`, `lib-*`)."""
    data = json.loads(content)
    deps = {}
    for section in ("require", "require-dev"):
        for name, version in data.get(section, {}).items():
            if name in _COMPOSER_SKIP_NAMES or name.startswith(_COMPOSER_SKIP_PREFIXES):
                continue
            deps[name] = version
    return deps


def bump_composer_json(content: str, name: str, new_version: str) -> str:
    data = json.loads(content)
    for section in ("require", "require-dev"):
        if name in data.get(section, {}):
            old = data[section][name]
            prefix = re.match(r"^[^\d]*", old).group()
            data[section][name] = f"{prefix}{new_version}"
    return json.dumps(data, indent=4) + "\n"


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


# Recognized version-constraint prefixes a stored dependency spec might
# start with, longest-first so e.g. '~=' matches before the bare '~'
# alternative would.
_PIN_OP_RE = re.compile(r"^(==|>=|~=|\^|~)")


def _op_and_base(spec: str):
    """Split a stored dependency spec into (operator, base_version).
    operator is None for a bare version with no recognized prefix
    (go.mod's 'v1.2.3', Cargo.toml's bare '1.0.152', etc.).
    """
    match = _PIN_OP_RE.match(spec)
    if not match:
        return None, spec
    op = match.group(1)
    return op, spec[len(op):]


def _caret_tilde_allows(op: str, base: str, latest: str):
    """npm/Poetry-style ^/~ range check: does the range implied by
    `op` + `base` already allow `latest`? Returns True/False, or None
    if either version fails to parse (caller falls back to a plain
    numeric comparison in that case). This is an approximation of real
    npm/Poetry range resolution, not a full implementation — it
    doesn't special-case every edge (e.g. a bare `~1` with no minor
    segment) the way a real semver-range library would.
    """
    try:
        base_v = Version(base)
        latest_v = Version(latest)
    except InvalidVersion:
        return None
    if latest_v <= base_v:
        return True
    if op == "~":
        return (latest_v.major, latest_v.minor) == (base_v.major, base_v.minor)
    # op == "^" — npm/Poetry caret: locks the leftmost non-zero segment.
    if base_v.major > 0:
        return latest_v.major == base_v.major
    if base_v.minor > 0:
        return latest_v.major == 0 and latest_v.minor == base_v.minor
    return latest_v.major == 0 and latest_v.minor == 0 and latest_v.micro == base_v.micro


def is_outdated(current: str, latest: str) -> bool:
    """True if `current` should be bumped toward `latest`.

    For plain pins (no operator, or `==`/`>=`) this is "is latest
    newer than current" — proper PEP 440 / semver comparison via
    `packaging` when both parse cleanly, falling back to numeric-
    segment comparison otherwise (e.g. some Go pseudo-versions).

    For range operators — `^`/`~` as used by npm and Poetry, and PEP
    440's `~=` as used in requirements.txt/pyproject.toml — this
    checks whether the *range* already allows `latest` before calling
    it outdated. A `^2.1.0` dependency isn't outdated when `2.9.0` is
    released, since npm/Poetry would already resolve to it; it only
    becomes outdated once a release escapes the declared range (e.g.
    `3.0.0`), at which point the range's floor gets bumped to match.
    """
    if not current or not latest:
        return False

    op, base = _op_and_base(current)

    if op in ("^", "~"):
        allowed = _caret_tilde_allows(op, base, latest)
        if allowed is not None:
            return not allowed
    elif op == "~=":
        try:
            return Version(latest) not in SpecifierSet(f"~={base}")
        except (InvalidVersion, InvalidSpecifier):
            pass  # unparseable — fall through to a plain comparison

    compare_current = base if op else current
    current_v = _parse_version(compare_current)
    latest_v = _parse_version(latest)
    if current_v is not None and latest_v is not None:
        return latest_v > current_v
    return _version_tuple(latest) > _version_tuple(compare_current)


def bump_severity(current: str, latest: str) -> str:
    """Classify a version bump as 'major', 'minor', 'patch', or
    'unknown' (when either side doesn't parse cleanly as a plain
    version). Used to gate config-driven auto-merge to patch-only
    updates — see config.py's `automerge` key.
    """
    _, base = _op_and_base(current)
    current_v = _parse_version(base)
    latest_v = _parse_version(latest)
    if current_v is None or latest_v is None:
        return "unknown"
    if latest_v.major != current_v.major:
        return "major"
    if latest_v.minor != current_v.minor:
        return "minor"
    return "patch"
