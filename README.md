# mini-dep-bot

A small, self-built GitHub bot inspired by Renovate and Dependabot. It scans
a repository's `package.json`, `requirements.txt`, `go.mod`, `pyproject.toml`,
`Cargo.toml`, `Gemfile`, and `composer.json` for outdated dependencies —
checking npm, PyPI, the Go module proxy, crates.io, RubyGems, and Packagist —
and opens a pull request per manifest bundling every update it finds.

## Features

- Checks seven ecosystems: npm (`package.json`), Python (`requirements.txt`,
  Poetry-style and PEP 621 `pyproject.toml`), Go (`go.mod`), Rust
  (`Cargo.toml`), Ruby (`Gemfile`), and PHP (`composer.json`)
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
  when the registry exposes one, and flags when a companion lockfile needs
  regenerating
- Optional auto-merge for a PR where every bundled bump is patch-level,
  gated behind `.mini-dep-bot.yml`
- `--dry-run` (or `DRY_RUN=true`) reports what it would change using only
  read-only API calls — no branch, commit, PR, label, or auto-merge setting
  gets created or changed
- Optional `.mini-dep-bot.yml` config to ignore specific packages, pin one
  to a max major version, or opt into auto-merge
- Runs on demand or on a schedule via GitHub Actions
- No wrapper libraries — talks to the GitHub REST/GraphQL APIs directly
  with `requests`

**Two things worth knowing up front:**
- Some ecosystem parsing is intentionally narrow (e.g. only certain
  `Cargo.toml`/`Gemfile` styles — see [Limitations](#limitations) for the
  exact scope of each).
- Lockfiles aren't updated automatically, so you'll still need a manual
  step for `package-lock.json`/`Cargo.lock`/etc. after merging.

## How it works

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

## Project layout

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
    ├── dependency-check.yml      # runs the bot weekly via GitHub Actions
    └── tests.yml                 # runs tests/ on push and PR
```

## Setup

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

### Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```
Everything in `tests/` runs against mocked GitHub/registry calls — no
network access or token needed.

## Deploying with GitHub Actions

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

Without this, the workflow will run but fail at the PR-creation step.

If you plan to use `automerge` in `.mini-dep-bot.yml`, also turn on
**Settings → General → Allow auto-merge** — without it, GitHub silently
ignores the auto-merge request (the bot logs this as a no-op, not an error).

### 3. That's it — no secrets to add

`.github/workflows/dependency-check.yml` already uses GitHub's automatic,
per-run `secrets.GITHUB_TOKEN` and resolves the target repo itself via
`${{ github.repository }}`. There's no personal access token to generate
and no repo secret to configure.

### 4. Run it

- **Automatically:** the workflow runs every Monday at 06:00 UTC, per the
  `cron` schedule in the workflow file.
- **Manually, any time:** go to the **Actions** tab → **mini-dep-bot** →
  **Run workflow**. This button exists because of `workflow_dispatch` in
  the workflow file — it has a **Dry run** checkbox if you want to preview
  changes without opening any PRs.

### 5. Check the results

- **Pull requests** tab — any dependency bumps the bot opened, labeled by
  ecosystem
- **Actions** tab → the run's logs — a summary of what it checked and
  either the opened PR links or "everything is up to date"

### Changing the schedule

Edit the `cron` line in `.github/workflows/dependency-check.yml`. It uses
standard 5-field cron syntax in UTC, e.g. `0 6 * * 1` = every Monday at
06:00 UTC, `0 0 * * *` = daily at midnight UTC.

## Configuration

Drop a `.mini-dep-bot.yml` at the root of the target repo to customize
what the bot touches (see `.mini-dep-bot.yml.example` for a template).
All three keys are optional:

```yaml
ignore:
  - some-noisy-package     # never opens a PR for this one

pin:
  some-package: 2          # stays on major version 2 — picks up
                            # minor/patch releases, not major bumps

automerge: patch           # auto-merge a manifest's PR once checks
                            # pass, but only when every bump in it is
                            # patch-level (also accepts true/yes)
```

No config file at all means the previous behavior: nothing ignored,
nothing pinned, auto-merge off.

## Limitations

- Version comparison uses `packaging`'s PEP 440 comparator (with a
  numeric-segment fallback for anything it can't parse, e.g. some Go
  pseudo-versions) — reliable for standard version schemes, less so for
  unusual ones.
- Range-awareness for `^`/`~` (npm/Poetry) is an approximation, not a full
  semver-range implementation — it doesn't special-case every edge (e.g. a
  bare `~1` with no minor segment).
- Grouping is per-manifest, not per-repo — a repo with outdated npm *and*
  pip packages gets two PRs, one per manifest, not one combined PR.
- `requirements.txt` only tracks `==`, `>=`, and `~=` pins. `<=`, `<`, and
  `!=` are left alone — those represent an explicit ceiling or exclusion,
  and bumping past one would silently violate a constraint that's there on
  purpose.
- `pyproject.toml` understands both Poetry's
  `[tool.poetry.dependencies]` tables and PEP 621's
  `[project] dependencies = [...]` array (only its `==`/`>=`/`~=` entries —
  same reasoning as `requirements.txt` above).
- `Cargo.toml` only understands the inline `[dependencies]` /
  `[dev-dependencies]` / `[build-dependencies]` style, not nested
  `[dependencies.name]` tables. A bare version with no operator (e.g.
  `serde = "1.0.152"`) is Cargo's default caret requirement, but this bot
  compares it as an exact pin rather than modeling that implicit range.
- `Gemfile` only bumps `gem "name", "version"` lines with an explicit
  version constraint; unpinned or `git:`/`path:`-sourced gems are skipped.
  Ruby's pessimistic `~>` operator is compared numerically, not with true
  pessimistic-constraint range logic.
- **Lockfiles aren't regenerated.** `package-lock.json`, `poetry.lock`,
  `Cargo.lock`, `Gemfile.lock`, `go.sum`, and `composer.lock` all encode a
  fully resolved dependency graph with integrity hashes — correctly
  updating one means running the actual package manager, which needs a
  real repo checkout and toolchain this bot (deliberately scoped to the
  GitHub API only, no local checkout) doesn't have. A naive hand-edit risks
  a broken lockfile (e.g. a stale integrity hash). Instead, the bot detects
  a companion lockfile and adds a note to the PR body naming the command to
  regenerate it.
- The `automerge` config only ever asks GitHub to auto-merge — it never
  bypasses branch protection or required status checks, and does nothing
  if the repo's "Allow auto-merge" setting is off.
- Go module proxy lookups need outbound access to `proxy.golang.org`;
  crates.io, RubyGems, Packagist, and OSV lookups likewise need access to
  `crates.io`, `rubygems.org`, `repo.packagist.org`, and `api.osv.dev`.

## Support

Questions, bug reports, or integration issues: [az4if@proton.me](mailto:az4if@proton.me)

## License

This project is licensed under the [MIT License](LICENSE).
