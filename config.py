"""
Loads optional bot configuration from a `.mini-dep-bot.yml` file at the
root of the target repo.

Supported keys:

    ignore:
      - some-noisy-package        # never touched by the bot

    pin:
      some-package: 2             # never bump past major version 2

    automerge: patch              # auto-merge a PR when every bump in
                                   # it is patch-level (also accepts:
                                   # true / "true" / "yes" as an alias
                                   # for "patch")

    combined_pr: true             # one PR for every manifest instead
                                   # of one PR per manifest

    exclude_paths:                # directories/files never scanned
      - examples/                 # for manifests, on top of the
      - legacy-app/               # built-in node_modules/vendor/etc
                                   # exclusions (see bot.py)

All five keys are optional; a missing config file (or a repo with none
at all) behaves exactly as before — nothing ignored, nothing pinned,
auto-merge off, one PR per manifest, every manifest in the repo scanned
except the built-in noise-directory defaults.

A malformed entry (wrong type, unparseable value) is skipped rather
than crashing the run — but skipping it silently would look identical
to "nothing configured", which is exactly what makes a typo hard to
notice. So each one is also recorded as a plain-English string in the
returned "warnings" list, which bot.py surfaces in both the console
output and the GitHub Actions step summary.
"""

import yaml

CONFIG_PATH = ".mini-dep-bot.yml"

_ON_VALUES = {"true", "yes", "1"}
_OFF_VALUES = {"false", "no", "0", ""}
_AUTOMERGE_ON_VALUES = _ON_VALUES | {"patch"}


def _defaults(warnings=None) -> dict:
    """Fresh, independent default containers every time — never a
    shared dict/set/list reused across calls, so nothing that later
    mutates a returned config in place can corrupt the next call's
    defaults.
    """
    return {
        "ignore": set(),
        "pin": {},
        "automerge_patch": False,
        "combined_pr": False,
        "exclude_paths": set(),
        "warnings": warnings or [],
    }


def load_config(client, repo: str, branch: str) -> dict:
    """Return {"ignore": set(...), "pin": {name: max_major, ...},
    "automerge_patch": bool, "combined_pr": bool,
    "exclude_paths": set(...), "warnings": [str, ...]}.
    """
    try:
        content, _ = client.get_file(repo, CONFIG_PATH, branch)
    except Exception:
        return _defaults()

    data = yaml.safe_load(content)
    if data is None:
        return _defaults()
    if not isinstance(data, dict):
        return _defaults(warnings=[
            f"{CONFIG_PATH} isn't a YAML mapping at the top level — ignoring the whole file"
        ])

    warnings = []

    ignore = set()
    raw_ignore = data.get("ignore")
    if raw_ignore is not None:
        if isinstance(raw_ignore, list):
            ignore = {str(item) for item in raw_ignore}
        else:
            warnings.append(
                f"ignore: expected a list, got {type(raw_ignore).__name__} — ignoring this key"
            )

    pin = {}
    raw_pin = data.get("pin")
    if raw_pin is not None:
        if isinstance(raw_pin, dict):
            for name, major in raw_pin.items():
                try:
                    pin[str(name)] = int(major)
                except (TypeError, ValueError):
                    warnings.append(
                        f"pin: '{name}: {major}' isn't a valid major version "
                        f"(expected an integer) — skipped"
                    )
        else:
            warnings.append(
                f"pin: expected a mapping, got {type(raw_pin).__name__} — ignoring this key"
            )

    automerge_display = data.get("automerge", "")
    automerge_raw = str(automerge_display).strip().lower()
    automerge_patch = automerge_raw in _AUTOMERGE_ON_VALUES
    if automerge_raw not in _AUTOMERGE_ON_VALUES and automerge_raw not in _OFF_VALUES:
        warnings.append(
            f"automerge: '{automerge_display}' isn't recognized "
            f"(expected patch/true/yes, or false/no) — treated as off"
        )

    combined_display = data.get("combined_pr", False)
    combined_raw = str(combined_display).strip().lower()
    combined_pr = combined_raw in _ON_VALUES
    if combined_raw not in _ON_VALUES and combined_raw not in _OFF_VALUES:
        warnings.append(
            f"combined_pr: '{combined_display}' isn't recognized "
            f"(expected true/false) — treated as off"
        )

    exclude_paths = set()
    raw_exclude = data.get("exclude_paths")
    if raw_exclude is not None:
        if isinstance(raw_exclude, list):
            for raw_path in raw_exclude:
                cleaned = str(raw_path).strip().rstrip("/")
                if cleaned:
                    exclude_paths.add(cleaned)
        else:
            warnings.append(
                f"exclude_paths: expected a list, got {type(raw_exclude).__name__} — ignoring this key"
            )

    return {
        "ignore": ignore,
        "pin": pin,
        "automerge_patch": automerge_patch,
        "combined_pr": combined_pr,
        "exclude_paths": exclude_paths,
        "warnings": warnings,
    }
