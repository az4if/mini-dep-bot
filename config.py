"""
Loads optional bot configuration from a `.mini-dep-bot.yml` file at the
root of the target repo.

Supported keys:

    ignore:
      - some-noisy-package        # never touched by the bot

    pin:
      some-package: 2             # never bump past major version 2

    automerge: patch              # auto-merge a manifest's grouped PR
                                   # when every bump in it is patch-level
                                   # (also accepts: true / "true" / "yes"
                                   # as an alias for "patch")

All three keys are optional; a missing config file (or a repo with
none at all) behaves exactly as before — nothing ignored, nothing
pinned, auto-merge off.
"""

import yaml

CONFIG_PATH = ".mini-dep-bot.yml"

_AUTOMERGE_PATCH_VALUES = {"patch", "true", "yes", "1"}


def load_config(client, repo: str, branch: str) -> dict:
    """Return {"ignore": set(...), "pin": {name: max_major, ...},
    "automerge_patch": bool}.
    """
    try:
        content, _ = client.get_file(repo, CONFIG_PATH, branch)
    except Exception:
        return {"ignore": set(), "pin": {}, "automerge_patch": False}

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

    return {"ignore": ignore, "pin": pin, "automerge_patch": automerge_patch}
