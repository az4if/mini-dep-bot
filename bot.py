#!/usr/bin/env python3
"""
mini-dep-bot
============
A small GitHub bot that checks a repo's dependency manifests
(package.json, requirements.txt, go.mod) against the latest versions
published on npm, PyPI, and the Go module proxy, and opens a pull
request for each outdated dependency it finds.

Usage:
    export GITHUB_TOKEN=ghp_xxxxx     # repo scope, or Contents+PR read/write
    export TARGET_REPO=owner/name
    python bot.py
"""

import os
import sys

from dotenv import load_dotenv
from rich.console import Console

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


def check_manifest(client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn):
    try:
        content, _ = client.get_file(repo, path, base_branch)
    except Exception:
        return []  # manifest not present in this repo — skip it

    deps = parse_fn(content)
    opened = []

    for name, current in deps.items():
        latest = lookup_fn(name)
        if not latest or not is_outdated(current, latest):
            continue

        branch = f"{BRANCH_PREFIX}/{path.replace('.', '-')}/{name}-{latest}"
        client.create_branch(repo, branch, client.get_ref_sha(repo, base_branch))

        fresh_content, fresh_sha = client.get_file(repo, path, branch)
        updated = bump_fn(fresh_content, name, latest)
        client.update_file(
            repo, path, branch, updated, fresh_sha,
            message=f"chore(deps): bump {name} from {current} to {latest}",
        )

        pr_url = client.open_pull_request(
            repo, branch, base_branch,
            title=f"chore(deps): bump {name} from {current} to {latest}",
            body=(
                f"Automated update for **{name}** in `{path}`.\n\n"
                f"- Current: `{current}`\n- Latest: `{latest}`\n\n"
                f"_Opened by mini-dep-bot._"
            ),
        )
        opened.append(pr_url)

    return opened


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
    console.print(f"[bold]mini-dep-bot[/bold] checking [cyan]{repo}[/cyan]@{base_branch}")

    all_prs = []
    for path, parse_fn, lookup_fn, bump_fn in MANIFESTS:
        console.print(f"  [dim]scanning[/dim] {path}...")
        all_prs += check_manifest(client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn)

    if all_prs:
        console.print(f"[bold green]Opened {len(all_prs)} pull request(s):[/bold green]")
        for url in all_prs:
            console.print(f"  - {url}")
    else:
        console.print(
            "[green]Everything is already up to date[/green] "
            "(or no supported manifest files were found)."
        )


if __name__ == "__main__":
    main()
