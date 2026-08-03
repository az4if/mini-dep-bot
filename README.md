# mini-dep-bot

A small, self-built GitHub bot inspired by Renovate and Dependabot. It scans
a repository's `package.json`, `requirements.txt`, and `go.mod` for outdated
dependencies — checking npm, PyPI, and the Go module proxy — and opens a
pull request for each one it finds.

## Features

- Checks three ecosystems: npm (`package.json`), Python (`requirements.txt`),
  and Go (`go.mod`)
- Looks up real latest-version data from each package registry
- Opens one pull request per outdated dependency, with a clear title and
  description of the version bump
- Runs on demand or on a schedule via GitHub Actions
- No wrapper libraries — talks to the GitHub REST API directly with `requests`

## How it works

1. Reads `package.json` / `requirements.txt` / `go.mod` from a target repo
   via the GitHub Contents API (`github_api.py`).
2. Parses out each dependency and its current pinned version (`parsers.py`).
3. Looks up the latest published version from the relevant registry —
   npm registry, PyPI, or the Go module proxy (`registries.py`).
4. For anything outdated: creates a branch, updates the manifest file, and
   opens a PR through the GitHub REST API.

## Project layout

```
mini-dep-bot/
├── bot.py               # entrypoint / orchestration
├── github_api.py         # GitHub REST API calls (branches, files, PRs)
├── parsers.py             # manifest parsing + version comparison
├── registries.py          # npm / PyPI / Go proxy "latest version" lookups
├── requirements.txt       # this project's own dependency (requests)
└── .github/workflows/
    └── dependency-check.yml   # runs the bot weekly via GitHub Actions
```

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Create a [personal access token](https://github.com/settings/tokens):
   classic token with `repo` scope, or a fine-grained token with
   Contents (read/write) and Pull requests (read/write) permissions on
   the target repo.
3. Set environment variables:
   ```bash
   export GITHUB_TOKEN=ghp_xxxxxxxxxxxx
   export TARGET_REPO=your-username/your-repo
   ```
4. Run it:
   ```bash
   python bot.py
   ```

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
whichever of `package.json`, `requirements.txt`, or `go.mod` it finds at
the root, and skips any that aren't there.

### 2. Allow Actions to open pull requests

By default, some repos restrict what the built-in Actions token can do.
Go to your repo's **Settings → Actions → General → Workflow permissions**
and:

- Select **"Read and write permissions"**
- Check **"Allow GitHub Actions to create and approve pull requests"**

Without this, the workflow will run but fail at the PR-creation step.

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
  the workflow file.

### 5. Check the results

- **Pull requests** tab — any dependency bumps the bot opened
- **Actions** tab → the run's logs — a summary of what it checked and
  either the opened PR links or "everything is up to date"

### Changing the schedule

Edit the `cron` line in `.github/workflows/dependency-check.yml`. It uses
standard 5-field cron syntax in UTC, e.g. `0 6 * * 1` = every Monday at
06:00 UTC, `0 0 * * *` = daily at midnight UTC.

## Limitations

- Version comparison uses a simple numeric-segment comparator, not full
  semver / PEP 440 / Go module semantics — fine for plain `X.Y.Z`
  versions, less reliable for pre-releases or unusual version schemes.
- One PR per outdated dependency (no grouping).
- Only exact (`==`) pins are handled in `requirements.txt`.
- Go module proxy lookups need outbound access to `proxy.golang.org`.

## Support

Questions, bug reports, or integration issues: [az4if@proton.me](mailto:az4if@proton.me)

## License

This project is licensed under the [MIT License](LICENSE).

