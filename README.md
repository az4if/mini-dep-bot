# 🤖 mini-dep-bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://github.com/az4if/your-repo/actions/workflows/tests.yml/badge.svg)](https://github.com/az4if/your-repo/actions/workflows/tests.yml)
![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)

> A small, self-built GitHub bot inspired by Renovate and Dependabot — no
> wrapper libraries, talks to the GitHub REST/GraphQL APIs directly.

It scans every `package.json`, `requirements.txt`, `go.mod`, `pyproject.toml`,
`Cargo.toml`, `Gemfile`, and `composer.json` anywhere in a repository — not
just the root, so monorepos are covered too — for outdated dependencies,
checking npm, PyPI, the Go module proxy, crates.io, RubyGems, and Packagist,
and opens a pull request per manifest bundling every update it finds.

> [!NOTE]
> The **Tests** badge above points at `your-username/your-repo` — swap that
> for wherever you push this project (see [Setup](#setup)) and it'll turn
> green once the workflow has run.

## 📋 Contents

- [✨ Features](#features)
- [⚙️ How it works](#how-it-works)
- [🗂️ Project layout](#project-layout)
- [🚀 Setup](#setup)
- [☁️ Deploying with GitHub Actions](#deploying-with-github-actions)
- [🏢 Monorepos](#monorepos)
- [🔒 Lockfiles](#lockfiles)
- [🛠️ Configuration](#configuration)
- [🙈 Ignoring a single dependency](#ignoring-a-single-dependency)
- [⚠️ Limitations](#limitations)
- [💬 Support](#support)
- [📄 License](#license)

<a name="features"></a>
## ✨ Features

- Checks seven ecosystems: npm (`package.json`), Python (`requirements.txt`,
  Poetry-style and PEP 621 `pyproject.toml`), Go (`go.mod`), Rust
  (`Cargo.toml`), Ruby (`Gemfile`), and PHP (`composer.json`)
- Scans the whole repo, not just the root — a monorepo with several
  `package.json`/`requirements.txt` files gets all of them, automatically
  (see [🏢 Monorepos](#monorepos) below)
- Range-aware: a `^2.1.0` / `~=2.31` / etc. dependency is only flagged
  outdated once a release actually escapes that range, not on every new
  patch it already allows
- Looks up real latest-version data from each package registry, cached for
  the run so the same package is never looked up twice
- Opens one pull request per manifest, bundling every outdated dependency
  found in it, labeled `dependencies` + an ecosystem tag — re-running the
  bot updates that same PR with a new commit instead of opening a duplicate
- Notes in the PR when a bump also closes a known vulnerability (via
  [OSV.dev](https://osv.dev)), links each dependency's changelog/homepage
  when the registry exposes one, and — when run via the provided GitHub
  Actions workflow — regenerates any companion lockfile for real, using the
  actual package manager, whichever of npm/Yarn/pnpm's is actually present
  for a JS manifest (see [🔒 Lockfiles](#lockfiles) below)
- Optional auto-merge for a PR where every bundled bump is patch-level,
  gated behind `.mini-dep-bot.yml`
- Optional combined mode: one PR for every manifest instead of one PR per
  manifest, via `.mini-dep-bot.yml`'s `combined_pr: true`
- A `# mini-dep-bot: ignore` comment on any dependency line excludes it,
  no config file edit required (see [🙈 Ignoring a single
  dependency](#ignoring-a-single-dependency))
- A readable job summary in the GitHub Actions UI (`$GITHUB_STEP_SUMMARY`)
  in addition to console logs
- A companion workflow deletes a mini-dep-bot branch once its PR merges
- `--dry-run` (or `DRY_RUN=true`) reports what it would change using only
  read-only API calls — no branch, commit, PR, label, or auto-merge setting
  gets created or changed
- Optional `.mini-dep-bot.yml` config to ignore specific packages, pin one
  to a max major version, opt into auto-merge, combine every manifest into
  one PR, or exclude extra paths from being scanned
- Runs on demand or on a schedule via GitHub Actions
- No wrapper libraries — talks to the GitHub REST/GraphQL APIs directly
  with `requests`

> [!IMPORTANT]
> Two things worth knowing up front:
> - Ecosystem parsing covers the common cases for each format (including
>   Cargo's nested `[dependencies.name]` tables and implicit caret default,
>   and Ruby's pessimistic `~>` operator) but isn't exhaustive — see
>   [⚠️ Limitations](#limitations) for the exact scope of each.
> - Lockfiles get regenerated for real, but only when run through the
>   provided GitHub Actions workflow (see [🔒 Lockfiles](#lockfiles)) —
>   running `bot.py` standalone still leaves that as a manual step.

<a name="how-it-works"></a>
## ⚙️ How it works

1. Reads each supported manifest from a target repo via the GitHub Contents
   API (`github_api.py`).
2. Parses out each dependency and its current pinned version (`parsers.py`).
3. Looks up the latest published version from the relevant registry —
   npm, PyPI, the Go module proxy, crates.io, RubyGems, or Packagist
   (`registries.py`) — and whether current/latest differ enough to matter
   given any range operator involved.
4. Best-effort: checks OSV.dev for vulnerabilities the bump would resolve,
   and fetches a changelog/homepage link (`security.py`, `registries.py`).
5. For anything outdated: creates a branch, updates the manifest file, and
   opens (or updates) a labeled PR through the GitHub REST API — enabling
   auto-merge if configured and every bump is patch-level — unless
   `--dry-run` is set, in which case it only reports what it would have done.

<a name="project-layout"></a>
## 🗂️ Project layout

```
mini-dep-bot/
├── bot.py                      # entrypoint / orchestration
├── config.py                   # loads optional .mini-dep-bot.yml
├── github_api.py                # GitHub REST + GraphQL calls
├── parsers.py                   # manifest parsing + version comparison
├── registries.py                 # "latest version" + changelog lookups
├── security.py                   # OSV.dev vulnerability lookups
├── requirements.txt              # runtime dependencies
├── requirements-dev.txt          # +pytest, for running tests/
├── tests/                        # pytest suite (parsers, config, bot, etc.)
├── .mini-dep-bot.yml.example     # template for the optional config file
└── .github/workflows/
    ├── dependency-check.yml      # runs the bot + lockfile regen weekly
    ├── cleanup-merged-branches.yml  # deletes a bot branch once its PR merges
    └── tests.yml                 # runs tests/ on push and PR
```

<a name="setup"></a>
## 🚀 Setup

Running it locally is the fastest way to try it out; [☁️ Deploying with
GitHub Actions](#deploying-with-github-actions) below is the way to actually
run it long-term.

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a [personal access token](https://github.com/settings/tokens):
   classic token with `repo` scope, or a fine-grained token with
   Contents (read/write), Pull requests (read/write), and Issues (write —
   needed for labels) permissions on the target repo.
3. Set environment variables:
   ```bash
   export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
   export TARGET_REPO=your-username/your-repo
   ```
4. Run it:
   ```bash
   python bot.py
   ```
   Or, to see what it would do without touching the repo:
   ```bash
   python bot.py --dry-run
   ```

> [!TIP]
> Start with `--dry-run` on a real repo before letting it open PRs for
> real — it makes only read-only API calls, so nothing gets touched.

<a name="running-the-tests"></a>
### Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
Everything in `tests/` runs against mocked GitHub/registry calls — no
network access or token needed.

<a name="deploying-with-github-actions"></a>
## ☁️ Deploying with GitHub Actions

This is the intended way to run the bot — no external hosting, no
timeouts to worry about, and it uses GitHub's own automatic token instead
of a personal access token you'd have to create and store.

### 1. Push the project to a repo

```bash
cd mini-dep-bot
git init
git add .
git commit -m "Add mini-dep-bot"
git remote add origin https://github.com/your-username/your-repo.git
git branch -M main
git push -u origin main
```

This can be a brand-new repo or an existing one — the bot only touches
whichever supported manifests it finds at the root, and skips any that
aren't there.

### 2. Allow Actions to open pull requests

By default, some repos restrict what the built-in Actions token can do.
Go to your repo's **Settings → Actions → General → Workflow permissions**
and:

- Select **"Read and write permissions"**
- Check **"Allow GitHub Actions to create and approve pull requests"**

> [!WARNING]
> Without this, the workflow will run but fail at the PR-creation step.
>
> If you plan to use `automerge` in `.mini-dep-bot.yml`, also turn on
> **Settings → General → Allow auto-merge** — without it, GitHub rejects
> the auto-merge request (the bot now surfaces this as a warning, both in
> the console output and the Actions step summary, instead of staying quiet
> about it).

### 3. That's it — no secrets to add

`.github/workflows/dependency-check.yml` already uses GitHub's automatic,
per-run `secrets.GITHUB_TOKEN` and resolves the target repo itself via
`${{ github.repository }}`. There's no personal access token to generate
and no repo secret to configure. `cleanup-merged-branches.yml` uses the
same automatic token — nothing extra to set up for it either.

### 4. Run it

- **Automatically:** the workflow runs every Monday at 06:00 UTC, per the
  `cron` schedule in the workflow file.
- **Manually, any time:** go to the **Actions** tab → **mini-dep-bot** →
  **Run workflow**. This button exists because of `workflow_dispatch` in
  the workflow file — it has a **Dry run** checkbox if you want to preview
  changes without opening any PRs.
- Triggering a manual run while the scheduled one is still going doesn't
  race — `dependency-check.yml`'s `concurrency:` block queues the newer
  run instead of letting both touch the same branches at once.

### 5. Check the results

- **Pull requests** tab — any dependency bumps the bot opened, labeled by
  ecosystem
- **Actions** tab → the run → its **Summary** — a readable breakdown of
  what was checked, the active config (plus any `.mini-dep-bot.yml`
  warnings or rejected auto-merge requests, if either happened), and the
  PRs opened/updated ($GITHUB_STEP_SUMMARY), in addition to the full
  console logs on the same page
- Once a bot PR merges, **cleanup-merged-branches.yml** deletes its branch
  automatically (skip this by turning off/deleting that workflow file — it's
  redundant if the repo already has **Settings → General → Automatically
  delete head branches** turned on for everything)

<a name="changing-the-schedule"></a>
### Changing the schedule

Edit the `cron` line in `.github/workflows/dependency-check.yml`. It uses
standard 5-field cron syntax in UTC, e.g. `0 6 * * 1` = every Monday at
06:00 UTC, `0 0 * * *` = daily at midnight UTC.

<a name="monorepos"></a>
## 🏢 Monorepos

Manifest discovery isn't hardcoded to the repo root — before scanning,
`bot.py` lists every file git tracks in the repo (one call, via the
recursive Git Trees API) and finds every `package.json`,
`requirements.txt`, etc at any depth. A repo with `apps/web/package.json`
and `apps/api/requirements.txt` gets both, automatically, no config needed.
Each nested manifest gets its own PR (or its own commit within a combined
PR, if `combined_pr: true`) and its own branch, e.g.
`mini-dep-bot/apps-web-package-json/updates`.

A few defaults keep this safe:
- `node_modules`, `vendor`, `.venv`/`venv`, `dist`, `build`, `target`, and a
  few other common noise directories are never scanned, even if a repo
  commits them. In practice this rarely matters — a properly gitignored
  `node_modules` never shows up in the tracked-file listing in the first
  place — but it's there as a backstop.
- If listing the repo's files fails for any reason, discovery falls back
  to root-only manifests — the same behavior as before monorepo support
  existed, rather than failing the run.
- Add your own exclusions with `.mini-dep-bot.yml`'s `exclude_paths` (see
  [🛠️ Configuration](#configuration)) — useful for a vendored/example
  directory that isn't one of the built-in defaults.

<a name="lockfiles"></a>
## 🔒 Lockfiles

`bot.py` itself only ever edits a manifest file via the GitHub API — it
has no repo checkout or toolchain, and hand-editing a lockfile risks
producing one with a stale integrity hash or a resolution that doesn't
match what's declared. So instead, the provided workflow handles this
properly in a follow-up step that has both:

1. `bot.py` records which `(branch, lockfile)` pairs it actually changed
   to `.mini-dep-bot-updates.json` — `lockfile` is the full path next to
   the manifest that changed (e.g. `apps/web/package-lock.json` for a
   monorepo, not just `package-lock.json`).
2. The workflow's **"Regenerate lockfiles"** step checks out each of those
   branches, `cd`s into the lockfile's own directory, and runs the real
   package manager there — `npm install --package-lock-only`, `yarn
   install --mode update-lockfile`, `pnpm install --lockfile-only`,
   `cargo update`, `bundle lock`, `poetry lock --no-update`, `go mod
   tidy`, or `composer update --lock` — then pushes the regenerated
   lockfile back to the same branch if it changed. It ends up as a second
   commit on the same PR the bot opened.

For a JS manifest specifically, `bot.py` checks for `package-lock.json`,
`yarn.lock`, and `pnpm-lock.yaml` in that order and uses whichever one is
actually present next to the manifest — a Yarn or pnpm project gets the
right lockfile touched, not an npm one by default.

This relies on `ubuntu-latest` runners already having Node/npm,
Ruby/Bundler, Go, Rust/Cargo, PHP/Composer, and `pipx` installed, which is
true of the default GitHub-hosted runner image; `corepack enable` (already
in the workflow) covers Yarn/pnpm on Node 16.9+ without a separate setup
step. If your runner is missing one of these anyway, add the matching
`actions/setup-*` step before "Regenerate lockfiles" in the workflow file.

> [!NOTE]
> This step only runs as part of the GitHub Actions workflow. Running
> `python bot.py` standalone updates the manifest but not the lockfile —
> regenerate it yourself with the command above for your ecosystem, from
> the manifest's own directory.

<a name="configuration"></a>
## 🛠️ Configuration

Drop a `.mini-dep-bot.yml` at the root of the target repo to customize
what the bot touches (see `.mini-dep-bot.yml.example` for a template).
All five keys are optional:

```yaml
ignore:
  - some-noisy-package     # never opens a PR for this one

pin:
  some-package: 2          # stays on major version 2 — picks up
                            # minor/patch releases, not major bumps

automerge: patch           # auto-merge a PR once checks pass, but
                            # only when every bump in it is patch-level
                            # (also accepts true/yes)

combined_pr: true          # one PR for every manifest instead of one
                            # PR per manifest

exclude_paths:              # directories/files never scanned for
  - examples/                # manifests, on top of the built-in
  - legacy-app/               # node_modules/vendor/etc exclusions
```

No config file at all means the previous behavior: nothing ignored,
nothing pinned, auto-merge off, one PR per manifest, every manifest in
the repo scanned except the built-in noise-directory defaults.

> [!TIP]
> A malformed entry — wrong type, or a value like `pin: {some-pkg: two}`
> that isn't parseable — doesn't crash the run, but it also doesn't fail
> silently: it's skipped and reported both in the console output and the
> GitHub Actions step summary, so a typo in this file is never
> indistinguishable from "nothing configured".

<a name="ignoring-a-single-dependency"></a>
## 🙈 Ignoring a single dependency

For a one-off "don't touch this" that doesn't need a `.mini-dep-bot.yml`
edit, add a `mini-dep-bot: ignore` comment on the same line as the
dependency. The whole line is skipped by the parser — the bot won't see
it at all, so it can't be bumped or counted toward anything.

```
# requirements.txt
requests==2.31.0  # mini-dep-bot: ignore
```
```json
// package.json — not supported, JSON has no comment syntax.
// Use .mini-dep-bot.yml's `ignore:` list instead.
```
```toml
# pyproject.toml / Cargo.toml
serde = "1.0.152"  # mini-dep-bot: ignore
```
```ruby
# Gemfile
gem "rails", "7.1.2"  # mini-dep-bot: ignore
```
```go
// go.mod
github.com/some/module v1.2.3 // mini-dep-bot: ignore
```

This works for every format that has a comment syntax. `package.json` and
`composer.json` are JSON, which doesn't support comments — use
`.mini-dep-bot.yml`'s `ignore:` list for those instead.

<a name="limitations"></a>
## ⚠️ Limitations

- Version comparison uses `packaging`'s PEP 440 comparator (with a
  numeric-segment fallback for anything it can't parse, e.g. some Go
  pseudo-versions) — reliable for standard version schemes, less so for
  unusual ones.
- Range-awareness for `^`/`~` (npm/Poetry) is an approximation, not a full
  semver-range implementation — it doesn't special-case every edge (e.g. a
  bare `~1` with no minor segment).
- Grouping is per-manifest by default — a repo with outdated npm *and* pip
  packages gets two PRs, one per manifest. Set `combined_pr: true` in
  `.mini-dep-bot.yml` for one PR across every manifest instead (still one
  commit per manifest that changed, just bundled into a single PR).
- The `# mini-dep-bot: ignore` inline comment only works on formats with a
  comment syntax. `package.json` and `composer.json` are JSON, which
  doesn't support comments — use `.mini-dep-bot.yml`'s `ignore:` list for
  those.
- `requirements.txt` only tracks `==`, `>=`, and `~=` pins. `<=`, `<`, and
  `!=` are left alone — those represent an explicit ceiling or exclusion,
  and bumping past one would silently violate a constraint that's there on
  purpose.
- `pyproject.toml` understands both Poetry's
  `[tool.poetry.dependencies]` tables and PEP 621's
  `[project] dependencies = [...]` array (only its `==`/`>=`/`~=` entries —
  same reasoning as `requirements.txt` above).
- `Cargo.toml` understands both the inline `[dependencies]` style and
  nested `[dependencies.name]` tables. A bare version with no operator
  (e.g. `serde = "1.0.152"`) is normalized to Cargo's actual default —
  caret semantics — rather than compared as an exact pin.
- `Gemfile` bumps `gem "name", "version"` lines using an exact version, a
  `>=` floor, or Ruby's pessimistic `~>` operator (compared with its real
  range semantics — equivalent to PEP 440's `~=`). `<=`, `<`, and `!=` are
  left alone (explicit ceiling/exclusion, same reasoning as
  `requirements.txt`), as are unpinned or `git:`/`path:`-sourced gems —
  there's no registry version to compare those against.
- **Lockfiles aren't hand-edited** — see [🔒 Lockfiles](#lockfiles) above
  for how they get regenerated for real when run via the provided
  workflow, and what still requires a manual step outside of it.
- Manifest discovery (see [🏢 Monorepos](#monorepos)) uses GitHub's
  recursive Git Trees API, which caps out on extremely large repos (very
  high file counts / response size) — a truncated listing just means some
  deeply nested manifests may not get discovered, rather than the run
  failing.
- The `automerge` config only ever asks GitHub to auto-merge — it never
  bypasses branch protection or required status checks, and does nothing
  if the repo's "Allow auto-merge" setting is off. If GitHub rejects the
  request, that's surfaced as a console warning and a section in the
  GitHub Actions step summary — it isn't retried, since the usual causes
  (branch protection not configured, the repo setting being off) aren't
  transient.
- Go module proxy lookups need outbound access to `proxy.golang.org`;
  crates.io, RubyGems, Packagist, and OSV lookups likewise need access to
  `crates.io`, `rubygems.org`, `repo.packagist.org`, and `api.osv.dev`.

<a name="support"></a>
## 💬 Support

Questions, bug reports, or integration issues: [az4if@proton.me](mailto:az4if@proton.me)

<a name="license"></a>
## 📄 License

This project is licensed under the [MIT License](LICENSE).
