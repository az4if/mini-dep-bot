#!/usr/bin/env python3
"""
mini-dep-bot
============
A small GitHub bot that checks a repo's dependency manifests
(package.json, requirements.txt, go.mod) against the latest versions
published on npm, PyPI, and the Go module proxy, and opens a pull
request per manifest bundling every outdated dependency it finds.

Optional `.mini-dep-bot.yml` at the repo root can list packages to
ignore entirely, or pin specific packages to a max major version —
see config.py.

Usage:
    export GITHUB_TOKEN=ghp_xxxxx     # repo scope, or Contents+PR read/write
    export TARGET_REPO=owner/name
    python bot.py
"""

import os
import sys

from dotenv import load_dotenv
from rich.console import Console

from config import load_config
from github_api import GitHubClient
from parsers import (
    parse_package_json, bump_package_json,
    parse_requirements_txt, bump_requirements_txt,
    parse_go_mod, bump_go_mod,
    is_outdated,
)
from registries import latest_npm_version, latest_pypi_version, latest_go_version

# Loads GITHUB_TOKEN / TARGET_REPO from a local .env file if present;
# a no-op (and harmless) when running in GitHub Actions, where the
# environment is already set via `env:` in the workflow.
load_dotenv()

console = Console()

# Each entry: (path in repo, parser, registry lookup, bump function)
MANIFESTS = [
    ("package.json", parse_package_json, latest_npm_version, bump_package_json),
    ("requirements.txt", parse_requirements_txt, latest_pypi_version, bump_requirements_txt),
    ("go.mod", parse_go_mod, latest_go_version, bump_go_mod),
]

BRANCH_PREFIX = "mini-dep-bot"


def find_updates(deps, lookup_fn, config):
    """Return [(name, current, latest), ...] for every dependency that
    has a newer version available, skipping ignored packages and
    respecting per-package major-version pins.
    """
    updates = []
    for name, current in deps.items():
        if name in config["ignore"]:
            continue
        max_major = config["pin"].get(name)
        latest = lookup_fn(name, max_major=max_major)
        if not latest or not is_outdated(current, latest):
            continue
        updates.append((name, current, latest))
    return updates


def check_manifest(client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn, config):
    """Bundle every outdated dependency in this manifest into a single
    branch and PR. If a PR for this manifest is already open, push an
    updated commit to it instead of opening a duplicate.

    Returns the PR url if one was opened or updated, else None.
    """
    try:
        content, _ = client.get_file(repo, path, base_branch)
    except Exception:
        return None  # manifest not present in this repo — skip it

    deps = parse_fn(content)
    updates = find_updates(deps, lookup_fn, config)
    if not updates:
        return None

    branch = f"{BRANCH_PREFIX}/{path.replace('.', '-')}/updates"
    existing_pr = client.find_open_pr(repo, branch, base_branch)

    # Creating a branch that already exists is a no-op (handled as a
    # 422 in GitHubClient), so this is safe whether or not `branch`
    # already exists from a previous run.
    client.create_branch(repo, branch, client.get_ref_sha(repo, base_branch))

    fresh_content, fresh_sha = client.get_file(repo, path, branch)
    updated = fresh_content
    for name, current, latest in updates:
        updated = bump_fn(updated, name, latest)

    if updated == fresh_content:
        # Every one of these bumps is already reflected on the branch
        # (e.g. the bot already ran this week) — nothing new to push.
        return existing_pr

    summary = "\n".join(
        f"- **{name}**: `{current}` → `{latest}`" for name, current, latest in updates
    )
    count = len(updates)
    message = f"chore(deps): update {count} dependenc{'y' if count == 1 else 'ies'} in {path}"

    client.update_file(repo, path, branch, updated, fresh_sha, message=message)

    if existing_pr:
        return existing_pr  # new commit was just pushed to the open PR

    return client.open_pull_request(
        repo, branch, base_branch,
        title=message,
        body=f"Automated dependency updates for `{path}`.\n\n{summary}\n\n_Opened by mini-dep-bot._",
    )


def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("TARGET_REPO")
    if not token or not repo:
        console.print(
            "[bold red]Set GITHUB_TOKEN and TARGET_REPO[/bold red] "
            "(environment variables, or a .env file).",
            file=sys.stderr,
        )
        sys.exit(1)

    client = GitHubClient(token)
    base_branch = client.get_default_branch(repo)
    config = load_config(client, repo, base_branch)
    console.print(f"[bold]mini-dep-bot[/bold] checking [cyan]{repo}[/cyan]@{base_branch}")
    if config["ignore"] or config["pin"]:
        console.print(
            f"  [dim]config:[/dim] ignoring {sorted(config['ignore']) or 'none'}, "
            f"pinning {config['pin'] or 'none'}"
        )

    all_prs = []
    for path, parse_fn, lookup_fn, bump_fn in MANIFESTS:
        console.print(f"  [dim]scanning[/dim] {path}...")
        pr_url = check_manifest(client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn, config)
        if pr_url:
            all_prs.append(pr_url)

    if all_prs:
        console.print(f"[bold green]Opened/updated {len(all_prs)} pull request(s):[/bold green]")
        for url in all_prs:
            console.print(f"  - {url}")
    else:
        console.print(
            "[green]Everything is already up to date[/green] "
            "(or no supported manifest files were found)."
        )


if __name__ == "__main__":
    main()
