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
"""

import yaml

CONFIG_PATH = ".mini-dep-bot.yml"

_AUTOMERGE_PATCH_VALUES = {"patch", "true", "yes", "1"}
_TRUTHY_VALUES = {"true", "yes", "1"}

_DEFAULT_CONFIG = {
    "ignore": set(),
    "pin": {},
    "automerge_patch": False,
    "combined_pr": False,
    "exclude_paths": set(),
}


def load_config(client, repo: str, branch: str) -> dict:
    """Return {"ignore": set(...), "pin": {name: max_major, ...},
    "automerge_patch": bool, "combined_pr": bool,
    "exclude_paths": set(...)}.
    """
    try:
        content, _ = client.get_file(repo, CONFIG_PATH, branch)
    except Exception:
        return dict(_DEFAULT_CONFIG)

    data = yaml.safe_load(content) or {}

    ignore = set(data.get("ignore") or [])

    pin = {}
    for name, major in (data.get("pin") or {}).items():
        try:
            pin[str(name)] = int(major)
        except (TypeError, ValueError):
            continue  # malformed entry — skip rather than crash the run

    automerge_raw = str(data.get("automerge", "")).strip().lower()
    automerge_patch = automerge_raw in _AUTOMERGE_PATCH_VALUES

    combined_raw = data.get("combined_pr", False)
    combined_pr = (
        combined_raw is True
        or str(combined_raw).strip().lower() in _TRUTHY_VALUES
    )

    exclude_paths = set()
    for raw_path in (data.get("exclude_paths") or []):
        cleaned = str(raw_path).strip().rstrip("/")
        if cleaned:
            exclude_paths.add(cleaned)

    return {
        "ignore": ignore,
        "pin": pin,
        "automerge_patch": automerge_patch,
        "combined_pr": combined_pr,
        "exclude_paths": exclude_paths,
    }
