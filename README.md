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

## Running it automatically

`.github/workflows/dependency-check.yml` runs the bot every Monday via
GitHub Actions, using the repo's built-in `GITHUB_TOKEN`. In the target
repo's **Settings → Actions → General**, make sure "Allow GitHub Actions
to create and approve pull requests" is enabled, or the PR-opening step
will be rejected.

## Limitations

- Version comparison uses a simple numeric-segment comparator, not full
  semver / PEP 440 / Go module semantics — fine for plain `X.Y.Z`
  versions, less reliable for pre-releases or unusual version schemes.
- One PR per outdated dependency (no grouping).
- Only exact (`==`) pins are handled in `requirements.txt`.
- Go module proxy lookups need outbound access to `proxy.golang.org`.

## License

MIT
