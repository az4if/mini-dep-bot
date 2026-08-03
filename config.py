"""
Loads optional bot configuration from a `.mini-dep-bot.yml` file at the
root of the target repo.

Supported keys:

    ignore:
      - some-noisy-package        # never touched by the bot

    pin:
      some-package: 2             # never bump past major version 2

Both keys are optional; a missing config file (or a repo with none at
all) behaves exactly as before — nothing ignored, nothing pinned.
"""

import yaml

CONFIG_PATH = ".mini-dep-bot.yml"


def load_config(client, repo: str, branch: str) -> dict:
    """Return {"ignore": set(...), "pin": {name: max_major, ...}}."""
    try:
        content, _ = client.get_file(repo, CONFIG_PATH, branch)
    except Exception:
        return {"ignore": set(), "pin": {}}

    data = yaml.safe_load(content) or {}

    ignore = set(data.get("ignore") or [])

    pin = {}
    for name, major in (data.get("pin") or {}).items():
        try:
            pin[str(name)] = int(major)
        except (TypeError, ValueError):
            continue  # malformed entry — skip rather than crash the run

    return {"ignore": ignore, "pin": pin}
