#!/usr/bin/env python3
"""
mini-dep-bot
============
A small GitHub bot that checks a repo's dependency manifests
(package.json, requirements.txt, go.mod, pyproject.toml, Cargo.toml,
Gemfile, composer.json) against the latest versions published on npm,
PyPI, the Go module proxy, crates.io, RubyGems, and Packagist, and
opens a pull request per manifest bundling every outdated dependency
it finds — at any depth in the repo, not just the root, so a monorepo
with several `package.json`/`requirements.txt` files gets all of them.

Each PR gets:
  - `dependencies` + an ecosystem label (npm/python/go/rust/ruby/php)
  - a note when a bump also closes a known vulnerability (OSV.dev)
  - a changelog/homepage link per dependency, best-effort
  - a lockfile regenerated for real (via the provided GitHub Actions
    workflow's follow-up step — see LOCKFILES / WORKFLOW_UPDATES_FILE
    below), or a manual heads-up when run standalone. For package.json
    specifically, whichever of npm/Yarn/pnpm's lockfile is actually
    present next to that manifest is the one that gets used.
  - auto-merge enabled, if `.mini-dep-bot.yml` opts in and every bump
    in the PR is patch-level

Optional `.mini-dep-bot.yml` at the repo root can list packages to
ignore entirely, pin specific packages to a max major version, opt
patch-only bumps into auto-merge, combine every manifest into one PR,
or exclude extra paths from discovery — see config.py.

Usage:
    export GITHUB_TOKEN=ghp_xxxxx     # repo scope, or Contents+PR read/write
    export TARGET_REPO=owner/name
    python bot.py [--dry-run]

    # --dry-run (or DRY_RUN=true) reports what would change — every
    # call it makes is read-only, so no branch, commit, PR, label, or
    # auto-merge setting is created/changed.
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
from rich.console import Console

import security
from config import load_config
from github_api import GitHubClient
from parsers import (
    parse_package_json, bump_package_json,
    parse_requirements_txt, bump_requirements_txt,
    parse_go_mod, bump_go_mod,
    parse_pyproject_toml, bump_pyproject_toml,
    parse_cargo_toml, bump_cargo_toml,
    parse_gemfile, bump_gemfile,
    parse_composer_json, bump_composer_json,
    is_outdated, bump_severity,
)
from registries import (
    latest_npm_version, latest_pypi_version, latest_go_version,
    latest_crate_version, latest_rubygems_version, latest_packagist_version,
    homepage_url,
)

# Loads GITHUB_TOKEN / TARGET_REPO from a local .env file if present;
# a no-op (and harmless) when running in GitHub Actions, where the
# environment is already set via `env:` in the workflow.
load_dotenv()

console = Console()

# Each entry: (basename to discover, parser, registry lookup, bump
# function). The basename — not a fixed path — is what makes monorepo
# support work: discover_manifest_paths() finds every file in the repo
# matching one of these names, at any depth.
MANIFESTS = [
    ("package.json", parse_package_json, latest_npm_version, bump_package_json),
    ("requirements.txt", parse_requirements_txt, latest_pypi_version, bump_requirements_txt),
    ("go.mod", parse_go_mod, latest_go_version, bump_go_mod),
    ("pyproject.toml", parse_pyproject_toml, latest_pypi_version, bump_pyproject_toml),
    ("Cargo.toml", parse_cargo_toml, latest_crate_version, bump_cargo_toml),
    ("Gemfile", parse_gemfile, latest_rubygems_version, bump_gemfile),
    ("composer.json", parse_composer_json, latest_packagist_version, bump_composer_json),
]

# A short ecosystem label per manifest type, added to every PR
# alongside "dependencies" so a repo with several ecosystems can
# filter by one. Keyed by basename ("package.json"), not full path —
# an "apps/web/package.json" still gets the "npm" label.
MANIFEST_LABELS = {
    "package.json": "npm",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "composer.json": "php",
}

# manifest type -> [(candidate lockfile name, command to regenerate
# it), ...], checked in order and the first one that actually exists
# wins. package.json has three real candidates since a JS project
# could be on npm, Yarn, or pnpm — everything else has exactly one.
#
# mini-dep-bot itself only edits the manifest via the GitHub API — it
# has no repo checkout or toolchain, and correctly resolving a lockfile
# means running the real package manager (a naive hand-edit risks e.g.
# a stale npm/yarn integrity hash). The provided GitHub Actions
# workflow covers this properly instead: bot.py records which
# (branch, lockfile) pairs changed, and a follow-up step in
# dependency-check.yml checks out each branch, cds into that lockfile's
# directory, runs the real command below, and pushes the regenerated
# lockfile to the same branch/PR — see WORKFLOW_UPDATES_FILE below.
# Running bot.py standalone (outside that workflow) skips this step,
# so the PR note still applies there.
LOCKFILES = {
    "package.json": [
        ("package-lock.json", "npm install --package-lock-only"),
        ("yarn.lock", "yarn install --mode update-lockfile"),
        ("pnpm-lock.yaml", "pnpm install --lockfile-only"),
    ],
    "pyproject.toml": [("poetry.lock", "poetry lock --no-update")],
    "Cargo.toml": [("Cargo.lock", "cargo update --workspace")],
    "Gemfile": [("Gemfile.lock", "bundle lock")],
    "go.mod": [("go.sum", "go mod tidy")],
    "composer.json": [("composer.lock", "composer update --lock --no-interaction")],
}

# Where main() writes the list of {"path", "branch", "lockfile"} dicts
# for manifests that got a real commit this run — read by the
# "Regenerate lockfiles" step in dependency-check.yml. Only written
# when non-empty and not dry-run; its absence just means that step has
# nothing to do.
WORKFLOW_UPDATES_FILE = os.environ.get("MINI_DEP_BOT_UPDATES_FILE", ".mini-dep-bot-updates.json")

BRANCH_PREFIX = "mini-dep-bot"

# Directory names never scanned for manifests, even if a repo commits
# them. Well-behaved repos won't have these in the git tree at all —
# list_tree() only returns files git actually tracks, so a properly
# gitignored node_modules never shows up in the first place — this is
# a defensive backstop for the repos that don't. Extra project-specific
# exclusions go in .mini-dep-bot.yml's `exclude_paths`.
EXCLUDED_DIR_NAMES = {
    "node_modules", "vendor", "bower_components",
    ".venv", "venv", "env",
    "dist", "build", "target",
    ".git", ".tox", "__pycache__",
}


def _is_excluded_path(path: str, extra_excludes) -> bool:
    """True if `path` sits inside a directory mini-dep-bot never
    scans — a built-in noise directory, or one of the target repo's
    own `.mini-dep-bot.yml` `exclude_paths` entries.
    """
    parts = path.split("/")
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    return any(
        path == excluded or path.startswith(excluded + "/")
        for excluded in extra_excludes
    )


def discover_manifest_paths(client, repo, branch, config):
    """Find every tracked file in the repo matching one of MANIFESTS'
    basenames, at any depth — this is what makes monorepos work
    without hardcoding subdirectory paths. Returns
    {basename: [full_path, ...]} (sorted, for stable output).

    Falls back to root-only ({basename: [basename]}) if the tree
    listing fails for any reason — same behavior as before monorepo
    support existed, rather than failing the whole run.
    """
    basenames = {name for name, *_ in MANIFESTS}
    try:
        all_paths = client.list_tree(repo, branch)
    except Exception:
        return {name: [name] for name in basenames}

    found = {name: [] for name in basenames}
    for full_path in all_paths:
        base = full_path.rsplit("/", 1)[-1]
        if base in found and not _is_excluded_path(full_path, config["exclude_paths"]):
            found[base].append(full_path)

    for name in found:
        found[name].sort()
    return found


def _sibling_path(manifest_path: str, filename: str) -> str:
    """Join `filename` into the same directory as `manifest_path` —
    e.g. ("apps/web/package.json", "package-lock.json") ->
    "apps/web/package-lock.json". A root-level manifest (no "/")
    just returns `filename` bare, unchanged.
    """
    if "/" in manifest_path:
        return manifest_path.rsplit("/", 1)[0] + "/" + filename
    return filename


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


def _format_update_line(manifest_type, name, current, latest):
    """One PR-body bullet for a single dependency bump, with a
    best-effort changelog link and vulnerability note. Both lookups
    are best-effort — a failure just omits that part of the line.
    `manifest_type` is the manifest's basename (e.g. "package.json"),
    used to pick the right registry/ecosystem — not its full path.
    """
    line = f"- **{name}**: `{current}` → `{latest}`"

    link = homepage_url(manifest_type, name)
    if link:
        line += f" — [{name}]({link})"

    fixed = security.fixed_vulnerabilities(manifest_type, name, current, latest)
    if fixed:
        ids = ", ".join(fixed[:3]) + (", ..." if len(fixed) > 3 else "")
        line += f" 🔒 fixes {ids}"

    return line


def _find_lockfile(client, repo, base_branch, manifest_path, manifest_type):
    """Return (lockfile_path, command) for whichever lockfile actually
    exists next to `manifest_path`, checking each ecosystem-appropriate
    candidate in order (e.g. package.json checks package-lock.json,
    then yarn.lock, then pnpm-lock.yaml — whichever is really there is
    the one used) — or None if none exist / the manifest type has no
    known lockfile. Read-only — safe to call in dry-run.
    """
    candidates = LOCKFILES.get(manifest_type)
    if not candidates:
        return None
    for name, command in candidates:
        lockfile_path = _sibling_path(manifest_path, name)
        try:
            if client.file_exists(repo, lockfile_path, base_branch):
                return lockfile_path, command
        except Exception:
            continue
    return None


def _lockfile_note(client, repo, base_branch, manifest_path, manifest_type):
    """Best-effort note about a companion lockfile, or "" if there
    isn't one / the check fails. Read-only — safe to call in dry-run.
    """
    found = _find_lockfile(client, repo, base_branch, manifest_path, manifest_type)
    if not found:
        return ""
    lockfile_path, command = found
    lockfile_dir = lockfile_path.rsplit("/", 1)[0] if "/" in lockfile_path else None
    run_from = f" (from the `{lockfile_dir}` directory)" if lockfile_dir else ""
    return (
        f"\n\n⚠️ This repo also has `{lockfile_path}`. If you're running the provided "
        f"GitHub Actions workflow, its \"Regenerate lockfiles\" step pushes an "
        f"updated `{lockfile_path}` to this branch automatically. Running `bot.py` "
        f"standalone, or if that step is disabled: regenerate it yourself "
        f"(e.g. `{command}`{run_from}) and push it to this branch before merging."
    )


def _write_step_summary(repo, base_branch, dry_run, config, all_prs, automerge_failures=None):
    """Best-effort: append a readable summary to GitHub Actions' job
    summary UI (the $GITHUB_STEP_SUMMARY file), so the run's outcome
    is visible without opening the console logs. Writes nothing when
    that env var isn't set — i.e. running outside GitHub Actions.
    """
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    lines = [f"## mini-dep-bot — `{repo}`@`{base_branch}`", ""]
    if dry_run:
        lines.append("**Dry run** — no branches, commits, or PRs were created.")
        lines.append("")

    if config["warnings"]:
        lines.append(f"### ⚠️ {len(config['warnings'])} config warning(s) in `.mini-dep-bot.yml`")
        lines.append("")
        for warning in config["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if config["ignore"] or config["pin"] or config["automerge_patch"] or config["combined_pr"]:
        lines.append("**Config:**")
        if config["ignore"]:
            lines.append(f"- Ignoring: {', '.join(sorted(config['ignore']))}")
        if config["pin"]:
            pins = ", ".join(f"{name} → v{major}" for name, major in sorted(config["pin"].items()))
            lines.append(f"- Pinned: {pins}")
        if config["automerge_patch"]:
            lines.append("- Auto-merge: patch-only bumps")
        if config["combined_pr"]:
            lines.append("- Mode: combined PR across all manifests")
        lines.append("")

    if all_prs:
        verb = "Would open/update" if dry_run else "Opened/updated"
        lines.append(f"### {verb} {len(all_prs)} pull request(s)")
        lines.append("")
        for url in all_prs:
            lines.append(f"- {url}")
    else:
        lines.append("### Everything is up to date")
        lines.append("No outdated dependencies found (or no supported manifest was present).")

    if automerge_failures:
        lines.append("")
        lines.append(f"### ⚠️ Auto-merge rejected for {len(automerge_failures)} PR(s)")
        lines.append("")
        lines.append(
            "GitHub didn't accept the auto-merge request — check the repo's "
            "\"Allow auto-merge\" setting and branch protection rules."
        )
        lines.append("")
        for failure in automerge_failures:
            lines.append(f"- `{failure['path']}` — {failure['pr_url']}")

    try:
        with open(summary_path, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass  # best-effort — a missing/unwritable summary file is never fatal


def check_manifest(client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn, config,
                    manifest_type=None, dry_run=False, updates_log=None, automerge_failures=None):
    """Bundle every outdated dependency in this manifest into a single
    branch and PR. If a PR for this manifest is already open, push an
    updated commit to it instead of opening a duplicate.

    `path` is the manifest's actual location in the repo (e.g.
    "apps/web/package.json" for a monorepo) — used for every GitHub
    API call and the branch name. `manifest_type` is its basename
    (e.g. "package.json"), used for ecosystem-keyed lookups (labels,
    OSV, lockfile candidates); it defaults to `path` when omitted, for
    the common case of a root-level manifest where they're the same.

    When `automerge_failures` is given, a {"path", "pr_url"} dict is
    appended to it if GitHub rejects an eligible auto-merge request
    (branch protection not configured, "Allow auto-merge" off, etc) —
    see _write_step_summary, which surfaces these instead of letting
    a rejected request pass by silently.

    In dry-run mode, only read-only GitHub/registry/OSV calls are made
    — no branch, commit, PR, label, or auto-merge setting is created —
    and the return value is a human-readable summary rather than a PR
    url.

    When `updates_log` is given, a {"path", "branch", "lockfile"} dict
    is appended to it whenever a real commit is pushed — see
    WORKFLOW_UPDATES_FILE and dependency-check.yml's lockfile step.

    Returns the PR url (or dry-run summary) if there's anything to
    report, else None.
    """
    manifest_type = manifest_type or path

    try:
        content, _ = client.get_file(repo, path, base_branch)
    except Exception:
        return None  # manifest not present in this repo — skip it

    deps = parse_fn(content)
    updates = find_updates(deps, lookup_fn, config)
    if not updates:
        return None

    count = len(updates)
    message = f"chore(deps): update {count} dependenc{'y' if count == 1 else 'ies'} in {path}"
    branch = f"{BRANCH_PREFIX}/{path.replace('.', '-')}/updates"
    severities = [bump_severity(current, latest) for _, current, latest in updates]
    automerge_eligible = config["automerge_patch"] and all(s == "patch" for s in severities)

    if dry_run:
        existing_pr = client.find_open_pr(repo, branch, base_branch)  # read-only
        action = f"push a new commit to the open PR ({existing_pr['html_url']})" if existing_pr \
            else "open a new PR"
        console.print(f"    [yellow]would {action}[/yellow] for {path}:")
        for (name, current, latest), severity in zip(updates, severities):
            console.print(f"      - {name}: {current} -> {latest}  [{severity}]")
        if automerge_eligible:
            console.print("      [dim]would enable auto-merge (all patch-level, automerge: patch)[/dim]")
        return (existing_pr["html_url"] if existing_pr
                else f"[dry-run] {path}: {count} update(s), would open a new PR")

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
        return existing_pr["html_url"] if existing_pr else None

    summary = "\n".join(
        _format_update_line(manifest_type, name, current, latest) for name, current, latest in updates
    )
    body = f"Automated dependency updates for `{path}`.\n\n{summary}\n\n_Opened by mini-dep-bot._"
    body += _lockfile_note(client, repo, base_branch, path, manifest_type)

    client.update_file(repo, path, branch, updated, fresh_sha, message=message)

    if updates_log is not None:
        found = _find_lockfile(client, repo, base_branch, path, manifest_type)
        updates_log.append({
            "path": path,
            "branch": branch,
            "lockfile": found[0] if found else None,
        })

    if existing_pr:
        pr = existing_pr  # new commit was just pushed to the open PR
    else:
        pr = client.open_pull_request(repo, branch, base_branch, title=message, body=body)

    try:
        client.add_labels(repo, pr["number"], ["dependencies", MANIFEST_LABELS[manifest_type]])
    except Exception:
        pass  # labeling is a nice-to-have — never let it fail the run

    if automerge_eligible:
        if not client.enable_auto_merge(pr["node_id"]):
            console.print(
                f"    [yellow]⚠ auto-merge was requested but GitHub rejected it[/yellow] "
                f"for {path} ({pr['html_url']}) — check the repo's \"Allow auto-merge\" "
                f"setting and branch protection rules"
            )
            if automerge_failures is not None:
                automerge_failures.append({"path": path, "pr_url": pr["html_url"]})

    return pr["html_url"]


COMBINED_BRANCH = f"{BRANCH_PREFIX}/all-updates"


def run_combined(client, repo, base_branch, config, manifest_paths=None, dry_run=False,
                  updates_log=None, automerge_failures=None):
    """Like check_manifest, but bundles every outdated dependency
    across every manifest — including nested ones in a monorepo —
    into a single branch/PR instead of one PR per manifest. Opt in via
    `.mini-dep-bot.yml`'s `combined_pr: true`.

    `manifest_paths` is a {basename: [full_path, ...]} dict from
    discover_manifest_paths(); defaults to the root-only case
    ({basename: [basename]}) when omitted.

    Each manifest that changes is still its own commit on that shared
    branch — the GitHub Contents API can only write one file per
    commit — so the PR ends up with one commit per touched manifest
    rather than a single multi-file commit, but it's one PR either way.

    Same dry-run, updates_log, and automerge_failures contract as
    check_manifest. Returns the PR url (or dry-run summary) if there's
    anything to report, else None.
    """
    if manifest_paths is None:
        manifest_paths = {name: [name] for name, *_ in MANIFESTS}

    per_file = []  # (path, manifest_type, bump_fn, updates, severities)
    for manifest_type, parse_fn, lookup_fn, bump_fn in MANIFESTS:
        for path in manifest_paths.get(manifest_type, []):
            console.print(f"  [dim]scanning[/dim] {path}...")
            try:
                content, _ = client.get_file(repo, path, base_branch)
            except Exception:
                continue  # manifest not present in this repo
            deps = parse_fn(content)
            updates = find_updates(deps, lookup_fn, config)
            if not updates:
                continue
            severities = [bump_severity(current, latest) for _, current, latest in updates]
            per_file.append((path, manifest_type, bump_fn, updates, severities))

    if not per_file:
        return None

    branch = COMBINED_BRANCH
    all_severities = [s for *_, severities in per_file for s in severities]
    automerge_eligible = config["automerge_patch"] and all(s == "patch" for s in all_severities)

    if dry_run:
        existing_pr = client.find_open_pr(repo, branch, base_branch)  # read-only
        action = f"push new commits to the open PR ({existing_pr['html_url']})" if existing_pr \
            else "open a new combined PR"
        console.print(f"    [yellow]would {action}[/yellow] across {len(per_file)} manifest(s):")
        for path, _, _, updates, severities in per_file:
            for (name, current, latest), severity in zip(updates, severities):
                console.print(f"      - [{path}] {name}: {current} -> {latest}  [{severity}]")
        if automerge_eligible:
            console.print("      [dim]would enable auto-merge (all patch-level, automerge: patch)[/dim]")
        return (existing_pr["html_url"] if existing_pr
                else f"[dry-run] combined: {len(per_file)} manifest(s) with updates, would open a new PR")

    existing_pr = client.find_open_pr(repo, branch, base_branch)
    client.create_branch(repo, branch, client.get_ref_sha(repo, base_branch))

    body_sections = []
    any_pushed = False

    for path, manifest_type, bump_fn, updates, _severities in per_file:
        fresh_content, fresh_sha = client.get_file(repo, path, branch)
        updated = fresh_content
        for name, current, latest in updates:
            updated = bump_fn(updated, name, latest)
        if updated == fresh_content:
            continue  # already applied on this branch from a previous run

        count = len(updates)
        message = f"chore(deps): update {count} dependenc{'y' if count == 1 else 'ies'} in {path}"
        client.update_file(repo, path, branch, updated, fresh_sha, message=message)
        any_pushed = True

        if updates_log is not None:
            found = _find_lockfile(client, repo, base_branch, path, manifest_type)
            updates_log.append({
                "path": path, "branch": branch,
                "lockfile": found[0] if found else None,
            })

        summary = "\n".join(
            _format_update_line(manifest_type, name, current, latest) for name, current, latest in updates
        )
        section = f"### `{path}`\n\n{summary}"
        section += _lockfile_note(client, repo, base_branch, path, manifest_type)
        body_sections.append(section)

    if not any_pushed:
        return existing_pr["html_url"] if existing_pr else None

    body = "Automated dependency updates.\n\n" + "\n\n".join(body_sections) + "\n\n_Opened by mini-dep-bot._"

    if existing_pr:
        pr = existing_pr  # new commit(s) were just pushed to the open PR
    else:
        title = f"chore(deps): update {len(per_file)} manifest(s)"
        pr = client.open_pull_request(repo, branch, base_branch, title=title, body=body)

    labels = sorted({"dependencies"} | {MANIFEST_LABELS[manifest_type] for _, manifest_type, *_ in per_file})
    try:
        client.add_labels(repo, pr["number"], labels)
    except Exception:
        pass  # labeling is a nice-to-have — never let it fail the run

    if automerge_eligible:
        if not client.enable_auto_merge(pr["node_id"]):
            console.print(
                f"    [yellow]⚠ auto-merge was requested but GitHub rejected it[/yellow] "
                f"for the combined PR ({pr['html_url']}) — check the repo's "
                f"\"Allow auto-merge\" setting and branch protection rules"
            )
            if automerge_failures is not None:
                automerge_failures.append({"path": "combined", "pr_url": pr["html_url"]})

    return pr["html_url"]


def parse_args():
    parser = argparse.ArgumentParser(description="mini-dep-bot")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without creating branches, commits, or PRs",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    dry_run = args.dry_run or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

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
    if dry_run:
        console.print("[bold yellow]DRY RUN[/bold yellow] — no branches, commits, or PRs will be created")
    if config["warnings"]:
        console.print(f"[bold yellow]⚠ {len(config['warnings'])} problem(s) in .mini-dep-bot.yml:[/bold yellow]")
        for warning in config["warnings"]:
            console.print(f"  [yellow]- {warning}[/yellow]")
    if config["ignore"] or config["pin"] or config["automerge_patch"] or config["combined_pr"] or config["exclude_paths"]:
        console.print(
            f"  [dim]config:[/dim] ignoring {sorted(config['ignore']) or 'none'}, "
            f"pinning {config['pin'] or 'none'}, "
            f"automerge {'patch-only' if config['automerge_patch'] else 'off'}, "
            f"mode {'combined' if config['combined_pr'] else 'per-manifest'}, "
            f"excluding {sorted(config['exclude_paths']) or 'none'}"
        )

    manifest_paths = discover_manifest_paths(client, repo, base_branch, config)
    discovered_count = sum(len(paths) for paths in manifest_paths.values())
    console.print(f"  [dim]discovered[/dim] {discovered_count} manifest file(s) across the repo")

    all_prs = []
    updates_log = []
    automerge_failures = []
    if config["combined_pr"]:
        console.print("  [dim]mode:[/dim] combined PR across all manifests")
        pr_url = run_combined(
            client, repo, base_branch, config, manifest_paths=manifest_paths,
            dry_run=dry_run, updates_log=updates_log, automerge_failures=automerge_failures,
        )
        if pr_url:
            all_prs.append(pr_url)
    else:
        for manifest_type, parse_fn, lookup_fn, bump_fn in MANIFESTS:
            for path in manifest_paths.get(manifest_type, []):
                console.print(f"  [dim]scanning[/dim] {path}...")
                pr_url = check_manifest(
                    client, repo, base_branch, path, parse_fn, lookup_fn, bump_fn, config,
                    manifest_type=manifest_type, dry_run=dry_run, updates_log=updates_log,
                    automerge_failures=automerge_failures,
                )
                if pr_url:
                    all_prs.append(pr_url)

    if updates_log and not dry_run:
        try:
            with open(WORKFLOW_UPDATES_FILE, "w") as f:
                json.dump(updates_log, f)
        except OSError:
            pass  # best-effort — the workflow's lockfile step just finds nothing to do

    if all_prs:
        verb = "Would open/update" if dry_run else "Opened/updated"
        console.print(f"[bold green]{verb} {len(all_prs)} pull request(s):[/bold green]")
        for url in all_prs:
            console.print(f"  - {url}")
    else:
        console.print(
            "[green]Everything is already up to date[/green] "
            "(or no supported manifest files were found)."
        )

    _write_step_summary(repo, base_branch, dry_run, config, all_prs, automerge_failures)


if __name__ == "__main__":
    main()
