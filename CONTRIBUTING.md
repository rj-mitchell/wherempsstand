# Contributing

Corrections, bug reports and pull requests are welcome. For a problem with something the site says about a person,
use the report form on the site ([/corrections](https://wherempsstand.com/corrections)), so that it is handled under
the site's corrections policy.

## Setting up

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and Node 22.

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample
uv run hansard serve
```

The sample data is enough for most work on the code and the pages. [docs/OPERATIONS.md](docs/OPERATIONS.md)
describes running the site with the full record.

## Tests and checks

Run these before opening a pull request. CI runs the same checks.

```bash
uv run ruff check src tests scripts
uv run pytest
cd web && npm run typecheck && npm test && npm run build
```

The Python tests run offline, with the AI model faked, and need no API key.

## Style

- Python follows the ruff configuration in `pyproject.toml` (line length 100).
- Copy on the site and in the documents is in plain British English, with sentence-case headings and dates written
  as "6 November 2026". The Where They Stand family's style guide (`family/GLOSSARY.md` in the charity's repository)
  has the shared names and wording.
- The files in `web/public/family/` are copied from the family kit and are not edited here
  ([docs/DESIGN.md](docs/DESIGN.md)).
- Shared behaviour (settings, caps, checks on briefings, reports, backups) belongs in
  [wts-core](https://github.com/rj-mitchell/wts-core) and reaches this site as a new version of the package, not as
  a local patch.
- A change to what the site does for readers should be recorded in [docs/DECISIONS.md](docs/DECISIONS.md) with its
  date.

## Commits and pull requests

Write commit messages as plain sentences: a short summary line, a blank line, then what changed and why. Keep each
pull request to one change, and say in its description how it was tested.

## Licence

By contributing you agree that your contribution is licensed under the AGPL-3.0, the licence of this repository.
