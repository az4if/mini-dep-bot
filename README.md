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

## Running it on Vercel (alternative to GitHub Actions)

You can run this on Vercel instead of Actions. `api/check.py` wraps the
same checking logic behind an HTTP endpoint (Vercel functions must
respond to requests — they can't run a plain script), and `vercel.json`
schedules it with Vercel Cron.

1. Push this repo to GitHub and import it into Vercel.
2. In the Vercel project's **Settings → Environment Variables**, add:
   - `GITHUB_TOKEN` — a token with Contents + Pull requests read/write on the target repo
   - `TARGET_REPO` — `owner/repo`
3. Deploy. Vercel will register the cron schedule from `vercel.json`
   automatically (default: every Monday at 06:00 UTC).
4. To trigger it manually and check it's working:
   ```bash
   curl https://your-project.vercel.app/api/check
   ```

**Things to know:**

- Vercel provisions a `CRON_SECRET` env var automatically and sends it as
  `Authorization: Bearer <value>` on cron-triggered requests. `api/check.py`
  checks for this, so manual `curl` calls without it will get a 401 once
  `CRON_SECRET` exists in your project — that's expected, not a bug.
- **Hobby plan**: cron jobs can only run once per day (weekly, as configured,
  is fine), and functions default to a 10s timeout, 60s max — set here via
  `maxDuration` in `vercel.json`. If a repo has many outdated dependencies,
  the sequential API calls could approach that limit; Pro raises the max to
  300s.
- Unlike the Actions workflow, this needs the env vars set manually in the
  Vercel dashboard rather than as repo secrets.

## Limitations

- Version comparison uses a simple numeric-segment comparator, not full
  semver / PEP 440 / Go module semantics — fine for plain `X.Y.Z`
  versions, less reliable for pre-releases or unusual version schemes.
- One PR per outdated dependency (no grouping).
- Only exact (`==`) pins are handled in `requirements.txt`.
- Go module proxy lookups need outbound access to `proxy.golang.org`.

## License

MIT
